"""Current effect resource ownership, shared by clipboard and publication.

Authored resources and the primary source's resource bindings follow scene
references. Borrowed unchanged game resources stay logical references; their
whole dependency graph is not part of the author's resource collection.
"""
import base64
import copy
import re

if __package__:
    from . import eiem_effect_document as document
else:
    import eiem_effect_document as document

KINDS = frozenset((21, 28, 43))
_FIELD = re.compile(r'\.([^\.\[\]]+)|\[(\d+)\]')


def path(field):
    return tuple(int(index) if index else name for name, index in _FIELD.findall(field))


def pointers(value, prefix=()):
    if isinstance(value, dict):
        if set(value) == {'m_FileID', 'm_PathID'}:
            yield prefix, value
        else:
            for key, item in value.items():
                yield from pointers(item, (*prefix, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from pointers(item, (*prefix, index))


def target(edit, owner, pointer):
    """Resolve the current pointer; never trust a stale reference's identity."""
    fid, pid = int(pointer.get('m_FileID', 0)), int(pointer.get('m_PathID', 0))
    if not pid:
        return None
    own = edit.objects[owner]['native']['cab'].casefold()
    cab = own if fid == 0 else None
    if fid > 0:
        table = edit.state.get('externalTables', {}).get(own, [])
        if fid <= len(table):
            cab = table[fid - 1]
        if cab is None:
            cabs = {r.get('cab') for spec in edit.objects.values()
                    if spec['native']['cab'].casefold() == own
                    for r in spec.get('references', [])
                    if int(r.get('fileId', -1)) == fid and r.get('cab')}
            if len(cabs) == 1:
                cab = cabs.pop()
    candidate = str(cab).casefold() + ':' + str(pid) if cab else None
    return next((key for key in edit.objects if candidate and key.casefold() == candidate), None)


def references(edit, identity):
    """Enumerate actual PPtrs, including new array entries absent from catalog."""
    metadata = {path(r.get('field', '')): r for r in edit.objects[identity].get('references', [])}
    for field, pointer in pointers(edit.state['values'].get(identity, {})):
        ref = copy.deepcopy(metadata.get(field, {}))
        # A logical binding describes a field only while that binding is selected.
        if (int(ref.get('fileId', -1)), int(ref.get('pathId', -1))) != (
                int(pointer['m_FileID']), int(pointer['m_PathID'])):
            ref.pop('gameLogical', None)
        yield field, pointer, target(edit, identity, pointer), ref


def owned(edit, identity):
    spec = edit.objects.get(identity, {})
    if spec.get('classId') not in KINDS:
        return False
    if any(row['identity'] == identity for row in edit.state.get('author_assets', [])):
        return True
    return (identity in edit.state.get('values', {}) and
            edit.state['values'][identity] != edit.state.get('original', {}).get(identity))


def reachable(edit, seeds):
    """Return active scene objects and owned resources; stop at game resources."""
    active, pending = set(), list(seeds)
    while pending:
        identity = pending.pop()
        if identity in active or identity not in edit.objects:
            continue
        active.add(identity)
        for _, _, resource, _ in references(edit, identity):
            if resource:
                spec = edit.objects[resource]
                if (owned(edit, resource) or
                        spec.get('classId') in KINDS and not spec.get('gameSource')):
                    pending.append(resource)
    return active


def _publication_view(edit, payload):
    """Overlay the encoded snapshot without changing the live author state."""
    state = dict(edit.state, catalog=dict(edit.state['catalog']),
                 values=dict(edit.state['values']),
                 author_assets=copy.deepcopy(payload.get('assets', [])))
    state['catalog']['objects'] = list(edit.state['catalog']['objects'])
    view = document.Editor(state)
    for row in [*payload.get('objects', []), *payload.get('emitters', {}).get('added', [])]:
        identity = row['identity']
        if identity not in view.objects:
            spec = copy.deepcopy(view.objects[row['template']])
            spec.update(identity=identity, template=row['template'])
            spec['native']['pathId'] = int(identity.rsplit(':', 1)[1])
            view.objects[identity] = spec
            state['catalog']['objects'].append(spec)
        state['values'][identity] = row['data']
    if payload.get('assets'):
        if __package__:from .nativepack import type_tree
        else:from nativepack import type_tree
        for row in payload['assets']:
            identity, template = row['identity'], row['template']
            spec = copy.deepcopy(view.objects[template])
            spec.update(identity=identity, template=template, logicalPaths=[])
            spec['native']['pathId'] = int(identity.rsplit(':', 1)[1])
            view.objects[identity] = spec
            state['catalog']['objects'].append(spec)
            # Material PPtrs determine its resources. Mesh/Texture data has no
            # live dependency PPtrs and decoding its large pixel buffers is waste.
            if spec['classId'] == 21:
                schema = type_tree.Schema.compile(state['schemas'][spec['schema']]['nodes'])
                tree = type_tree.decode(schema, base64.b64decode(row['payloadBase64'], validate=True))
                state['values'][identity] = document._native_plain(tree)
            else:
                state['values'][identity] = {}
    return view


def prune(edit, payload, scene_ids=None):
    """Keep only resources used by the current scene/export scope.

    Source templates remain in the preserved baseline. This only trims active
    author additions and logical leases, so it is safe to call again after a
    narrower emitter selection has been resolved.
    """
    view = _publication_view(edit, payload)
    if scene_ids is None:
        seeds = {identity for node in edit.state['catalog'].get('hierarchy', [])
                 for identity in [node['identity'], *node.get('components', [])]}
    else:
        seeds = set(scene_ids)
    active = reachable(view, seeds)
    payload['assets'] = [row for row in payload.get('assets', []) if row['identity'] in active]
    if not payload['assets']:
        payload.pop('assets')
    payload['gameReferences'] = [row for row in payload.get('gameReferences', []) if row['owner'] in active]
    if not payload['gameReferences']:
        payload.pop('gameReferences')
    payload['objects'] = [row for row in payload.get('objects', [])
                          if view.objects[row['identity']]['classId'] not in KINDS or row['identity'] in active]
    return payload


_RESOURCE_FIELDS = {
    28: {'m_Name', 'm_Width', 'm_Height', 'm_CompleteImageSize', 'm_MipsStripped',
         'm_TextureFormat', 'm_MipCount', 'm_IsPreProcessed', 'm_StreamingMipmaps',
         'm_ColorSpace', 'm_TextureSettings', 'm_PlatformBlob', 'image data', 'm_StreamData'},
    43: {'m_Name', 'm_SubMeshes', 'm_Shapes', 'm_BindPose', 'm_BoneNameHashes',
         'm_RootBoneNameHash', 'm_BonesAABB', 'm_VariableBoneCountWeights',
         'm_MeshCompression', 'm_IndexFormat', 'm_IndexBuffer', 'm_VertexData',
         'm_CompressedMesh', 'm_LocalAABB', 'm_StreamData', 'm_BonesPerVertex'},
}


def schema_hash(edit, identity):
    spec = edit.objects[identity]
    schema = edit.state['schemas'].get(spec.get('schema'))
    return document.digest(document.dumps(schema).encode('utf-8')) if schema else ''


def schema_shape_hash(edit, identity):
    """Compare fields for value transfer, independent of binary metadata.

    Clipboard values are encoded with the destination TypeTree. Alignment,
    editor flags and source byte sizes need not match; field names, types and
    hierarchy must. Managed type declarations remain an exact match.
    """
    schema = edit.state['schemas'].get(edit.objects[identity].get('schema'))
    if not schema:
        return ''
    shape = dict(nodes=[(n['level'], n['type'], n['name']) for n in schema['nodes']],
                 managedTypes=schema.get('managedTypes', []))
    return document.digest(document.dumps(shape).encode('utf-8'))


def schema_matches(edit, identity, row):
    return (row.get('schemaSha256') == schema_hash(edit, identity) or
            bool(row.get('schemaShapeSha256')) and
            row['schemaShapeSha256'] == schema_shape_hash(edit, identity))


def templates(trial, rows, preserve_defaults=True):
    result = {}
    reader = None
    defaults = {}
    def compatible(identity, row):
        nonlocal reader
        if not schema_matches(trial, identity, row):
            return False
        allowed = _RESOURCE_FIELDS.get(row['classId'])
        if allowed is None or not preserve_defaults:
            return True
        if identity not in defaults:
            if reader is None:
                if __package__:from .eiem_effect_native import Reader
                else:from eiem_effect_native import Reader
                reader = Reader(trial)
            tree, _ = reader.tree(identity)
            defaults[identity] = document._native_plain(tree, encode_bytes=True)
        return all(key in allowed or row['value'].get(key) == value
                   for key, value in defaults[identity].items())
    for row in rows:
        template = next((identity for identity, spec in trial.objects.items()
                         if spec.get('classId') == row['classId'] and
                         not spec.get('gameSource') and not spec.get('template') and
                         compatible(identity, row)), None)
        document.require(template, '目标特效缺少兼容的作者资源结构模板：' + row.get('name', row['type']))
        result[row['key']] = template
    return result

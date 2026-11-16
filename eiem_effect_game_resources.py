"""Game-owned effect resources: portable bindings and optional offline previews.

Borrowed source data is an editor preview, never an authored runtime payload.
Resource selection records logical identities. Export preserves those bindings
without opening the game installation or its global index.
"""
import copy
import json
from pathlib import Path

if __package__:
    from . import eiem_effect_document as document
else:
    import eiem_effect_document as document


def field_path(path):
    return '$' + ''.join('[' + str(p) + ']' if isinstance(p, int) else '.' + p for p in path)


def valid_logical_path(path):
    if not isinstance(path, str):
        return False
    path = path.strip()
    if not (path.startswith('assets/') or path.startswith('packages/')) or any(c in path for c in '\\:\0'):
        return False
    return all(part not in ('', '.', '..') for part in path.split('/'))


PROOF_FIELDS = ('sourcePrefab', 'sourceRoot', 'sourceRootIdentity', 'sourceVfsFingerprint',
                'resourceIdentity', 'resourceClassId')


def source_proof(source, manifest=None):
    """Portable lease evidence from the exact exported container entry.

    The donor directory is an optional editor hint. It is never the identity
    of a game-owned resource and need not survive publication or copying.
    """
    if manifest is None:
        manifest = json.loads(document.safe_path(source, 'source/manifest.json').read_bytes())
    prefab, identity = manifest.get('prefab'), manifest.get('root')
    document.require(manifest.get('format') == 'EFFSOURCE' and valid_logical_path(prefab)
                     and prefab.endswith('.prefab'), 'Game reference source is not a logical Prefab')
    roots = [r for r in manifest.get('hierarchy', []) if r.get('identity') == identity]
    entries = [r for r in manifest.get('containerEntries', [])
               if str(r.get('logicalPath', '')).casefold() == prefab.casefold()
               and str(r.get('asset', {}).get('identity', '')).casefold() == str(identity).casefold()]
    document.require(len(roots) == 1 and roots[0].get('name') and len(entries) == 1,
                     'Game reference source has no exact Prefab container proof')
    return dict(sourcePrefab=prefab, sourceRoot=roots[0]['name'],
                sourceRootIdentity=identity, sourceVfsFingerprint=manifest['vfsFingerprint'])


def portable_binding(logical, source='', target=None):
    result = copy.deepcopy(logical)
    if target and not target.get('template'):
        # Preserve the selected game asset, never a local author clone.
        paths = target.get('logicalPaths') or []
        if any(p.get('path', '').casefold() == result.get('path', '').casefold()
               and (not result.get('asset') or p.get('name', '') == result['asset']) for p in paths):
            result.update(resourceIdentity=target['identity'], resourceClassId=target['classId'])
    if not result.get('resourceIdentity') and source:
        try:
            catalog = json.loads(document.safe_path(source, 'effect.json').read_bytes())
            target = document.resolve_logical_asset(catalog['objects'], result['type'],
                                                    result['path'], result.get('asset', ''))
            result.update(resourceIdentity=target['identity'], resourceClassId=target['classId'])
        except (OSError, ValueError, KeyError):
            pass
    if result.get('resourceIdentity') and not result.get('sourcePrefab') and source:
        try:
            result.update(source_proof(source))
        except (OSError, ValueError):
            pass
    return result


def import_asset(edit, source, expected, path, asset=''):
    donor = document.Editor(document.load_source(source))
    target = document.resolve_logical_asset(donor.objects.values(), expected, path, asset)
    pending, visited = [target['identity']], set()
    while pending:
        identity = pending.pop()
        if identity in visited:
            continue
        visited.add(identity)
        spec = donor.objects.get(identity)
        if not spec or identity in edit.objects:
            continue
        document.require(spec['classId'] not in {1, 4, 114, 198, 199},
                         '游戏资源引用不能移植来源场景节点：' + spec.get('name', identity))
        spec = copy.deepcopy(spec)
        spec['gameSource'] = str(Path(source).resolve())
        edit.objects[identity] = spec
        edit.state['catalog']['objects'].append(spec)
        for key in ('values', 'original', 'hashes'):
            if identity in donor.state[key]:
                edit.state[key][identity] = copy.deepcopy(donor.state[key][identity])
        if spec.get('schema') in donor.state['schemas']:
            edit.state['schemas'][spec['schema']] = copy.deepcopy(donor.state['schemas'][spec['schema']])
        pending.extend(r.get('identity') for r in spec.get('references', []) if r.get('identity'))
    resources = edit.state['catalog'].setdefault('preview', {}).setdefault('resources', [])
    known = {r['identity'] for r in resources}
    for row in donor.state['catalog'].get('preview', {}).get('resources', []):
        if row['identity'] in visited and row['identity'] not in known:
            row = copy.deepcopy(row)
            if row.get('file'):
                row['file'] = str(document.safe_path(source, row['file']))
            resources.append(row)
            known.add(row['identity'])
    return edit.objects[target['identity']]


def pointer(edit, owner, target):
    own = edit.objects[owner]['native']['cab'].casefold()
    spec = edit.objects[target]
    cab = spec['native']['cab'].casefold()
    if own == cab:
        return dict(m_FileID=0, m_PathID=int(spec['native']['pathId']))
    tables = edit.state.setdefault('externalTables', {})
    if own not in tables:
        manifest = document.safe_path(edit.state['source'], 'source/manifest.json')
        data = json.loads(manifest.read_bytes())
        row = next(r for r in data['serializedFiles'] if r['cab'].casefold() == own)
        tables[own] = [str(r['path']).replace('\\', '/').rsplit('/', 1)[-1].casefold()
                       for r in row['externals']]
    table = tables[own]
    if cab not in table:
        table.append(cab)
    return dict(m_FileID=table.index(cab) + 1, m_PathID=int(spec['native']['pathId']))


def bind(edit, owner, path, target, logical):
    ptr = pointer(edit, owner, target)
    current = edit.value(owner)
    for part in path[:-1]:
        current = current[part]
    current[path[-1]] = ptr
    field = field_path(path)
    refs = edit.objects[owner].setdefault('references', [])
    row = next((r for r in refs if r.get('field') == field), None)
    if row is None:
        row = dict(field=field)
        refs.append(row)
    spec = edit.objects[target]
    row.update(fileId=ptr['m_FileID'], pathId=ptr['m_PathID'], identity=target,
               cab=spec['native']['cab'], type=logical['type'], resolved=True, isNull=False,
               gameLogical=copy.deepcopy(logical))
    return ptr


def bind_logical(edit, owner, path, logical_path, asset='', expected='Object', preview_source='', source_prefab=''):
    logical_path = logical_path.strip() if isinstance(logical_path, str) else ''
    document.require(valid_logical_path(logical_path), '请输入有效的 assets/... 或 packages/... 游戏逻辑路径')
    logical = dict(path=logical_path, asset=str(asset or '').strip(), type=expected,
                   previewSource=preview_source or edit.state.get('source', ''))
    source_prefab=str(source_prefab or '').strip()
    if source_prefab:
        document.require(valid_logical_path(source_prefab) and source_prefab.startswith('assets/') and source_prefab.endswith('.prefab'),
                         '来源 Prefab 请填写 assets/.../*.prefab 游戏逻辑路径')
        logical['sourcePrefab']=source_prefab
    normalized = logical_path.replace('\\', '/').casefold()
    present = any(str(row.get('path', '')).replace('\\', '/').casefold() == normalized
                  for spec in edit.objects.values() for row in spec.get('logicalPaths') or [])
    if present:
        target = document.resolve_logical_asset(edit.objects.values(), expected, logical_path, logical['asset'])
        logical = portable_binding(logical, target.get('gameSource') or logical['previewSource'], target)
        return bind(edit, owner, path, target['identity'], logical)

    ptr = dict(m_FileID=0, m_PathID=0)
    value = edit.value(owner)
    for part in path[:-1]:
        value = value[part]
    value[path[-1]] = ptr
    field = field_path(path)
    refs = edit.objects[owner].setdefault('references', [])
    row = next((r for r in refs if r.get('field') == field), None)
    if row is None:
        row = dict(field=field)
        refs.append(row)
    row.update(fileId=0, pathId=0, identity=None, cab=None, type=expected,
               resolved=False, isNull=False, gameLogical=logical)
    return None


def clear_binding(edit, owner, path):
    field = field_path(path)
    for row in edit.objects[owner].get('references', []):
        if row.get('field') == field:
            row.pop('gameLogical', None)
            row.update(fileId=0, pathId=0, identity=None, cab=None,
                       resolved=False, isNull=True)


def bindings(edit):
    result = []
    active = {i for n in edit.state['catalog'].get('hierarchy', [])
              for i in [n['identity'], *n.get('components', [])]}
    active.update(i for i, spec in edit.objects.items()
                  if not spec.get('gameSource') and spec.get('classId') in (21, 28, 43))
    for owner in sorted(active):
        spec = edit.objects[owner]
        for ref in spec.get('references', []):
            logical = ref.get('gameLogical')
            if not logical:
                continue
            try:
                ptr = document._json_path(edit.value(owner), ref['field'])
            except (KeyError, IndexError, TypeError):
                continue
            if ptr != dict(m_FileID=ref['fileId'], m_PathID=ref['pathId']):
                continue
            result.append(dict(owner=owner, field=ref['field'], **portable_binding(
                logical, logical.get('previewSource', ''), edit.objects.get(ref.get('identity')))))
    return result


def restore(edit, rows):
    # Apply after emitter topology restoration, when destination owners exist.
    for row in rows:
        logical = portable_binding({k: v for k, v in row.items() if k not in ('owner', 'field')},
                                   row.get('previewSource', ''))
        path = tuple(int(i) if i else k for k, i in document.re.findall(r'\.([^\.\[\]]+)|\[(\d+)\]', row['field']))
        selected = copy.deepcopy(edit.value(row['owner'], path))
        try:
            target = import_asset(edit, row.get('previewSource', ''), row['type'], row['path'], row.get('asset', ''))
        except (OSError, ValueError):
            bind_logical(edit, row['owner'], path, row['path'], row.get('asset', ''),
                         row['type'], row.get('previewSource', ''))
            reference = next(r for r in edit.objects[row['owner']]['references'] if r['field'] == row['field'])
            reference['gameLogical'] = logical
        else:
            bind(edit, row['owner'], path, target['identity'], logical)
        # The logical declaration also supplies the unchanged game Shader.
        # Retain an explicitly authored Material/Texture that superseded that
        # game resource, rather than discarding its local images on roundtrip.
        current = edit.value(row['owner'], path)
        reference = next((r for r in edit.objects[row['owner']].get('references', [])
                          if r.get('field') == row['field']), None)
        cab = reference.get('cab') if reference else None
        candidate = str(cab).casefold() + ':' + str(selected.get('m_PathID'))
        authored = next((r for r in edit.state.get('author_assets', [])
                         if r.get('identity', '').casefold() == candidate), None)
        spec = edit.objects.get(authored['identity']) if authored else None
        if (spec and selected.get('m_FileID') == current.get('m_FileID') and
                spec.get('type') == row['type']):
            bind(edit, row['owner'], path, spec['identity'], logical)

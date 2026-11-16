"""Portable effect clipboard data.

The clipboard is deliberately based on *visual effect content*.  An emitter
payload contains the selected particle-system subtree, its Renderer bindings,
and the references needed to rebuild it in another effect source.  Blender
objects and native CAB/PathID identities are never used as destination
identities; they are remapped when the payload is pasted.
"""
import copy
import base64
import json
import re
import uuid

if __package__:
    from . import eiem_effect_document as document
    from . import eiem_effect_game_resources as game_resources
    from . import eiem_effect_resources as resource_set
else:
    import eiem_effect_document as document
    import eiem_effect_game_resources as game_resources
    import eiem_effect_resources as resource_set


FORMAT = 'EFF_EFFECT_BLOCK_CLIPBOARD'
VERSION = 1
_FIELD = re.compile(r'\.([^\.\[\]]+)|\[(\d+)\]')
# Match the native author's resource edit contract. A compatible TypeTree alone
# is insufficient: immutable texture/mesh settings must match the new template.


def _path(field):
    """Convert a catalog reference path such as $.Shape.m_Mesh to keys."""
    if not isinstance(field, str) or not field.startswith('$'):
        return None
    return tuple(int(index) if index else name
                 for name, index in _FIELD.findall(field))


def _at(value, path):
    for part in path:
        value = value[part]
    return value


def _relative_path(module, path):
    prefix = (module,)
    return path[len(prefix):] if path[:len(prefix)] == prefix else None


def _logical_reference(edit, target_spec, ref):
    metadata = ref.get('gameLogical') or {}
    paths = copy.deepcopy((target_spec or {}).get('logicalPaths') or [])
    logical_path = metadata.get('path')
    logical_asset = metadata.get('asset', '')
    if logical_path and not any(row.get('path') == logical_path and
                                row.get('name', '') == logical_asset for row in paths):
        paths.append(dict(path=logical_path, name=logical_asset))
    if not paths:
        return None
    result = dict(logicalPaths=paths,
                assetName=logical_asset or (target_spec or {}).get('name', ''),
                previewSource=(metadata.get('previewSource') or
                               (target_spec or {}).get('gameSource') or
                               edit.state.get('source', '')))
    selected = dict(metadata, path=logical_path or paths[0]['path'],
                    asset=logical_asset or paths[0].get('name', ''),
                    type=metadata.get('type') or ref.get('type') or (target_spec or {}).get('type', 'Object'))
    selected = game_resources.portable_binding(selected, result['previewSource'], target_spec)
    result.update({key: selected[key] for key in game_resources.PROOF_FIELDS if selected.get(key)})
    return result


def _pointer_identity(edit, owner_identity, pointer, fallback=''):
    return resource_set.target(edit, owner_identity, pointer)


def _reference_rows(edit, identity, module):
    spec = edit.objects[identity]
    value = edit.value(identity)
    rows = []
    for ref in spec.get('references', []):
        path = _path(ref.get('field'))
        relative = _relative_path(module, path or ())
        if relative is None:
            continue
        try:
            pointer = _at(value, path)
        except (KeyError, IndexError, TypeError):
            continue
        if not isinstance(pointer, dict) or set(pointer) != {'m_FileID', 'm_PathID'}:
            continue
        target = _pointer_identity(edit, identity, pointer, ref.get('identity', ''))
        target_spec = edit.objects.get(target) if target else None
        logical = _logical_reference(edit, target_spec, ref)
        row = {
            'path': list(relative),
            'type': ref.get('type') or '',
            'logicalPaths': logical['logicalPaths'] if logical else [],
            'assetName': logical['assetName'] if logical else (target_spec or {}).get('name', ''),
            'previewSource': logical['previewSource'] if logical else '',
        }
        if logical:
            row.update({key: logical[key] for key in game_resources.PROOF_FIELDS if logical.get(key)})
        rows.append(row)
    return rows


def _node_map(edit):
    return {row['identity']: row for row in edit.state['catalog'].get('hierarchy', [])}


def _node_components(edit, node):
    return [node['identity'], *node.get('components', [])]


def _has_particle(edit, node):
    return any(edit.objects.get(identity, {}).get('classId') == 198
               for identity in node.get('components', []))


def _emitter_subtree(edit, identity):
    """Return visual emitter nodes below ``identity`` in source order."""
    nodes = _node_map(edit)
    document.require(identity in nodes, '婧愬彂灏勫櫒涓嶅瓨鍦ㄦ潵婧愬眰绾у浘涓?')
    result = []

    def walk(node_id):
        node = nodes[node_id]
        if _has_particle(edit, node):
            result.append(node)
        for child_transform in node.get('children', []):
            child = next((row for row in nodes.values()
                          if row.get('transform') == child_transform), None)
            if child:
                walk(child['identity'])

    walk(identity)
    document.require(result, '閫夋嫨鐨勮妭鐐规病鏈夌矑瀛愬彂灏勫櫒')
    return result


def _schema_hash(edit, identity):
    return resource_set.schema_hash(edit, identity)


def _schema_shape_hash(edit, identity):
    return resource_set.schema_shape_hash(edit, identity)


def _schema_matches(edit, identity, row):
    return resource_set.schema_matches(edit, identity, row)


def _sanitise_pointers(value):
    """Clear native pointer numbers; reference rows carry portable targets."""
    if isinstance(value, dict):
        if set(value) == {'m_FileID', 'm_PathID'}:
            return {'m_FileID': 0, 'm_PathID': 0}
        return {key: _sanitise_pointers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitise_pointers(item) for item in value]
    return copy.deepcopy(value)


def _object_reference_rows(edit, identity, included, node_transforms):
    """Describe every parsed PPtr without carrying source native IDs."""
    rows = []
    for path, pointer, target, ref in resource_set.references(edit, identity):
        # The parent of the copied root is deliberately supplied by the paste
        # destination, so the original scene hierarchy is never transplanted.
        if identity in node_transforms and path[-1] == 'm_Father':
            rows.append({'path': list(path), 'kind': 'destinationParent',
                         'type': ref.get('type') or 'Transform'})
            continue
        if target and target in included:
            rows.append({'path': list(path), 'kind': 'internal', 'target': included[target]})
            continue
        target_spec = edit.objects.get(target) if target else None
        logical = _logical_reference(edit, target_spec, ref)
        if logical:
            rows.append({'path': list(path), 'kind': 'logical',
                         'type': ref.get('type') or '',
                         **logical})
            continue
        if not target:
            document.require(not pointer.get('m_PathID'),
                             '资源引用缺少可移植来源：' + game_resources.field_path(path))
            rows.append({'path': list(path), 'kind': 'null', 'type': ref.get('type') or ''})
            continue
        # Same-source copies can retain an authored resource by identity. A
        # cross-file paste will reject it rather than inventing a null pointer.
        rows.append({'path': list(path), 'kind': 'existing',
                     'type': ref.get('type') or '', 'sourceIdentity': target})
    return rows


def copy_emitter(edit, identity):
    """Create a portable visual emitter tree, including child emitters."""
    nodes = _emitter_subtree(edit, identity)
    transforms = {node['transform'] for node in nodes}
    root_transforms = {node['transform'] for node in nodes if node['identity'] == identity}
    object_ids = set()
    for node in nodes:
        object_ids.update(_node_components(edit, node))
    resources = sorted(identity for identity in resource_set.reachable(edit, object_ids)
                       if resource_set.owned(edit, identity))
    keys = {object_id: 'o' + str(index) for index, object_id in enumerate(sorted(object_ids))}
    keys.update({identity: 'r' + str(index) for index, identity in enumerate(resources)})
    portable = []
    for node in nodes:
        for object_id in _node_components(edit, node):
            spec = edit.objects[object_id]
            document.require(spec.get('classId') in {1, 4, 114, 198, 199},
                             '鍙戝皠鍣ㄥ寘鍚繕鏈敮鎸佺殑缁勪欢')
            portable.append({
                'key': keys[object_id],
                'classId': spec['classId'],
                'type': spec.get('type', ''),
                'name': spec.get('name', ''),
                'schema': spec.get('schema', ''),
                'schemaSha256': _schema_hash(edit, object_id),
                'schemaShapeSha256': _schema_shape_hash(edit, object_id),
                'value': _sanitise_pointers(edit.value(object_id)),
                'references': _object_reference_rows(edit, object_id, keys, root_transforms),
                'components': [keys[i] for i in node.get('components', [])]
                if spec['classId'] == 1 else [],
            })
    portable_nodes = []
    for node in nodes:
        parent_transform = node.get('parent')
        parent_node = next((row for row in nodes if row.get('transform') == parent_transform), None)
        portable_nodes.append({
            'key': keys[node['identity']],
            'transform': keys[node['transform']],
            'parent': keys[parent_transform] if parent_node else None,
            'children': [keys[child] for child in node.get('children', [])
                         if child in transforms],
        })
    root_key = keys[identity]
    result = {
        'format': FORMAT,
        'version': VERSION,
        'kind': 'emitter',
        'root': {'key': root_key, 'name': edit.objects[identity].get('name', ''),
                 'nodeCount': len(portable_nodes)},
        'nodes': portable_nodes,
        'objects': portable,
    }
    if resources:
        if __package__:from .eiem_effect_native import Reader
        else:from eiem_effect_native import Reader
        reader = None
        authored = {row['identity']: row for row in edit.state.get('author_assets', [])}
        result['resources'] = []
        for resource in resources:
            spec = edit.objects[resource]
            if spec['classId'] == 21:
                value = copy.deepcopy(edit.value(resource))
            else:
                if reader is None:reader = Reader(edit)
                tree, _ = reader.tree(resource)
                value = document._native_plain(tree, encode_bytes=True)
            row = dict(key=keys[resource], classId=spec['classId'], type=spec['type'],
                       name=spec.get('name', ''), schemaSha256=_schema_hash(edit, resource),
                       schemaShapeSha256=_schema_shape_hash(edit, resource),
                       value=_sanitise_pointers(value),
                       references=_object_reference_rows(edit, resource, keys, set()))
            for key in ('sourceMaterial', 'sourceTexture', 'authorDataBase64'):
                if authored.get(resource, {}).get(key):
                    row[key] = copy.deepcopy(authored[resource][key])
            if spec['classId'] == 21 and not row.get('sourceMaterial'):
                if __package__:from .eiem_effect_material_origin import material_for_origin
                else:from eiem_effect_material_origin import material_for_origin
                row['sourceMaterial'] = material_for_origin(edit, resource)
            if authored.get(resource, {}).get('previewPngBase64'):
                row['previewPngBase64'] = authored[resource]['previewPngBase64']
            result['resources'].append(row)
    return result


def copy_module(edit, identity, module):
    """Create a portable clipboard envelope for one ParticleSystem module."""
    document.require(identity in edit.objects, '源特效块对象不存在')
    spec = edit.objects[identity]
    document.require(spec.get('classId') == 198, '只能复制 ParticleSystem 模块块')
    value = edit.value(identity)
    document.require(module in value and isinstance(value[module], dict),
                     '源 ParticleSystem 不包含该模块')
    return {
        'format': FORMAT,
        'version': VERSION,
        'kind': 'module',
        'root': {
            'classId': 198,
            'type': spec.get('type', 'ParticleSystem'),
            'module': module,
            'name': spec.get('name', ''),
        },
        'value': _sanitise_pointers(value[module]),
        'references': _reference_rows(edit, identity, module),
    }


def encode(payload):
    """Serialize clipboard data deterministically for Blender's clipboard."""
    validate(payload)
    return json.dumps(payload, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def decode(text):
    document.require(isinstance(text, str) and text.strip(), '特效剪贴板为空')
    try:
        payload = json.loads(text)
    except (TypeError, ValueError) as error:
        raise ValueError('特效剪贴板不是有效 JSON') from error
    validate(payload)
    return payload


def _validate_emitter(payload):
    document.require(isinstance(payload, dict) and payload.get('format') == FORMAT and
                     payload.get('version') == VERSION and payload.get('kind') == 'emitter',
                     'unsupported emitter clipboard')
    root = payload.get('root'); nodes = payload.get('nodes'); objects = payload.get('objects')
    document.require(isinstance(root, dict) and isinstance(root.get('key'), str),
                     'emitter clipboard root is invalid')
    document.require(isinstance(nodes, list) and 0 < len(nodes) <= 512,
                     'emitter clipboard hierarchy is invalid')
    document.require(isinstance(objects, list) and 0 < len(objects) <= 4096,
                     'emitter clipboard object list is invalid')
    resources = payload.get('resources', [])
    document.require(isinstance(resources, list) and len(resources) <= 4096,
                     'emitter clipboard resource list is invalid')
    document.require(all(isinstance(row, dict) and row.get('classId') in resource_set.KINDS
                         for row in resources), 'unsupported authored clipboard resource')
    keys = [row.get('key') for row in [*objects, *resources]]
    document.require(all(isinstance(key, str) and key for key in keys) and
                     len(keys) == len(set(keys)), 'emitter clipboard object keys are invalid')
    document.require(root['key'] in keys, 'emitter clipboard root is missing')
    for row in nodes:
        document.require(isinstance(row, dict) and row.get('key') in keys and
                         row.get('transform') in keys, 'emitter clipboard node is invalid')
        document.require(row.get('parent') is None or row['parent'] in keys,
                         'emitter clipboard parent is invalid')
        document.require(all(child in keys for child in row.get('children', [])),
                         'emitter clipboard child is invalid')
    for row in [*objects, *resources]:
        document.require(isinstance(row.get('value'), dict) and
                         isinstance(row.get('references'), list),
                         'emitter clipboard object data is invalid')
        seen = set()
        for ref in row['references']:
            document.require(isinstance(ref, dict) and isinstance(ref.get('path'), list),
                             'emitter clipboard reference is invalid')
            key = tuple(ref['path'])
            document.require(key not in seen and
                             all(isinstance(part, (str, int)) for part in key),
                             'emitter clipboard reference path is invalid')
            seen.add(key)
            if ref.get('kind') == 'internal':
                document.require(ref.get('target') in keys, 'emitter clipboard resource target is missing')
    return payload


def validate(payload):
    if isinstance(payload, dict) and payload.get('kind') == 'emitter':
        return _validate_emitter(payload)
    document.require(isinstance(payload, dict) and payload.get('format') == FORMAT and
                     payload.get('version') == VERSION, '不支持的特效剪贴板格式')
    document.require(payload.get('kind') == 'module', '当前剪贴板不是 ParticleSystem 模块块')
    root = payload.get('root')
    document.require(isinstance(root, dict) and root.get('classId') == 198 and
                     isinstance(root.get('module'), str) and root['module'],
                     '特效剪贴板缺少模块身份')
    document.require(isinstance(payload.get('value'), dict), '特效剪贴板模块数据无效')
    refs = payload.get('references', [])
    document.require(isinstance(refs, list) and len(refs) <= 4096, '特效剪贴板引用表无效')
    seen = set()
    for ref in refs:
        document.require(isinstance(ref, dict) and isinstance(ref.get('path'), list),
                         '特效剪贴板引用路径无效')
        key = tuple(ref['path'])
        document.require(key not in seen, '特效剪贴板包含重复引用')
        seen.add(key)
        document.require(all(isinstance(part, (str, int)) for part in key),
                         '特效剪贴板引用路径包含无效键')
    return payload


def _resolve_reference(trial, target_identity, module, value, ref):
    path = [module, *ref['path']]
    pointer = _at(value, path)
    if not isinstance(pointer, dict) or set(pointer) != {'m_FileID', 'm_PathID'}:
        raise ValueError('特效剪贴板引用字段已改变: ' + '.'.join(map(str, path)))
    logical = ref.get('logicalPaths') or []
    if not logical:
        if pointer.get('m_FileID') == 0 and pointer.get('m_PathID') == 0:
            return
        raise ValueError('特效剪贴板存在没有逻辑路径的外部引用: ' + '.'.join(map(str, path)))
    _resolve_logical_reference(trial, target_identity, path, ref)


def _resolve_logical_reference(trial, owner, path, ref):
    logical = ref.get('logicalPaths') or []
    source = ref.get('previewSource') or trial.state.get('source', '')
    matches = {}
    for row in logical:
        if not isinstance(row, dict) or not row.get('path'):
            continue
        asset = row.get('name', ref.get('assetName', ''))
        try:
            target = document.resolve_logical_asset(
                trial.objects.values(), ref.get('type') or 'Object', row['path'], asset)
        except ValueError:
            if not source:
                continue
            try:
                target = game_resources.import_asset(
                    trial, source, ref.get('type') or 'Object', row['path'], asset)
            except (OSError, ValueError):
                continue
        matches[target['identity']] = row
    if not matches and logical:
        preferred = next((row for row in logical if isinstance(row, dict) and row.get('path')), None)
        document.require(preferred, '特效剪贴板缺少游戏逻辑路径')
        pointer = game_resources.bind_logical(
            trial, owner, path, preferred['path'],
            preferred.get('name', ref.get('assetName', '')),
            ref.get('type') or 'Object', source)
        binding = next(r for r in trial.objects[owner]['references']
                       if r.get('field') == game_resources.field_path(path))['gameLogical']
        binding.update({key: ref[key] for key in game_resources.PROOF_FIELDS if ref.get(key)})
        return pointer
    document.require(len(matches) == 1,
                     '目标特效缺少唯一逻辑资源: ' + '/'.join(
                         row.get('path', '') for row in logical if isinstance(row, dict)))
    identity, row = next(iter(matches.items()))
    spec = trial.objects[identity]
    binding = dict(path=row['path'], asset=row.get('name', ref.get('assetName', '')),
                   type=ref.get('type') or spec['type'],
                   previewSource=spec.get('gameSource') or source)
    binding.update({key: ref[key] for key in game_resources.PROOF_FIELDS if ref.get(key)})
    pointer = game_resources.bind(trial, owner, path, identity, binding)
    parent_value = trial.value(owner)
    for part in path[:-1]:
        parent_value = parent_value[part]
    parent_value[path[-1]] = pointer
    return pointer


def paste_module(edit, target_identity, payload):
    """Return a validated Editor copy with a module pasted into the target.

    The input editor is never mutated until all references resolve.  Callers
    can store the returned editor state as one Blender undo transaction.
    """
    validate(payload)
    document.require(target_identity in edit.objects, '目标特效块对象不存在')
    target = edit.objects[target_identity]
    document.require(target.get('classId') == 198, '模块块只能粘贴到 ParticleSystem')
    module = payload['root']['module']
    document.require(module in edit.value(target_identity), '目标 ParticleSystem 不包含该模块')
    trial = document.Editor(copy.deepcopy(edit.state))
    trial.value(target_identity)[module] = copy.deepcopy(payload['value'])
    for ref in payload.get('references', []):
        _resolve_reference(trial, target_identity, module, trial.value(target_identity), ref)
    return trial


def _native_pointer(edit, owner, target):
    owner_cab = edit.objects[owner]['native']['cab'].casefold()
    target_spec = edit.objects[target]
    target_cab = target_spec['native']['cab'].casefold()
    if owner_cab == target_cab:
        file_id = 0
    else:
        ids = {int(ref['fileId']) for spec in edit.objects.values()
               if spec['native']['cab'].casefold() == owner_cab
               for ref in spec.get('references', [])
               if (ref.get('cab') or '').casefold() == target_cab and int(ref.get('fileId', 0)) > 0}
        document.require(len(ids) == 1, 'target effect has no native dependency for this resource')
        file_id = ids.pop()
    return {'m_FileID': file_id, 'm_PathID': int(target_spec['native']['pathId'])}


def _set_path(value, path, replacement):
    parent = value
    for part in path[:-1]:
        parent = parent[part]
    parent[path[-1]] = replacement


def _template_node(edit, parent_identity):
    nodes = _node_map(edit)
    selected = nodes.get(parent_identity)
    if selected and _has_particle(edit, selected):
        return selected
    return next((node for node in nodes.values() if _has_particle(edit, node)), None)


def _resource_templates(trial, rows, preserve_defaults=True):
    return resource_set.templates(trial, rows, preserve_defaults=preserve_defaults)


def _finish_resources(trial, rows, keys, templates):
    if not rows:
        return
    if __package__:
        from .eiem_effect_native import Reader
        from .nativepack import type_tree
    else:
        from eiem_effect_native import Reader
        from nativepack import type_tree
    reader = Reader(trial)
    additions = trial.state.setdefault('author_assets', [])
    total = 0
    for row in rows:
        identity, template = keys[row['key']], templates[row['key']]
        tree, original = reader.tree(template)
        data = trial.value(identity)
        tree = type_tree.from_data(tree.schema, document._native_input(data))
        raw = type_tree.encode(tree)
        total += len(raw)
        document.require(total <= 256 * 1024 * 1024, '作者资源合计超过 256 MiB')
        asset = dict(identity=identity, template=template,
                     sourceObjectSha256=document.digest(original),
                     payloadBase64=base64.b64encode(raw).decode('ascii'))
        for key in ('sourceMaterial', 'sourceTexture', 'authorDataBase64'):
            if row.get(key):asset[key] = copy.deepcopy(row[key])
        if row.get('previewPngBase64'):
            asset['previewPngBase64'] = row['previewPngBase64']
            document._decode_preview_png(asset, row['classId'])
        additions.append(asset)
        value = document._workspace_plain(document._native_plain(tree))
        trial.state['values'][identity] = value
        trial.state['original'][identity] = copy.deepcopy(value)
        trial.state['hashes'][identity] = document.digest(document.dumps(value).encode('utf-8'))


def paste_emitter(edit, parent_identity, payload):
    """Paste a complete visual emitter subtree below a destination node."""
    validate(payload)
    document.require(payload['kind'] == 'emitter', 'clipboard does not contain an emitter')
    trial = document.Editor(copy.deepcopy(edit.state))
    nodes = _node_map(trial)
    parent_node = nodes.get(parent_identity)
    document.require(parent_node, 'target effect node does not exist')
    template_node = _template_node(trial, parent_identity)
    document.require(template_node, 'target effect has no particle emitter template')
    object_rows = {row['key']: row for row in payload['objects']}
    source_to_template = {}
    for row in payload['objects']:
        if row['classId'] == 1:
            source_to_template[row['key']] = template_node['identity']
            continue
        template = next((identity for identity in template_node.get('components', [])
                         if trial.objects[identity]['classId'] == row['classId']), None)
        document.require(template, 'target emitter is missing a matching component')
        source_to_template[row['key']] = template
        document.require(_schema_matches(trial, template, row),
                         'source and target effect TypeTrees are incompatible')
    resource_rows = payload.get('resources', [])
    resource_keys = {row['key'] for row in resource_rows}
    source_to_template.update(_resource_templates(trial, resource_rows))
    target_cab = trial.objects[parent_node['transform']]['native']['cab'].casefold()
    key_to_identity = {}
    for row in [*payload['objects'], *resource_rows]:
        cab = trial.objects[source_to_template[row['key']]]['native']['cab'].casefold() if row['key'] in resource_keys else target_cab
        while True:
            path_id = int.from_bytes(uuid.uuid4().bytes[:8], 'little', signed=True)
            identity = cab + ':' + str(path_id)
            if path_id and identity not in trial.objects and identity not in key_to_identity.values():
                break
        key_to_identity[row['key']] = identity
    for row in [*payload['objects'], *resource_rows]:
        new = key_to_identity[row['key']]
        template = source_to_template[row['key']]
        spec = copy.deepcopy(trial.objects[template])
        spec['identity'] = new
        spec['template'] = spec.get('template', template)
        spec['native']['pathId'] = int(new.rsplit(':', 1)[1])
        spec['name'] = row.get('name', spec.get('name', ''))
        spec['references'] = []
        if row['key'] in resource_keys:
            spec['owner'] = None
            spec['logicalPaths'] = []
        data = copy.deepcopy(row['value'])
        trial.objects[new] = spec
        trial.state['catalog']['objects'].append(spec)
        trial.state['values'][new] = data
    for row in [*payload['objects'], *resource_rows]:
        new = key_to_identity[row['key']]
        data = trial.value(new)
        for ref in row['references']:
            path = ref['path']; kind = ref.get('kind')
            if kind == 'null':
                target = {'m_FileID': 0, 'm_PathID': 0}
            elif kind == 'internal':
                target = _native_pointer(trial, new, key_to_identity[ref['target']])
            elif kind == 'existing':
                source = ref.get('sourceIdentity')
                document.require(source in trial.objects, 'target effect is missing an external resource')
                target = _native_pointer(trial, new, source)
            elif kind == 'logical':
                target = _resolve_logical_reference(trial, new, path, ref)
            elif kind == 'destinationParent':
                target = _native_pointer(trial, new, parent_node['transform'])
            else:
                raise ValueError('unsupported portable emitter reference')
            _set_path(data, path, target)
            if not any(r.get('field') == game_resources.field_path(path) for r in trial.objects[new]['references']):
                target_id = _pointer_identity(trial, new, target)
                target_spec = trial.objects.get(target_id, {})
                trial.objects[new]['references'].append(dict(field=game_resources.field_path(path),
                    fileId=target['m_FileID'], pathId=target['m_PathID'], identity=target_id,
                    cab=target_spec.get('native', {}).get('cab'), type=ref.get('type') or target_spec.get('type', ''),
                    isNull=not target['m_PathID'], resolved=bool(target_id)))
        if trial.objects[new]['classId'] == 1:
            trial.objects[new]['name'] = data.get('m_Name', trial.objects[new].get('name', ''))
    _finish_resources(trial, resource_rows, key_to_identity, source_to_template)
    for row in payload['objects']:
        if row['classId'] == 1:
            continue
        owner = next((key for key, value in object_rows.items()
                      if row['key'] in value.get('components', [])), None)
        document.require(owner, 'portable emitter component has no owner')
        trial.objects[key_to_identity[row['key']]]['owner'] = key_to_identity[owner]
    new_nodes = []
    for row in payload['nodes']:
        go = key_to_identity[row['key']]
        transform = key_to_identity[row['transform']]
        parent = key_to_identity[row['parent']] if row.get('parent') else parent_node['transform']
        children = [key_to_identity[key] for key in row.get('children', [])]
        node = dict(identity=go, name=trial.value(go).get('m_Name', ''), transform=transform,
                    parent=parent, children=children,
                    components=[key_to_identity[key] for key in object_rows[row['key']]['components']])
        trial.state['catalog']['hierarchy'].append(node)
        new_nodes.append(node)
    root = next(row for row in new_nodes if row['identity'] == key_to_identity[payload['root']['key']])
    trial.value(parent_node['transform'])['m_Children'].append(
        {'m_FileID': 0, 'm_PathID': int(root['transform'].rsplit(':', 1)[1])})
    parent_node['children'].append(root['transform'])
    return trial, new_nodes

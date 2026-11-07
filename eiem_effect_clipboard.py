"""Portable effect clipboard data.

The clipboard is deliberately based on *visual effect content*.  An emitter
payload contains the selected particle-system subtree, its Renderer bindings,
and the references needed to rebuild it in another effect source.  Blender
objects and native CAB/PathID identities are never used as destination
identities; they are remapped when the payload is pasted.
"""
import copy
import json
import re
import uuid

if __package__:
    from . import eiem_effect_document as document
else:
    import eiem_effect_document as document


FORMAT = 'EFF_EFFECT_BLOCK_CLIPBOARD'
VERSION = 1
_FIELD = re.compile(r'\.([^\.\[\]]+)|\[(\d+)\]')


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


def _pointer_identity(edit, owner_identity, pointer, fallback=''):
    owner = edit.objects[owner_identity]['native']['cab'].casefold()
    file_id = int(pointer.get('m_FileID', 0))
    path_id = int(pointer.get('m_PathID', 0))
    if not path_id:
        return None
    if file_id == 0:
        candidate = owner + ':' + str(path_id)
        return candidate if candidate in edit.objects else fallback
    for spec in edit.objects.values():
        if spec['native']['cab'].casefold() != owner:
            continue
        for ref in spec.get('references', []):
            if int(ref.get('fileId', -1)) == file_id and ref.get('cab'):
                candidate = ref['cab'].casefold() + ':' + str(path_id)
                if candidate in edit.objects:
                    return candidate
    return fallback


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
        rows.append({
            'path': list(relative),
            'type': ref.get('type') or '',
            'sourceIdentity': target or '',
            'fileId': int(pointer.get('m_FileID', 0)),
            'pathId': int(pointer.get('m_PathID', 0)),
            'logicalPaths': copy.deepcopy((target_spec or {}).get('logicalPaths') or []),
            'assetName': (target_spec or {}).get('name', ''),
        })
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
    spec = edit.objects[identity]
    schema = edit.state['schemas'].get(spec.get('schema'))
    return document.digest(document.dumps(schema).encode('utf-8')) if schema else ''


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
    spec = edit.objects[identity]
    value = edit.value(identity)
    rows = []
    for ref in spec.get('references', []):
        path = _path(ref.get('field'))
        if path is None:
            continue
        try:
            pointer = _at(value, path)
        except (KeyError, IndexError, TypeError):
            continue
        if not isinstance(pointer, dict) or set(pointer) != {'m_FileID', 'm_PathID'}:
            continue
        target = _pointer_identity(edit, identity, pointer, ref.get('identity', ''))
        # The parent of the copied root is deliberately supplied by the paste
        # destination, so the original scene hierarchy is never transplanted.
        if identity in node_transforms and path[-1] == 'm_Father':
            rows.append({'path': list(path), 'kind': 'destinationParent',
                         'type': ref.get('type') or 'Transform'})
            continue
        if not target:
            rows.append({'path': list(path), 'kind': 'null', 'type': ref.get('type') or ''})
            continue
        if target in included:
            rows.append({'path': list(path), 'kind': 'internal', 'target': included[target]})
            continue
        target_spec = edit.objects.get(target)
        logical = copy.deepcopy((target_spec or {}).get('logicalPaths') or [])
        if logical:
            rows.append({'path': list(path), 'kind': 'logical',
                         'type': ref.get('type') or '',
                         'logicalPaths': logical,
                         'assetName': (target_spec or {}).get('name', '')})
            continue
        # Same-source copies can retain an authored resource by identity. A
        # cross-file paste will reject it rather than inventing a null pointer.
        rows.append({'path': list(path), 'kind': 'existing',
                     'type': ref.get('type') or '', 'sourceIdentity': target})
    return rows


def copy_emitter(edit, identity):
    """Create a portable visual emitter tree, including child emitters."""
    nodes = _emitter_subtree(edit, identity)
    node_ids = {node['identity'] for node in nodes}
    transforms = {node['transform'] for node in nodes}
    root_transforms = {node['transform'] for node in nodes if node['identity'] == identity}
    object_ids = set()
    for node in nodes:
        object_ids.update(_node_components(edit, node))
    keys = {object_id: 'o' + str(index) for index, object_id in enumerate(sorted(object_ids))}
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
    return {
        'format': FORMAT,
        'version': VERSION,
        'kind': 'emitter',
        'root': {'key': root_key, 'name': edit.objects[identity].get('name', ''),
                 'nodeCount': len(portable_nodes)},
        'nodes': portable_nodes,
        'objects': portable,
    }


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
            'sourceIdentity': identity,
        },
        'value': copy.deepcopy(value[module]),
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
    keys = [row.get('key') for row in objects]
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
    for row in objects:
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
    matches = []
    for row in logical:
        if not isinstance(row, dict) or not row.get('path'):
            continue
        try:
            target = document.resolve_logical_asset(
                trial.objects.values(), ref.get('type') or 'Object',
                row['path'], row.get('name', ref.get('assetName', '')))
        except ValueError:
            continue
        matches.append(target['identity'])
    matches = sorted(set(matches))
    document.require(len(matches) == 1,
                     '目标特效缺少唯一逻辑资源: ' + '/'.join(str(x) for x in logical))
    trial.set_pointer(target_identity, path, matches[0])


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
        document.require(row.get('schemaSha256') == _schema_hash(trial, template),
                         'source and target effect TypeTrees are incompatible')
    target_cab = trial.objects[parent_node['transform']]['native']['cab'].casefold()
    key_to_identity = {}
    for row in payload['objects']:
        while True:
            path_id = int.from_bytes(uuid.uuid4().bytes[:8], 'little', signed=True)
            identity = target_cab + ':' + str(path_id)
            if path_id and identity not in trial.objects and identity not in key_to_identity.values():
                break
        key_to_identity[row['key']] = identity
    for row in payload['objects']:
        new = key_to_identity[row['key']]
        template = source_to_template[row['key']]
        spec = copy.deepcopy(trial.objects[template])
        spec['identity'] = new
        spec['template'] = template
        spec['native']['pathId'] = int(new.rsplit(':', 1)[1])
        spec['name'] = row.get('name', spec.get('name', ''))
        data = copy.deepcopy(row['value'])
        trial.objects[new] = spec
        trial.state['catalog']['objects'].append(spec)
        trial.state['values'][new] = data
    for row in payload['objects']:
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
                matches = []
                for logical in ref.get('logicalPaths', []):
                    if not logical.get('path'):
                        continue
                    try:
                        matches.append(document.resolve_logical_asset(
                            trial.objects.values(), ref.get('type') or 'Object',
                            logical['path'], logical.get('name', ref.get('assetName', '')))['identity'])
                    except ValueError:
                        pass
                matches = sorted(set(matches))
                document.require(len(matches) == 1, 'target effect is missing a unique logical resource')
                target = _native_pointer(trial, new, matches[0])
            elif kind == 'destinationParent':
                target = _native_pointer(trial, new, parent_node['transform'])
            else:
                raise ValueError('unsupported portable emitter reference')
            _set_path(data, path, target)
        if trial.objects[new]['classId'] == 1:
            trial.objects[new]['name'] = data.get('m_Name', trial.objects[new].get('name', ''))
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

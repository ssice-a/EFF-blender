"""Author independent native emitter instances using source component templates."""
import copy
import uuid

if __package__:from . import eiem_effect_document as document
else:import eiem_effect_document as document

KINDS = {1, 4, 114, 198, 199}


def source_hierarchy(edit):
    return edit.state.setdefault('source_hierarchy', copy.deepcopy(edit.state['catalog']['hierarchy']))


def template_node(edit, identity):
    return next((n for n in edit.state['catalog']['hierarchy'] if n['identity'] == identity), None)


def duplicate(edit, identity, name):
    source_hierarchy(edit)
    node = template_node(edit, identity) or next((n for n in source_hierarchy(edit) if n['identity'] == identity), None)
    document.require(node, '请选择来源发射器')
    ids = [node['identity']] + node['components']
    kinds = [edit.objects[i]['classId'] for i in ids]
    document.require(198 in kinds and 199 in kinds, '该节点没有完整的粒子系统与渲染器')
    document.require(all(k in KINDS for k in kinds), '此发射器包含尚不支持复制的组件')
    document.require(all(i in edit.state['values'] for i in ids), '此发射器有未解析组件，不能可靠复制')
    # IDs are stored in the .blend once; repeated builds retain their identity.
    mapping = {}
    for old in ids:
        cab = edit.objects[old]['native']['cab'].lower()
        while True:
            pid = int.from_bytes(uuid.uuid4().bytes[:8], 'little', signed=True)
            new = cab + ':' + str(pid)
            if pid and new not in edit.objects and new not in mapping.values():break
        mapping[old] = new
    document.require(len({edit.objects[i]['native']['cab'].lower() for i in ids}) == 1,
                     '发射器的节点和组件必须位于同一来源文件')
    cab = edit.objects[identity]['native']['cab'].lower()
    def remap(value):
        if isinstance(value, dict):
            if set(value) == {'m_FileID', 'm_PathID'}:
                target = cab + ':' + str(value['m_PathID']) if value['m_FileID'] == 0 else None
                if target in mapping:value['m_PathID'] = int(mapping[target].rsplit(':', 1)[1])
            else:
                for v in value.values():remap(v)
        elif isinstance(value, list):
            for v in value:remap(v)
    parent = node['parent'] or next(n['transform'] for n in edit.state['catalog']['hierarchy'] if n['identity'] == edit.state['catalog']['root'])
    active_transforms = {n['transform'] for n in edit.state['catalog']['hierarchy']}
    while parent not in active_transforms:
        parent_node = next(n for n in source_hierarchy(edit) if n['transform'] == parent)
        parent = parent_node['parent']
    document.require(parent in edit.state['values'] and edit.objects[parent]['native']['cab'].lower() == cab,
                     '发射器挂点必须位于同一来源文件')
    for old, new in mapping.items():
        spec = copy.deepcopy(edit.objects[old])
        spec['template'] = spec.get('template', old)
        spec['identity'] = new; spec['native']['pathId'] = int(new.rsplit(':', 1)[1])
        spec['owner'] = mapping.get(spec.get('owner'), spec.get('owner'))
        for ref in spec.get('references', []):
            if ref.get('identity') in mapping:
                ref['identity'] = mapping[ref['identity']];ref['pathId'] = int(ref['identity'].rsplit(':', 1)[1])
        data = copy.deepcopy(edit.value(old));remap(data)
        if spec['classId'] == 1:data['m_Name'] = name;spec['name'] = name
        if spec['classId'] == 4:
            data['m_Children'] = [];data['m_Father'] = dict(m_FileID=0, m_PathID=int(parent.rsplit(':',1)[1]))
        edit.objects[new] = spec;edit.state['catalog']['objects'].append(spec)
        edit.state['values'][new] = data
    clone = dict(identity=mapping[identity], name=name, transform=mapping[node['transform']],
                 parent=parent, children=[], components=[mapping[i] for i in node['components']])
    edit.state['catalog']['hierarchy'].append(clone)
    edit.value(parent)['m_Children'].append(dict(m_FileID=0, m_PathID=int(clone['transform'].rsplit(':',1)[1])))
    parent_node = next(n for n in edit.state['catalog']['hierarchy'] if n['transform'] == parent)
    parent_node['children'].append(clone['transform'])
    return clone


def remove(edit, identity):
    source_hierarchy(edit)
    node = template_node(edit, identity)
    document.require(node and identity != edit.state['catalog']['root'], '不能删除特效根；请选择子发射器')
    document.require(any(edit.objects[i]['classId'] == 198 for i in node['components']), '所选节点不是发射器')
    document.require(not node['children'], '请先删除此节点的子发射器；非粒子子节点需要保留')
    ids = {node['identity'], *node['components']}
    document.require(all(i in edit.state['values'] for i in ids), '此发射器有未解析组件，不能可靠删除')
    document.require(all(edit.objects[i]['classId'] in KINDS for i in ids), '此发射器包含尚不支持删除的组件')
    parent = node['parent']
    edit.value(parent)['m_Children'][:] = [p for p in edit.value(parent)['m_Children'] if p['m_PathID'] != int(node['transform'].rsplit(':',1)[1])]
    parent_node = next(n for n in edit.state['catalog']['hierarchy'] if n['transform'] == parent)
    parent_node['children'] = [i for i in parent_node['children'] if i != node['transform']]
    edit.state['catalog']['hierarchy'].remove(node)
    if 'template' in edit.objects[identity]:
        for i in ids:
            edit.state['values'].pop(i, None);edit.objects.pop(i)
        edit.state['catalog']['objects'][:] = [o for o in edit.state['catalog']['objects'] if o['identity'] not in ids]
    else:
        edit.state.setdefault('removed_emitters', []).append(identity)
    return ids


def expected_children(edit, identity):
    """Derive declared topology, rejecting unrelated raw ownership edits."""
    cab = edit.objects[identity]['native']['cab'].lower()
    removed = {n['transform'] for n in edit.state.get('source_hierarchy', [])
               if n['identity'] in edit.state.get('removed_emitters', [])}
    children = [copy.deepcopy(p) for p in edit.state['original'][identity]['m_Children']
                if p['m_FileID'] != 0 or cab + ':' + str(p['m_PathID']) not in removed]
    children.extend(dict(m_FileID=0, m_PathID=int(n['transform'].rsplit(':',1)[1]))
                    for n in edit.state['catalog']['hierarchy']
                    if n['parent'] == identity and edit.objects[n['transform']].get('template'))
    return children


def export(edit):
    added = [dict(identity=i, template=o['template'], data=copy.deepcopy(edit.value(i)))
             for i, o in edit.objects.items() if o.get('template')]
    removed = edit.state.get('removed_emitters', [])
    return dict(added=added, removed=removed) if added or removed else None

"""An export's emitter selection, independent of Blender visibility/state."""
import copy

if __package__:
    from . import eiem_effect_document as document
else:
    import eiem_effect_document as document


def resolve(edit, selected):
    nodes = {n['identity']: n for n in edit.state['catalog']['hierarchy']}
    by_transform = {n['transform']: n['identity'] for n in nodes.values()}
    owners = {i: n['identity'] for n in nodes.values()
              for i in [n['identity'], *n.get('components', [])]}
    active, pending = set(), list(selected)
    while pending:
        identity = pending.pop()
        document.require(identity in nodes, '导出的发射器已不在当前特效层级中：' + identity)
        if identity in active:
            continue
        active.add(identity)
        node = nodes[identity]
        pending.extend(by_transform[c] for c in node.get('children', []) if c in by_transform)
        # Sub-emitter and other scene-component PPtrs are dependencies of the
        # selected visual behaviour, even when the target is a sibling node.
        for component in node.get('components', []):
            spec = edit.objects[component]
            for ref in spec.get('references', []):
                try:
                    ptr = document._json_path(edit.value(component), ref['field'])
                except (KeyError, IndexError, TypeError):
                    continue
                if not isinstance(ptr, dict) or not ptr.get('m_PathID'):
                    continue
                cab = spec['native']['cab'] if ptr.get('m_FileID') == 0 else (
                    ref.get('cab') if ptr.get('m_FileID') == ref.get('fileId') else None)
                target = (str(cab).casefold() + ':' + str(ptr['m_PathID'])) if cab else None
                target_id = next((k for k in owners if k.casefold() == target), None)
                owner = owners.get(target_id)
                # Transform parents provide placement, not sibling effects.
                if owner and edit.objects[component]['classId'] == 198 and edit.objects[target_id]['classId'] == 198:
                    pending.append(owner)
    return tuple(sorted(active))


def scene_ids(edit, scope):
    selected = set(scope)
    nodes = edit.state['catalog']['hierarchy']
    by_transform = {n['transform']: n for n in nodes}
    retained = set(selected)
    for n in nodes:
        if n['identity'] not in selected:
            continue
        parent = n.get('parent')
        while parent in by_transform:
            node = by_transform[parent]
            if node['identity'] in retained:
                break
            retained.add(node['identity'])
            parent = node.get('parent')
    return {i for n in nodes if n['identity'] in retained
            for i in [n['identity'], *n.get('components', [])]
            if n['identity'] in selected or edit.objects[i]['classId'] not in (198, 199)}


def payload(edit, value, selected):
    if selected is None:
        return value
    value = copy.deepcopy(value)
    scope = resolve(edit, selected)
    value['emitterScope'] = list(scope)
    if __package__:
        from . import eiem_effect_resources as resources
    else:
        import eiem_effect_resources as resources
    keep = scene_ids(edit, scope)
    value = resources.prune(edit, value, keep)
    # Preserve exactly the same scope in the editable author package. Leaving
    # excluded additions here would resurrect their topology on reimport and
    # leave their pointers referring to resources we correctly pruned above.
    operations = value.get('emitters', {})
    added = operations.get('added', [])
    excluded = {row['identity'] for row in added if row['identity'] not in keep}
    operations['added'] = [row for row in added if row['identity'] in keep]
    def target(owner, pointer):
        return owner.rsplit(':', 1)[0] + ':' + str(pointer['m_PathID']) if (
            pointer.get('m_FileID') == 0 and pointer.get('m_PathID')) else None
    for row in operations.get('added', []):
        data, identity = row['data'], row['identity']
        if edit.objects[identity]['classId'] == 1:
            data['m_Component'] = [p for p in data['m_Component']
                                   if target(identity, p['component']) not in excluded]
        if edit.objects[identity]['classId'] == 4:
            data['m_Children'] = [p for p in data['m_Children']
                                  if target(identity, p) not in excluded]
    scene = {i for n in edit.state['catalog']['hierarchy']
             for i in [n['identity'], *n['components']]}
    value['objects'] = [row for row in value.get('objects', [])
                        if row['identity'] not in scene or row['identity'] in keep]
    return value


def restore(edit, scope):
    """Project the exported hierarchy for editing; source remains the baseline."""
    document.require(isinstance(scope, list) and scope and len(scope) <= 4096 and
                     all(isinstance(i, str) for i in scope), '特效发射器导出范围无效')
    nodes = edit.state['catalog']['hierarchy']
    document.require(set(scope).issubset(n['identity'] for n in nodes), '特效导出范围节点缺失')
    edit.state.setdefault('source_hierarchy', copy.deepcopy(nodes))
    keep = scene_ids(edit, scope)
    retained = [copy.deepcopy(n) for n in nodes if n['identity'] in keep]
    for node in retained:
        node['components'] = [i for i in node['components'] if i in keep]
        node['children'] = [i for i in node['children'] if i in keep]
    edit.state['catalog']['hierarchy'] = retained
    edit.state['emitter_scope'] = list(scope)

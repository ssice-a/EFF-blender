"""Resolve exact effect mount LOD consumers from the source Prefab manifest."""
if __package__:
    from .eiem_lod import LOD_TOKEN_RE
else:
    from eiem_lod import LOD_TOKEN_RE


def lod_targets(proof, selected):
    resources = {r['identity']: r for r in proof.get('resources', []) if r.get('type') == 'Mesh'}
    base = resources.get(selected.get('mesh', {}).get('identity'))
    owner_path = selected.get('ownerPath', '')
    if not base or not LOD_TOKEN_RE.search(owner_path) or not LOD_TOKEN_RE.search(base['name']):
        return []
    family = lambda s: LOD_TOKEN_RE.sub('{lod}', s).casefold()
    hierarchy = {h['transform']: h['path'] for h in proof.get('hierarchy', [])}
    targets = []
    for use in proof.get('rendererUses', []):
        mesh = resources.get(use.get('mesh', {}).get('identity'))
        if (use == selected or not mesh or family(use['ownerPath']) != family(owner_path) or
                family(mesh['name']) != family(base['name'])):
            continue
        bones = [hierarchy.get(p.get('identity')) for p in use.get('bones', [])]
        if any(p is None for p in bones):
            raise ValueError('特效 LOD 来源缺少完整骨骼路径：' + use['ownerPath'])
        targets.append(dict(renderer=use['ownerPath'], path=mesh['logicalPath'],
                            asset=mesh['name'], bones=bones))
    return sorted(targets, key=lambda t: t['renderer'])


def lod_anchor(proof, selected, targets, bone='', mesh_space=False):
    """Keep static mounts outside LOD activation branches, with the full basis.

    Preserve the transform chain rather than collapsing rotated/nonuniform
    scales into a single TRS. Bone mounts already use their skeleton anchor.
    """
    if bone or (not targets and not mesh_space):
        return None
    paths = [selected['ownerPath']] + [t['renderer'] for t in targets]
    common = paths[0].split('/')[:-1]
    for path in paths[1:]:
        parts = path.split('/')[:-1]
        common = common[:next((i for i, (a, b) in enumerate(zip(common, parts))
                              if a != b), min(len(common), len(parts)))]
    parent = '/'.join(common)
    hierarchy = {h['path']: h for h in proof.get('hierarchy', [])}
    nodes = []
    for i in range(len(common) + 1, len(paths[0].split('/')) + 1):
        path = '/'.join(paths[0].split('/')[:i])
        if path not in hierarchy:
            raise ValueError('特效跨 LOD 挂点缺少来源变换：' + path)
        h = hierarchy[path]
        nodes.append(dict(position=h['localPosition'], rotation=h['localRotation'],
                          scale=h['localScale']))
    if mesh_space:
        # Mesh buffers are edited with X reflection only; Transform/particle
        # data uses the full (-X,-Z,Y) basis. Bridge those frames BEFORE the
        # authored Empty/root, keeping rotated nonuniform source TRS separate.
        # Also used by source-only mounts; no LOD sibling is required.
        from math import sqrt
        nodes.append(dict(position=[0, 0, 0], rotation=[sqrt(.5), 0, 0, sqrt(.5)],
                          scale=[1, 1, 1]))
    return dict(path=parent, nodes=nodes)

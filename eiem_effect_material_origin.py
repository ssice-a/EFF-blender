"""Imported Material provenance, shared by author snapshots and clipboard.

This module uses preserved author data only and has no Blender dependency.
"""
import copy
import json

if __package__:
    from . import eiem_effect_document as document
    from . import eiem_effect_resources as resources
    from . import eiem_effect_game_resources as game_resources
else:
    import eiem_effect_document as document
    import eiem_effect_resources as resources
    import eiem_effect_game_resources as game_resources


def _retain_container(edit, record, source=None):
    """Keep the exact imported container hint, without guessing by name."""
    if record.get('sourcePrefab'):
        return record
    candidates = [i for i, row in edit.objects.items() if row.get('classId') == 21 and any(
            p.get('path') == record.get('source') and
            (not record.get('asset') or p.get('name') == record['asset'])
            for p in row.get('logicalPaths', []))]
    if source is not None and source not in candidates:
        candidates.append(source)
    containers = set()
    for identity in candidates:
        spec = edit.objects[identity]
        if spec.get('sourcePrefab'):
            containers.add(spec['sourcePrefab'])
            continue
        packages = {spec.get('gameSource'), edit.state.get('source')}
        packages.update(r.get('gameLogical', {}).get('previewSource') for r in spec.get('references', []))
        for package in packages - {None, ''}:
            try:
                manifest = json.loads(document.safe_path(package, 'source/manifest.json').read_bytes())
                name = record.get('asset') or record.get('value', {}).get('m_Name')
                rows = manifest.get('resources', [])
                found = any(r.get('classId') == 21 and r.get('logicalPath') == record.get('source') and
                            (not name or r.get('name') == name) for r in rows)
                if not found:
                    catalog = json.loads(document.safe_path(package, 'effect.json').read_bytes())
                    found = any(r.get('classId') == 21 and any(p.get('path') == record.get('source') and
                                (not name or p.get('name') == name) for p in r.get('logicalPaths', []))
                                for r in catalog.get('objects', []))
                if found:
                    containers.add(game_resources.source_proof(package, manifest)['sourcePrefab'])
            except (OSError, ValueError, KeyError):
                continue
    if len(containers) == 1:
        record['sourcePrefab'] = containers.pop()
    return record


def material_for_origin(edit, identity, *, migrate=False):
    spec = edit.objects[identity]
    author = next((r for r in edit.state.get('author_assets', []) if r['identity'] == identity), {})
    if author.get('sourceMaterial'):
        result = copy.deepcopy(author['sourceMaterial'])
        return _retain_container(edit, result, identity) if migrate else result
    source = identity
    if not spec.get('logicalPaths'):
        name = edit.state['values'].get(identity, {}).get('m_Name', spec.get('name', ''))
        donors = [i for i, row in edit.objects.items() if row.get('classId') == 21 and
                  row.get('logicalPaths') and row.get('name') == name]
        if len(donors) == 1:
            source = donors[0]
        else:
            # One-time legacy migration reads the exact recorded local donor,
            # never a game index. Fresh imports already carry this information.
            paths = {r['gameLogical']['previewSource'] for r in spec.get('references', [])
                     if r.get('gameLogical', {}).get('previewSource')}
            if migrate and len(paths) == 1:
                donor = document.Editor(document.load_source(next(iter(paths))))
                candidates = [i for i,s in donor.objects.items() if s['classId']==21 and s.get('logicalPaths')]
                exact = [i for i in candidates if donor.objects[i].get('name') == name]
                selected = exact if exact else candidates
                document.require(len(selected) == 1, '旧材质缺少唯一的导入来源，请通过 EFF 材质导入按钮重新指定：' + name)
                return material_for_origin(donor, selected[0])
            source = spec.get('template', identity)
            # Native clones retain explicit template lineage even after a rename.
            # A borrowed material has its own donor/provenance and must never
            # inherit a structure template merely because its schema matches.
            original = edit.state['original'].get(identity, {})
            baseline = edit.state['original'].get(source, {})
            native_clone = (not paths and original.get('m_Shader') is not None and
                            original.get('m_Shader') == baseline.get('m_Shader'))
            document.require(not spec.get('template') or native_clone,
                '材质缺少可验证的导入来源，请通过 EFF 材质导入按钮指定原材质：' + name)
    baseline = edit.state['original'].get(source)
    paths = edit.objects[source].get('logicalPaths') or []
    logical = paths[0] if paths else {}
    bindings = []
    for path, ptr, target, ref in resources.references(edit, source):
        if not ptr.get('m_PathID') and not ref.get('gameLogical'):
            continue
        inherited = copy.deepcopy(ref.get('gameLogical'))
        target_spec = edit.objects.get(target, {})
        if not inherited:
            target_paths = target_spec.get('logicalPaths') or []
            if target_paths:
                chosen = target_paths[0]
                inherited = dict(path=chosen['path'], asset=chosen.get('name', ''), type=target_spec['type'])
        if inherited:
            bindings.append(dict(field=game_resources.field_path(path), **inherited))
    return _retain_container(edit, dict(format='EFF_MATERIAL_IMPORT/1', source=logical.get('path', ''),
                asset=logical.get('name', edit.objects[source].get('name', '')),
                value=copy.deepcopy(baseline), bindings=bindings, textures={}), source)


def migrate_origins(edit):
    """Persist missing provenance once at the legacy workspace restore seam."""
    changed = False
    for asset in edit.state.get('author_assets', []):
        if edit.objects[asset['identity']]['classId'] == 21 and not asset.get('sourceMaterial', {}).get('sourcePrefab'):
            value = material_for_origin(edit, asset['identity'], migrate=True)
            if value != asset.get('sourceMaterial'):
                asset['sourceMaterial'] = value
                changed = True
    return changed

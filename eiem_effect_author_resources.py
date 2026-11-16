"""Assemble effect-owned data through the ordinary Mod resource declarations.

The editable snapshot retains its native author data for lossless clipboard and
round trips. Only the compiler input is projected here: components retain their
logic, while Mesh/Material/Texture become the same reusable resources as models.
This projection reads the imported snapshot and never queries the game index.
"""
import base64
import copy
import json
import re
from pathlib import Path

if __package__:
    from . import eiem_effect_document as document
    from . import eiem_effect_resources as resource_set
    from . import eiem_effect_native as native
    from . import eiem_format as format_io
    from .nativepack import type_tree, mesh as mesh_codec
else:
    import eiem_effect_document as document
    import eiem_effect_resources as resource_set
    import eiem_effect_native as native
    import eiem_format as format_io
    from nativepack import type_tree, mesh as mesh_codec


KINDS = {21: 'Material', 28: 'Texture', 43: 'Mesh'}


def _name(kind, label, occupied):
    token = re.sub(r'[^A-Za-z0-9_]', '_', str(label)).strip('_')
    if not token:
        token = str(1)
    preferred = kind + '_' + token
    value, index = preferred, 2
    while value.casefold() in occupied:
        value = preferred + '_' + str(index)
        index += 1
    occupied.add(value.casefold())
    return value


def _logical(spec):
    paths = spec.get('logicalPaths') or []
    selected = next((p for p in paths if p.get('path')), {})
    return dict(path=selected.get('path', ''), asset=selected.get('name', spec.get('name', '')))


def _write_pointer(data, field, value):
    parts = resource_set.path(field)
    parent = data
    for key in parts[:-1]:
        parent = parent[key]
    parent[parts[-1]] = value


def _material_values(data):
    """The shared EFFMAT vocabulary, independent of Blender UI data types."""
    values = {}
    for group, kind in (('m_Floats', 'float'), ('m_Ints', 'int'), ('m_Colors', 'value4')):
        for row in data.get('m_SavedProperties', {}).get(group, []):
            v = row['second']
            values[kind + '.' + row['first']] = ','.join(str(v[k]) for k in 'rgba') if kind == 'value4' else str(v)
    for row in data.get('m_SavedProperties', {}).get('m_TexEnvs', []):
        for name, field in (('texture_scale', 'm_Scale'), ('texture_offset', 'm_Offset')):
            values[name + '.' + row['first']] = ','.join(str(row['second'][field][k]) for k in 'xy')
    if 'm_CustomRenderQueue' in data:
        values['render_queue'] = str(data['m_CustomRenderQueue'])
    for row in data.get('stringTagMap', []):
        values['tag.' + row['first']] = str(row['second'])
    names = data.get('m_ValidKeywords', data.get('m_ShaderKeywords', '').split())
    for name in names:
        values['keyword.' + name] = '1'
    return values


def externalize(edit, payload):
    """Return compiler logic and ordinary resource records; leave payload intact."""
    compiled = copy.deepcopy(payload)
    view = resource_set._publication_view(edit, payload)
    reader = native.Reader(edit)
    additions = {r['identity']: r for r in payload.get('assets', [])}
    overrides = {r['identity']: r for r in payload.get('objects', [])}
    owned = {i for i in additions if view.objects[i]['classId'] in KINDS}
    owned.update(i for i in overrides if view.objects[i]['classId'] in KINDS)
    resources = dict(materials={}, textures=[], meshes=[], bindings=[])
    if not owned:
        return compiled, resources
    occupied, ids, trees = set(), {}, {}

    def tree(identity):
        if identity not in trees:
            asset = additions.get(identity)
            if asset:
                source, _ = reader.tree(asset['template'])
                result = type_tree.decode(source.schema, base64.b64decode(asset['payloadBase64'], validate=True))
            else:
                source, _ = reader.tree(identity)
                result = type_tree.from_data(source.schema, view.value(identity)) if identity in overrides else source
            trees[identity] = result
        return trees[identity]

    for identity in sorted(owned):
        spec = view.objects[identity]
        ids[identity] = _name(KINDS[spec['classId']], tree(identity)['m_Name'].data, occupied)
        resources['bindings'].append(dict(identity=identity, resource=ids[identity], type=KINDS[spec['classId']]))

    game_refs = {(r['owner'], r['field']): r for r in payload.get('gameReferences', [])}
    logical_textures = {}

    def game_texture(binding):
        key = (binding['path'], binding.get('asset', ''))
        if key not in logical_textures:
            label = binding.get('asset') or Path(binding['path']).stem
            tid = _name('Texture', label, occupied)
            resources['textures'].append(dict(id=tid, label=label,
                sourcePath=binding['path'], sourceAsset=binding.get('asset', '')))
            logical_textures[key] = tid
        return logical_textures[key]

    for identity in sorted(owned):
        spec, rid = view.objects[identity], ids[identity]
        asset = additions.get(identity, {})
        template = asset.get('template', spec.get('template', identity))
        template_spec = edit.objects[template]
        current = native.plain(tree(identity))
        origin = _logical(template_spec)
        if spec['classId'] == 21:
            imported = asset.get('sourceMaterial', {})
            source = dict(path=imported.get('source') or origin['path'],
                          asset=imported.get('asset') or imported.get('value', {}).get('m_Name') or origin['asset'])
            document.require(source['path'], '作者材质缺少已导入的逻辑来源：' + current['m_Name'])
            baseline = imported.get('value')
            if baseline is None:
                baseline_tree, _ = reader.tree(template)
                baseline = native.plain(baseline_tree)
            before, after = _material_values(baseline), _material_values(current)
            values = {k: v for k, v in after.items() if before.get(k) != v}
            values.update({k: '0' for k in before if k.startswith('keyword.') and k not in after})
            bindings = []
            baseline_bindings = {r['field']: r for r in imported.get('bindings', [])}
            for path, ptr in resource_set.pointers(current):
                if path[:2] != ('m_SavedProperties', 'm_TexEnvs') or path[-1] != 'm_Texture':
                    continue
                prop = current['m_SavedProperties']['m_TexEnvs'][path[2]]['first']
                field = '$' + ''.join('[' + str(k) + ']' if isinstance(k, int) else '.' + k for k in path)
                target = resource_set.target(view, identity, ptr)
                if target in ids:
                    bindings.append(dict(property=prop, texture=ids[target]))
                    continue
                binding = game_refs.get((identity, field))
                if binding:
                    default = baseline_bindings.get(field)
                    # Unchanged source slots remain owned by the game Material.
                    if default and (binding['path'], binding.get('asset', '')) == (default['path'], default.get('asset', '')):
                        continue
                    bindings.append(dict(property=prop, texture=game_texture(binding)))
                    continue
                try:
                    default_pointer = document._json_path(baseline, field)
                except (KeyError, IndexError, TypeError):
                    default_pointer = dict(m_FileID=0, m_PathID=0)
                if target and ptr != default_pointer:
                    logical = _logical(view.objects[target])
                    document.require(logical['path'], '材质贴图缺少逻辑来源：' + prop)
                    bindings.append(dict(property=prop, texture=game_texture(logical)))
            lines = ['format=EFFMAT', 'version=1', 'overrides=true',
                     'source=' + source['path'], 'name=' + source['asset']]
            lines.extend(k + '=' + v for k, v in sorted(values.items()))
            # Import data is author metadata only; runtime reads the sparse edits.
            if imported:
                lines.append(';EFF_MATERIAL_IMPORT=' + document.dumps(imported))
            resources['materials'][rid] = dict(id=rid, label=current['m_Name'],
                sourcePath=source['path'], sourceAsset=source['asset'],
                sourcePrefab=imported.get('sourcePrefab', ''),
                nativeTypeHash=template_spec['native'].get('typeHash', '00' * 16),
                textureEdits=bindings, authorData=('\n'.join(lines) + '\n').encode('utf-8'), fieldData={})
        elif spec['classId'] == 28:
            source = dict(origin, **{k: v for k, v in asset.get('sourceTexture', {}).items() if v})
            pixels = current['image data']
            if not pixels and current['m_StreamData']['size']:
                pixels = reader.stream(template, current['m_StreamData'])
            settings = current['m_TextureSettings']
            resources['textures'].append(dict(id=rid, pixelLabel=current['m_Name'],
                sourcePath=source.get('path', ''), sourceAsset=source.get('asset', ''),
                width=current['m_Width'], height=current['m_Height'], mipCount=current['m_MipCount'],
                formatId=current['m_TextureFormat'], pixels=bytes(pixels),
                colorSpace=current['m_ColorSpace'], filter=settings['m_FilterMode'],
                wrap=settings['m_WrapU'], aniso=settings['m_Aniso'], mipBias=settings['m_MipBias'],
                ddsSourcePath=None, ddsHash=None))
        elif spec['classId'] == 43:
            author = asset.get('authorDataBase64')
            if author:
                author_data = base64.b64decode(author, validate=True)
                snapshot = format_io.decode_mesh(author_data)
            else:
                snapshot = mesh_codec.decode_native_mesh(tree(identity), [])
                author_data = format_io.encode_mesh(snapshot)
            resources['meshes'].append(dict(id=rid, label=current['m_Name'],
                authorData=author_data, vertices=snapshot['vertex_count'], indices=len(snapshot['indices']),
                bonePaths=snapshot.get('bone_paths', []), shapeNames=[c[0] for c in snapshot.get('blend_channels', [])],
                fieldData={k: type_tree.encode(v) for k, v in tree(identity).data.items()}))
            mesh_row = dict(asset)
            if not mesh_row:
                source_tree, source_bytes = reader.tree(template)
                mesh_row = dict(identity=identity, template=template,
                    sourceObjectSha256=document.digest(source_bytes),
                    payloadBase64=base64.b64encode(type_tree.encode(tree(identity))).decode('ascii'))
            mesh_row['resource'] = rid
            compiled.setdefault('authorMeshes', []).append(mesh_row)

    component_rows = {r['identity']: r for r in compiled.get('objects', [])}
    added_rows = {r['identity']: r for r in compiled.get('emitters', {}).get('added', [])}
    scene = {i for n in edit.state['catalog']['hierarchy'] for i in [n['identity'], *n.get('components', [])]}
    if payload.get('emitterScope'):
        if __package__:from . import eiem_effect_scope as scope
        else:import eiem_effect_scope as scope
        scene = scope.scene_ids(edit, payload['emitterScope'])
    for owner in sorted(scene):
        if owner not in view.objects or view.objects[owner]['classId'] in KINDS:
            continue
        value = view.state['values'].get(owner, {})
        for path, ptr in resource_set.pointers(value):
            target = resource_set.target(view, owner, ptr)
            if target not in ids:
                continue
            field = '$' + ''.join('[' + str(k) + ']' if isinstance(k, int) else '.' + k for k in path)
            row = component_rows.get(owner) or added_rows.get(owner)
            if row is None:
                row = dict(identity=owner, sourceDataSha256=edit.state['hashes'][owner], data=copy.deepcopy(value))
                compiled.setdefault('objects', []).append(row)
                component_rows[owner] = row
            _write_pointer(row['data'], field, dict(m_FileID=0, m_PathID=0))
            compiled.setdefault('authorReferences', []).append(dict(owner=owner, field=field,
                resource=ids[target], type=KINDS[view.objects[target]['classId']]))
    compiled['objects'] = [r for r in compiled.get('objects', []) if r['identity'] not in owned]
    compiled.pop('assets', None)
    author_slots = {(r['owner'], r['field']) for r in compiled.get('authorReferences', [])}
    compiled['gameReferences'] = [r for r in compiled.get('gameReferences', [])
        if r['owner'] not in owned and (r['owner'], r['field']) not in author_slots]
    if not compiled['gameReferences']:
        compiled.pop('gameReferences')
    return compiled, resources


def built_resources(resources, build):
    """Attach the compiler's independently isolated native Mesh archives."""
    result = copy.deepcopy(resources)
    if result.get('meshes'):
        build = Path(build)
        receipt = json.loads((build / 'build.json').read_bytes())
        meshes = {r['resource']: r for r in receipt.get('authorMeshes', [])}
        for mesh in result['meshes']:
            document.require(mesh['id'] in meshes, '原生构建缺少独立 Mesh 资源：' + mesh['id'])
            row = meshes[mesh['id']]
            path = document.safe_path(build, row['file'])
            mesh['native'] = dict(row, data=path.read_bytes())
    return result


def merge_resource_sets(effects, existing=None, occupied=()):
    """Disambiguate readable names once across effect roots and normal resources.

    Mutates only publication rows, never the editable snapshots or compiled bytes.
    The compiler's private relocation IDs remain stable while INI names change.
    """
    merged = dict(materials={}, textures=[], meshes=[])
    existing = existing or {}
    occupied = {i.casefold() for i in occupied}
    signatures = {}

    def content(kind, row):
        # Human-readable labels and internal native archive identifiers do not
        # change the shared material parameters or authored geometry/pixels.
        omitted = {'id', 'label', 'pixelLabel', 'section', 'native'}
        value = {k: v for k, v in row.items() if k not in omitted}
        if kind == 'Mesh':
            value.pop('fieldData', None)
        def stable(item):
            if isinstance(item, bytes):return ('bytes', document.digest(item))
            if isinstance(item, dict):return tuple((k, stable(v)) for k, v in sorted(item.items()))
            if isinstance(item, (list, tuple)):return tuple(stable(v) for v in item)
            return item
        return kind, stable(value)

    for kind, rows in (('Material', existing.get('materials', {}).values()),
                       ('Texture', existing.get('textures', [])), ('Mesh', existing.get('meshes', []))):
        for row in rows:
            occupied.add(row['id'].casefold())
            signatures[content(kind, row)] = row['id']
    for effect in effects:
        if effect.get('_native') is not None:
            effect['native'] = effect.pop('_native')
        source = effect.pop('_resources', None)
        if not source:
            continue
        source = copy.deepcopy(source)
        names = {}
        kept = dict(Material=[], Texture=[], Mesh=[])
        for kind, rows in (('Material', source.get('materials', {}).values()),
                           ('Texture', source.get('textures', [])), ('Mesh', source.get('meshes', []))):
            for row in rows:
                old = row['id']
                # Resolve texture names before comparing Materials below.
                if kind == 'Material':
                    continue
                signature = content(kind, row)
                if signature in signatures:
                    names[old] = signatures[signature]
                    continue
                chosen, index = old, 2
                while chosen.casefold() in occupied:
                    chosen = old + '_' + str(index)
                    index += 1
                occupied.add(chosen.casefold())
                names[old] = chosen
                row['id'] = chosen
                signatures[signature] = chosen
                kept[kind].append(row)
        for material in source.get('materials', {}).values():
            for binding in material.get('textureEdits', []):
                binding['texture'] = names.get(binding['texture'], binding['texture'])
            old = material['id']
            signature = content('Material', material)
            if signature in signatures:
                names[old] = signatures[signature]
                continue
            chosen, index = old, 2
            while chosen.casefold() in occupied:
                chosen = old + '_' + str(index)
                index += 1
            occupied.add(chosen.casefold())
            names[old] = chosen
            material['id'] = chosen
            signatures[signature] = chosen
            merged['materials'][chosen] = material
        merged['textures'].extend(kept['Texture'])
        merged['meshes'].extend(kept['Mesh'])
        runtime = effect.get('native') or effect.get('_native') or {}
        for binding in runtime.get('authorResources', []):
            binding['resource'] = names.get(binding['resource'], binding['resource'])
        effect['_authorResourceBindings'] = [dict(r, resource=names.get(r['resource'], r['resource']))
                                             for r in source.get('bindings', [])]
    return merged


def finalize_author_resources(root, effects, written):
    """Record public file provenance in the private editable author snapshot."""
    locations = written.get('_resourceLocations', {})
    parser = written['_ini']
    authored = [s for s in parser.sections() if parser[s].get('type') == 'effect-author']
    for effect in effects:
        bindings = effect.get('_authorResourceBindings')
        if not bindings:
            continue
        matched = False
        for section in authored:
            relative = parser[section]['path']
            path = document.safe_path(root, relative)
            saved = format_io.read_format3_effect_author_resource(path)
            if str(saved['attachment'].get('id')) != str(effect['id']):
                continue
            rows = []
            for row in bindings:
                document.require(row['resource'] in locations, '作者资源缺少发布路径：' + row['resource'])
                published = locations[row['resource']]
                rows.append(dict(identity=row['identity'], type=row['type'],
                                 resource=published['id'], path=published['path']))
            saved.setdefault('edit', {})['resourceBindings'] = rows
            data = format_io.encode_format3_effect_snapshot(saved['_source_archive'], saved['workspace'],
                                                            saved['attachment'], saved['edit'])
            if __package__:from .nativepack.author_format3 import encode_effect_author_resource
            else:from nativepack.author_format3 import encode_effect_author_resource
            path.write_bytes(encode_effect_author_resource(section, relative, data))
            matched = True
            break
        document.require(matched, '发布后缺少可更新的特效作者资源：' + effect['id'])


def overlay_resources(state, root, parser):
    """Restore current reachable INI declarations over the editable template.

    Snapshot identities locate existing native structures. They are not a
    whitelist: a current Material may refer to a newly declared shared DDS.
    """
    records = state.pop('author_resource_bindings', [])
    if not records:
        return
    edit = document.Editor(state)
    reader = native.Reader(edit)
    assets = {r['identity']: r for r in state.get('author_assets', [])}
    resolved, missing = {}, {}
    for row in records:
        section = row['resource']
        if section not in parser:
            candidates = [s for s in parser.sections() if row.get('path') and
                          parser[s].get('dds', parser[s].get('path', '')) == row['path']]
            section = candidates[0] if len(candidates) == 1 else None
        if section is None:
            missing[row['identity']] = row['resource']
        else:
            resolved[row['identity']] = (section, parser[section])
    identities = {section: identity for identity, (section, _) in resolved.items()}

    def save(identity, tree):
        encoded = type_tree.encode(tree)
        if identity in assets:
            assets[identity]['payloadBase64'] = base64.b64encode(encoded).decode('ascii')
        state['values'][identity] = document._workspace_plain(document._native_plain(tree))

    def adopt(section, cls, template=None):
        document.require(section in parser, '资源声明缺失：' + section)
        declaration = parser[section]
        document.require(section.startswith(KINDS[cls]), '资源声明类型不匹配：' + section)
        if cls == 28 and not declaration.get('dds'):
            document.require(declaration.get('source.path'), '贴图没有 DDS 或游戏逻辑来源：' + section)
            return None
        if section in identities:
            identity = identities[section]
            document.require(edit.objects[identity]['classId'] == cls, '资源类型与原生引用不匹配：' + section)
            return identity
        # Only a TypeTree is borrowed. The selected declaration supplies pixels,
        # geometry or Material values; no installed game index is queried.
        if template is None or edit.objects[template]['classId'] != cls:
            template = next((i for i,o in edit.objects.items() if o['classId'] == cls and
                             o.get('state') != 'game-reference' and not o.get('gameSource')), None)
        document.require(template, '当前特效缺少资源结构模板：' + section)
        template = edit.objects[template].get('template', template)
        tree, raw = reader.tree(template)
        cab = edit.objects[template]['native']['cab']
        pid = -int(document.digest(section.encode('utf-8'))[:15], 16) - 1
        while cab + ':' + str(pid) in edit.objects:pid -= 1
        identity = cab + ':' + str(pid)
        tree.set('m_Name', declaration.get('source.asset') or section)
        row = dict(identity=identity, template=template,
                   sourceObjectSha256=document.digest(raw),
                   payloadBase64=base64.b64encode(type_tree.encode(tree)).decode('ascii'))
        spec = copy.deepcopy(edit.objects[template])
        spec.update(identity=identity, template=template, state='parsed', owner=None,
                    logicalPaths=[], references=[])
        spec['native']['pathId'] = pid
        edit.objects[identity] = spec
        state['catalog']['objects'].append(spec)
        state.setdefault('author_assets', []).append(row)
        assets[identity] = row
        identities[section] = identity
        resolved[identity] = (section, declaration)
        save(identity, tree)
        state['original'][identity] = copy.deepcopy(state['values'][identity])
        state['hashes'][identity] = document.digest(document.dumps(state['values'][identity]).encode('utf-8'))
        if cls == 28:
            row['sourceTexture'] = dict(path=declaration.get('source.path', ''),
                                        asset=declaration.get('source.asset', ''))
        return identity

    seeds = {i for node in state['catalog'].get('hierarchy', [])
             for i in [node['identity'], *node.get('components', [])]}
    active = resource_set.reachable(edit, seeds)
    for identity, (section, declaration) in list(resolved.items()):
        if identity not in active:continue
        cls = edit.objects[identity]['classId']
        if cls == 43:
            author = format_io.read_format3_mesh_resource(document.safe_path(root, declaration['path']))
            tree, _ = reader.tree(identity)
            tree, _ = mesh_codec.native_mesh(tree, author, author.get('name', tree['m_Name'].data))
            save(identity, tree)
            if identity in assets:assets[identity]['authorDataBase64'] = base64.b64encode(format_io.encode_mesh(author)).decode('ascii')
        if cls != 21:
            continue
        if __package__:
            from . import eiem_native_export as writer
            from . import eiem_effect_game_resources as game
        else:
            import eiem_native_export as writer
            import eiem_effect_game_resources as game
        values = format_io.read_format3_material_resource(document.safe_path(root, declaration['path']))
        imported = json.loads(values['_native_bindings']) if values.get('_native_bindings') else assets.get(identity, {}).get('sourceMaterial', {})
        if imported and identity in assets:assets[identity]['sourceMaterial'] = imported
        if declaration.get('source.prefab') and imported:
            imported['sourcePrefab'] = declaration['source.prefab']
        original = imported.get('value')
        tree, _ = reader.tree(identity)
        if original is not None:tree = type_tree.from_data(tree.schema, original)
        writer.material_fields(tree, {k:v for k,v in values.items() if k not in
            ('format','version','overrides','source','name','shader') and not k.startswith('_')}, type_tree.encode)
        save(identity, tree)
        if original is not None:
            for binding in imported.get('bindings', []):
                path = resource_set.path(binding['field'])
                game.bind_logical(edit, identity, path, binding['path'], binding.get('asset', ''), binding['type'])
        for index, slot in enumerate(edit.value(identity).get('m_SavedProperties', {}).get('m_TexEnvs', [])):
            key = 'texture.' + slot['first']
            if key not in declaration:
                continue
            target = declaration[key]
            path = ('m_SavedProperties', 'm_TexEnvs', index, 'second', 'm_Texture')
            previous = resource_set.target(edit, identity, slot['second']['m_Texture'])
            target_identity = adopt(target, 28, previous)
            if target_identity:
                ptr = game.pointer(edit, identity, target_identity)
                slot['second']['m_Texture'] = ptr
                game.clear_binding(edit, identity, path)
            else:
                document.require(target in parser, '材质贴图声明缺失：' + target)
                texture = parser[target]
                game.bind_logical(edit, identity, path, texture['source.path'], texture.get('source.asset', ''), 'Texture')
        tree = type_tree.from_data(tree.schema, edit.value(identity))
        save(identity, tree)

    active = resource_set.reachable(edit, seeds)
    for identity, section in missing.items():
        document.require(identity not in active, '特效仍引用已删除的资源声明：' + section)
    # DDS is authoritative, including external Photoshop edits and native blocks.
    for identity, (section, declaration) in resolved.items():
        if identity not in active:continue
        if edit.objects[identity]['classId'] != 28:
            continue
        dds = declaration.get('dds')
        if not dds:
            continue
        if __package__:from . import eiem_texture_compression as compression
        else:import eiem_texture_compression as compression
        path = document.safe_path(root, dds)
        data = compression.parse_source_dds(path.read_bytes())
        tree, _ = reader.tree(identity)
        for field, value in dict(m_Width=data['width'], m_Height=data['height'],
                m_MipCount=data['mipCount'], m_TextureFormat=data['formatId'],
                m_CompleteImageSize=len(data['pixels']), m_StreamingMipmaps=False,
                m_ColorSpace=0 if declaration.get('linear', 'false').lower() in ('true', '1') else 1).items():
            tree.set(field, value)
        tree.set('image data', data['pixels'])
        for field, value in dict(offset=0, size=0, path='').items():tree['m_StreamData'].set(field, value)
        for field, key, default, convert in (('m_FilterMode', 'filter', '1', int),
            ('m_WrapU', 'wrap', '0', int), ('m_WrapV', 'wrap', '0', int),
            ('m_Aniso', 'aniso', '1', int), ('m_MipBias', 'mip_bias', '0', float)):
            tree['m_TextureSettings'].set(field, convert(declaration.get(key, default)))
        save(identity, tree)
        if identity in assets:
            # Use the published image for Blender preview too, without recompressing.
            if __package__:from . import eiem_shared_resources as shared
            else:import eiem_shared_resources as shared
            shared.add_vendor_path()
            import io
            from PIL import Image
            with Image.open(path) as image:
                output = io.BytesIO()
                image.convert('RGBA').save(output, format='PNG')
                assets[identity]['previewPngBase64'] = base64.b64encode(output.getvalue()).decode('ascii')

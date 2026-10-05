"""Blender format-2 export. Uses the shared source parser and native writer.

No editable Mesh file, material merge, game deployment or runtime setter is
part of this entry. The C++ parser is shared; geometry encoding currently uses
the already verified nativepack writer. Full C++ geometry acceleration is not
claimed by this bridge.
"""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile


# Native partial exports used to rebuild the Blender snapshot on every click,
# even when the selected Mesh had not changed. Keep a bounded session cache;
# the add-on's content token includes vertices, weights, shapes, slots and ID
# revisions, so an edit naturally misses without trusting mtimes.
_NATIVE_SNAPSHOT_CACHE = {}
_NATIVE_SNAPSHOT_CACHE_LIMIT = 128


def _cached_mesh_snapshot(addon, obj):
    token_fn = getattr(addon, '_mesh_resource_token', None)
    if token_fn is None:
        return addon.write_mesh(None, obj, return_snapshot=True), False
    token = token_fn(obj)
    key = (int(obj.as_pointer()), token)
    cached = _NATIVE_SNAPSHOT_CACHE.get(key)
    if cached is not None:
        return cached, True
    snapshot = addon.write_mesh(None, obj, return_snapshot=True)
    _NATIVE_SNAPSHOT_CACHE[key] = snapshot
    while len(_NATIVE_SNAPSHOT_CACHE) > _NATIVE_SNAPSHOT_CACHE_LIMIT:
        _NATIVE_SNAPSHOT_CACHE.pop(next(iter(_NATIVE_SNAPSHOT_CACHE)))
    return snapshot, False

def pack_modules():
    local = Path(__file__).parent/'nativepack'
    canonical = Path(__file__).parent.parent/'nativepack'
    root = local if (local/'author_source.py').is_file() else canonical
    if not (root/'author_source.py').is_file():
        raise ValueError('缺少 EFF 原生导出模块，请安装完整插件包')
    name = __package__ + '.nativepack'
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, root/'__init__.py',
                                                      submodule_search_locations=[str(root)])
        # The canonical core is a namespace package, with no initializer.
        if not (root/'__init__.py').exists():
            import types
            module = types.ModuleType(name); module.__path__ = [str(root)]
        else:
            module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        sys.modules[name] = module
    from importlib import import_module
    modules = {n: import_module(name+'.'+n) for n in
               ('author_source', 'mesh', 'delta_apply', 'type_tree', 'ini_package', 'ini_reader')}
    dll = root/'eff_author_core.dll'
    if not dll.is_file():
        dll = Path(__file__).resolve().parents[2]/'bin/author-core/eff_author_core.dll'
    if not dll.is_file():
        raise ValueError('缺少 eff_author_core.dll，请安装完整插件包')
    return modules, dll

def token(value):
    return re.sub(r'[^A-Za-z0-9_.-]', '_', str(value))

def baseline_for(objects, configured):
    if configured:
        path = Path(configured).resolve()
        if not (path/'source/manifest.json').is_file():
            raise ValueError('原生来源目录缺少 source/manifest.json：'+str(path))
        return path
    sources = {Path(o.get('eiem_author_package', '')).resolve() for o in objects}
    if len(sources) != 1 or not (next(iter(sources))/'source/manifest.json').is_file():
        raise ValueError('旧工程未保留完整原生来源，请在导出窗口指定包含 source/manifest.json 的解包目录')
    return next(iter(sources))

def split_material(author):
    # The snapshot already has one consistent corner/skin/shape vertex map.
    # Keeping unused vertices avoids inventing a second remapping algorithm.
    for slot, (_, start, count, _, _, _) in enumerate(author['submeshes']):
        if not count: continue
        part = dict(author)
        part['indices'] = author['indices'][start:start+count]
        part['index_count'] = count
        part['submeshes'] = [(0, 0, count, 0, 0, author['vertex_count'])]
        yield slot, part

def material_fields(tree, overrides, encode):
    before = {key: encode(tree[key]) for key in tree.data}
    for key, value in overrides.items():
        if key.startswith('texture.'): continue
        kind, _, prop = key.partition('.')
        maps = {'float': 'm_Floats', 'int': 'm_Ints', 'value4': 'm_Colors', 'color': 'm_Colors'}
        if kind not in maps:
            raise ValueError('尚未支持的材质字段修改：'+key)
        entries = tree['m_SavedProperties'][maps[kind]].items()
        entry = next((p for p in entries if p['first'].data == prop), None)
        if entry is None: raise ValueError('原生材质没有此属性：'+key)
        if kind in ('float', 'int'):
            entry.set('second', float(value) if kind == 'float' else int(value))
        else:
            values = [float(x) for x in value.split(',')]
            if len(values) != 4: raise ValueError('材质向量需要四个分量：'+key)
            entry.set('second', dict(zip(('r','g','b','a'), values)))
    changed = {}
    for key in tree.data:
        encoded = encode(tree[key])
        if encoded != before[key]:
            changed[key] = encoded
    return changed

def material_id(material):
    return 'Material'+token(material.get('eiem_section', material.name)).removeprefix('Material')

def collect_material(addon, source, mat, images, textures, temporary, encode,
                     existing=None, texture_only=False):
    from PIL import Image
    mid = material_id(mat)
    matpath = str(mat.get('eiem_target_path', '') or mat.get('eiem_source', ''))
    matasset = str(mat.get('eiem_target_asset', '') or mat.get('eiem_name', mat.name))
    native = source.resolve('Material', matpath, matasset)
    values, _ = addon.material_override_payload(mat, images, force=True)
    props = dict(line.split('=', 1) for line in values if '=' in line)
    overrides = {k: v for k, v in props.items() if k not in
                 ('format', 'version', 'overrides', 'source', 'name', 'shader')}
    # A published authored slot remains exportable even when Blender's baseline
    # now equals its path. Original inherited slots have no published binding.
    if texture_only and existing is not None and mid in existing:
        bindings = addon.parse_json_property(mat, 'eiem_texture_sections_json', {})
        for key in existing[mid]:
            if not key.startswith('texture.') or key in overrides:
                continue
            value = str(mat.get('eiem_' + key, '')).strip()
            if value and not value.replace('\\', '/').lower().startswith('assets/'):
                overrides[key] = addon.resolve_material_texture(mat, key, value, images, bindings)
    edits = []
    for key, section in overrides.items():
        if not key.startswith('texture.'):
            continue
        prop = key.removeprefix('texture.')
        pointer = next((x['pointer'] for x in native['details']['textures'] if x['property'] == prop), None)
        if pointer is None or pointer['isNull']:
            raise ValueError('纹理属性缺少原生来源：' + prop)
        tex = next(r for r in source.manifest['resources'] if r['identity'] == pointer['identity'])
        image = images[section]
        png = Path(temporary)/(hashlib.sha256(section.encode()).hexdigest()+'.png')
        addon.write_texture(png, image)
        with Image.open(png) as img:
            mip = img.convert('RGBA'); width, height = mip.size; raw = bytearray(); count = 0
            while True:
                raw.extend(mip.transpose(Image.Transpose.FLIP_TOP_BOTTOM).tobytes()); count += 1
                if str(image.get('eiem_mipmaps', 'true')) != 'true' or mip.size == (1, 1):
                    break
                mip = mip.resize((max(1, mip.width//2), max(1, mip.height//2)), Image.Resampling.BOX)
        record = dict(id=tex['identity'], sourcePath=tex['logicalPath'], sourceAsset=tex['name'],
                      pixels=bytes(raw), pixelLabel=token(Path(image.filepath).stem or image.name),
                      format='RGBA32', width=width, height=height, mipCount=count,
                      colorSpace=0 if str(image.get('eiem_linear', 'false')) == 'true' else 1,
                      filter=int(image.get('eiem_filter', 1)), wrap=int(image.get('eiem_wrap', 0)),
                      aniso=int(image.get('eiem_aniso', 1)), mipBias=float(image.get('eiem_mip_bias', 0)))
        if tex['identity'] in textures and textures[tex['identity']] != record:
            raise ValueError('同一原生纹理存在冲突修改：' + tex['name'])
        textures[tex['identity']] = record
        edits.append(dict(property=prop, texture=tex['identity']))
    return dict(id=mid, sourcePath=matpath, sourceAsset=matasset,
                nativeTypeHash=native['native']['typeHash'], textureEdits=edits,
                fieldData={} if texture_only else material_fields(source.tree(native), overrides, encode))

def publish(staging, destination, reader):
    staging = Path(staging).resolve()
    destination = Path(destination).resolve()
    marker = destination/'mod.ini'
    previous = None
    if destination.exists() and any(destination.iterdir()):
        if not marker.is_file() or not marker.read_text('utf-8-sig').startswith('; EFF resource-input Mod format 2'):
            raise ValueError('不能覆盖非 format 2 目录，请选择新的 Mod 输出目录')
        previous = reader(destination)
    candidate = reader(staging)
    old_files = previous['_files'] if previous is not None else set()
    new_files = candidate['_files']

    def same_bytes(a, b):
        if not b.is_file() or a.stat().st_size != b.stat().st_size:
            return False
        with a.open('rb') as first, b.open('rb') as second:
            while True:
                chunk = first.read(1024*1024)
                if chunk != second.read(1024*1024):
                    return False
                if not chunk:
                    return True

    changed = [name for name in sorted(new_files) if not same_bytes(staging/name, destination/name)]
    removed = sorted(old_files - new_files)
    if not changed and not removed:
        return previous if previous is not None else candidate
    # Keep the Mod directory identity: Windows readers/file browsers may retain
    # a handle which forbids renaming it. Prepare only changed payloads on the
    # same volume; replace each complete file atomically, then publish INI last.
    # Native generation publication still happens only after compilation.
    destination.mkdir(parents=True, exist_ok=True)
    pending = Path(tempfile.mkdtemp(prefix='.'+destination.name+'.publish-', dir=destination.parent))
    if pending.resolve().parent != destination.parent:
        raise ValueError('导出临时路径超出输出父目录')
    backup = pending/'backup'
    applied = []
    cleanup = True
    try:
        for name in changed:
            output = pending/'new'/name
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(staging/name, output)
        for name in set(changed) | set(removed):
            original = destination/name
            if original.is_file():
                saved = backup/name
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(original, saved)
        for name in removed:
            os.replace(destination/name, backup/name)
            applied.append(name)
        for name in sorted(changed, key=lambda value: (value == 'mod.ini', value)):
            output = destination/name
            output.parent.mkdir(parents=True, exist_ok=True)
            os.replace(pending/'new'/name, output)
            applied.append(name)
        return reader(destination)
    except Exception as error:
        try:
            for name in reversed(applied):
                saved = backup/name
                if saved.is_file():
                    os.replace(saved, destination/name)
                else:
                    (destination/name).unlink()
        except Exception as rollback:
            cleanup = False
            raise RuntimeError('导出失败且回滚未完成，原文件备份保留在 '+str(backup)+
                               '：'+str(error)+'；'+str(rollback)) from error
        raise
    finally:
        if cleanup:
            shutil.rmtree(pending, ignore_errors=True)

def export_native_package(addon, destination, mesh_objects, armatures=None,
                          physics_objects=None, mesh_only=False, lod_levels=None,
                          include_switches=True, source_baseline='', resource_scope='ALL'):
    import bpy
    vendor=Path(__file__).parent/'vendor'
    if vendor.is_dir() and str(vendor) not in sys.path: sys.path.insert(0,str(vendor))
    if resource_scope not in ('ALL', 'MESH', 'MATERIALS', 'TEXTURES'):
        raise ValueError('未知导出资源范围：' + resource_scope)
    if bpy.context.mode != 'OBJECT': raise ValueError('请回到物体模式后导出')
    objects = list(mesh_objects)
    if not objects: raise ValueError('没有选中的 EFF Mesh')
    if physics_objects: raise ValueError('原生格式的新增 Physics 导出尚未接通，请仅选择 Mesh')
    modules, dll = pack_modules()
    source = modules['author_source'].AuthorSource(baseline_for(objects, source_baseline),
                modules['author_source'].AuthorCore(dll))
    destination = Path(destination).resolve()
    resource_cache = {}
    def read_package(path):
        return modules['ini_reader'].read_package(path, resource_cache)
    existing_ini = None
    if (destination/'mod.ini').is_file():
        read_package(destination)
        existing_ini = modules['ini_package'].ini_document(destination)
    if resource_scope != 'ALL' and existing_ini is None:
        raise ValueError('部分导出需要已有的完整 Mod；首次请选择全部资源')
    if __package__:
        from .eiem_offline_reload import reload_context, compile_reload
    else:
        from eiem_offline_reload import reload_context, compile_reload
    offline_context = reload_context(destination)
    if offline_context is None:
        raise ValueError('完整原生导出需要离线编译目标：请设置 EFF_RELOAD_GAME 或 nativepack/offline-target.json，提供同版本游戏与来源基线')
    if destination == source.package or source.package in destination.parents:
        raise ValueError('不能覆盖原生来源目录')
    bpy.context.view_layer.update()
    plan = (addon.plan_mesh_only_export(objects) if mesh_only or resource_scope != 'ALL' else
            addon.plan_switch_export(objects, include_switches=include_switches))
    if lod_levels is not None and resource_scope == 'ALL': plan = addon.expand_lod_plan(plan, lod_levels)
    encode = modules['type_tree'].encode
    images = {str(i.get('eiem_section', '')): i for i in bpy.data.images if i.get('eiem_section')}
    parts, materials, textures, rules = [], {}, {}, []
    snapshots, part_cache = {}, {}
    switches = [dict(variable=var, key=key, stateCount=len(states), default=default)
                for group, states, default, key, var in plan['groups']]
    with tempfile.TemporaryDirectory(prefix='eff-native-export-') as temporary:
        staging = Path(temporary)/'mod'
        if resource_scope != 'ALL':
            shutil.copytree(destination, staging)
        else:
            staging.mkdir()
        resource_cache['__trusted_roots__'] = (staging.resolve(),)
        if resource_scope in ('MATERIALS', 'TEXTURES'):
            for view in objects:
                original = addon.mesh_export_template(view)
                slots = {polygon.material_index for polygon in original.data.polygons}
                for slot in sorted(slots):
                    mat = original.data.materials[slot] if slot < len(original.data.materials) else None
                    if mat is None:
                        raise ValueError(original.name+' 缺少材质槽 '+str(slot))
                    mid = material_id(mat)
                    if mid not in materials:
                        materials[mid] = collect_material(addon, source, mat, images, textures, temporary,
                                                         encode, existing_ini, resource_scope == 'TEXTURES')
        for group in plan['sources']:
            if resource_scope in ('MATERIALS', 'TEXTURES'):
                break
            obj = group[0]
            path = str(obj.data.get('eiem_target_path', '') or obj.data.get('eiem_source', ''))
            asset = str(obj.data.get('eiem_target_asset', '') or obj.data.get('eiem_asset', ''))
            target = source.resolve('Mesh', path, asset)
            level_match = re.search(r'_lod(\d+)$', asset)
            level = int(level_match.group(1)) if level_match else 0
            members = []
            for view in group:
                if view in plan['hidden']: continue
                original = addon.mesh_export_template(view)
                identity = original.as_pointer()
                if identity not in snapshots:
                    snapshots[identity], _ = _cached_mesh_snapshot(addon, original)
                if identity not in part_cache:
                    cache = []
                    splits = list(split_material(snapshots[identity]))
                    for slot, author in splits:
                        mat = original.data.materials[slot] if slot < len(original.data.materials) else None
                        if mat is None: raise ValueError(original.name+' 缺少材质槽 '+str(slot))
                        mid = material_id(mat)
                        if resource_scope == 'ALL' and mid not in materials:
                            materials[mid] = collect_material(addon, source, mat, images, textures, temporary, encode)
                        mesh, _ = modules['mesh'].native_mesh(source.tree(target), author, asset)
                        suffix = '' if len(splits)==1 else '_mat'+str(slot)
                        pid = 'Mesh'+token(original.name).removeprefix('Mesh')+suffix
                        controls = [dict((k,getattr(c,k)) for k in
                                         ('shape','enabled','identity','label','default','minimum','maximum',
                                          'hotkey_increase','hotkey_decrease','hotkey_speed'))
                                    for c in (() if mesh_only else original.data.eiem_shape_controls)]
                        parts.append(dict(partId=pid,object=pid,material=mid,vertices=author['vertex_count'],
                                          indices=len(author['indices']),bonePaths=author['bone_paths'],
                                          shapeNames=[c[0] for c in author['blend_channels']],shapeControls=controls,
                                          visibilityBindings=plan['bindings'].get(view,[]),
                                          nativeTypeHash=target['native']['typeHash'],
                                          fieldData={f:encode(mesh[f]) for f in modules['delta_apply'].GEOMETRY_FIELDS}))
                        cache.append(pid)
                    part_cache[identity]=cache
                members.extend(part_cache[identity])
            key = tuple(members) if members else ('hidden', tuple(addon.mesh_export_template(v).as_pointer() for v in group))
            existing = next((r for r in rules if r['_key']==key),None)
            if existing:
                existing['targets'].append(dict(level=level,sourcePath=path,sourceAsset=asset))
            else:
                rules.append(dict(_key=key,sourcePath=path,sourceAsset=asset,hidden=not members,parts=members,
                                  targets=[dict(level=level,sourcePath=path,sourceAsset=asset)]))
        # Hide rules across LODs group by the exact original author's family.
        for rule in rules:
            base=min(rule['targets'],key=lambda t:t['level'])
            rule['sourcePath'],rule['sourceAsset']=base['sourcePath'],base['sourceAsset']
            base['level']=0
        if resource_scope == 'ALL':
            modules['ini_package'].export_directory(staging,mod_id=token(destination.name),parts=parts,
                        materials=list(materials.values()),textures=list(textures.values()),rules=rules,
                        switches=switches,resource_cache=resource_cache)
        else:
            modules['ini_package'].update_directory(staging,parts=parts,materials=list(materials.values()),
                        textures=list(textures.values()),texture_only=resource_scope == 'TEXTURES',
                        resource_cache=resource_cache)
        package=publish(staging,destination,read_package)
        offline = compile_reload(destination, offline_context) if offline_context else dict(offlineReloadPrepared=False)
        return dict(meshes=len(parts),materials=0 if resource_scope == 'TEXTURES' else len(materials),
                    textures=len(textures),skeletons=0,physics=0,format=2,
                    rules=len(rules) if resource_scope == 'ALL' else 0,
                    resourceScope=resource_scope,nativeSubmission=False, **offline)

"""Run against the open author scene; output only isolated Mod copies."""
import hashlib
import importlib
import json
from pathlib import Path
import re
import shutil
import sys
import time

import bpy


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def run(production, evidence, prepared_cache=None, compiler_path=None):
    from EIEM_Blender import eiem_blender_addon as addon
    from EIEM_Blender import eiem_native_export as exporter
    from EIEM_Blender import eiem_offline_reload as offline
    importlib.reload(exporter)
    importlib.reload(offline)
    modules, _ = exporter.pack_modules()
    importlib.reload(importlib.import_module(modules['ini_package'].__package__ + '.texture_fields'))
    importlib.reload(modules['ini_reader'])
    writer = importlib.reload(modules['ini_package'])
    sys.path.insert(0, str(Path(exporter.__file__).parent/'vendor'))
    from PIL import Image
    decoder = importlib.import_module(writer.__package__ + '.resource_file')
    production, evidence = Path(production), Path(evidence)
    evidence.mkdir(parents=True, exist_ok=False)
    destination = evidence/'LZY'
    shutil.copytree(production, destination)
    selected = list(bpy.context.selected_objects)
    active = bpy.context.view_layer.objects.active
    body = next(o for o in selected if o.name == 'MeshS_actor_lizhiyan_body_01_lod0_0')
    material = body.data.materials[0]
    saved = dict(material.items())
    old_images = set(bpy.data.images)
    original_path = Path(bpy.path.abspath(material['eiem_texture._BaseMap']))
    source_bytes = original_path.read_bytes()
    same_path = evidence/'body.png'
    baseline = hashes(destination)
    report = {'productionUnchanged': False, 'tests': []}
    production_before = hashes(production)
    write_mesh = addon.write_mesh
    compile_reload = offline.compile_reload
    if prepared_cache or compiler_path:
        def reuse_cache(destination, context=None):
            compiler, baseline, cache, game = context
            return compile_reload(destination, (Path(compiler_path) if compiler_path else compiler,
                                    baseline, Path(prepared_cache) if prepared_cache else cache, game))
        offline.compile_reload = reuse_cache

    def no_mesh(*args, **kwargs):
        raise AssertionError('texture/material-only export called write_mesh')

    def pixel_check(root, source):
        package = modules['ini_reader'].read_package(root)
        body_material = next(m for m in package['materials'] if m['id'] == 'MaterialM_actor_lizhiyan_body_01_0')
        texture_id = next(e['texture'] for e in body_material['textureEdits'] if e['property'] == '_BaseMap')
        texture = next(t for t in package['textures'] if t['id'] == texture_id)
        pixels = decoder.decode_resource((root/texture['file']).read_bytes(), decoder.PIXELS)['blocks']['pixels']
        with Image.open(source) as image:
            mip = image.convert('RGBA'); expected = bytearray()
            for _ in range(texture['mipCount']):
                expected.extend(mip.transpose(Image.Transpose.FLIP_TOP_BOTTOM).tobytes())
                mip = mip.resize((max(1, mip.width//2), max(1, mip.height//2)), Image.Resampling.BOX)
        assert pixels == bytes(expected), 'source image/mip pixel mismatch'
        return texture['sha256']

    try:
        # Normalize the legacy fixture once before checking pixel-only INI stability.
        legacy = modules['ini_reader'].read_package(destination)
        textures = [dict(t, sourceAsset=t['id'].removeprefix('Texture'),
                         pixelLabel=re.sub(r'_[0-9a-f]{12}$', '', Path(t['file']).stem),
                         pixels=decoder.decode_resource((destination/t['file']).read_bytes(), decoder.PIXELS)['blocks']['pixels'])
                    for t in legacy['textures']]
        writer.update_directory(destination, textures=textures, texture_only=True)
        baseline = hashes(destination)
        material['eiem_texture._BaseMap'] = str(same_path)
        with Image.open(original_path) as image:
            Image.new('RGBA', image.size, (255, 255, 255, 255)).save(same_path)
        addon.write_mesh = no_mesh
        start = time.perf_counter()
        white = exporter.export_native_package(addon, destination, [body],
                    source_baseline=bpy.context.scene.eiem_source_baseline, resource_scope='TEXTURES')
        first = hashes(destination)
        assert first['mod.ini'] == baseline['mod.ini'], 'pixel-only export rewrote INI'
        assert all(first[name] == value for name, value in baseline.items() if not name.startswith('textures/'))
        white_hash = pixel_check(destination, same_path)
        report['tests'].append(dict(name='white-texture-export', elapsed=time.perf_counter()-start, **white))

        # The Blender image cache still contains WHITE; the disk path is unchanged.
        shutil.copy2(original_path, same_path)
        start = time.perf_counter()
        restored = exporter.export_native_package(addon, destination, [body],
                    source_baseline=bpy.context.scene.eiem_source_baseline, resource_scope='TEXTURES')
        second = hashes(destination)
        assert first['mod.ini'] == second['mod.ini']
        assert white_hash != pixel_check(destination, same_path)
        assert white['offlineReloadKey'] != restored['offlineReloadKey']
        assert all(first[name] == second[name] for name in first if not name.startswith('textures/'))
        report['tests'].append(dict(name='same-path-restored-texture-export', elapsed=time.perf_counter()-start, **restored))

        # Material-only controls are verified with native author bytes; compilation
        # was already exercised above using the installed compiler.
        offline.compile_reload = lambda *args: dict(offlineReloadPrepared=False)
        material['eiem_float._BumpScale'] = '0.75'
        stats = exporter.export_native_package(addon, destination, [body],
                    source_baseline=bpy.context.scene.eiem_source_baseline, resource_scope='MATERIALS')
        third = hashes(destination)
        assert third['materials/MaterialM_actor_lizhiyan_body_01_0.mat'] != second['materials/MaterialM_actor_lizhiyan_body_01_0.mat']
        assert all(third[name] == second[name] for name in second if name.startswith('meshes/'))
        report['tests'].append(dict(name='material-only-native-delta', **stats))

        addon.write_mesh = write_mesh
        stats = exporter.export_native_package(addon, destination, [body],
                    source_baseline=bpy.context.scene.eiem_source_baseline, resource_scope='MESH')
        fourth = hashes(destination)
        assert third['mod.ini'] == fourth['mod.ini']
        assert all(third[name] == fourth[name] for name in third if not name.startswith('meshes/'))
        report['tests'].append(dict(name='mesh-only-preserves-materials-controls', **stats))

        full = evidence/'full-body'/'LZY'
        stats = exporter.export_native_package(addon, full, [body],
                    source_baseline=bpy.context.scene.eiem_source_baseline,
                    lod_levels=[0, 1, 2, 3], resource_scope='ALL')
        assert stats['meshes'] == 1 and stats['textures'] == 2
        package = modules['ini_reader'].read_package(full)
        assert len(package['shapeControls']) == 2 and len(package['rules'][0]['targets']) == 4
        report['tests'].append(dict(name='full-export-default-shapes-lods', **stats))
        stats = exporter.export_native_package(addon, full, [body],
                    source_baseline=bpy.context.scene.eiem_source_baseline,
                    lod_levels=[0, 1, 2, 3], resource_scope='ALL')
        package = modules['ini_reader'].read_package(full)
        assert all(Path(t['file']).name in ('body.tex', 'body_NM.tex') for t in package['textures'])
        report['tests'].append(dict(name='full-export-existing-texture-paths', **stats))
        report['success'] = True
    finally:
        addon.write_mesh = write_mesh
        offline.compile_reload = compile_reload
        for key in list(material.keys()):
            if key not in saved:
                del material[key]
        for key, value in saved.items():
            material[key] = value
        for image in set(bpy.data.images)-old_images:
            bpy.data.images.remove(image)
        assert selected == list(bpy.context.selected_objects) and active == bpy.context.view_layer.objects.active
        assert original_path.read_bytes() == source_bytes
        report['productionUnchanged'] = production_before == hashes(production)
        assert report['productionUnchanged']
        (evidence/'verification.json').write_text(json.dumps(report, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    run(PRODUCTION, EVIDENCE)

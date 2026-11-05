"""Real source import/unchanged export must not require tangent regeneration.

Run in factory-startup Blender with -- <addon-dir> <source.efffx> <output-dir>.
"""
import importlib
import json
from pathlib import Path
import sys
import bpy

addon_path, source, output = sys.argv[sys.argv.index('--') + 1:]
sys.path.insert(0, str(Path(addon_path).resolve().parent))
addon = importlib.import_module(Path(addon_path).name)
addon.register()
editor = addon.eiem_blender_addon.effect_editor
assets = editor.assets
original = addon.eiem_blender_addon.write_mesh
calls = []
def forbidden(*args, **kwargs):
    calls.append(True)
    raise AssertionError('unchanged source entered native Mesh regeneration')
addon.eiem_blender_addon.write_mesh = forbidden
try:
    root = editor.import_source(bpy.context, source)
    edit = editor.synchronize(root)
    baseline = assets.export_document(root, edit)
    assert not calls and not baseline.get('assets')
    assert not any(edit.objects[row['identity']]['classId'] == 43 for row in baseline['objects'])
    meshes = assets.source_meshes(root)
    assert meshes
    sample = next(iter(meshes.values()))
    before = assets.mesh_fingerprint(sample)
    sample.location.x += 2
    assert assets.mesh_fingerprint(sample) == before, 'template placement entered geometry fingerprint'
    sample.data.vertices[0].co.x += .01
    sample.data.update()
    bpy.context.view_layer.update()
    assert assets.mesh_fingerprint(sample) != before, 'edited evaluated geometry was missed'
    sample.data.vertices[0].co.x -= .01
    sample.data.update()
    # A save/reopen must preserve the baseline without running the encoder.
    path = Path(output); path.mkdir(parents=True, exist_ok=True)
    name = root.name
    bpy.ops.wm.save_as_mainfile(filepath=str(path / 'source-geometry.blend'))
    bpy.ops.wm.open_mainfile(filepath=str(path / 'source-geometry.blend'))
    # Float subtraction is not bit-exact; use a fresh import for the no-edit roundtrip.
    root = editor.import_source(bpy.context, source)
    name = root.name
    bpy.ops.wm.save_as_mainfile(filepath=str(path / 'source-geometry.blend'))
    bpy.ops.wm.open_mainfile(filepath=str(path / 'source-geometry.blend'))
    root = bpy.data.objects[name]
    assert assets.export_document(root, editor.synchronize(root)) == baseline
    assert not calls
    print('PASS source import, zero native Mesh changes, evaluated edit detection, placement and save/reopen')
finally:
    addon.eiem_blender_addon.write_mesh = original
    addon.unregister()

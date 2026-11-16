"""Reopen a real mixed Billboard/Mesh edit and build its native Prefab."""
import importlib
import json
from pathlib import Path
import sys

import bpy

addon_path, fixture, output, compiler = map(Path, sys.argv[sys.argv.index('--') + 1:])
sys.path.insert(0, str(addon_path.resolve().parent))
addon = importlib.import_module(addon_path.name)
addon.register()
fx = addon.eiem_blender_addon.effect_editor
payload = json.loads(fixture.read_bytes())
state = fx.document.load_source(payload['source'])
fx.document.apply_edit(state, payload)
root = fx.import_workspace(bpy.context, state, preserve_source=True)
root.eff_effect.preview = False
edit = fx.synchronize(root)
emitters = [n for n in edit.state['catalog']['hierarchy']
            if any(edit.objects[i]['classId'] == 198 for i in n['components'])]
assert len(emitters) == 4, [n['name'] for n in emitters]
mesh_node = next(n for n in emitters if n['name'] == '网格自旋_fx_nr007')
renderer = next(i for i in mesh_node['components'] if edit.objects[i]['classId'] == 199)
assert edit.value(renderer)['m_RenderMode'] == 4
mesh_object = next(o for o in fx.objects(root) if o.get('eff_effect_node') == mesh_node['identity'])
assert mesh_object.eff_effect.particle_mesh and mesh_object.eff_effect.particle_mesh.data.polygons
material = fx.preview._target(edit, renderer, ['m_Materials', 0])
assert fx.assets.material_object(root, material)
output.mkdir(parents=True, exist_ok=True)
first = fx.assets.export_document(root, edit)
build = fx.effect_publish.Build(first, compiler, output / 'native-first', 'mesh-merge', fx.effect_codec().read_native_build)
assert fx.effect_codec().read_native_build(build.run())
bpy.ops.wm.save_as_mainfile(filepath=str(output / 'merged.blend'))
bpy.ops.wm.open_mainfile(filepath=str(output / 'merged.blend'), load_ui=False, use_scripts=False)
root = next(o for o in bpy.data.objects if o.get('eff_effect_document'))
after = fx.assets.export_document(root, fx.synchronize(root))
assert after == first, 'save/reopen changed the composed resources'
snapshot = fx.assets.clipboard_snapshot(root, fx.synchronize(root))
copied = fx.block_clipboard.copy_emitter(snapshot, mesh_node['identity'])
assert any(r['classId'] == 43 for r in copied['resources'])
assert any(r['classId'] == 21 for r in copied['resources'])
print('PASS four emitters, Mesh/Material/Texture references, native build, save/reopen, portable Mesh emitter copy')
addon.unregister()

"""Real Mesh emitter export: reuse scene materials and preserve native submeshes."""
import importlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

import bpy
from mathutils import Matrix, Vector

arguments = list(map(Path, sys.argv[sys.argv.index('--') + 1:]))
addon_path, fixture, output = arguments[:3]
sys.path.insert(0, str(addon_path.resolve().parent))
addon = importlib.import_module(addon_path.name)
addon.register()
fx = addon.eiem_blender_addon.effect_editor
with bpy.data.libraries.load(str(fixture)) as (source, destination):
    destination.objects = source.objects
for obj in destination.objects:
    bpy.context.scene.collection.objects.link(obj)
root = next(o for o in destination.objects if o.get('eff_effect_document'))
root.eff_effect.preview = False
node = next(o for o in fx.objects(root) if o.eff_effect.particle_mesh and
            o.name.startswith('网格自旋'))
selected = node.eff_effect.particle_mesh
material = selected.data.materials[0]
assert material and material.eff_effect_author is None
edit = fx.synchronize(root)
before = root['eff_effect_document']
bindings = importlib.import_module(addon.__name__ + '.eiem_effect_scene_material')
with patch.object(bindings.Import, 'source', side_effect=AssertionError('export reread imported material source')), \
     patch.object(fx.document, 'load_source', side_effect=AssertionError('export reread material donor')):
    first = fx.assets.export_document(root, edit)
    assert first == fx.assets.export_document(root, edit), 'scene material export is not deterministic'
assert material.get(bindings.IMPORT_KEY), 'old scene metadata was not recovered from loaded EFF data'
assert root['eff_effect_document'] == before, 'export rewrote the author workspace'

# Adopt a placed, parented shape without adding the mount offset twice. The
# object origin is explicit; native Transform and GN placement share the pose.
parent = bpy.data.objects.new('particle placement parent', None)
bpy.context.scene.collection.objects.link(parent)
parent.location = (.3, -.7, .2)
parent.rotation_euler = (.1, .3, -.2)
parent.scale = (.5, .7, .4)
placed_data = bpy.data.meshes.new('off-origin shape with explicit origin edit')
placed_data.from_pydata([(2,3,4),(4,3,4),(4,5,4),(2,5,4)], [], [(0,1,2),(0,2,3)])
placed = bpy.data.objects.new('placed shape', placed_data)
bpy.context.scene.collection.objects.link(placed)
placed.parent = parent
placed.location = (-.2, .4, .6)
placed.rotation_euler = (.2, -.4, .1)
placed.scale = (.6, .6, .6)
bpy.context.view_layer.update()
visible = [placed.matrix_world @ v.co for v in placed_data.vertices]
bpy.ops.object.select_all(action='DESELECT')
placed.select_set(True);bpy.context.view_layer.objects.active=placed
bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY', center='BOUNDS')
bpy.context.view_layer.update()
assert all((old-placed.matrix_world @ v.co).length<1e-5 for old,v in zip(visible,placed_data.vertices))
node.eff_effect.particle_mesh=placed
edit=fx.synchronize(root)
placement=fx.preview.emitter_matrix(root,node)
assert (placement.translation-placed.matrix_world.translation).length<1e-5, 'mount offset applied twice'
vertices,_,_,_=fx.assets.preview_mesh(placed)
actual=[placement @ (fx.assets.BASIS @ v) for v in vertices]
assert all(any((point-old).length<1e-5 for old in visible) for point in actual), 'placed particle differs from source'
transform=next(i for i in json.loads(node['eff_effect_components']) if edit.objects[i]['classId']==4)
native=edit.value(transform)
local=node.matrix_parent_inverse @ node.matrix_basis
expected=fx.BASIS.inverted() @ local @ fx.BASIS
assert (Vector(tuple(native['m_LocalPosition'][k] for k in 'xyz'))-expected.translation).length<1e-5
# Later author placement is independent: ordinary export must not adopt again.
node.location.x+=.25
bpy.context.view_layer.update();fx.synchronize(root)
authored=node.matrix_world.copy()
fx.assets.mesh_snapshot(placed)
assert node.matrix_world==authored, 'Mesh snapshot reset emitter placement'
node.eff_effect.particle_mesh=selected
print('PASS placed Mesh, object origin, mount compensation, native Transform and independent author placement')
for asset in first.get('assets', []):
    origin = asset.get('sourceMaterial')
    if not origin:continue
    if origin['value']['m_Name'] == 'M_fx_liino_kv_139':
        assert origin['source'] == 'assets/beyond/arts/effects/battle/character/chr_0035_liino/material/m_fx_liino_kv_139.mat'
    if origin['value']['m_Name'] == 'M_fxmap_map02_lv008_lightspot_2501':
        assert origin['source'].endswith('/p_fxmap_map02_lv008_lightspot_2501.prefab')
    assert origin['source'] != 'assets/beyond/arts/effects/map/material/m_fx_interactive_shokcwavetower_804.mat', \
        'arbitrary schema template replaced the real imported Material origin'

# Multiple used slots and UV-less geometry are both legal native Mesh data.
mesh = bpy.data.meshes.new('two submeshes, no UV')
mesh.from_pydata([(0,0,0),(1,0,0),(0,1,0),(0,0,1)], [], [(0,1,2),(0,2,3)])
mesh.materials.append(material)
second_material = material.copy()
second_material['eiem_float._UseBlend'] = '0'
mesh.materials.append(second_material)
mesh.polygons[1].material_index = 1
obj = bpy.data.objects.new('two material particle Mesh', mesh)
bpy.context.scene.collection.objects.link(obj)
node.eff_effect.particle_mesh = obj
snapshot = fx.assets.mesh_snapshot(obj)
assert len(snapshot['submeshes']) == 2
assert not snapshot['uvs'][0]
export = fx.assets.export_document(root, fx.synchronize(root))
state = fx.document.load_source(export['source'])
fx.document.apply_edit(state, export)
view = fx.document.Editor(state)
ids = json.loads(node['eff_effect_components'])
renderer = next(i for i in ids if edit.objects[i]['classId'] == 199)
value = view.value(renderer)
assert len(value['m_Materials']) == 2
assert value['m_Materials'][0] != value['m_Materials'][1]
identity = fx.preview._target(view, renderer, ['m_Mesh'])
native = fx.assets.NativeAssets(view, 'verify').reader.mesh(identity)
assert len(native['submeshes']) == 2
assert [len(s) for s in native['submeshes']] == [1, 1]
assert sum(len(s) for s in native['submeshes']) == 2
assert not native['uvs']
output.mkdir(parents=True, exist_ok=True)
(output/'scene-material.edits.json').write_text(fx.document.dumps(first), encoding='utf-8')
(output/'multi-material.edits.json').write_text(fx.document.dumps(export), encoding='utf-8')
if len(arguments) > 3:
    for label, payload in (('scene', first), ('multi', export)):
        build = fx.effect_publish.Build(payload, arguments[3], output / ('native-' + label),
                                        'scene-material-' + label, fx.effect_codec().read_native_build)
        assert fx.effect_codec().read_native_build(build.run())
        print('PASS compiled native particle Mesh:', label)
print('PASS unrelated scene EFF material, deterministic export, two materials/submeshes, UV-less Mesh, workspace unchanged')
addon.unregister()

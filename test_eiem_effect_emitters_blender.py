"""Real Blender: degree controls, independent emitters, removal and persistence."""
import copy
import importlib
import json
import math
from pathlib import Path
import sys

import bpy

addon_path, source, output = sys.argv[sys.argv.index('--') + 1:]
output = Path(output); output.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(addon_path).resolve().parent))
addon = importlib.import_module(Path(addon_path).name); addon.register()
editor = addon.eiem_blender_addon.effect_editor
root = editor.import_source(bpy.context, source)
initial_edits = copy.deepcopy(editor.editor(root).export()['objects'])
edit = editor.synchronize(root)
node = next(o for o in editor.objects(root) if o.name.startswith('leaves_01'))
identity = next(i for i in json.loads(node['eff_effect_components']) if edit.objects[i]['classId'] == 198)
assert edit.export()['objects']==initial_edits, 'preparing degree controls changed native values'

# Degree editing stores radians exactly once, independent of scene units.
path = ['InitialModule', 'startRotation', 'scalar']
key = editor.field_key(identity, path)
bpy.context.scene.unit_settings.system_rotation = 'RADIANS'
original = node[key]
assert abs(node.eff_effect_angles[key].degrees - math.degrees(original)) < 1e-3
node.eff_effect_angles[key].degrees = 180
assert abs(editor.synchronize(root).value(identity, path) - math.pi) < 1e-7
node[key] = -math.pi / 2
assert abs(node.eff_effect_angles[key].degrees + 90) < 1e-4
node[key] = original
speed_path = ['RotationModule', 'curve', 'scalar']
speed_key = editor.field_key(identity, speed_path)
node.eff_effect_angles[speed_key].degrees = 90
assert abs(editor.synchronize(root).value(identity, speed_path) - math.pi / 2) < 1e-7
assert editor.effect_ui.angle_unit(['ShapeModule', 'angle']) is None
assert editor.effect_ui.angle_unit(['ShapeModule', 'm_Rotation', 'x']) is None
assert editor.effect_ui.angle_unit(['RotationModule', 'curve', 'maxCurve', 'm_Curve', 0, 'value']) is None
node.eff_effect_angles.clear();node['eff_effect_angles_version'] = 0
node[key] = math.pi / 3
editor.upgrade_loaded()
assert abs(node.eff_effect_angles[key].degrees - 60) < 1e-4

# Continuous mode removes bursts and emits across cycle boundaries.
bpy.context.view_layer.objects.active = node
initial = copy.deepcopy(editor.synchronize(root).value(identity, ['InitialModule']))
assert bpy.ops.eiem.effect_continuous(object_name=node.name, identity=identity, rate=5, capacity=100) == {'FINISHED'}
edit = editor.synchronize(root);data = edit.value(identity)
assert data['InitialModule']['startLifetime'] == initial['startLifetime']
assert data['looping'] and data['EmissionModule']['m_Bursts'] == []
assert data['EmissionModule']['m_BurstCount'] == 0
assert data['EmissionModule']['rateOverTime']['minMaxState'] == 0
for time in (2, 9.9, 10.1, 12, 25):
    births = [p['born'] for p in editor.preview.sim.particles(data, time)]
    assert births and len(births) == len(set(births))
    assert time - max(births) < .201
    assert all(abs(born * 5 - round(born * 5)) < 1e-5 for born in births)

# Add a complete emitter (GO, Transform, PS, renderer and native script).
assert bpy.ops.eiem.effect_emitter_add(template=node['eff_effect_node'], name='新增花瓣') == {'FINISHED'}
clone = bpy.context.object
edit = editor.synchronize(root)
clone_ids = json.loads(clone['eff_effect_components'])
clone_ps = next(i for i in clone_ids if edit.objects[i]['classId'] == 198)
assert clone != node and clone.eff_effect.particle_mesh == node.eff_effect.particle_mesh
assert not set(clone_ids) & set(json.loads(node['eff_effect_components']))
assert editor.preview._target(edit, clone_ps, ['m_GameObject']) == clone['eff_effect_node']
clone.eff_effect_angles[editor.field_key(clone_ps, path)].degrees = 270
clone[editor.field_key(clone_ps, ['InitialModule', 'startLifetime', 'minMaxState'])] = 0
clone[editor.field_key(clone_ps, ['InitialModule', 'startLifetime', 'scalar'])] = 2.5
clone.location.x += .3
edit = editor.synchronize(root)
assert abs(edit.value(identity, path) - math.pi/3) < 1e-7
assert abs(edit.value(clone_ps, path) - math.pi*1.5) < 1e-6
assert edit.value(clone_ps, ['InitialModule','startLifetime','scalar']) == 2.5
bpy.context.scene.frame_set(75)
assert not root.get('预览错误'), root.get('预览错误')
assert any(o.get('eff_preview_key') == root['eff_effect_id'] + clone_ps for o in root.children_recursive)

# Add from an already authored emitter and remove it without leaving assets.
assert bpy.ops.eiem.effect_emitter_add(template=clone['eff_effect_node'], name='临时发射器') == {'FINISHED'}
temporary = bpy.context.object
temporary_ids = {temporary['eff_effect_node'], *json.loads(temporary['eff_effect_components'])}
assert bpy.ops.eiem.effect_emitter_remove(object_name=temporary.name) == {'FINISHED'}
assert not temporary_ids & set(editor.editor(root).objects)
assert not any(o.get('eff_effect_node') in temporary_ids for o in bpy.data.objects)

# Remove a source emitter as well. Source records are preserved as inactive
# native tombstones by the compiler, with no surviving Blender/preview node.
other = next(o for o in editor.objects(root) if o.name.startswith('leaves_02'))
removed_go = other['eff_effect_node']
removed_ps = next(i for i in json.loads(other['eff_effect_components']) if edit.objects[i]['classId'] == 198)
# A rejected deletion must leave both the native state and Blender node intact.
child = bpy.data.objects.new('作者子对象',None);root.users_collection[0].objects.link(child);child.parent = other
before_reject = copy.deepcopy(editor.synchronize(root).state)
try:bpy.ops.eiem.effect_emitter_remove(object_name=other.name)
except RuntimeError as error:assert '子对象' in str(error)
else:raise AssertionError('deleting an emitter with author children was accepted')
assert editor.editor(root).state == before_reject and bpy.data.objects.get(other.name) == other
bpy.data.objects.remove(child,do_unlink=True)
assert bpy.ops.eiem.effect_emitter_remove(object_name=other.name) == {'FINISHED'}
edit = editor.synchronize(root)
assert removed_go in edit.state['removed_emitters']
assert not any(o.get('eff_effect_node') == removed_go for o in bpy.data.objects)
assert not any(o.get('eff_preview_key') == root['eff_effect_id'] + removed_ps for o in bpy.data.objects)
document = editor.assets.export_document(root, edit)
assert len(document['emitters']['added']) == 5
assert document['emitters']['removed'] == [removed_go]
assert len([n for n in edit.state['catalog']['hierarchy'] if any(edit.objects[i]['classId']==198 for i in n['components'])]) == 2
# Raw hierarchy mutation cannot masquerade as a declared emitter operation.
transform = next(n['transform'] for n in edit.state['catalog']['hierarchy'] if n['identity'] == edit.state['catalog']['root'])
edit.value(transform)['m_Children'].append(dict(m_FileID=0,m_PathID=1234567))
try:edit.export()
except ValueError as error:assert '子级' in str(error)
else:raise AssertionError('undeclared raw child mutation was ignored')
edit.value(transform)['m_Children'].pop()
editor.assets.write_document(root, edit, output / 'emitters.effedit.json')

# Save/reopen and repeated registration must preserve topology, parameters and
# all custom source bindings. A raw Shift+D copy is rejected, not aliased.
clone_name, root_name = clone.name, root.name
bpy.ops.wm.save_as_mainfile(filepath=str(output / 'emitters.blend'))
bpy.ops.wm.open_mainfile(filepath=str(output / 'emitters.blend'))
root, clone = bpy.data.objects[root_name], bpy.data.objects[clone_name]
assert abs(clone.eff_effect_angles[editor.field_key(clone_ps,path)].degrees - 270) < 1e-4
assert editor.assets.export_document(root, editor.synchronize(root)) == document
for _ in range(2): addon.unregister(); addon.register()
editor.upgrade_loaded()
assert editor.assets.export_document(root, editor.synchronize(root)) == document
bad = clone.copy();root.users_collection[0].objects.link(bad);bad.parent = clone.parent
try:
    editor.synchronize(root)
except ValueError as error:
    assert 'Shift+D' in str(error)
else:raise AssertionError('raw duplicate silently aliased the original native emitter')
bpy.data.objects.remove(bad, do_unlink=True)
# Geometry/material edits must also bind the new virtual renderer, using the
# same native clone as the remaining original renderer.
particle=clone.eff_effect.particle_mesh
particle.data.vertices[0].co.x += .125;particle.data.update()
mat=particle.data.materials[0]
image=bpy.data.images.new('new-emitter-test-image',width=2,height=2,alpha=True)
image.pixels[:]=[1,.25,.5,.75]*4
material_author=mat.eff_effect_author
material_author.eff_effect_textures[0].image=image
editor.assets.write_document(root,editor.synchronize(root),output/'emitters-assets.effedit.json')
print('PASS degrees/native radians, continuous cycles, independent emitter components, duplicate clone, source/new deletion, preview, save/reopen, registration and raw-duplicate rejection')
addon.unregister()

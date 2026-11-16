"""Real depsgraph -> preview timer must settle after edits, without idle loops.

Run in a separate factory-startup Blender with -- ADDON PROJECT OUTPUT.
Never writes the user's project or source resources.
"""
import copy
import importlib
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch

import bpy
from mathutils import Vector

addon_path, project, output = map(Path, sys.argv[sys.argv.index('--') + 1:])
sys.path.insert(0, str(addon_path.resolve().parent))
addon = importlib.import_module(addon_path.name)
addon.register()
bpy.ops.wm.open_mainfile(filepath=str(project))
editor = addon.eiem_blender_addon.effect_editor
preview = editor.preview
roots = [obj for obj in bpy.context.scene.objects if obj.get('eff_effect_document')]
assert len(roots) == 4
for root in roots:
    root.eff_effect.preview = False
    root.eff_effect.show_shape = False
root = next(obj for obj in roots if obj.name == 'P_fxui_liino_relax_sp_01')
root.eff_effect.preview = True
root.eff_effect.source_animation = True
baselines = {obj.name: copy.deepcopy(editor.synchronize(obj).export()) for obj in roots}
bpy.context.scene.frame_set(73)
bpy.context.view_layer.update()

results = {}


def pump():
    # Expire the debounce deterministically; evaluate Blender's *real* queued
    # dependency updates, including those produced by synchronization itself.
    for key, (_, nodes) in list(preview._pending_updates.items()):
        preview._pending_updates[key] = (0, nodes)
    start = time.perf_counter()
    result = preview.live_update()
    bpy.context.view_layer.update()
    return round((time.perf_counter() - start) * 1000, 3), result


def set_property(obj, key, value):
    # Python IDProperty assignments do not tag the graph like UI editing does.
    obj[key] = value
    obj.update_tag()


def settle(label, edit=None):
    if edit:edit()
    bpy.context.view_layer.update()
    with patch.object(preview, '_sync', wraps=preview._sync) as sync:
        timings, counts = [], []
        for _ in range(8):
            timings.append(pump()[0])
            counts.append(sync.call_count)
        results[label] = dict(tickMs=timings, syncCounts=counts, pending=len(preview._pending_updates))
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(json.dumps(results, indent=2), 'utf-8')
        assert counts[-1] == counts[-4], (label, 'idle keeps synchronizing', counts)
        assert not preview._pending_updates, (label, 'preview regenerated author invalidations')
        assert all(not obj.get('预览错误') for obj in roots)
    return counts[-1]


settle('initial')
assert all(editor.synchronize(obj).export() == baselines[obj.name] for obj in roots), 'idle preview changed native author data'
# An identical depsgraph event (selection/redraw/parent reevaluation) must not
# synchronize or rebuild particles, even when it names a specific author node.
with patch.object(preview, '_sync', side_effect=AssertionError('unchanged author synchronized')), \
     patch.object(preview, 'refresh', side_effect=AssertionError('unchanged preview rebuilt')):
    preview.schedule_refresh(root, {root.as_pointer()})
    pump()

node = next(obj for obj in editor.objects(root) if obj.name == 'wenli2')
native = editor.editor(root)
identity = next(i for i in json.loads(node['eff_effect_components']) if native.objects[i]['classId'] == 198)
key = editor.field_key(identity, ['InitialModule', 'startSpeed', 'scalar'])
original = node[key]
assert settle('scalar', lambda: set_property(node, key, float(original) + .25)) > 0
assert native.value(identity)['InitialModule']['startSpeed']['scalar'] == node[key]
settle('restore_scalar', lambda: set_property(node, key, original))

position = node.location.copy()
native_transform = next(i for i in json.loads(node['eff_effect_components']) if native.objects[i]['classId'] == 4)
rest_transform = copy.deepcopy(native.value(native_transform))
assert settle('transform', lambda: setattr(node, 'location', position + Vector((.1, 0, 0)))) > 0
settle('restore_transform', lambda: setattr(node, 'location', position))
# Moving Blender floats through TRS decompositions can round the native source.
# Restore the exact native fields, as undo/field restoration would do.
def restore_transform_fields():
    for field in ('m_LocalPosition', 'm_LocalRotation', 'm_LocalScale'):
        for axis, value in rest_transform[field].items():
            node[editor.field_key(native_transform, [field, axis])] = value
    node.update_tag()
settle('restore_native_transform', restore_transform_fields)

mesh = next(obj.eff_effect.particle_mesh for obj in editor.objects(root) if obj.eff_effect.particle_mesh)
vertex = mesh.data.vertices[0]
coordinate = vertex.co.copy()
def change_mesh(value):
    vertex.co = value
    mesh.data.update()
with patch.object(editor.assets, 'preview_mesh', wraps=editor.assets.preview_mesh) as evaluate:
    assert settle('mesh', lambda: change_mesh(coordinate + Vector((.01, 0, 0)))) > 0
    assert evaluate.called, 'actual Mesh edits no longer invalidate particle geometry'
settle('restore_mesh', lambda: change_mesh(coordinate))

# GN input values live in the modifier's properties, not its RNA property list.
# Keep geometry invalidations while discarding irrelevant Object updates.
tree = bpy.data.node_groups.new('Idle regression geometry', 'GeometryNodeTree')
tree.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
tree.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
scale = tree.interface.new_socket(name='Scale', in_out='INPUT', socket_type='NodeSocketFloat')
scale.default_value = 1
inp = tree.nodes.new('NodeGroupInput')
transform = tree.nodes.new('GeometryNodeTransform')
out = tree.nodes.new('NodeGroupOutput')
tree.links.new(inp.outputs['Geometry'], transform.inputs['Geometry'])
tree.links.new(inp.outputs['Scale'], transform.inputs['Scale'])
tree.links.new(transform.outputs['Geometry'], out.inputs['Geometry'])
modifier = mesh.modifiers.new('Idle regression', 'NODES')
modifier.node_group = tree
settle('add_modifier')
def change_modifier():
    getattr(mesh.modifiers['Idle regression'].properties.inputs, scale.identifier).value = .8
    mesh.update_tag(refresh={'DATA'})
with patch.object(editor.assets, 'preview_mesh', wraps=editor.assets.preview_mesh) as evaluate:
    assert settle('modifier_input', change_modifier) > 0
    assert evaluate.called, 'Geometry Nodes inputs no longer invalidate particle geometry'
settle('remove_modifier', lambda: mesh.modifiers.remove(mesh.modifiers['Idle regression']))
bpy.data.node_groups.remove(tree)

author = editor.assets.material_object(root, next(i for i, spec in native.objects.items()
    if spec.get('name') == 'M_fx_liino_relax_wenli_901' and spec['classId'] == 21))
material = editor.material_bridge.material_for(author)
alpha = material['eiem_float._TintColorAlpha']
assert settle('material', lambda: set_property(material, 'eiem_float._TintColorAlpha', '.4')) > 0
settle('restore_material', lambda: set_property(material, 'eiem_float._TintColorAlpha', alpha))
assert all(editor.synchronize(obj).export() == baselines[obj.name] for obj in roots), 'preview changed native author data'
results['passed'] = True
Path(output).write_text(json.dumps(results, indent=2), 'utf-8')
addon.unregister()
print('PASS idle timer settles; unchanged updates skipped; live scalar/TRS/Mesh/material edits retained')

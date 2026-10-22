"""Real ColorRamp interaction -> independent native Gradient tracks and history."""
import copy
import importlib
import json
import math
import statistics
import sys
import time
from pathlib import Path
import bpy

addon_path, source, output = sys.argv[sys.argv.index('--') + 1:]
output = Path(output); output.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(addon_path).resolve().parent))
addon = importlib.import_module(Path(addon_path).name); addon.register()
ed = addon.eiem_blender_addon.effect_editor
ramps = ed.gradient_editor
root = ed.import_source(bpy.context, source)
node = next(o for o in ed.objects(root) if o.name == 'leaves_01')
identity = next(i for i in json.loads(node['eff_effect_components']) if ed.editor(root).objects[i]['classId'] == 198)
path = ['ColorModule', 'gradient', 'maxGradient']
root_name, node_name = root.name, node.name
selection = [o.name for o in bpy.context.selected_objects]
active = bpy.context.object.name
baseline = copy.deepcopy(ed.editor(root).state)
tree = ramps.tree_for(node)
key = ramps.key_for(identity, path)

def record(): return ramps.metadata(ramps.tree_for(node))[key]
def ramp(channel): return ramps.tree_for(node).nodes[record()[channel]].color_ramp
def value(): return ed.editor(root).value(identity, path)
def sync(): return ed.synchronize(root)

assert tree is not None
for _ in range(3): sync()
assert ed.editor(root).state == baseline, 'Opening ramps changed native data'
assert [o.name for o in bpy.context.selected_objects] == selection and bpy.context.object.name == active

# Native RGB handles do not touch independent Alpha times, values or count.
before = copy.deepcopy(value())
point = ramp('rgb').elements.new(.35); point.color = (.2, .4, .7, 1)
sync()
assert value()['m_NumColorKeys'] == 3 and value()['ctime1'] == round(.35 * 65535)
assert abs(value()['key1']['g'] - .4) < 1e-6
for name in ['m_NumAlphaKeys'] + ['atime'+str(i) for i in range(8)]: assert value()[name] == before[name]
assert all(value()['key'+str(i)]['a'] == before['key'+str(i)]['a'] for i in range(8))
before = copy.deepcopy(value())
point = ramp('alpha').elements.new(.8); point.alpha = .25
sync()
assert value()['m_NumAlphaKeys'] == 3 and value()['atime1'] == round(.8 * 65535)
assert abs(value()['key1']['a'] - .25) < 1e-6
assert all(value()['ctime'+str(i)] == before['ctime'+str(i)] for i in range(8))
assert all(all(value()['key'+str(i)][c] == before['key'+str(i)][c] for c in 'rgb') for i in range(8))
ramp('rgb').elements.remove(ramp('rgb').elements[1]); sync()
assert value()['m_NumColorKeys'] == 2 and value()['m_NumAlphaKeys'] == 3

# One native interpolation mode is shared by both independent tracks.
ramp('alpha').interpolation = 'CONSTANT'; sync()
assert value()['m_Mode'] == 1 and ramp('rgb').interpolation == 'CONSTANT'
ramp('rgb').interpolation = 'LINEAR'; sync()
assert value()['m_Mode'] == 0 and ramp('alpha').interpolation == 'LINEAR'

# Do not wait for timers when exporting a last-moment drag.
ramp('alpha').elements[1].position = .7
exported = ed.assets.export_document(root, sync())
assert value()['atime1'] == round(.7 * 65535)
assert all('gradient_tree' not in str(row) for row in exported['objects'])

# Advanced native widgets update the ramps; old ramp data must not overwrite them.
node[ed.field_key(identity, path + ['atime1'])] = 40000
sync()
assert abs(ramp('alpha').elements[1].position - 40000/65535) < 1e-6
assert value()['atime1'] == 40000

# Random min/max gradients and disabled modules remain separate.
other_path = ['ColorModule', 'gradient', 'minGradient']
other = copy.deepcopy(ed.editor(root).value(identity, other_path))
node[ed.field_key(identity, ['ColorModule', 'gradient', 'minMaxState'])] = 3
node[ed.field_key(identity, ['ColorModule', 'enabled'])] = False
sync(); ramp('rgb').elements[0].color = (.7, .2, .1, 1); sync()
assert ed.editor(root).value(identity, other_path) == other
assert ed.editor(root).value(identity, ['ColorModule', 'enabled']) is False

# Keep valid author data on over-capacity or unrepresentable interpolation.
valid = copy.deepcopy(value())
for i in range(7): ramp('rgb').elements.new((i + 1)/10)
try: sync(); raise AssertionError('9-key ramp accepted')
except ValueError as error: assert '8' in str(error)
assert value() == valid
while len(ramp('rgb').elements) > 8: ramp('rgb').elements.remove(ramp('rgb').elements[-2])
sync(); assert value()['m_NumColorKeys'] == 8
valid = copy.deepcopy(value())
ramp('rgb').interpolation = 'EASE'
try: sync(); raise AssertionError('Unsupported interpolation accepted')
except ValueError as error: assert 'Linear' in str(error)
assert value() == valid
ramp('rgb').interpolation = 'LINEAR'; sync()

# A source with one color key is displayed without silently expanding it.
edit = ed.editor(root)
edit.value(identity, other_path)['m_NumColorKeys'] = 1
ed.store(root, edit); ed.refresh_fields(root)
before = copy.deepcopy(edit.state); sync(); assert ed.editor(root).state == before

# Real Blender undo/redo and save/reopen retain both projections and author data.
ed.store(root, sync()); before = copy.deepcopy(ed.editor(root).state)
bpy.context.preferences.edit.use_global_undo = True
bpy.ops.ed.undo_push(message='before ramp edit')
ramp('alpha').elements[1].alpha = .6; ed.store(root, sync())
after = copy.deepcopy(ed.editor(root).state)
bpy.ops.ed.undo_push(message='after ramp edit')
assert bpy.ops.ed.undo() == {'FINISHED'}
root, node = bpy.data.objects[root_name], bpy.data.objects[node_name]
assert sync().state == before
assert bpy.ops.ed.redo() == {'FINISHED'}
root, node = bpy.data.objects[root_name], bpy.data.objects[node_name]
assert sync().state == after
ramp('alpha').elements[1].alpha = .45
bpy.ops.wm.save_as_mainfile(filepath=str(output / 'native-gradients.blend'))
expected = copy.deepcopy(sync().state)
bpy.ops.wm.open_mainfile(filepath=str(output / 'native-gradients.blend'))
root, node = bpy.data.objects[root_name], bpy.data.objects[node_name]
assert sync().state == expected

# Dragging never rebuilds field rows, nodes or elements; idle sync does no writes.
ed.preview.live_update(); samples = []
fields = node['eff_effect_fields']; tree = ramps.tree_for(node); count = len(tree.nodes)
element = ramp('alpha').elements[1]; element_id = element.as_pointer()
for i in range(12):
    element.position = .6 + i*.001
    started = time.perf_counter(); ed.preview.live_update(); samples.append(time.perf_counter()-started)
    assert value()['atime1'] == round(float(element.position) * 65535)
    assert node['eff_effect_fields'] == fields and len(tree.nodes) == count
    assert ramp('alpha').elements[1].as_pointer() == element_id
assert not root.get('预览错误'), root.get('预览错误')

# The edited native object survives the actual TypeTree encoder, not just JSON.
codec = importlib.import_module(Path(addon_path).name + '.nativepack.type_tree')
edit = sync(); schema = codec.Schema.compile(edit.state['schemas'][edit.objects[identity]['schema']]['nodes'])
for field in path: schema = next(c for c in schema.children if c.name == field)
decoded = codec.decode(schema, codec.encode(codec.from_data(schema, value()))).plain()
for field, expected_value in value().items():
    if isinstance(expected_value, dict):
        assert all(math.isclose(decoded[field][c], expected_value[c], abs_tol=1e-7) for c in 'rgba')
    else: assert decoded[field] == expected_value
ed.assets.write_document(root, edit, output / 'gradients.effedit.json')
report = dict(passed=True, rgb_alpha_independent=True, max_keys=8, undo_redo=True,
              save_reopen=True, native_type_tree_roundtrip=True,
              drag_ms_median=statistics.median(samples)*1000, drag_ms_max=max(samples)*1000)
(output / 'report.json').write_text(json.dumps(report, indent=2), 'utf-8')
print('PASS native ColorRamp authoring', json.dumps(report), flush=True)
addon.unregister()

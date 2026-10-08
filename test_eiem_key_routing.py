"""Real Blender key assignment/planning; use a separate factory-startup process."""
import importlib.util
from pathlib import Path
import sys

import bpy

path = Path(__file__).with_name('eiem_blender_addon.py')
spec = importlib.util.spec_from_file_location('eiem_key_routing_test', path)
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()

objects = []
for name in ('A', 'B'):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    mesh['eiem_section'] = 'Mesh' + name
    mesh['eiem_asset'] = name
    mesh['eiem_source'] = 'assets/' + name.lower()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    objects.append(obj)

a, b = objects
first = addon.create_switch_group('First', 'NUMPAD1', [a])
second = addon.create_switch_group('Second', 'NUMPAD1', [b])
assert addon.set_switch_group_key(first, 'CTRL+F8') == 'CTRL+F8'
assert addon.set_switch_group_key(second, 'CTRL+F8') == 'CTRL+F8'
a.shape_key_add(name='Basis')
shape = a.shape_key_add(name='ClothesShape')
shape.data[0].co.z = .1
control = addon.add_shape_control(a, shape.name, automatic=True)
assert addon.set_shape_control_hotkey(a, control, 'CTRL+F8', 'INCREASE') == 'CTRL+F8'
assert len(addon.plan_switch_export(objects)['groups']) == 2
assert addon.plan_shape_controls(objects)[2]
try:
    addon.set_switch_group_key(first, 'NOT_A_KEY')
except ValueError:
    pass
else:
    raise AssertionError('invalid key vocabulary accepted')
addon.unregister()
print('EFF_KEY_ROUTING_OK shared switch/switch/shape chords accepted; export plans preserved')

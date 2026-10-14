"""Real Blender regression for deleted keys and copied control metadata."""
import importlib
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).parent.parent))
addon = importlib.import_module('Blender.eiem_blender_addon')
addon.register()


def mesh_object(name):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    mesh['eiem_section'] = 'MeshSource'
    mesh['eiem_asset'] = name
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    obj['eiem_render_asset'] = name
    obj['eiem_render_section'] = 'RenderSource'
    obj.shape_key_add(name='Basis')
    return obj


try:
    # Reproduce the live failure: copied Meshes retain automatic controls after
    # Blender removes the entire Key datablock, rather than just one KeyBlock.
    empty = mesh_object('All keys removed')
    for name in ('Pulse', 'Up Puh'):
        empty.shape_key_add(name=name)
        addon.add_shape_control(empty, name, automatic=True)
    empty.shape_key_clear()
    assert empty.data.shape_keys is None
    declarations, bindings, hotkeys = addon.plan_shape_controls([empty])
    assert declarations == hotkeys == [] and bindings[empty] == []
    assert len(empty.data.eiem_shape_controls) == 0
    assert addon.native_export.shape_controls(addon, empty, True) == []

    # Deleting one key also removes its explicit control/hotkey, while a
    # surviving control keeps its identity, label, range and author settings.
    mixed = mesh_object('Some keys removed')
    keep = mixed.shape_key_add(name='Keep')
    survivor = addon.add_shape_control(mixed, 'Keep')
    survivor.label = 'Custom label'
    survivor.default = .4
    survivor.minimum = -.5
    survivor.maximum = 1.5
    survivor.hotkey_increase = 'F7'
    survivor.hotkey_speed = .25
    preserved = addon.native_export.shape_controls(addon, mixed, True)
    for name, automatic in (('Automatic', True), ('Manual', False)):
        key = mixed.shape_key_add(name=name)
        addon.add_shape_control(mixed, name, automatic).hotkey_increase = 'F8'
        mixed.shape_key_remove(key)
    declarations, bindings, hotkeys = addon.plan_shape_controls([mixed])
    assert len(declarations) == len(bindings[mixed]) == len(hotkeys) == 1
    assert addon.native_export.shape_controls(addon, mixed, True) == preserved
    assert 'shape.Keep=' in bindings[mixed][0] and hotkeys[0]['key'] == 'F7'

    # Independent copies may inherit the records but then lose all keys. This
    # must not mutate the source Mesh's surviving control or its Shape Keys.
    copied = mixed.copy()
    copied.data = mixed.data.copy()
    copied.name = 'Copied without keys'
    bpy.context.collection.objects.link(copied)
    copied.shape_key_clear()
    assert addon.native_export.shape_controls(addon, copied, True) == []
    assert addon.native_export.shape_controls(addon, mixed, True) == preserved
    assert mixed.data.shape_keys.key_blocks.get('Keep') == keep
    # Sync remains harmless on repeated export and disabled valid controls stay
    # disabled; deleting a key does not disable another key on the same Mesh.
    mixed.data.eiem_shape_controls[0].enabled = False
    assert addon.plan_shape_controls([mixed])[0] == []
    assert not mixed.data.eiem_shape_controls[0].enabled
    mixed.shape_key_clear()
    assert addon.plan_shape_controls([mixed])[0] == []
    assert len(mixed.data.eiem_shape_controls) == 0

    invalid = mesh_object('Invalid controls')
    invalid.shape_key_add(name='Valid')
    addon.add_shape_control(invalid, 'Valid')
    duplicate = invalid.data.eiem_shape_controls.add()
    duplicate.shape = 'Valid'
    try:
        addon.plan_shape_controls([invalid])
    except ValueError as error:
        assert '重复' in str(error)
    else:
        raise AssertionError('Duplicate control accepted')
    invalid.data.eiem_shape_controls.remove(1)
    invalid.data.eiem_shape_controls[0].shape = 'Basis'
    try:
        addon.plan_shape_controls([invalid])
    except ValueError as error:
        assert 'Basis' in str(error)
    else:
        raise AssertionError('Basis control accepted')
    print('PASS deleted Shape Keys: all/one, automatic/manual, copied metadata, '
          'valid settings retained, duplicate/Basis validation retained')
finally:
    addon.unregister()

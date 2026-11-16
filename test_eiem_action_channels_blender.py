"""Action edits are authoritative for UI and mixed animation export."""
import importlib
from pathlib import Path
import sys

import bpy

sys.path.insert(0, str(Path(__file__).parent.parent))
addon = importlib.import_module('Blender.eiem_blender_addon')
addon.register()
model = addon.shape_animation
try:
    mesh = bpy.data.meshes.new('Channels')
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    obj = bpy.data.objects.new('Channels', mesh)
    bpy.context.collection.objects.link(obj)
    mesh['eiem_section'] = 'MeshChannels'
    obj.shape_key_add(name='Basis')
    first = obj.shape_key_add(name='First')
    action = bpy.data.actions.new('OneAction')
    materials = []
    for index in range(2):
        material = bpy.data.materials.new('Ring' + str(index))
        mesh.materials.append(material)
        material['eiem_source'] = 'assets/fixture.mat'
        material['eiem_float._MainTexUVRotate'] = '0'
        material['eiem_value4._MainTexUVRotateMat'] = '1,0,0,1'
        row = material.eiem_editor_values.add()
        row.name = row.key = 'eiem_float._MainTexUVRotate'
        animation = material.animation_data_create()
        animation.action = action
        animation.action_slot = action.slots.new(id_type='MATERIAL', name=material.name)
        for frame, value in ((0, 0), (100, 360)):
            row.scalar = value
            row.keyframe_insert('scalar', frame=frame)
        for curve in addon.material_animation.action_curves(material):
            for point in curve.keyframe_points:
                point.interpolation = 'LINEAR'
        materials.append(material)
    keys = mesh.shape_keys
    animation = keys.animation_data_create()
    animation.action = action
    animation.action_slot = action.slots.new(id_type='KEY', name=keys.name)
    for frame, value in ((0, 0), (100, 1)):
        first.value = value
        first.keyframe_insert('value', frame=frame)
    clip = model.create_action([obj])
    clip.autoplay = True
    clip.sample_rate = 30
    bpy.context.scene.render.fps = 30
    assert len(model.action_channels(clip.identity)) == 3

    # Delete a curve in Blender, leaving the old EFF metadata untouched.
    material = materials[0]
    bag = action.layers[0].strips[0].channelbag(material.animation_data.action_slot)
    bag.fcurves.remove(bag.fcurves[0])
    assert addon.material_animation.export_clips(material, 'Material0', addon) == []
    assert len(model.action_channels(clip.identity)) == 2

    # Add a different animated property, without an EFF "add channel" step.
    material['eiem_float._EmissionBrightness'] = '4'
    row = material.eiem_editor_values.add()
    row.name = row.key = 'eiem_float._EmissionBrightness'
    for frame, value in ((0, 1), (100, 4)):
        row.scalar = value
        row.keyframe_insert('scalar', frame=frame)
    rows = addon.material_animation.export_clips(material, 'Material0', addon)
    assert len(rows) == 1 and rows[0]['id'] == clip.identity
    assert rows[0]['targets'][0]['property'] == '_EmissionBrightness'
    assert rows[0]['targets'][0]['samples'][-1] == 4
    assert len(model.action_channels(clip.identity)) == 3

    second = obj.shape_key_add(name='Second')
    for frame, value in ((0, 1), (100, 0)):
        second.value = value
        second.keyframe_insert('value', frame=frame)
    shape_bag = action.layers[0].strips[0].channelbag(keys.animation_data.action_slot)
    first_curve = next(c for c in shape_bag.fcurves if c.data_path == first.path_from_id('value'))
    shape_bag.fcurves.remove(first_curve)
    shapes = model.export_clips(obj, addon)
    assert list(shapes[0]['channels']) == ['Second']
    assert len(model.action_channels(clip.identity)) == 3

    # Each material owns a slot in one Action and merges into one runtime clip.
    tracks = [row for i, m in enumerate(materials)
              for row in addon.material_animation.export_clips(m, 'Material' + str(i), addon)]
    assert len(tracks) == 2 and {r['id'] for r in tracks} == {clip.identity}
    assert len(tracks[1]['targets']) == 4
    assert all(len(t['samples']) == 101 for r in tracks for t in r['targets'])
    assert len({m.animation_data.action for m in materials}) == 1

    rig = bpy.data.objects.new('RigChannels', bpy.data.armatures.new('RigChannels'))
    bpy.context.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    root = rig.data.edit_bones.new('Root')
    root.head, root.tail = (0, 0, 0), (0, 0, 1)
    tip = rig.data.edit_bones.new('Tip')
    tip.head, tip.tail, tip.parent = (0, 0, 1), (0, 0, 2), root
    bpy.ops.object.mode_set(mode='OBJECT')
    animation = rig.animation_data_create()
    animation.action = action
    animation.action_slot = action.slots.new(id_type='OBJECT', name=rig.name)
    for frame, value in ((0, 0), (100, 1)):
        rig.pose.bones['Root'].location.x = value
        rig.pose.bones['Root'].keyframe_insert('location', frame=frame)
    model.add_action_channels(clip, [rig])
    bag = action.layers[0].strips[0].channelbag(animation.action_slot)
    for curve in list(bag.fcurves):
        bag.fcurves.remove(curve)
    assert model.export_bone_clips(rig, addon, 'SkeletonChannels') == []
    for frame, value in ((0, 0), (100, 1)):
        rig.pose.bones['Tip'].location.x = value
        rig.pose.bones['Tip'].keyframe_insert('location', frame=frame)
    bones = model.export_bone_clips(rig, addon, 'SkeletonChannels')
    decoded = addon.bone_animation.decode_animation(bones[0]['data'])
    assert [track['path'] for track in decoded['tracks']] == ['Root/Tip']
    assert {row['name'] for row in model.action_channels(clip.identity)
            if row['kind'] == 'BONE'} == {'Tip'}
    # The per-action export switch remains authoritative for all contributions.
    clip.enabled = False
    assert model.export_clips(obj, addon) == []
    assert model.export_bone_clips(rig, addon, 'SkeletonChannels') == []
    assert all(addon.material_animation.export_clips(m, 'Material', addon) == [] for m in materials)
    print('PASS Action channel edits: deletion, addition, mixed slots, shared identity, export switch')
finally:
    addon.unregister()

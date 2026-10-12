"""Real Blender round trip of an independently decoded source facial library.

blender --background --factory-startup --python-exit-code 1 --python THIS -- ADDON EFF LIBRARY
"""
import json
import math
import sys
from pathlib import Path

import bpy

addon_dir, source_dir, library_path = sys.argv[sys.argv.index('--') + 1:]
sys.path.insert(0, addon_dir)
import eiem_blender_addon as addon
import eiem_animation as animation

addon.register()
payload = addon.read_skeleton(next((Path(source_dir) / 'skeletons').glob('*.skeleton')))
collection = bpy.data.collections.new('Animation test')
bpy.context.scene.collection.children.link(collection)
rig = addon.make_armature('Animation source', payload, collection)
bpy.context.view_layer.objects.active = rig
rig.select_set(True)
original = bpy.data.actions.new('Original action to retain')
original.use_fake_user = True
rig.animation_data_create().action = original
initial_bones = set(rig.data.bones.keys())
frame = bpy.context.scene.frame_current
result = animation.import_animations(library_path, rig, addon.unity_transform_matrix_to_blender_basis())
library = animation.read_library(library_path)
assert result['created'] == len(library['clips'])
assert rig.animation_data.action == original and bpy.context.scene.frame_current == frame
assert set(result['helpers']) == {'jawJoint', 'TongueMd04Joint'}
assert set(rig.data.bones.keys()) == initial_bones | set(result['helpers'])
assert all(not rig.data.bones[n].use_deform for n in result['helpers'])
again = animation.import_animations(library_path, rig, addon.unity_transform_matrix_to_blender_basis())
assert again['created'] == 0 and again['reused'] == len(library['clips'])
assert not again['helpers']
blink = next(c for c in library['clips'] if c['name'] == 'data_facialmorph_anim_blink_02')
action = next(a for a in bpy.data.actions if a.get('eiem_animation_source') == blink['name'])
rig.animation_data.action = action
if hasattr(action, 'slots'):
    rig.animation_data.action_slot = action.slots[0]
sample_index = blink['times'].index(0.25)
fps = bpy.context.scene.render.fps / bpy.context.scene.render.fps_base
target_frame = 1 + blink['times'][sample_index] * fps
bpy.context.scene.frame_set(math.floor(target_frame), subframe=target_frame % 1)
bpy.context.view_layer.update()
basis = addon.unity_transform_matrix_to_blender_basis()
inverse = basis.inverted()
worlds = {}
tracks = {t['path']: t for t in blink['tracks']}
paths = {str(b.get('eiem_path')): b for b in rig.data.bones}
maximum_error = 0.0
for node in library['skeleton']:
    track = tracks.get(node['path'])
    value = (track['samples'][0 if 'times' in track else sample_index] if track else
             node['position'] + node['rotation'] + node['scale'])
    if not track and node['path'] in paths:
        value = [v for field in ('position', 'rotation', 'scale')
                 for v in paths[node['path']]['eiem_local_' + field]]
    local = animation._matrix(value)
    worlds[node['path']] = worlds[node['parent']] @ local if node['parent'] is not None else local
    if track:
        actual = rig.pose.bones[paths[node['path']].name].matrix
        expected = basis @ worlds[node['path']] @ inverse
        maximum_error = max(maximum_error, max(abs(actual[r][c]-expected[r][c]) for r in range(4) for c in range(4)))
assert maximum_error < 0.0001, maximum_error
# A different/edited rest pose must fail before adding any actions or helper bones.
bone_name = next(b.name for b in rig.data.bones if b.get('eiem_path') in tracks)
bpy.ops.object.mode_set(mode='EDIT')
rig.data.edit_bones[bone_name].head.x += 0.01
bpy.ops.object.mode_set(mode='OBJECT')
counts = (len(bpy.data.actions), len(rig.data.bones))
try:
    animation.import_animations(library_path, rig, basis)
except ValueError as error:
    assert '参考姿态' in str(error)
else:
    raise AssertionError('Edited reference pose accepted')
assert counts == (len(bpy.data.actions), len(rig.data.bones))
print(json.dumps({'actions': result['created'], 'helpers': result['helpers'],
                  'blink_world_matrix_error': maximum_error, 'duplicate_import_reused': again['reused'],
                  'edited_rest_rejected': True}))
addon.unregister()

"""Factory-scene test; never reads or saves the user's Blender file."""
import importlib,sys
from pathlib import Path
import bpy
sys.path.insert(0,str(Path(__file__).parent.parent))
addon=importlib.import_module('Blender.eiem_blender_addon');addon.register()
obj=bpy.data.objects.new('Shape animation fixture',bpy.data.meshes.new('Shape fixture'));bpy.context.collection.objects.link(obj)
obj.data.from_pydata([(0,0,0),(1,0,0),(0,1,0)],[],[(0,1,2)]);obj.data['eiem_section']='MeshTest'
bpy.context.view_layer.objects.active=obj;obj.select_set(True)
obj.shape_key_add(name='Basis');a=obj.shape_key_add(name='展翅');b=obj.shape_key_add(name='收拢')
for frame,value in [(1,0),(16,1),(31,0)]:a.value=value;a.keyframe_insert('value',frame=frame);b.value=1-value;b.keyframe_insert('value',frame=frame)
bpy.context.scene.render.fps=30
clip=addon.shape_animation.add_current(obj)
assert len(clip.channels)==2 and clip.start==1 and clip.end==31
clip.channels[1].enabled=False
manual=b.value;old_action=obj.data.shape_keys.animation_data.action;slot=obj.data.shape_keys.animation_data.action_slot
result=addon.shape_animation.export_clips(obj,addon)
assert result[0]['duration']==1 and list(result[0]['channels'])==['展翅']
assert abs(result[0]['channels']['展翅'][30]-1)<1e-6
assert b.value==manual and obj.data.shape_keys.animation_data.action==old_action and obj.data.shape_keys.animation_data.action_slot==slot
other=bpy.data.actions.new('Object action');other.slots.new(id_type='OBJECT',name='Object')
clip.action=other
try:addon.shape_animation.export_clips(obj,addon);raise AssertionError('Wrong Action accepted')
except ValueError:pass
clip.action=old_action
view=bpy.context.screen
before=(bpy.context.scene.frame_start,bpy.context.scene.frame_end,bpy.context.scene.frame_current,tuple(k.value for k in obj.data.shape_keys.key_blocks))
play_result=bpy.ops.eiem.animation_preview()
assert play_result=={'FINISHED'}
preview=obj.data.shape_keys.animation_data.action
assert preview!=old_action and len(addon.shape_animation.curves(preview,obj.data.shape_keys.animation_data.action_slot.handle))==1
assert bpy.ops.eiem.animation_preview(stop=True)=={'FINISHED'}
assert obj.data.shape_keys.animation_data.action==old_action and obj.data.shape_keys.animation_data.action_slot==slot
assert before==(bpy.context.scene.frame_start,bpy.context.scene.frame_end,bpy.context.scene.frame_current,tuple(k.value for k in obj.data.shape_keys.key_blocks))
print('PASS real Blender shape animation: Shape Keys Action/slot, two curves, checked channel only, scene FPS/duration, samples, original Action/values retained, Object Action rejected')
addon.unregister()

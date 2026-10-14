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

module=addon.shape_animation
module.remove_group(clip.identity)
def animated_mesh(name,with_action=True):
    result=bpy.data.objects.new(name,bpy.data.meshes.new(name+' Mesh'));bpy.context.collection.objects.link(result)
    result.data.from_pydata([(0,0,0),(1,0,0),(0,1,0)],[],[(0,1,2)]);result.data['eiem_section']='Mesh'+name
    result.shape_key_add(name='Basis');key=result.shape_key_add(name='展翅')
    if with_action:
        for frame,value in [(1,1),(16,0),(31,1)]:key.value=value;key.keyframe_insert('value',frame=frame)
    return result
second=animated_mesh('Second')
# One Blender 5 Action has separate KEY slots for the two meshes, as in the
# user's scene. A group must read each member's own curves.
second_keys=second.data.shape_keys;second_keys.animation_data.action=old_action
second_slot=old_action.slots.new(id_type='KEY',name=second_keys.name)
second_keys.animation_data.action_slot=second_slot
bag=old_action.layers[0].strips[0].channelbag(second_slot,ensure=True)
fc=bag.fcurves.new(data_path=second_keys.key_blocks['展翅'].path_from_id('value'),index=0)
for frame,value in [(1,1),(16,0),(31,1)]:fc.keyframe_points.insert(frame,value)
invalid=animated_mesh('NoAction',False)
try:module.create_group([obj,invalid]);raise AssertionError('Unanimated member accepted')
except ValueError:pass
assert not obj.data.eiem_shape_animations and not invalid.data.eiem_shape_animations
for member in bpy.context.selected_objects:member.select_set(False)
obj.select_set(True);second.select_set(True);bpy.context.view_layer.objects.active=obj
assert bpy.ops.eiem.shape_animation()=={'FINISHED'}
clip=obj.data.eiem_shape_animations[0];clip.channels[1].enabled=False
identity=clip.identity;other=second.data.eiem_shape_animations[0]
assert identity==other.identity and other.slot_handle==second_slot.handle
assert len(module.group_members(identity))==2
for field,value in [('label','同步衣装'),('hotkey','F8'),('start',1),('end',31),('speed',2),('sample_rate',30),('loop',False),('autoplay',True),('enabled',False)]:
    setattr(clip,field,value);assert getattr(other,field)==value
clip.enabled=True
assert other.action==old_action and other.slot_handle!=clip.slot_handle and len(other.channels)==1
left=module.export_clips(obj,addon)[0];right=module.export_clips(second,addon)[0]
assert left['id']==right['id'] and left['duration']==right['duration'] and left['channels']['展翅'][0]!=right['channels']['展翅'][0]
try:module.validate_export_members([obj]);raise AssertionError('Incomplete multi-mesh export accepted')
except ValueError:pass
module.validate_export_members([obj,second])
alias=bpy.data.objects.new('Second alias',second.data);bpy.context.collection.objects.link(alias)
assert len(module.group_members(identity))==2
third=animated_mesh('Third');third.select_set(True)
assert bpy.ops.eiem.animation_members(operation='ADD')=={'FINISHED'}
assert len(module.group_members(identity))==3 and len(second.data.eiem_shape_animations)==1
third_clip=third.data.eiem_shape_animations[0];assert third_clip.identity==identity and third_clip.speed==2
assert bpy.ops.eiem.animation_members(operation='REMOVE',target_name=third.name)=={'FINISHED'}
assert not third.data.eiem_shape_animations and len(module.group_members(identity))==2
assert bpy.ops.eiem.animation_members(operation='SELECT')=={'FINISHED'}
assert obj.select_get() and second.select_get() and alias.select_get() and not invalid.select_get()
assert bpy.ops.eiem.animation_members(operation='EDIT',target_name=second.name)=={'FINISHED'}
assert bpy.context.object==second and second.data.eiem_shape_animations[second.data.eiem_shape_animation_index].identity==identity
bpy.context.view_layer.objects.active=obj
group_members=module.group_members(identity)
before_scene=(bpy.context.scene.frame_start,bpy.context.scene.frame_end,bpy.context.scene.frame_current)
before_members=[(o.data.shape_keys,o.data.shape_keys.animation_data.action,o.data.shape_keys.animation_data.action_slot,tuple(k.value for k in o.data.shape_keys.key_blocks)) for o,c in group_members]
assert bpy.ops.eiem.animation_preview()=={'FINISHED'}
assert obj.data.shape_keys.animation_data.action==second_keys.animation_data.action!=old_action
assert obj.data.shape_keys.animation_data.action_slot!=second_keys.animation_data.action_slot
bpy.context.scene.frame_set(1)
assert abs(obj.data.shape_keys.key_blocks['展翅'].value)<1e-6 and abs(second_keys.key_blocks['展翅'].value-1)<1e-6
assert bpy.ops.eiem.animation_preview(stop=True)=={'FINISHED'}
assert before_scene==(bpy.context.scene.frame_start,bpy.context.scene.frame_end,bpy.context.scene.frame_current)
for keys,action,slot,values in before_members:assert keys.animation_data.action==action and keys.animation_data.action_slot==slot and tuple(k.value for k in keys.key_blocks)==values
other.channels[0].name='missing'
try:module.start_preview(bpy.context,clip);raise AssertionError('Invalid member preview accepted')
except ValueError:pass
assert not module.EFF_OT_animation_preview._saved and obj.data.shape_keys.animation_data.action==old_action
other.channels[0].name='展翅'
first_id=identity
second_group=module.create_group([obj,second]);second_id=second_group.identity
assert first_id!=second_id and len(obj.data.eiem_shape_animations)==2 and len(second.data.eiem_shape_animations)==2
module.set_active_group(bpy.context,second_id)
assert module.active_clip(bpy.context).identity==second_id
assert len(module.export_clips(obj,addon))==2
obj.select_set(False);second.select_set(False);alias.select_set(False);bpy.context.view_layer.objects.active=None
assert module.EFF_PT_shape_animation.poll(bpy.context)
assert {c.identity for c in module.all_groups()}=={first_id,second_id}
assert module.active_clip(bpy.context).identity==second_id
class Layout:
    def __init__(self):self.labels=[];self.props=[];self.children=[];self.operators=[]
    def label(self,**kw):self.labels.append(kw.get('text',''))
    def prop(self,data,name,**kw):self.props.append((data,name,kw))
    def operator(self,id,**kw):
        from types import SimpleNamespace
        result=SimpleNamespace();self.operators.append((id,kw,result));return result
    def row(self,**kw):return self
    def box(self,**kw):return self
    def column(self,**kw):return self
    def separator(self):pass
layout=Layout()
from types import SimpleNamespace
module.EFF_PT_shape_animation.draw(SimpleNamespace(layout=layout),bpy.context)
assert len([o for o in layout.operators if o[0]=='eiem.animation_group_select'])==2
assert len([p for p in layout.props if p[1]=='action'])==2
assert bpy.ops.eiem.animation_group_select(identity=first_id)=={'FINISHED'}
assert module.active_clip(bpy.context).identity==first_id
assert bpy.ops.eiem.shape_animation(remove=True)=={'FINISHED'}
assert module.active_clip(bpy.context).identity==second_id
module.remove_group(second_id)
bpy.context.view_layer.objects.active=obj;obj.select_set(True)
assert not obj.data.eiem_shape_animations and not second.data.eiem_shape_animations
module.create_group([obj,second]);assert bpy.ops.eiem.shape_animation(remove=True)=={'FINISHED'}
assert not obj.data.eiem_shape_animations and not second.data.eiem_shape_animations
print('PASS multi-mesh group: atomic creation, separate KEY slots, shared playback settings, membership, complete-export validation, synchronized preview and restoration')
print('PASS unified animation manager without active mesh, per-member inline Action/channel editing and multiple groups sharing one mesh/channel')
addon.unregister()

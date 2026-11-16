"""A native Action with KEY, OBJECT and MATERIAL slots uses one EFF action."""
import importlib
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

import bpy

sys.path.insert(0,str(Path(__file__).parent.parent))
addon=importlib.import_module('Blender.eiem_blender_addon');addon.register()
model=addon.shape_animation
try:
    mesh=bpy.data.meshes.new('ActionFixture')
    mesh.from_pydata([(0,0,0),(1,0,0),(0,1,0)],[],[(0,1,2)])
    obj=bpy.data.objects.new('ActionFixture',mesh);bpy.context.collection.objects.link(obj)
    obj.data['eiem_section']='MeshFixture'
    obj.shape_key_add(name='Basis');key=obj.shape_key_add(name='Pulse')
    rig=bpy.data.objects.new('ActionRig',bpy.data.armatures.new('ActionRig'))
    bpy.context.collection.objects.link(rig);bpy.context.view_layer.objects.active=rig
    rig.select_set(True);bpy.ops.object.mode_set(mode='EDIT')
    bone=rig.data.edit_bones.new('Root');bone.head=(0,0,0);bone.tail=(0,0,1)
    bpy.ops.object.mode_set(mode='OBJECT')
    material=bpy.data.materials.new('ActionMaterial');mesh.materials.append(material)
    material['eiem_source']='assets/fixture.mat'
    material['eiem_float._EmissionBrightness']='1'
    value=material.eiem_editor_values.add();value.name=value.key='eiem_float._EmissionBrightness'
    action=bpy.data.actions.new('MixedAction')
    targets=[(obj.data.shape_keys,'KEY'),(rig,'OBJECT'),(material,'MATERIAL')]
    for target,id_type in targets:
        target.animation_data_create();target.animation_data.action=action
        target.animation_data.action_slot=action.slots.new(id_type=id_type,name=target.name)
    for frame,number in ((1,0.0),(16,1.0),(31,0.0)):
        key.value=number;key.keyframe_insert('value',frame=frame)
        rig.pose.bones['Root'].location.x=number
        rig.pose.bones['Root'].keyframe_insert('location',frame=frame)
        value.scalar=number;value.keyframe_insert('scalar',frame=frame)
    for other in bpy.context.selected_objects:other.select_set(False)
    for member in (obj,rig):member.select_set(True)
    bpy.context.view_layer.objects.active=obj;bpy.context.scene.render.fps=30
    assert bpy.ops.eiem.animation(operation='CREATE')=={'FINISHED'}
    clip=model.active_clip(bpy.context);identity=clip.identity
    assert clip.label=='MixedAction' and (clip.start,clip.end)==(1,31)
    assert {row['kind'] for row in model.action_channels(identity)}=={'SHAPE','BONE','MATERIAL'}
    assert {row['action'] for row in model.action_channels(identity)}=={action}
    with patch.object(addon.material_animation,'import_action',side_effect=ValueError('injected import failure')):
        try:model.create_action([obj,rig]);raise AssertionError('Partial mixed action committed')
        except ValueError as error:assert str(error)=='injected import failure'
    assert len(model.all_groups())==1 and len(model.action_channels(identity))==3
    clip.hotkey='F6';clip.sample_rate=30;clip.autoplay=True
    for row in model.action_channels(identity):
        assert row['clip'].hotkey=='F6' and row['clip'].autoplay and row['clip'].sample_rate==30
    before=len(model.action_channels(identity))
    assert bpy.ops.eiem.animation(operation='ADD')=={'FINISHED'}
    assert len(model.action_channels(identity))==before

    shapes=model.export_clips(obj,addon)
    bones=model.export_bone_clips(rig,addon,'FixtureSkeleton')
    materials=addon.material_animation.export_clips(material,'MaterialFixture',addon)
    assert len(shapes)==len(bones)==len(materials)==1
    assert {row['id'] for row in shapes+bones+materials}=={identity}
    assert {row['key'] for row in shapes+bones+materials}=={'F6'}
    assert {row['duration'] for row in shapes+bones+materials}=={1.0}
    from nativepack import author_format3
    part=dict(partId='Fixture',object=obj.name,visibilityBindings=[],shapeAnimations=shapes,
        shapeControls=[dict(enabled=True,identity='Pulse',shape='Pulse',variable='$pulse',
            label='Pulse',default=0,minimum=0,maximum=1,hotkey_increase='',hotkey_decrease='',hotkey_speed=1)])
    state=author_format3._controls_resource('MixedActionMod',[part],[],materials,[],
        bone_animations=bones,return_state=True)
    assert list(state['animations'])==[identity]
    runtime=state['animations'][identity]
    assert len(runtime['tracks'])==2 and len(runtime['boneTracks'])==1
    assert {len(samples) for samples in runtime['tracks'].values()}=={31}

    class Layout:
        def __init__(self):self.properties=[];self.operators=[];self.labels=[]
        def row(self,**kwargs):return self
        def box(self):return self
        def label(self,**kwargs):self.labels.append(kwargs.get('text',''))
        def separator(self):pass
        def operator(self,name,**kwargs):
            value=SimpleNamespace();self.operators.append((name,value));return value
        def prop(self,data,name,**kwargs):
            getattr(data,name);assert name in data.bl_rna.properties
            self.properties.append(name)
    layout=Layout();model.EFF_PT_shape_animation.draw(SimpleNamespace(layout=layout),bpy.context)
    assert layout.properties.count('hotkey')==1
    assert all(name=='eiem.animation' or name in {'eiem.animation_group_select','eiem.animation_key'}
               for name,_ in layout.operators)
    assert not hasattr(bpy.types,'EFF_PT_material_animation')
    assert not hasattr(bpy.types,'EFF_OT_material_animation')
    assert not hasattr(bpy.types,'EFF_OT_animation_material_import')

    # A later import validation failure must not partially add shape channels.
    unbound=bpy.data.objects.new('InvalidShape',bpy.data.meshes.new('InvalidShape'))
    bpy.context.collection.objects.link(unbound)
    unbound.data.from_pydata([(0,0,0),(1,0,0),(0,1,0)],[],[(0,1,2)])
    unbound.shape_key_add(name='Basis');bad=unbound.shape_key_add(name='Bad')
    bad.keyframe_insert('value',frame=1);bad.value=1;bad.keyframe_insert('value',frame=31)
    try:model.add_action_channels(clip,[obj,unbound]);raise AssertionError('Invalid shape binding accepted')
    except ValueError:pass
    assert len(model.action_channels(identity))==before

    # Removing an editor track keeps its source keys and native Action slots.
    assert bpy.ops.eiem.animation(operation='REMOVE_CHANNEL',kind='MATERIAL',
        owner_name=material.name,channel_name='_EmissionBrightness')=={'FINISHED'}
    assert not material.eiem_material_animations
    assert len(addon.material_animation.action_curves(material))==1
    assert bpy.ops.eiem.animation(operation='ADD')=={'FINISHED'}
    assert len(model.action_channels(identity))==before
    assert bpy.ops.eiem.animation(operation='REMOVE')=={'FINISHED'}
    assert not model.all_groups() and action in bpy.data.actions.values()
    assert all(target.animation_data.action==action for target,_ in targets)

    # Importing a listed Action binds every supported slot in a single step.
    assert bpy.ops.eiem.animation(operation='IMPORT',action_name=action.name)=={'FINISHED'}
    assert {row['kind'] for row in model.action_channels(model.active_clip(bpy.context).identity)}=={'SHAPE','BONE','MATERIAL'}
    extra=obj.shape_key_add(name='Extra')
    extra.keyframe_insert('value',frame=1);extra.value=1;extra.keyframe_insert('value',frame=31)
    assert bpy.ops.eiem.animation(operation='ADD')=={'FINISHED'}
    channels=model.action_channels(model.active_clip(bpy.context).identity)
    assert [row['name'] for row in channels if row['kind']=='SHAPE']==['Pulse','Extra']
    print('PASS unified Action UI: one native mixed Action, one F6 clip, all channel types, atomic add, removal and reimport')
finally:addon.unregister()

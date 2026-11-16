"""Factory scene only; verifies real Material Action slots and offline samples."""
import importlib,sys
from pathlib import Path
from types import SimpleNamespace
import bpy
sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0,str(Path(__file__).parent.parent))
addon=importlib.import_module('Blender.eiem_blender_addon');addon.register()
try:
    mesh=bpy.data.meshes.new('fixture');mesh.from_pydata([(0,0,0),(1,0,0),(0,1,0)],[],[(0,1,2)])
    obj=bpy.data.objects.new('fixture',mesh);bpy.context.collection.objects.link(obj);bpy.context.view_layer.objects.active=obj;obj.select_set(True)
    m=bpy.data.materials.new('fixture');obj.data.materials.append(m);m['eiem_source']='assets/test.mat';m['eiem_float._EmissionBrightness']='4'
    clip=m.eiem_material_animations.add();clip.identity=clip.runtime_id='Emission'
    clip.label='Emission';clip.property='_EmissionBrightness';clip.kind='FLOAT';clip.start=1;clip.end=31
    for frame,value in [(1,0),(16,1),(31,0)]:clip.value=value;clip.keyframe_insert('value',frame=frame)
    bpy.context.scene.render.fps=30
    rows=addon.material_animation.export_clips(m,'MaterialTest',addon)
    assert len(rows)==1 and rows[0]['duration']==1 and rows[0]['targets'][0]['samples'][0]==0
    assert abs(rows[0]['targets'][0]['samples'][30]-1)<1e-5
    clip.loop=False;clip.speed=2;rows=addon.material_animation.export_clips(m,'MaterialTest',addon);assert rows[0]['speed']==2 and not rows[0]['loop']
    # Import the real editor Action shape used by the UV-angle controls. The
    # scalar angle must become the four-component Shader rotation matrix track.
    rotation=bpy.data.materials.new('UV rotation fixture');rotation['eiem_source']='assets/vfx.mat'
    rotation['eiem_float._MainTexUVRotate']='0';rotation['eiem_value4._MainTexUVRotateMat']='1,0,0,1'
    row=rotation.eiem_editor_values.add();row.name=row.key='eiem_float._MainTexUVRotate'
    for frame,value in [(0,0),(100,360)]:row.scalar=value;row.keyframe_insert(data_path='scalar',frame=frame)
    imported_count=addon.material_animation.import_action(rotation,scene=bpy.context.scene)
    assert imported_count==1
    rotation_group=rotation.eiem_material_animations[-1].identity
    assert rotation_group=='UVRotate_MainTex'
    rotation_clip=rotation.eiem_material_animations[-1];rotation_clip.start=0;rotation_clip.end=100;rotation_clip.sample_rate=30
    assert rotation_clip.identity==rotation_group and rotation_clip.runtime_id==rotation_group
    assert addon.shape_animation.material_group_members(rotation_group)==[(rotation,rotation_clip)]
    assert rotation_group in {clip.identity for clip in addon.shape_animation.all_groups()}
    native=addon.material_animation.export_clips(rotation,'MaterialRotation',addon)
    assert len(native)==1 and native[0]['id']==rotation_group and native[0]['duration']==100/30
    assert len(native[0]['targets'])==4
    by_component={track['component']:track for track in native[0]['targets']}
    assert all(abs(by_component[0]['samples'][index]-expected)<1e-5
               for index,expected in ((0,1.0),(50,-1.0),(100,1.0)))
    assert abs(by_component[1]['samples'][50])<1e-6 and abs(by_component[2]['samples'][50])<1e-6
    assert by_component[0]['property']=='_MainTexUVRotateMat' and by_component[0]['kind']=='VECTOR'
    variable=by_component[0]['variable']
    assert variable=='$mat_'+rotation_group+'_MaterialRotation_MainTexUVRotateMat_0'
    assert variable.isascii() and '____' not in variable
    try:addon.shape_animation.start_preview(bpy.context,rotation_clip);raise AssertionError('Material-only clip accepted by shape/bone preview')
    except ValueError as error:assert '没有形态键' in str(error)
    addon.shape_animation.remove_group(rotation_group)
    assert not rotation.eiem_material_animations
    auto=bpy.data.materials.new('Automatic material Action fixture');auto['eiem_source']='assets/vfx.mat'
    auto['eiem_float._MainTexUVRotate']='0';auto['eiem_value4._MainTexUVRotateMat']='1,0,0,1'
    obj.data.materials.append(auto);obj.active_material_index=len(obj.data.materials)-1
    row=auto.eiem_editor_values.add();row.name=row.key='eiem_float._MainTexUVRotate'
    for frame,value in [(0,0),(100,360)]:row.scalar=value;row.keyframe_insert(data_path='scalar',frame=frame)
    assert addon.material_animation.register_action_tracks([auto])==1
    auto_clip=auto.eiem_material_animations[0]
    assert (auto_clip.start,auto_clip.end)==(0,100)
    assert auto_clip.identity in {clip.identity for clip in addon.shape_animation.all_groups()}
    assert len(addon.material_animation.export_clips(auto,'MaterialAutomatic',addon)[0]['targets'])==4
    bpy.context.scene.eiem_animation_group=auto_clip.identity;auto_clip.start=20;auto_clip.end=60
    assert bpy.ops.eiem.shape_animation(use_range=True)=={'FINISHED'}
    assert (auto_clip.start,auto_clip.end)==(0,100)
    # Exercise the unified panel with a material-only group as its active clip.
    # The layout reads real RNA properties so a missing shared UI field fails
    # even when the animation exporter itself accepts the material tracks.
    class PanelLayout:
        def __init__(self):self.properties=[]
        def row(self,**kwargs):return self
        def box(self):return self
        def label(self,**kwargs):pass
        def separator(self):pass
        def operator(self,*args,**kwargs):return SimpleNamespace()
        def prop(self,data,name,**kwargs):
            getattr(data,name)
            assert name in data.bl_rna.properties,(data,name)
            self.properties.append(name)
    layout=PanelLayout()
    panel=SimpleNamespace(layout=layout)
    addon.shape_animation.EFF_PT_shape_animation.draw(panel,bpy.context)
    assert {'speed','sample_rate'}<=set(layout.properties)
    assert 'advanced' not in layout.properties
    assert not hasattr(bpy.types,'EFF_PT_material_animation')
    # Selecting an unrelated action in the UI must never change export grouping.
    separate=bpy.data.materials.new('Separate material Action');separate['eiem_source']='assets/vfx.mat'
    separate['eiem_float._EmissionBrightness']='1'
    row=separate.eiem_editor_values.add();row.name=row.key='eiem_float._EmissionBrightness'
    for frame,value in ((1,0),(31,1)):row.scalar=value;row.keyframe_insert('scalar',frame=frame)
    bpy.context.scene.eiem_animation_group=auto_clip.identity
    assert addon.material_animation.register_action_tracks([separate])==1
    assert separate.eiem_material_animations[0].identity!=auto_clip.identity
    # Existing collection-backed samples still survive removing an earlier track.
    survivor=m.eiem_material_animations.add();survivor.identity=survivor.runtime_id='Survivor'
    survivor.label='Survivor';survivor.property='_EmissionBrightness';survivor.kind='FLOAT';survivor.start=1;survivor.end=31
    for frame,value in ((1,0),(31,1)):survivor.value=value;survivor.keyframe_insert('value',frame=frame)
    addon.material_animation.remove_track(m,'Emission')
    remaining=addon.material_animation.export_clips(m,'MaterialTest',addon)
    assert len(remaining)==1 and remaining[0]['id']=='Survivor'
    assert remaining[0]['targets'][0]['samples'][0]==0 and remaining[0]['targets'][0]['samples'][-1]==1
    # One Material owns independent source Actions. Editor activation cannot
    # change the settings or samples of an already registered contribution.
    multi=bpy.data.materials.new('Multiple Actions');multi['eiem_source']='assets/vfx.mat'
    multi['eiem_float._EmissionBrightness']='1'
    row=multi.eiem_editor_values.add();row.name=row.key='eiem_float._EmissionBrightness'
    sources=[]
    for name,values in (('ActionA',(1,2)),('ActionB',(8,10))):
        action=bpy.data.actions.new(name);animation=multi.animation_data_create();animation.action=action
        animation.action_slot=action.slots.new(id_type='MATERIAL',name=multi.name)
        for frame,value in zip((0,100),values):row.scalar=value;row.keyframe_insert('scalar',frame=frame)
        assert addon.material_animation.register_action_tracks([multi])==1
        sources.append((action,animation.action_slot,multi.eiem_material_animations[-1]))
    for current in (sources[0],sources[1]):
        multi.animation_data.action=current[0];multi.animation_data.action_slot=current[1]
        assert addon.material_animation.pending_action_tracks(multi)==[]
        exported=addon.material_animation.export_clips(multi,'Multi',addon)
        actual={r['label']:(r['targets'][0]['samples'][0],r['targets'][0]['samples'][-1]) for r in exported}
        assert actual=={'ActionA':(1,2),'ActionB':(8,10)},actual
        assert addon.shape_animation.source_actions(sources[0][2].identity)=={sources[0][0]}
    multi.animation_data.action=None
    assert len(addon.material_animation.export_clips(multi,'Multi',addon))==2
    action,slot,clip=sources[0]
    for layer in action.layers:
        for strip in layer.strips:
            bag=strip.channelbag(slot)
            if bag:
                for curve in list(bag.fcurves):bag.fcurves.remove(curve)
    assert [r['label'] for r in addon.material_animation.export_clips(multi,'Multi',addon)]==['ActionB']
    print('PASS stable Material Action/slot ownership: two groups, active/unassigned editor Action, live curve deletion')
    print('PASS material animation: Material Action sampling, shared EFF action, UV-angle matrix conversion, unified panel draw and export timing')
finally:addon.unregister()
assert not hasattr(bpy.types.Material,'eiem_material_animations')

"""Real Blender authoring/preview regression and reviewable sample project."""
import copy,importlib,json,sys
from pathlib import Path
import bpy
addon_path,source_root,output=sys.argv[sys.argv.index('--')+1:]
sys.path.insert(0,str(Path(addon_path).resolve().parent))
addon=importlib.import_module(Path(addon_path).name);addon.register()
editor=addon.eiem_blender_addon.effect_editor
roots=[];baselines={}
for file in Path(source_root).rglob('effect.json'):
    if '.eff-backups' in str(file):continue
    root=editor.import_source(bpy.context,file.parent);roots.append(root)
    assert root.type=='EMPTY' and root.get('eff_effect_document')
    edit=editor.editor(root)
    baselines[root.name]=edit.export()['objects']
    for obj in editor.objects(root):
        for field in json.loads(obj.get('eff_effect_fields','[]')):
            value=obj[field['key']]
            if field['kind']=='float':assert type(value) is float
            if field['kind']=='int':assert type(value) is int
            if field['wide']:assert type(value) is str
    assert editor.synchronize(root).export()['objects']==baselines[root.name], 'UI synchronization changed the import defaults'
    root.location.x=len(roots)*4
    root.eff_effect.compiler=str(Path(addon_path).resolve().parents[1]/'tools/AnimeStudio/AnimeStudio.GUI/bin/Debug/net9.0-windows/AnimeStudio.GUI.exe')
bpy.context.scene.frame_set(60)
assert all(editor.synchronize(root).export()['objects']==baselines[root.name] for root in roots),'moving the mount root changed a native child transform'
assert any(o.get('预览粒子数',0)>0 for o in bpy.data.objects if o.get('eff_preview'))
scene=bpy.context.scene
root=roots[0];node=next(o for o in editor.objects(root) if any(editor.editor(root).objects[i]['classId']==198 for i in json.loads(o.get('eff_effect_components','[]'))))
edit=editor.synchronize(root);identity=next(i for i in json.loads(node['eff_effect_components']) if edit.objects[i]['classId']==198)
root.location.x=0;root.keyframe_insert('location',frame=1);root.location.x=10;root.keyframe_insert('location',frame=121)
data=copy.deepcopy(edit.value(identity));saved=data['moveWithTransform'];data['moveWithTransform']=1
data['looping']=True;data['lengthInSec']=10.0
data['EmissionModule']['enabled']=True
data['EmissionModule']['rateOverTime']={'minMaxState':0,'scalar':2.0,'minScalar':2.0}
data['EmissionModule']['m_Bursts']=[]
node[editor.field_key(identity,['moveWithTransform'])]=1
scene.frame_set(90)
sampled=[{'born':born} for born in (.5,1.0,1.5,2.0)]
matrices=editor.preview.birth_matrices(node,sampled,scene,data,root)
assert matrices and scene.frame_current==90
assert len({round(m.translation.x,3) for m in matrices.values()})>1,'world particles used current emitter matrix for all births'
root.animation_data_clear();root.location.x=4;data['moveWithTransform']=saved
for root in roots:
    nodes=[o for o in editor.objects(root) if not o.get('eff_preview') and any(editor.editor(root).objects[i]['classId']==198 for i in json.loads(o.get('eff_effect_components','[]')))]
    assert nodes
    obj=nodes[0];edit=editor.synchronize(root)
    identity=next(i for i in json.loads(obj['eff_effect_components']) if edit.objects[i]['classId']==198)
    key=editor.field_key(identity,['simulationSpeed']);before=obj[key];obj[key]=1.25
    assert editor.synchronize(root).value(identity,['simulationSpeed'])==1.25
    obj[key]=before
    # Exercise the timer explicitly; background Blender does not run its UI timer.
    root['预览错误']='stale error';obj[key]=1.15
    assert editor.preview.live_update()==editor.preview.POLL_INTERVAL
    assert editor.synchronize(root).value(identity,['simulationSpeed'])==1.15
    assert not root.get('预览错误'),root.get('预览错误')
    obj[key]=before;editor.preview.live_update()
    for shape_kind in (2,17):
        editor.preview._shape(root,obj,identity,{'ShapeModule':{'enabled':True,'type':shape_kind,'radius':{'value':1},'donutRadius':.2}})
        bounds=next(o for o in root.children_recursive if o.get('eff_preview_key')==root['eff_effect_id']+identity+'shape')
        assert len(bounds.data.edges)>50
        if shape_kind==2:assert all(v.co.y<=1e-5 for v in bounds.data.vertices),'hemisphere bounds extend below source equator'
    editor.preview.refresh(root,editor.synchronize(root))
    # Existing disabled modules become a typed editable block, preserving other fields.
    disabled=next((k for k,v in edit.value(identity).items() if k.endswith('Module') and isinstance(v,dict) and v.get('enabled') is False),None)
    if disabled:
        edit.set_scalar(identity,[disabled,'enabled'],True);editor.store(root,edit);editor.refresh_fields(root)
        assert obj[editor.field_key(identity,[disabled,'enabled'])] is True
    # Inline array growth remains valid after rebuild of the property map.
    edit=editor.synchronize(root);edit.array_change(identity,['EmissionModule','m_Bursts'],'ADD')
    editor.store(root,edit);editor.refresh_fields(root)
    assert len(edit.value(identity,['EmissionModule','m_Bursts']))==edit.value(identity,['EmissionModule','m_BurstCount'])
    # A layout recorder proves fields are inline props, not click-to-edit operators.
    class Layout:
        def __init__(self,expanded=False):self.errors=[];self.props=[];self.labels=[];self.operators=[];self.expanded=expanded
        def row(self,**kwargs):return self
        def column(self,**kwargs):return self
        def box(self):return self
        def template_color_ramp(self,*args,**kwargs):pass
        def separator(self,**kwargs):pass
        def panel(self,*args,**kwargs):return self,None if kwargs.get('default_closed') and not self.expanded else self
        def prop(self,*args,**kwargs):self.props.append(kwargs.get('text',''))
        def prop_search(self,*args,**kwargs):pass
        def operator(self,*args,**kwargs):self.operators.append(kwargs.get('text',''));return type('Op',(),{})()
        def label(self,**kwargs):
            self.labels.append(kwargs.get('text',''))
            if kwargs.get('icon')=='ERROR':self.errors.append(kwargs['text'])
    bpy.context.view_layer.objects.active=obj
    layout=Layout();editor.EFF_PT_effect_object.draw(type('Panel',(),{'layout':layout})(),bpy.context)
    assert not layout.errors,layout.errors
    assert '发射器基础' in layout.labels and '发射数量' in layout.labels and '发射形状与范围' in layout.labels,layout.labels
    assert any(p.startswith('出生寿命 (s)') for p in layout.props) and '每秒发射数' in layout.props
    mode_key=editor.field_key(identity,['InitialModule','startLifetime','minMaxState'])
    choice=obj.eff_effect_choices[mode_key];previous=choice.value
    choice.value='3';assert obj[mode_key]==3
    choice.value=previous
    assert not any(x in layout.labels for x in ('float','int','UInt32','编辑列表'))
    assert len(layout.props)<600, len(layout.props)
    keys=root.eff_effect.toggle_keys
    original_active=bpy.context.view_layer.objects.active
    for value in ('F7','CTRL+J'):
        item=keys.add();item.key=value
    root.select_set(True);bpy.context.view_layer.objects.active=root
    root_layout=Layout(True)
    editor.EFF_PT_effect_object.draw(type('Panel',(),{'layout':root_layout})(),bpy.context)
    assert '特效显隐按键' in root_layout.labels,root_layout.labels
    assert all(value in root_layout.labels for value in ('F7','CTRL+J')),root_layout.labels
    assert '录制按键' in root_layout.operators,root_layout.operators
    assert bpy.ops.eiem.effect_key('EXEC_DEFAULT',action='REMOVE',root_name=root.name,index=0)=={'FINISHED'}
    assert len(keys)==1 and keys[0].key=='CTRL+J'
    while len(keys):keys.remove(len(keys)-1)
    root.select_set(False);obj.select_set(True);bpy.context.view_layer.objects.active=original_active
    # Changing friendly widgets updates the same native fields and vice versa.
    color_path=['InitialModule','startColor','maxColor'];color_key='fxv_'+editor.field_key(identity,color_path)[3:]
    if color_key in obj:
        original=list(obj[color_key]);obj[color_key]=[.1,.2,.3,.4];editor.synchronize(root)
        assert abs(edit.value(identity,color_path+['a'])-.4)<1e-6
        obj[editor.field_key(identity,color_path+['a'])]=.7;editor.synchronize(root)
        assert abs(obj[color_key][3]-.7)<1e-6
        obj[color_key]=original;editor.synchronize(root)
    # All 23 real module layouts can render, including originally disabled ones.
    import sys as _sys
    for module in editor.effect_ui.MODULES:
        editor.effect_ui.Draw(_sys.modules[editor.__name__],obj,edit,identity).module(Layout(True),module)
    renderer=next(i for i in json.loads(obj['eff_effect_components']) if edit.objects[i]['classId']==199)
    enabled_key=editor.field_key(renderer,['m_Enabled']);old_enabled=obj[enabled_key]
    obj[enabled_key]=False;editor.preview.refresh(root,editor.synchronize(root))
    particle_mesh=next(o for o in root.children_recursive if o.get('eff_preview_key')==root['eff_effect_id']+identity)
    assert particle_mesh.hide_viewport
    obj[enabled_key]=old_enabled;editor.preview.refresh(root,editor.synchronize(root))
    resource_layout=Layout(True)
    editor.effect_ui.Draw(_sys.modules[editor.__name__],obj,edit,renderer).renderer(resource_layout)
    assert any('材质 1:' in label for label in resource_layout.labels),resource_layout.labels
    # Sorting edited key times must update the ID props, or the next sync would
    # silently assign the old values back to different native keys.
    curve_path=['InitialModule','startSpeed','maxCurve','m_Curve']
    while len(edit.value(identity,curve_path))<2:edit.array_change(identity,curve_path,'ADD')
    editor.refresh_fields(root)
    obj[editor.field_key(identity,curve_path+[0,'time'])]=.8
    obj[editor.field_key(identity,curve_path+[0,'value'])]=8.0
    obj[editor.field_key(identity,curve_path+[1,'time'])]=.2
    obj[editor.field_key(identity,curve_path+[1,'value'])]=2.0
    editor.synchronize(root)
    expected=[(p['time'],p['value']) for p in edit.value(identity,curve_path)]
    editor.synchronize(root)
    assert expected==[(p['time'],p['value']) for p in edit.value(identity,curve_path)]
    assert expected[:2]==[(.2,2.0),(.8,8.0)]
    # Real old .blend data has only fx_ properties and no friendly controls.
    root['eff_effect_ui_version']=0
    obj.eff_effect_choices.clear()
    obj[editor.field_key(identity,['simulationSpeed'])]=1.31
    editor.upgrade_loaded()
    assert root['eff_effect_ui_version']==2 and obj.eff_effect_choices
    assert editor.editor(root).value(identity,['simulationSpeed'])==1.31
output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=str(output))
bpy.ops.wm.open_mainfile(filepath=str(output))
assert len([o for o in bpy.data.objects if o.get('eff_effect_document')])==len(roots)
bpy.context.scene.frame_set(90)
assert not any(o.get('预览错误') for o in bpy.data.objects if o.get('eff_effect_document'))
for root in (o for o in bpy.data.objects if o.get('eff_effect_document')):
    edit=editor.editor(root)
    node=next(o for o in editor.objects(root) if any(edit.objects[i]['classId']==198 for i in json.loads(o.get('eff_effect_components','[]'))))
    identity=next(i for i in json.loads(node['eff_effect_components']) if edit.objects[i]['classId']==198)
    path=['InitialModule','startColor','maxColor'];key='fxv_'+editor.field_key(identity,path)[3:]
    node[key]=[.1,.3,.5,.8]
    editor.synchronize(root)
    assert abs(edit.value(identity,path+['a'])-.8)<1e-6
print('PASS effect empty hierarchy: typed inline fields, blocks, lists, source Mesh/texture preview, timeline, save/reopen')
addon.unregister()

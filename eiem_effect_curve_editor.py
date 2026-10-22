"""Particle AnimationCurve <-> Blender F-Curve; all interaction is native."""
import copy
import hashlib
import importlib
import json
import math

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, StringProperty

if __package__:from . import eiem_effect_curve as curves
else:import eiem_effect_curve as curves

MARKER='eff_effect_curve_projection'
PATHS='eff_effect_curve_paths'
_busy=False
_errors={}
_enum_items={}


def api():return importlib.import_module((__package__+'.' if __package__ else '')+'eiem_effect_editor')


def live(obj,edit,identity,path):
    value=edit.value(identity,path)
    if isinstance(value,dict):return {k:live(obj,edit,identity,path+[k]) for k in value}
    if isinstance(value,list):return [live(obj,edit,identity,path+[i]) for i in range(len(value))]
    return obj.get(api().field_key(identity,path),value)


def axis_paths(edit,identity,path):
    if len(path)!=2:return [path]
    module=edit.value(identity,[path[0]])
    if path[0] in ('VelocityModule','ForceModule') and path[1] in ('x','y','z'):return [[path[0],k] for k in ('x','y','z')]
    if module.get('separateAxes') and path[0] in ('SizeModule','SizeBySpeedModule','RotationModule','RotationBySpeedModule'):
        return [[path[0],k] for k in (('x','y','curve') if 'Rotation' in path[0] else ('curve','y','z')) if k in module]
    return [path]


def channels(obj,edit,identity,path,axes=False):
    result=[];paths=axis_paths(edit,identity,path) if axes else [path]
    for ordinal,p in enumerate(paths):
        value=live(obj,edit,identity,p)
        unit=api().effect_ui.angle_unit(p+['scalar'])
        multiplier=float(value.get('scalar',1))
        factor=(multiplier or 1)*(180/math.pi if unit and multiplier else 1)
        title=channel_name(p,value)
        axis=next((i for i,k in enumerate('xyz') if str(p[-1]).lower().endswith(k)),ordinal)
        common=dict(attribute=p,group=module_name(p[0]),axis=axis)
        if 'm_Curve' in value:result.append(dict(path=p,title=title,data=value,factor=1.0,**common))
        elif 'maxCurve' in value and value.get('minMaxState') in (1,2):
            for branch in (('minCurve','maxCurve') if value['minMaxState']==2 else ('maxCurve',)):
                result.append(dict(path=p+[branch],title=title+(' · A' if branch=='minCurve' else ' · B' if value['minMaxState']==2 else ''),
                    data=value[branch],factor=factor,zero=multiplier==0,**common))
    return result


def module_name(module):return api().effect_ui.MODULES.get(module,(api().effect_ui.name(module),''))[0]


def attribute_name(path):
    field=api().effect_ui.name(path[-1]);module=path[0]
    if path[-1]=='curve':field='角速度 Z / 统一角速度' if 'Rotation' in module else '大小 X / 统一大小' if 'Size' in module else field
    middle=' / '.join(str(p+1) if isinstance(p,int) else api().effect_ui.name(p) for p in path[1:-1])
    return module_name(module)+' / '+(middle+' / ' if middle else '')+field


def channel_name(path,value):
    module=path[0];field=path[-1];name=api().effect_ui.name(field)
    if len(path)!=2:return attribute_name(path)
    if module=='VelocityModule' and field in 'xyz':name='速度 '+field.upper()
    elif module=='ForceModule' and field in 'xyz':name='加速度 '+field.upper()
    elif module in ('RotationModule','RotationBySpeedModule'):
        name='角速度 '+('Z' if field=='curve' else str(field).upper())+' (°/s)'
        if module=='RotationBySpeedModule':name+=' · 随速度'
    elif module in ('SizeModule','SizeBySpeedModule'):
        name='大小 '+('X' if field=='curve' else str(field).upper())+' 倍率'
        if module=='SizeBySpeedModule':name+=' · 随速度'
    else:return attribute_name(path)
    return name


def available(edit,identity):
    """Only genuine numeric curves in this source schema, never synthetic tracks."""
    result=[]
    def visit(value,path):
        if isinstance(value,dict):
            if 'maxCurve' in value and 'minMaxState' in value or isinstance(value.get('m_Curve'),list):
                if edit.editable(identity,path):result.append(path)
                return
            for key,child in value.items():visit(child,path+[key])
        elif isinstance(value,list):
            for i,child in enumerate(value):visit(child,path+[i])
    visit(edit.value(identity),[])
    return result


def enabled_path(obj,edit,identity,path):
    module=live(obj,edit,identity,[path[0]])
    if not module.get('enabled',True):return False
    field=path[1] if len(path)>1 else ''
    if path[0]=='InitialModule':
        if field in ('startSizeY','startSizeZ') and not module.get('size3D'):return False
        if field in ('startRotationX','startRotationY') and not module.get('rotation3D'):return False
    if path[0] in ('SizeModule','SizeBySpeedModule','RotationModule','RotationBySpeedModule') and field in ('x','y','z') and not module.get('separateAxes'):return False
    if path[0]=='NoiseModule' and field in ('strengthY','strengthZ') and not module.get('separateAxes'):return False
    if path[0]=='NoiseModule' and str(field).startswith('remap') and not module.get('remapEnabled'):return False
    return True


def active_paths(obj,edit,identity):
    return [p for p in available(edit,identity) if enabled_path(obj,edit,identity,p) and channels(obj,edit,identity,p)]


def record_paths(edit,record):
    if 'paths' in record:return record['paths']
    return axis_paths(edit,record['identity'],record['path']) if record['axes'] else [record['path']]


def rows_for(obj,edit,identity,paths):
    valid=available(edit,identity)
    return [row for path in paths if path in valid for row in channels(obj,edit,identity,path)]


def remember(obj,paths):obj[PATHS]=api().document.dumps(paths)


def add_attribute(helper,path):
    editor=api();root=helper['eff_curve_root'];obj=helper['eff_curve_target']
    edit=editor.synchronize(root);record=metadata(helper);identity=record['identity']
    editor.document.require(path in available(edit,identity),'来源不支持这个曲线属性')
    value=edit.value(identity,path);module=edit.value(identity,[path[0]])
    if 'enabled' in module:module['enabled']=True
    field=path[1] if len(path)>1 else ''
    if path[0]=='InitialModule':
        if field in ('startSizeY','startSizeZ'):module['size3D']=True
        if field in ('startRotationX','startRotationY'):module['rotation3D']=True
    if path[0] in ('SizeModule','SizeBySpeedModule','RotationModule','RotationBySpeedModule','NoiseModule') and field in ('x','y','z','strengthY','strengthZ'):module['separateAxes']=True
    if path[0]=='NoiseModule' and str(field).startswith('remap'):module['remapEnabled']=True
    if 'minMaxState' in value and value['minMaxState'] in (0,3):
        maximum=float(value['scalar']);minimum=float(value.get('minScalar',maximum));random=value['minMaxState']==3
        # Keep actual constant/range values, including zero and negative values.
        value['scalar']=1.0;value['minMaxState']=2 if random else 1
        for branch,number in [('maxCurve',maximum)]+([('minCurve',minimum)] if random else []):
            template=next(iter(value[branch]['m_Curve']),None)
            value[branch]['m_Curve']=[curves.new_key(0,number,template),curves.new_key(1,number,template)]
    editor.populate(obj,edit,json.loads(obj['eff_effect_components']));editor.store(root,edit)
    paths=record_paths(edit,record)
    if path not in paths:paths.append(path)
    record.update(version=2,paths=paths);remember(obj,paths)
    project(helper,rows_for(obj,edit,identity,paths),record)


def remove_attribute(helper,path):
    editor=api();edit=editor.synchronize(helper['eff_curve_root']);record=metadata(helper)
    paths=[p for p in record_paths(edit,record) if p!=path]
    record.update(version=2,paths=paths);remember(helper['eff_curve_target'],paths)
    project(helper,rows_for(helper['eff_curve_target'],edit,record['identity'],paths),record)


def labels(path):
    module=str(path[0])
    x='系统周期 (%)' if module in ('InitialModule','EmissionModule') else '速度范围进度 (%)' if 'BySpeed' in module or module=='LifetimeByEmitterSpeedModule' else '拖尾进度 (%)' if module=='TrailModule' else '已过寿命 (%)'
    if 'Rotation' in module or any(str(p).startswith('startRotation') for p in path):y='角度 (°)' if module=='InitialModule' else '角速度 (°/s)'
    elif module=='VelocityModule':
        field=str(path[1]) if len(path)>1 else ''
        y='速度倍率' if field=='speedModifier' else '轨道中心偏移 (单位)' if field.startswith('orbitalOffset') else '数值' if field.startswith('orbital') else '速度 (单位/s)'
    elif module=='ForceModule':y='加速度 (单位/s²)'
    elif 'Size' in module:y='大小倍率'
    elif module=='EmissionModule':y='发射数 / s' if 'rateOverTime' in path else '发射数 / 单位距离' if 'rateOverDistance' in path else '发射数'
    elif module=='InitialModule':
        y='寿命 (s)' if 'startLifetime' in path else '初速度 (单位/s)' if 'startSpeed' in path else '出生大小' if any(str(p).startswith('startSize') for p in path) else '重力倍率' if 'gravityModifier' in path else '数值'
    else:y='数值'
    return x,y


def draw_summary(layout,draw,path):
    row=layout.row(align=True)
    button=row.operator('eiem.effect_curve_edit',text='在统一曲线窗口中编辑',icon='GRAPH')
    button.object_name=draw.obj.name;button.identity=draw.identity;button.path=json.dumps(path)
    button.axes=True
    for helper in helpers(api().root_of(draw.obj)):
        if helper.get('eff_curve_target')==draw.obj and helper.name in _errors:
            layout.label(text=_errors[helper.name],icon='ERROR');break


def fcurves(helper):
    animation=helper.animation_data
    action=animation.action if animation else None
    if not action:return []
    if hasattr(action,'fcurves'):return action.fcurves
    slot=animation.action_slot
    if slot:
        for layer in action.layers:
            for strip in layer.strips:
                bag=strip.channelbag(slot)
                if bag:return bag.fcurves
    return []


def geometry(curve):
    if curve is None:return None
    return dict(keys=[dict(co=list(p.co),left=list(p.handle_left),right=list(p.handle_right),
        interpolation=p.interpolation,left_type=p.handle_left_type,right_type=p.handle_right_type) for p in curve.keyframe_points],
        extrapolation=curve.extrapolation,modifiers=[m.type for m in curve.modifiers])


def helpers(root=None):
    return [o for o in bpy.data.objects if o.get(MARKER) and (root is None or o.get('eff_curve_root')==root)]


def metadata(helper):return json.loads(helper[MARKER])


def project(helper,rows,record):
    """A disposable, hidden Action projection; never animates an author field."""
    existing={f.data_path:f for f in fcurves(helper)}
    previous={tuple(r['path']):r for r in metadata(helper)['rows']} if helper.get(MARKER) else {}
    paths=set()
    for index,row in enumerate(rows):
        prop=row['title']
        if len(prop.encode('utf-8'))>63:prop=prop.encode('utf-8')[:54].decode('utf-8',errors='ignore')+' '+hashlib.sha1(prop.encode('utf-8')).hexdigest()[:6]
        path='["'+bpy.utils.escape_identifier(prop)+'"]';paths.add(path)
        helper[prop]=float(row['data']['m_Curve'][0]['value']*row['factor'] if row['data']['m_Curve'] else 1)
        helper.id_properties_ui(prop).update(description=labels(row['attribute'])[0]+'；纵轴已乘倍率')
        f=existing.get(path)
        old=previous.get(tuple(row['path']))
        if f is not None and old and all(old.get(k)==row.get(k) for k in ('data','factor','zero')) and geometry(f)==old['projection']:
            row['rna_path']=path;row['projection']=old['projection'];continue
        if f is None:
            helper.keyframe_insert(data_path=path,frame=0,group=row['group'])
            f=next(f for f in fcurves(helper) if f.data_path==path)
        f.extrapolation='CONSTANT';f.auto_smoothing='NONE'
        f.keyframe_points.clear()
        keys=row['data']['m_Curve'];f.keyframe_points.add(len(keys))
        for i,(p,key) in enumerate(zip(f.keyframe_points,keys)):
            p.co=(key['time']*100,key['value']*row['factor'])
            p.handle_left_type='FREE';p.handle_right_type='FREE';p.interpolation='BEZIER'
            for side in ('in','out'):
                t,v=curves.handle(keys,i,side)
                setattr(p,'handle_left' if side=='in' else 'handle_right',(t*100,v*row['factor']))
            p.select_control_point=False;p.select_left_handle=False;p.select_right_handle=False
        f.update();f.select=True
        f.color_mode='CUSTOM'
        base=((.95,.3,.25),(.25,.8,.4),(.3,.55,1))[row['axis']%3] if len(rows)>2 else ((1,.6,.16) if index==0 else (.25,.65,1))
        f.color=tuple(v*.7 if row['title'].endswith(' · A') and len(rows)>2 else v for v in base)
        row['rna_path']=path;row['projection']=geometry(f)
    for path,f in existing.items():
        if path not in paths:fcurves(helper).remove(f)
    record['rows']=rows;helper[MARKER]=api().document.dumps(record)


def create_projection(context,obj,identity,path,axes=False):
    editor=api();root=editor.root_of(obj)
    editor.document.require(root,'请选择特效发射器')
    if not identity:identity=next((i for i in json.loads(obj.get('eff_effect_components','[]')) if editor.editor(root).objects[i]['classId']==198),'')
    editor.document.require(identity in json.loads(obj.get('eff_effect_components','[]')) and editor.editor(root).objects[identity]['classId']==198,'请选择特效发射器')
    edit=editor.synchronize(root)
    paths=json.loads(obj[PATHS]) if obj.get(PATHS) is not None else active_paths(obj,edit,identity)
    if path:
        for p in axis_paths(edit,identity,path) if axes else [path]:
            if p not in paths and channels(obj,edit,identity,p):paths.append(p)
    record=dict(version=2,identity=identity,paths=paths)
    rows=rows_for(obj,edit,identity,paths)
    overlapping=[h for h in helpers(root) if h.get('eff_curve_target')==obj and metadata(h)['identity']==identity]
    if overlapping:
        helper=overlapping[0]
        project(helper,rows,record)
        for retired in overlapping[1:]:
            for window in bpy.context.window_manager.windows:
                for area in window.screen.areas:
                    for space in area.spaces:
                        if space.type=='GRAPH_EDITOR' and space.dopesheet.filter_collection in retired.users_collection:space.dopesheet.filter_collection=helper.users_collection[0]
            close_projection(retired,flush=False)
        remember(obj,paths)
        return helper
    collection=bpy.data.collections.new('EFF 曲线 · '+obj.name)
    context.scene.collection.children.link(collection);collection.hide_render=True
    helper=bpy.data.objects.new(obj.name+' · 特效曲线',None)
    collection.objects.link(helper);helper.hide_set(True);helper.hide_render=True
    helper['eff_curve_target']=obj;helper['eff_curve_root']=root
    try:project(helper,rows,record)
    except Exception:
        close_projection(helper,flush=False);raise
    if helper.animation_data and helper.animation_data.action:helper.animation_data.action.name='EFF 曲线 · '+helper.name
    remember(obj,paths)
    return helper


def decode(row,shape):
    """Preserve untouched native keys and unused weights; decode edited handles."""
    require=api().document.require
    if shape is None:shape=dict(keys=[],modifiers=[],extrapolation='CONSTANT')
    require(not shape['modifiers'],'特效曲线不支持 F-Curve 修改器，请移除修改器')
    require(shape['extrapolation']=='CONSTANT','特效曲线请使用恒定外插')
    points=shape['keys'];old=row['projection']['keys'];native=row['data']['m_Curve']
    require(all(p['interpolation'] in ('BEZIER','LINEAR') for p in points),'特效曲线支持贝塞尔 / 线性插值，请修改 T 菜单中的插值模式')
    require(all(-1e-5<=p['co'][0]<=100+1e-5 for p in points),'特效曲线横轴需要在 0–100% 内（G X 调整）')
    require(all(a['co'][0]<b['co'][0] for a,b in zip(points,points[1:])),'特效曲线不能有同一时间的重复关键点')
    require(all(math.isfinite(v) for p in points for field in ('co','left','right') for v in p[field]),'曲线需要有限数值')
    result=copy.deepcopy(row['data']);keys=[];used=set()
    for i,p in enumerate(points):
        match=next((j for j,q in enumerate(old) if j not in used and q['co'][0]==p['co'][0]),None)
        if match is None and len(points)==len(old) and i not in used:match=i
        if match is not None:used.add(match)
        source=native[match] if match is not None else native[0] if native else None
        key=copy.deepcopy(source) if source else curves.new_key(0,0)
        baseline=old[match] if match is not None else None
        if baseline is None or p['co']!=baseline['co']:
            key.update(time=max(0,min(1,p['co'][0]/100)),value=p['co'][1]/row['factor'])
        for side,handle,bit,neighbor in (('in','left',1,i-1),('out','right',2,i+1)):
            other=points[neighbor] if 0<=neighbor<len(points) else None
            old_neighbor=(old[match+(-1 if side=='in' else 1)] if match is not None and 0<=match+(-1 if side=='in' else 1)<len(old) else None)
            linear=(other['interpolation']=='LINEAR' if side=='in' and other else p['interpolation']=='LINEAR' if side=='out' and other else False)
            was_linear=(old_neighbor['interpolation']=='LINEAR' if side=='in' and old_neighbor else baseline['interpolation']=='LINEAR' if side=='out' and baseline and old_neighbor else False)
            if not linear and not was_linear and baseline and p['co']==baseline['co'] and p[handle]==baseline[handle] and (
                    (other is None and old_neighbor is None) or (other and old_neighbor and other['co'][0]==old_neighbor['co'][0])):continue
            dt=abs(other['co'][0]-p['co'][0]) if other else 100.0
            if linear:
                key[side+'Slope']=(other['co'][1]-p['co'][1])/(other['co'][0]-p['co'][0])*100/row['factor']
                key['weightedMode']=int(key.get('weightedMode',0))&~bit
            else:
                dx=p[handle][0]-p['co'][0];dy=p[handle][1]-p['co'][1]
                require((dx<=1e-5 if side=='in' else dx>=-1e-5),'切线手柄方向无效，请使用 V 菜单调整')
                require(not other or abs(dx)<=dt+1e-4,'切线手柄不能越过相邻关键点，请缩短手柄')
                require(abs(dx)>1e-9 or abs(dy)<1e-6,'不支持垂直切线，请稍微水平移动手柄')
                key[side+'Slope']=dy/dx*100/row['factor'] if abs(dx)>1e-9 else key.get(side+'Slope',0.0)
                key[side+'Weight']=max(0,min(1,abs(dx)/dt))
                key['weightedMode']=int(key.get('weightedMode',0))|bit
        require(all(math.isfinite(key[n]) and abs(key[n])<=3.4028234663852886e38 for n in ('time','value','inSlope','outSlope','inWeight','outWeight')),'曲线超出原生浮点范围')
        keys.append(key)
    result['m_Curve']=keys
    return result


def apply_rows(obj,edit,identity,rows):
    editor=api();changed=[];resized=False
    for row in rows:
        current=edit.value(identity,row['path'])
        if current==row['data']:continue
        resized|=len(current['m_Curve'])!=len(row['data']['m_Curve']);changed.append(row)
    for row in changed:edit.value(identity,row['path'][:-1])[row['path'][-1]]=copy.deepcopy(row['data'])
    if resized:editor.populate(obj,edit,json.loads(obj['eff_effect_components']))
    else:
        for row in changed:
            for path,kind,value in editor.native_fields(edit,identity,row['path']):
                key=editor.field_key(identity,path)
                if obj.get(key)!=value:obj[key]=value
    return bool(changed)


def synchronize(root,edit):
    """Called by author sync, including export/save; never depends on a timer."""
    global _busy
    if _busy:return set()
    _busy=True;changed=set()
    try:
        for helper in helpers(root):
            try:
                record=metadata(helper);obj=helper.get('eff_curve_target');identity=record['identity']
                if not obj or identity not in edit.state['values']:continue
                paths=record_paths(edit,record)
                current=rows_for(obj,edit,identity,paths)
                source={tuple(r['path']):r for r in current};fs={f.data_path:f for f in fcurves(helper)}
                # Deleting a whole native channel removes its attribute from the
                # editing list. It must never erase that effect's source curve.
                removed=[r.get('attribute',r['path'][:-1] if r['path'][-1] in ('minCurve','maxCurve') else r['path']) for r in record['rows'] if r['rna_path'] not in fs]
                paths=[p for p in paths if p not in removed and any(r.get('attribute')==p for r in current)]
                rows=[];graph_changed=False
                for row in record['rows']:
                    attribute=row.get('attribute',row['path'][:-1] if row['path'][-1] in ('minCurve','maxCurve') else row['path'])
                    if attribute not in paths:continue
                    shape=geometry(fs.get(row['rna_path']))
                    if shape==row['projection']:continue
                    live_row=source.get(tuple(row['path']))
                    api().document.require(live_row is not None and live_row['data']==row['data'],'曲线和原生数值同时改变，请先重新载入原生值')
                    result=copy.deepcopy(row);result['data']=decode(row,shape);result['projection']=shape
                    rows.append(result);graph_changed=True
                if graph_changed:
                    if apply_rows(obj,edit,identity,rows):changed.add(obj.as_pointer())
                    for row in record['rows']:
                        update=next((r for r in rows if r['path']==row['path']),None)
                        if update:row.update(update)
                    helper[MARKER]=api().document.dumps(record)
                fresh=rows_for(obj,edit,identity,paths)
                if record.get('version')!=2 or record.get('paths')!=paths or [{k:r[k] for k in ('path','data','factor','title')} for r in fresh]!=[{k:r[k] for k in ('path','data','factor','title')} for r in record['rows']]:
                    record.update(version=2,paths=paths);project(helper,fresh,record)
                remember(obj,paths)
                _errors.pop(helper.name,None)
            except (ValueError,KeyError) as error:
                _errors[helper.name]=str(error);raise ValueError(helper.name+'：'+str(error)) from error
    finally:_busy=False
    return changed


def close_projection(helper,flush=True):
    root=helper.get('eff_curve_root')
    if flush and root:api().store(root,api().synchronize(root))
    action=helper.animation_data.action if helper.animation_data else None
    collections=list(helper.users_collection);_errors.pop(helper.name,None)
    # Disabling the add-on or deleting its owner must not reveal other animations.
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type=='GRAPH_EDITOR' and area.spaces.active.dopesheet.filter_collection in collections:area.type='VIEW_3D'
    bpy.data.objects.remove(helper,do_unlink=True)
    if action and not action.users:bpy.data.actions.remove(action)
    for collection in collections:
        if not collection.objects:bpy.data.collections.remove(collection)


def poll_updates():
    if _busy:return .12
    for root in {h.get('eff_curve_root') for h in helpers()}:
        if not root:continue
        try:
            pending=any(geometry(next((f for f in fcurves(h) if f.data_path==r['rna_path']),None))!=r['projection'] for h in helpers(root) for r in metadata(h)['rows'])
            if pending:
                edit=api().synchronize(root)
                nodes={h['eff_curve_target'].as_pointer() for h in helpers(root) if h.get('eff_curve_target')}
                api().preview.refresh(root,edit,nodes)
                api().preview.curve_refreshed(root,nodes)
                for window in bpy.context.window_manager.windows:
                    for area in window.screen.areas:area.tag_redraw()
        except (ValueError,ReferenceError,RuntimeError):pass
    return .12


def cleanup_closed():
    if bpy.app.background:return .5
    shown={space.dopesheet.filter_collection for window in bpy.context.window_manager.windows for area in window.screen.areas for space in area.spaces if space.type=='GRAPH_EDITOR'}
    for helper in list(helpers()):
        if not helper.get('eff_curve_root') or not helper.get('eff_curve_target'):
            close_projection(helper,flush=False);continue
        if any(c in shown for c in helper.users_collection):continue
        try:close_projection(helper)
        except (ValueError,ReferenceError):pass
    return .5


def graph_helper(context):
    space=context.space_data
    if not space or space.type!='GRAPH_EDITOR':return None
    collection=space.dopesheet.filter_collection
    return next((o for o in collection.objects if o.get(MARKER)),None) if collection else None


def draw_header(self,context):
    helper=graph_helper(context)
    if not helper:return
    row=self.layout.row(align=True);row.label(text='EFF · '+helper['eff_curve_target'].name)
    row.operator('eiem.effect_curve_add',text='添加属性',icon='ADD')
    row.prop(context.space_data,'show_region_ui',text='',icon='PREFERENCES')


def show_sidebar(area):
    area.spaces.active.show_region_ui=True
    def select_tab():
        try:
            if area.type=='GRAPH_EDITOR':
                region=next(r for r in area.regions if r.type=='UI')
                if hasattr(region,'active_panel_category'):region.active_panel_category='EFF 曲线'
        except (ReferenceError,RuntimeError,StopIteration):pass
    bpy.app.timers.register(select_tab,first_interval=.1)


class EFF_OT_effect_curve_edit(bpy.types.Operator):
    bl_idname='eiem.effect_curve_edit';bl_label='编辑全部曲线';bl_options={'UNDO'}
    bl_description='同一发射器共用一个 Blender 曲线窗口；添加属性、多选一起编辑，自动同步参数和预览'
    object_name:StringProperty(options={'HIDDEN'})
    identity:StringProperty(options={'HIDDEN'})
    path:StringProperty(options={'HIDDEN'})
    axes:BoolProperty(default=False,options={'HIDDEN'})

    def execute(self,context):
        try:
            obj=bpy.data.objects.get(self.object_name) or context.object
            helper=create_projection(context,obj,self.identity,json.loads(self.path) if self.path else None,self.axes)
            collection=helper.users_collection[0]
            for window in context.window_manager.windows:
                for area in window.screen.areas:
                    if area.type=='GRAPH_EDITOR' and area.spaces.active.dopesheet.filter_collection==collection:
                        show_sidebar(area);area.tag_redraw();return {'FINISHED'}
            windows=list(context.window_manager.windows)
            bpy.ops.wm.window_new()
            window=next(w for w in context.window_manager.windows if w not in windows)
            area=max(window.screen.areas,key=lambda a:a.width*a.height);area.type='GRAPH_EDITOR'
            if len(window.screen.areas)>1:
                with context.temp_override(window=window,area=area):bpy.ops.screen.screen_full_area()
                area=next(a for a in window.screen.areas if a.type=='GRAPH_EDITOR')
            space=area.spaces.active;space.mode='FCURVES'
            space.dopesheet.filter_collection=collection;space.dopesheet.show_only_selected=False;space.dopesheet.show_hidden=True
            space.show_seconds=False;space.show_region_ui=True;space.use_normalization=False;space.show_cursor=True
            show_sidebar(area)
            with context.temp_override(window=window,area=area,region=next(r for r in area.regions if r.type=='WINDOW')):
                bpy.ops.anim.channels_expand(all=True)
                bpy.ops.graph.view_all()
            return {'FINISHED'}
        except Exception as error:
            self.report({'ERROR'},str(error));return {'CANCELLED'}


class EFF_OT_effect_curve_reset(bpy.types.Operator):
    bl_idname='eiem.effect_curve_reset';bl_label='丢弃图中未同步修改，重新载入原生值';bl_options={'UNDO'}
    def execute(self,context):
        helper=graph_helper(context)
        if not helper:return {'CANCELLED'}
        record=metadata(helper);obj=helper['eff_curve_target'];root=helper['eff_curve_root']
        edit=api().editor(root)
        project(helper,rows_for(obj,edit,record['identity'],record_paths(edit,record)),record)
        _errors.pop(helper.name,None);return {'FINISHED'}


def attribute_items(self,context):
    helper=graph_helper(context)
    if not helper:return []
    record=metadata(helper);obj=helper['eff_curve_target'];edit=api().editor(helper['eff_curve_root'])
    result=[]
    for i,p in enumerate(available(edit,record['identity'])):
        if p in record_paths(edit,record):continue
        key=(record['identity'],json.dumps(p))
        result.append(_enum_items.setdefault(key,(key[1],attribute_name(p),'添加并启用这个属性；常量或随机范围会转换为数值不变的曲线',i)))
    return result


class EFF_OT_effect_curve_add(bpy.types.Operator):
    bl_idname='eiem.effect_curve_add';bl_label='添加曲线属性（可搜索）';bl_options={'UNDO'}
    bl_description='选择游戏支持的参数，加入同一曲线窗口并启用所属模块；保留原本常量/随机范围的实际数值'
    bl_property='attribute'
    attribute:EnumProperty(name='属性',items=attribute_items)
    def invoke(self,context,event):
        context.window_manager.invoke_search_popup(self);return {'FINISHED'}
    def execute(self,context):
        try:
            helper=graph_helper(context);api().document.require(helper is not None,'请在特效曲线窗口内添加属性')
            path=json.loads(self.attribute);add_attribute(helper,path)
            wanted={r['rna_path'] for r in metadata(helper)['rows'] if r['attribute']==path}
            for f in fcurves(helper):
                f.select=f.data_path in wanted
                if f.select:f.hide=False
                for point in f.keyframe_points:point.select_control_point=False;point.select_left_handle=False;point.select_right_handle=False
            with context.temp_override(region=next(r for r in context.area.regions if r.type=='WINDOW')):
                bpy.ops.anim.channels_expand(all=True)
                bpy.ops.graph.view_all()
            return {'FINISHED'}
        except Exception as error:self.report({'ERROR'},str(error));return {'CANCELLED'}


class EFF_OT_effect_curve_remove(bpy.types.Operator):
    bl_idname='eiem.effect_curve_remove';bl_label='移出编辑列表';bl_options={'UNDO'}
    bl_description='只从当前编辑列表移出，保留游戏参数和曲线；可通过添加属性重新放回'
    path:StringProperty(options={'HIDDEN'})
    def execute(self,context):
        try:remove_attribute(graph_helper(context),json.loads(self.path));return {'FINISHED'}
        except Exception as error:self.report({'ERROR'},str(error));return {'CANCELLED'}


class EFF_OT_effect_curve_collect(bpy.types.Operator):
    bl_idname='eiem.effect_curve_collect';bl_label='加入已有曲线';bl_options={'UNDO'}
    bl_description='将该发射器已启用的数值曲线加入编辑列表，保留全部游戏参数'
    def execute(self,context):
        helper=graph_helper(context);editor=api();edit=editor.synchronize(helper['eff_curve_root']);record=metadata(helper)
        obj=helper['eff_curve_target'];paths=record_paths(edit,record)
        for path in active_paths(obj,edit,record['identity']):
            if path not in paths:paths.append(path)
        record.update(version=2,paths=paths);remember(obj,paths)
        project(helper,rows_for(obj,edit,record['identity'],paths),record);return {'FINISHED'}


class EFF_OT_effect_curve_visibility(bpy.types.Operator):
    bl_idname='eiem.effect_curve_visibility';bl_label='显示特效曲线'
    bl_description='切换曲线显示范围并自动缩放到可见曲线；不修改特效数据'
    all:BoolProperty(default=False,options={'HIDDEN'})
    def execute(self,context):
        with context.temp_override(region=next(r for r in context.area.regions if r.type=='WINDOW')):
            if self.all:bpy.ops.graph.reveal()
            else:bpy.ops.graph.hide(unselected=True)
            bpy.ops.graph.view_all()
        return {'FINISHED'}


class EFF_OT_effect_curve_constant(bpy.types.Operator):
    bl_idname='eiem.effect_curve_constant';bl_label='改为常量';bl_options={'UNDO'}
    bl_description='将此属性改为输入的常量并移出曲线列表；原关键点保留在来源参数中'
    path:StringProperty(options={'HIDDEN'})
    value:FloatProperty(name='常量值')
    def invoke(self,context,event):
        helper=graph_helper(context);record=metadata(helper);path=json.loads(self.path)
        data=live(helper['eff_curve_target'],api().editor(helper['eff_curve_root']),record['identity'],path)
        factor=180/math.pi if api().effect_ui.angle_unit(path+['scalar']) else 1
        self.value=curves.evaluate(data['maxCurve']['m_Curve'],0)*data['scalar']*factor
        return context.window_manager.invoke_props_dialog(self)
    def draw(self,context):
        path=json.loads(self.path);self.layout.label(text=attribute_name(path));self.layout.prop(self,'value',text=labels(path)[1])
        self.layout.label(text='停用该属性的曲线变化，改用这个数值。')
    def execute(self,context):
        try:
            helper=graph_helper(context);editor=api();edit=editor.synchronize(helper['eff_curve_root']);record=metadata(helper)
            path=json.loads(self.path);data=edit.value(record['identity'],path)
            factor=180/math.pi if editor.effect_ui.angle_unit(path+['scalar']) else 1
            data['minMaxState']=0;data['scalar']=self.value/factor
            obj=helper['eff_curve_target'];editor.populate(obj,edit,json.loads(obj['eff_effect_components']));editor.store(helper['eff_curve_root'],edit)
            paths=[p for p in record_paths(edit,record) if p!=path];record.update(version=2,paths=paths);remember(obj,paths)
            project(helper,rows_for(obj,edit,record['identity'],paths),record);return {'FINISHED'}
        except Exception as error:self.report({'ERROR'},str(error));return {'CANCELLED'}


class EFF_PT_effect_curves(bpy.types.Panel):
    bl_label='特效曲线';bl_space_type='GRAPH_EDITOR';bl_region_type='UI';bl_category='EFF 曲线'
    @classmethod
    def poll(cls,context):return graph_helper(context) is not None
    def draw(self,context):
        helper=graph_helper(context);record=metadata(helper);layout=self.layout;editor=api()
        obj=helper['eff_curve_target'];edit=editor.editor(helper['eff_curve_root']);identity=record['identity']
        layout.label(text='发射器：'+obj.name,icon='OUTLINER_OB_EMPTY')
        row=layout.row(align=True);row.operator('eiem.effect_curve_add',text='添加属性…',icon='ADD');row.operator('eiem.effect_curve_collect',text='加入已有曲线')
        row=layout.row(align=True);row.operator('eiem.effect_curve_visibility',text='只看选中曲线');row.operator('eiem.effect_curve_visibility',text='显示全部').all=True
        layout.label(text='Shift 多选曲线；G X / G Y 移动')
        layout.label(text='Ctrl+右键加点；选中点按 X 删除')
        paths=record_paths(edit,record)
        if not paths:layout.label(text='列表为空，点“添加属性…”开始。',icon='INFO')
        groups=list(dict.fromkeys(p[0] for p in paths))
        for module in groups:
            header,body=layout.panel('eff_curve_list_'+module,default_closed=False);header.label(text=module_name(module))
            if not body:continue
            if not live(obj,edit,identity,[module]).get('enabled',True):body.label(text='模块已关闭（仍可编辑曲线）',icon='INFO')
            for path in (p for p in paths if p[0]==module):
                box=body.box();box.label(text=attribute_name(path).split(' / ',1)[-1])
                x,y=labels(path);box.label(text=x+' → '+y)
                if 'minMaxState' in edit.value(identity,path):editor.effect_ui.Draw(editor,obj,edit,identity).prop(box,path+['minMaxState'],'类型')
                if any(r.get('zero') and r.get('attribute')==path for r in record['rows']):box.label(text='倍率为 0，图中为归一化值',icon='INFO')
                row=box.row(align=True)
                op=row.operator('eiem.effect_curve_remove',text='移出列表',icon='X');op.path=json.dumps(path)
                if 'minMaxState' in edit.value(identity,path):
                    op=row.operator('eiem.effect_curve_constant',text='改为常量…');op.path=json.dumps(path)
        if helper.name in _errors:
            editor.effect_ui.hint(layout,_errors[helper.name],icon='ERROR')
            layout.operator('eiem.effect_curve_reset',text='丢弃未同步修改 / 重新载入',icon='FILE_REFRESH')


CLASSES=(EFF_OT_effect_curve_edit,EFF_OT_effect_curve_reset,EFF_OT_effect_curve_add,EFF_OT_effect_curve_remove,EFF_OT_effect_curve_collect,EFF_OT_effect_curve_visibility,EFF_OT_effect_curve_constant,EFF_PT_effect_curves)


def clear_cache(*_):_errors.clear()


def register():
    for cls in CLASSES:bpy.utils.register_class(cls)
    bpy.types.GRAPH_HT_header.append(draw_header)
    bpy.app.timers.register(poll_updates,first_interval=.12)
    bpy.app.timers.register(cleanup_closed,first_interval=.5)


def unregister():
    for timer in (poll_updates,cleanup_closed):
        if bpy.app.timers.is_registered(timer):bpy.app.timers.unregister(timer)
    for helper in list(helpers()):
        try:close_projection(helper)
        except (ValueError,ReferenceError):pass
    bpy.types.GRAPH_HT_header.remove(draw_header);clear_cache()
    for cls in reversed(CLASSES):bpy.utils.unregister_class(cls)

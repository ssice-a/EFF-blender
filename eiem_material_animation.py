"""Material parameter Action tracks in shared EFF animation groups."""
import json
import math
import re
import bpy
from bpy.props import BoolProperty,CollectionProperty,EnumProperty,FloatProperty,FloatVectorProperty,IntProperty,StringProperty,PointerProperty

_addon=None
_CURVE_PATH=re.compile(r'^eiem_editor_values\[(\d+)\]\.(scalar|vector4|color)$')

def sync_settings(clip,context):
    if _addon is not None:
        _addon.shape_animation.sync_settings(clip,context)

def action_curves(material,action=None,slot_handle=None):
    animation=material.animation_data
    if action is None:
        if not animation or not animation.action:return []
        action=animation.action
        slot_handle=getattr(getattr(animation,'action_slot',None),'handle',0)
    if hasattr(action,'fcurves'):return list(action.fcurves)
    slot=next((s for s in action.slots if s.handle==slot_handle and s.target_id_type=='MATERIAL'),None)
    if slot is None:return []  # Removing a slot removes its live channels.
    curves=[]
    for layer in action.layers:
        for strip in layer.strips:
            if hasattr(strip,'channelbag'):
                bag=strip.channelbag(slot)
                if bag:curves.extend(bag.fcurves)
    return curves


def source_action(material,clip,strict=False):
    """Resolve old metadata once; never reinterpret a bound group on UI selection."""
    if clip.action:
        if not clip.source_bound and not hasattr(clip.action,'fcurves'):
            slots=[slot for slot in clip.action.slots if slot.target_id_type=='MATERIAL']
            named=[slot for slot in slots if slot.name_display==material.name]
            if len(named)==1:clip.slot_handle=named[0].handle
            elif len(slots)==1:clip.slot_handle=slots[0].handle
        clip.source_bound=True
        return clip.action
    if clip.source_bound:return None
    index=next((i for i,c in enumerate(material.eiem_material_animations) if c==clip),-1)
    path=clip.source_path or 'eiem_material_animations[%d].%s'%(index,'value' if clip.kind=='FLOAT' else 'vector')
    candidates=[]
    for action in bpy.data.actions:
        handles=[0] if hasattr(action,'fcurves') else [slot.handle for slot in action.slots if slot.target_id_type=='MATERIAL' and (not clip.slot_handle or slot.handle==clip.slot_handle)]
        for handle in handles:
            if any(c.data_path==path for c in action_curves(material,action,handle)):
                candidates.append((action,handle))
    named=[pair for pair in candidates if pair[0].name in {clip.label,clip.identity,clip.runtime_id}]
    if len(named)==1:candidates=named
    if len(candidates)==1:
        clip.action,clip.slot_handle=candidates[0]
        clip.source_bound=True
        return clip.action
    if not candidates:return None  # Deleted curves contribute no exported tracks.
    if strict:
        raise ValueError(material.name+' 的旧动画来源不唯一：'+clip.label+'；请在 EFF 动画中选择源 Action')
    return None


def discover_action_tracks(material,action=None,slot_handle=None):
    if action is None and (not material.animation_data or not material.animation_data.action):
        raise ValueError(material.name+' 没有可读取的材质 Action')
    from . import eiem_material_features as features
    buckets={}
    for curve in action_curves(material,action,slot_handle):
        match=_CURVE_PATH.fullmatch(curve.data_path)
        if not match:continue
        index=int(match.group(1))
        if index>=len(material.eiem_editor_values):continue
        row=material.eiem_editor_values[index];key=row.key
        if key.startswith('eiem_float.'):
            shader_property=key.split('.',1)[1];kind='FLOAT';mode='DIRECT'
        elif key.startswith('eiem_color.'):
            shader_property=key.split('.',1)[1];kind='COLOR';mode='DIRECT'
        elif key.startswith('eiem_value4.'):
            shader_property=key.split('.',1)[1];kind='VECTOR';mode='DIRECT'
        else:continue
        angle=next(((slot,fields) for slot,fields in features.VFX_UV.items()
                    if fields[2]==shader_property),None)
        if angle:
            slot,fields=angle
            shader_property=fields[3]
            if 'eiem_value4.'+shader_property not in material:continue
            kind='VECTOR';mode='UV_ANGLE'
        if shader_property.startswith(('_Use','_Enable')) or shader_property in {
                '_ZWrite','_ZTest','_Cull','_SrcBlend','_DstBlend','_AlphaSrcBlend',
                '_AlphaDstBlend','_SurfaceType','_QueueOffset','_AlphaClip'}:continue
        bucket=buckets.setdefault(shader_property,dict(property=shader_property,kind=kind,
            source_mode=mode,source_key=key,curves={}))
        if bucket['kind']!=kind or bucket['source_mode']!=mode:
            raise ValueError('一个材质参数存在不兼容的动画类型：'+shader_property)
        if curve.array_index in bucket['curves']:
            raise ValueError('材质 Action 有重复参数频道：'+shader_property)
        bucket['curves'][curve.array_index]=curve
    return list(buckets.values())

def animation_identity(tracks):
    first=tracks[0]
    if first['source_mode']=='UV_ANGLE':
        from . import eiem_material_features as features
        texture=next((slot for slot,fields in features.VFX_UV.items()
                      if fields[2]==first['source_key'].split('.',1)[-1]),'MainTex')
        stem='UVRotate_'+texture.removeprefix('_')
    else:
        stem='Material_'+re.sub(r'[^A-Za-z0-9_]','_',first['property']).strip('_')
    used={clip.identity for clip in _addon.shape_animation.all_groups()} if _addon else set()
    candidate=stem or 'MaterialAnimation';suffix=2
    while candidate in used:
        candidate=stem+'_'+str(suffix);suffix+=1
    return candidate

def _name_part(value):
    token=re.sub(r'[^A-Za-z0-9_]','_',str(value))
    token=re.sub(r'_+','_',token).strip('_')
    if not token or token[0].isdigit():token='_'+token
    return token

def runtime_variable(identity,material_id,property_name,component):
    return '$mat_'+'_'.join((_name_part(identity),_name_part(material_id),
        _name_part(property_name),str(int(component))))

def pending_action_tracks(material):
    animation=material.animation_data
    if not animation or not animation.action:return []
    handle=getattr(getattr(animation,'action_slot',None),'handle',0)
    existing={(clip.source_path,clip.property) for clip in material.eiem_material_animations
              if source_action(material,clip)==animation.action and clip.slot_handle==handle}
    return [item for item in discover_action_tracks(material)
            if (next(iter(item['curves'].values())).data_path,item['property']) not in existing]

def register_action_tracks(materials):
    """Attach raw tracks to their source Action, independent of UI selection."""
    if _addon is None:return 0
    shape=_addon.shape_animation
    candidates=[]
    for material in sorted({m for m in materials if m},key=lambda item:item.name.casefold()):
        tracks=pending_action_tracks(material)
        if tracks:candidates.append((material,tracks))
    if not candidates:return 0

    by_action={}
    for material,tracks in candidates:
        by_action.setdefault(material.animation_data.action,[]).append((material,tracks))
    added=0
    for action,entries in by_action.items():
        groups=shape.all_groups()
        target=next((clip for clip in groups
                     if action in shape.source_actions(clip.identity)),None)
        if target:
            identity=target.identity
            settings={field:getattr(target,field) for field in shape.SHARED_FIELDS}
        else:
            identity=animation_identity(entries[0][1])
            frames=[point.co.x for _material,tracks in entries for item in tracks
                    for curve in item['curves'].values() for point in curve.keyframe_points]
            if not frames:raise ValueError('材质 Action 没有关键帧')
            settings=dict(label=action.name,enabled=True,start=min(frames),
                end=max(max(frames),min(frames)+1),loop=True,autoplay=False,
                speed=1,speed_variable='',sample_rate=60,hotkey='')
        for material,tracks in entries:
            added+=import_action(material,identity,settings,tracks=tracks)
    return added


class EFF_PG_material_animation(bpy.types.PropertyGroup):
    identity:StringProperty()
    runtime_id:StringProperty(options={'HIDDEN'})
    speed_variable:StringProperty(options={'HIDDEN'})
    runtime_variables:StringProperty(options={'HIDDEN'})
    action:PointerProperty(name='源 Action',type=bpy.types.Action)
    source_bound:BoolProperty(options={'HIDDEN'})
    slot_handle:IntProperty(options={'HIDDEN'})
    label:StringProperty(name='名称',default='材质动画',update=sync_settings)
    enabled:BoolProperty(name='导出',default=True,update=sync_settings)
    track_enabled:BoolProperty(name='轨道启用',default=True)
    track_label:StringProperty(name='轨道名称')
    property:StringProperty(name='Shader 参数')
    source_key:StringProperty(options={'HIDDEN'})
    source_path:StringProperty(options={'HIDDEN'})
    source_mode:StringProperty(options={'HIDDEN'})
    kind:EnumProperty(items=(('FLOAT','数值',''),('COLOR','颜色',''),('VECTOR','四维向量','')))
    value:FloatProperty(name='动画值',description='在此数值上按 I 插入关键帧')
    vector:FloatVectorProperty(name='动画值',size=4,description='在此数值上按 I 插入关键帧')
    start:FloatProperty(name='起始帧',default=1,update=sync_settings)
    end:FloatProperty(name='结束帧',default=60,update=sync_settings)
    loop:BoolProperty(name='循环',default=True,update=sync_settings)
    autoplay:BoolProperty(name='初始自动播放',default=False,update=sync_settings)
    hotkey:StringProperty(name='游戏快捷键',description='例如 F6；播放/停止',update=sync_settings)
    advanced:BoolProperty(name='更多设置',default=False)
    speed:FloatProperty(name='默认倍率',default=1,min=.001,update=sync_settings)
    sample_rate:IntProperty(name='采样率',default=60,min=1,update=sync_settings)

def import_action(material,identity='',settings=None,scene=None,tracks=None):
    discovered=discover_action_tracks(material) if tracks is None else list(tracks)
    if not discovered:raise ValueError(material.name+' 的 Action 中没有可导出的材质参数曲线')
    scene=scene or bpy.context.scene
    action=material.animation_data.action
    handle=getattr(material.animation_data.action_slot,'handle',0)
    if not identity:identity=animation_identity(discovered)
    frames=[point.co.x for item in discovered for curve in item['curves'].values()
            for point in curve.keyframe_points]
    if not frames:raise ValueError('材质 Action 没有关键帧')
    start=min(frames);end=max(max(frames),start+1)
    shared=dict(settings or dict(label=action.name,enabled=True,start=start,end=end,
        loop=True,autoplay=False,speed=1,speed_variable='',sample_rate=60,hotkey=''))
    if settings:
        shared['label']=settings.get('label',action.name)
    added=0
    for item in discovered:
        if any(clip.identity==identity and source_action(material,clip)==action and clip.slot_handle==handle and clip.source_path==next(iter(item['curves'].values())).data_path
               and clip.property==item['property'] for clip in material.eiem_material_animations):continue
        clip=material.eiem_material_animations.add();clip.action=action;clip.source_bound=True;clip.identity=identity;clip.runtime_id=identity
        clip.label=shared['label'];clip.enabled=shared['enabled'];clip.start=shared['start'];clip.end=shared['end']
        clip.loop=shared['loop'];clip.autoplay=shared['autoplay'];clip.speed=shared['speed']
        clip.speed_variable=shared.get('speed_variable','');clip.sample_rate=shared['sample_rate'];clip.hotkey=shared['hotkey']
        clip.property=item['property'];clip.kind=item['kind'];clip.source_mode=item['source_mode'];clip.source_key=item['source_key']
        clip.source_path=next(iter(item['curves'].values())).data_path
        clip.track_label=item['source_key'].split('.',1)[-1]
        if item['source_mode']=='UV_ANGLE':clip.track_label=clip.track_label+' → '+item['property']
        animation=material.animation_data
        if animation and animation.action and hasattr(animation.action,'slots'):
            clip.slot_handle=getattr(animation.action_slot,'handle',0)
        added+=1
    if not added:raise ValueError('此 Action 曲线已加入该动画组')
    material.eiem_material_animation_index=len(material.eiem_material_animations)-1
    return added


def remove_track(material,identity,property_name=None):
    """Retire binding metadata while keeping source editor keyframes intact."""
    collections=[]
    bindings={(source_action(material,clip),clip.slot_handle) for clip in material.eiem_material_animations}
    animation=material.animation_data
    if animation and animation.action:bindings.add((animation.action,getattr(animation.action_slot,'handle',0)))
    for action,handle in bindings:
        if action is None:continue
        if hasattr(action,'fcurves'):collections.append(action.fcurves)
        else:
            slot=next((s for s in action.slots if s.handle==handle),None)
            if slot:
                for layer in action.layers:
                    for strip in layer.strips:
                        if hasattr(strip,'channelbag'):
                            bag=strip.channelbag(slot)
                            if bag:collections.append(bag.fcurves)
    pattern=re.compile(r'^eiem_material_animations\[(\d+)\](\..+)$')
    for index in range(len(material.eiem_material_animations)-1,-1,-1):
        clip=material.eiem_material_animations[index]
        if clip.identity!=identity or (property_name is not None and clip.property!=property_name):continue
        for curves in collections:
            for curve in list(curves):
                match=pattern.fullmatch(curve.data_path)
                if not match:continue
                old_index=int(match.group(1))
                if old_index==index:curves.remove(curve)
                elif old_index>index:curve.data_path='eiem_material_animations[%d]%s'%(old_index-1,match.group(2))
        material.eiem_material_animations.remove(index)
    material.eiem_material_animation_index=min(material.eiem_material_animation_index,
        max(0,len(material.eiem_material_animations)-1))


def action_tracks(material,strict=True):
    """Read live curves; stored rows supply playback settings, never a whitelist.

    Keep old collection-backed animation values readable without requiring
    their metadata to be mirrored into the native Action a second time.
    """
    clips=list(material.eiem_material_animations)
    result=[];bindings={}
    for clip in clips:
        if strict and not clip.enabled:continue
        action=source_action(material,clip,strict=strict)
        if action:bindings.setdefault((action,clip.slot_handle),[]).append(clip)
    for (action,handle),members in bindings.items():
        for track in discover_action_tracks(material,action,handle):
            path=next(iter(track['curves'].values())).data_path
            clip=next((c for c in members if c.source_key==track['source_key'] and c.property==track['property']),members[0])
            result.append((clip,dict(track,source_path=path)))
        curves=action_curves(material,action,handle)
        for clip in members:
            if clip.source_path:continue
            index=clips.index(clip)
            path='eiem_material_animations[%d].%s'%(index,'value' if clip.kind=='FLOAT' else 'vector')
            source={c.array_index:c for c in curves if c.data_path==path}
            if source:
                result.append((clip,dict(property=clip.property,kind=clip.kind,
                    source_mode='DIRECT',source_key='',source_path=path,curves=source)))
    return result


def export_clips(material,mid,addon,include_hotkeys=True):
    clips=[(c,track) for c,track in action_tracks(material) if c.enabled]
    if not clips:return []
    scene=bpy.context.scene;fps=scene.render.fps/scene.render.fps_base;result=[]
    grouped={}
    for c,track in clips:
        cid=c.runtime_id or c.identity
        if not cid:raise ValueError(c.label+' 缺少 EFF 动画身份')
        if not math.isfinite(c.end-c.start) or c.end<=c.start:raise ValueError(c.label+' 的帧范围无效')
        kind=track['kind'];property_name=track['property']
        count=max(2,math.ceil((c.end-c.start)/fps*c.sample_rate)+1);tracks=[];channels=1 if kind=='FLOAT' else 4
        source=track['curves']
        if track['source_mode']=='UV_ANGLE':
            angles=[float(source[0].evaluate(c.start+(c.end-c.start)*i/(count-1))) for i in range(count)]
            samples_by_component=[[],[],[],[]]
            for angle in angles:
                radians=math.radians(angle);co=math.cos(radians);si=math.sin(radians)
                for component,value in enumerate((co,si,-si,co)):samples_by_component[component].append(value)
        else:
            required=range(channels)
            match=_CURVE_PATH.fullmatch(track['source_path'])
            if match:
                row=material.eiem_editor_values[int(match.group(1))]
                value=getattr(row,match.group(2))
                defaults=[value] if channels==1 else list(value)
            else:defaults=[c.value] if channels==1 else list(c.vector)
            samples_by_component=[
                [float(source[component].evaluate(c.start+(c.end-c.start)*i/(count-1))) for i in range(count)]
                if component in source else [float(defaults[component])]*count
                for component in required]
        variables=json.loads(c.runtime_variables or '{}') if c.property==property_name and c.kind==kind else {}
        for component,samples in enumerate(samples_by_component):
            if not all(math.isfinite(v) and abs(v)<=3.402823466e38 for v in samples):raise ValueError(c.label+' 存在无效数值')
            variable=variables.get(str(component)) or runtime_variable(cid,mid,property_name,component)
            tracks.append(dict(variable=variable,material=mid,property=property_name,kind=kind,component=component,samples=samples))
        row=grouped.get(cid)
        if row is None:
            row=dict(id=cid,label=c.label,key=c.hotkey if include_hotkeys else '',duration=(c.end-c.start)/fps,
                speed=c.speed,speedVariable=c.speed_variable or None,loop=c.loop,autoplay=c.autoplay,
                sample_rate=int(c.sample_rate),targets=[]);grouped[cid]=row
        elif any(row[key]!=value for key,value in (('duration',(c.end-c.start)/fps),('speed',c.speed),
                ('loop',c.loop),('autoplay',c.autoplay),('sample_rate',int(c.sample_rate)))):
            raise ValueError('同一 EFF 动画组内的材质轨道播放参数不一致：'+c.label)
        row['targets'].extend(tracks)
    return list(grouped.values())


CLASSES=(EFF_PG_material_animation,)
def register(addon=None):
    global _addon
    _addon=addon
    for cls in CLASSES:bpy.utils.register_class(cls)
    bpy.types.Material.eiem_material_animations=CollectionProperty(type=EFF_PG_material_animation)
    bpy.types.Material.eiem_material_animation_index=IntProperty(min=0)
def unregister():
    del bpy.types.Material.eiem_material_animation_index;del bpy.types.Material.eiem_material_animations
    for cls in reversed(CLASSES):bpy.utils.unregister_class(cls)

"""Shape Keys Action authoring and offline weight sampling."""
import math
import re
import uuid
from types import SimpleNamespace
import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty, StringProperty

SHARED_FIELDS=('label','enabled','start','end','loop','autoplay','speed','sample_rate','hotkey')
_syncing=False

def sync_settings(clip, context):
    """Keep a shared group editable from any member without sharing its Action."""
    global _syncing
    if _syncing or not clip.identity: return
    _syncing=True
    try:
        clip.name=clip.label
        for mesh in bpy.data.meshes:
            for other in getattr(mesh,'eiem_shape_animations',()):
                if other.identity!=clip.identity or other.as_pointer()==clip.as_pointer(): continue
                for field in SHARED_FIELDS: setattr(other,field,getattr(clip,field))
                other.name=clip.label
    finally: _syncing=False

def group_members(identity):
    """One member per Mesh datablock; linked Object instances share its tracks."""
    members=[];seen=set()
    for obj in sorted(bpy.data.objects,key=lambda o:o.name):
        if obj.type!='MESH' or obj.data.as_pointer() in seen: continue
        seen.add(obj.data.as_pointer())
        clips=[c for c in getattr(obj.data,'eiem_shape_animations',()) if c.identity==identity]
        if len(clips)>1: raise ValueError('网格包含重复播放组：'+obj.name)
        if clips: members.append((obj,clips[0]))
    return members

def all_groups():
    groups={}
    for mesh in bpy.data.meshes:
        for clip in getattr(mesh,'eiem_shape_animations',()):
            if clip.identity and clip.identity not in groups: groups[clip.identity]=clip
    return sorted(groups.values(),key=lambda c:(c.label.casefold(),c.identity))

def active_clip(context):
    groups=all_groups();identity=context.scene.eiem_animation_group
    for clip in groups:
        if clip.identity==identity: return clip
    obj=context.object
    if not identity and obj and obj.type=='MESH':
        clips=obj.data.eiem_shape_animations;index=obj.data.eiem_shape_animation_index
        if 0<=index<len(clips): return clips[index]
    return groups[0] if groups else None

def set_active_group(context,identity):
    if not any(c.identity==identity for c in all_groups()): raise ValueError('播放组已删除')
    context.scene.eiem_animation_group=identity

def member_clip(identity,obj):
    return next((c for c in obj.data.eiem_shape_animations if c.identity==identity),None)

def unique_meshes(objects):
    result=[];seen=set()
    for obj in objects:
        if obj.type!='MESH' or obj.data.as_pointer() in seen: continue
        seen.add(obj.data.as_pointer());result.append(obj)
    return result

def current_descriptor(obj):
    if not obj.data.get('eiem_section'): raise ValueError(obj.name+' 没有 EFF 来源绑定')
    if obj.data.library: raise ValueError(obj.name+' 是只读链接网格，请先建立本地副本')
    keys=obj.data.shape_keys;animation=keys.animation_data if keys else None
    if not animation or not animation.action: raise ValueError(obj.name+'：请先给形态键权重插入关键帧')
    local=SimpleNamespace(action=animation.action,slot_handle=getattr(getattr(animation,'action_slot',None),'handle',0))
    found=shape_curves(obj,local)
    if not found: raise ValueError(obj.name+' 的当前 Action 没有形态键权重曲线')
    frames=[p.co.x for c in found.values() for p in c.keyframe_points]
    if not frames: raise ValueError(obj.name+' 的动作没有关键帧')
    return local,found,min(frames),max(frames)

def append_member(obj,identity,descriptor,settings):
    clip=obj.data.eiem_shape_animations.add();clip.identity=identity
    clip.action=descriptor[0].action;clip.slot_handle=descriptor[0].slot_handle
    for field,value in settings.items(): setattr(clip,field,value)
    clip.name=clip.label
    for name in descriptor[1]: item=clip.channels.add();item.name=name;item.enabled=True
    obj.data.eiem_shape_animation_index=len(obj.data.eiem_shape_animations)-1
    return clip

def create_group(objects):
    global _syncing
    members=unique_meshes(objects)
    if not members: raise ValueError('请先选择带形态键动画的网格')
    # Validate the entire selection before touching any member's settings.
    descriptors=[current_descriptor(o) for o in members]
    start=min(d[2] for d in descriptors);end=max(d[3] for d in descriptors)
    settings=dict(label=descriptors[0][0].action.name,enabled=True,start=start,end=max(end,start+1),loop=True,autoplay=False,speed=1,sample_rate=60,hotkey='')
    identity=uuid.uuid4().hex[:16];added=[];_syncing=True
    try:
        for obj,descriptor in zip(members,descriptors):
            added.append(obj);append_member(obj,identity,descriptor,settings)
        return members[0].data.eiem_shape_animations[-1]
    except Exception:
        for obj in added: remove_member(obj,identity)
        raise
    finally: _syncing=False

def add_members(clip,objects):
    global _syncing
    current={o.data.as_pointer() for o,c in group_members(clip.identity)}
    members=[o for o in unique_meshes(objects) if o.data.as_pointer() not in current]
    descriptors=[current_descriptor(o) for o in members]
    settings={field:getattr(clip,field) for field in SHARED_FIELDS};added=[];_syncing=True
    try:
        for obj,descriptor in zip(members,descriptors):
            added.append(obj);append_member(obj,clip.identity,descriptor,settings)
        return len(members)
    except Exception:
        for obj in added: remove_member(obj,clip.identity)
        raise
    finally: _syncing=False

def remove_member(obj,identity):
    for index in range(len(obj.data.eiem_shape_animations)-1,-1,-1):
        if obj.data.eiem_shape_animations[index].identity==identity: obj.data.eiem_shape_animations.remove(index)
    obj.data.eiem_shape_animation_index=min(obj.data.eiem_shape_animation_index,max(0,len(obj.data.eiem_shape_animations)-1))

def remove_group(identity):
    for obj,clip in group_members(identity): remove_member(obj,identity)

def validate_export_members(objects):
    selected={o.data.as_pointer() for o in objects};groups={c.identity:c.label for o in objects for c in o.data.eiem_shape_animations}
    for identity,label in groups.items():
        missing=[o.name for o,c in group_members(identity) if o.data.as_pointer() not in selected]
        if missing: raise ValueError('播放组“'+label+'”必须一起导出全部网格，缺少：'+', '.join(missing))

def curves(action, slot_handle):
    if action is None: return []
    if hasattr(action, 'fcurves'): return list(action.fcurves)
    slot=next((s for s in action.slots if s.handle==slot_handle and s.target_id_type=='KEY'),None)
    if slot is None: raise ValueError('请选择该形态键动画的 Key 插槽')
    result=[]
    for layer in action.layers:
        for strip in layer.strips:
            if hasattr(strip,'channelbag'):
                bag=strip.channelbag(slot)
                if bag: result.extend(bag.fcurves)
    return result

def shape_curves(obj, clip):
    keys=obj.data.shape_keys
    if not keys: raise ValueError('当前网格没有形态键')
    found={}
    for curve in curves(clip.action,clip.slot_handle):
        matches=[k.name for k in keys.key_blocks if k!=keys.reference_key and curve.data_path==k.path_from_id('value') and curve.array_index==0]
        if matches:
            if matches[0] in found: raise ValueError('动作中同一形态键存在重复曲线：'+matches[0])
            found[matches[0]]=curve
    return found

def compatible_action(self, action):
    return any(s.target_id_type=='KEY' for s in action.slots) if hasattr(action,'slots') else any(c.data_path.startswith('key_blocks[') for c in action.fcurves)

def add_current(obj):
    return create_group([obj])

def export_clips(obj, addon, include_hotkeys=True):
    result=[];keys=obj.data.shape_keys
    for clip in obj.data.eiem_shape_animations:
        if not clip.enabled: continue
        if not clip.action: raise ValueError('形态动画缺少 Action：'+clip.label)
        if keys.animation_data and (keys.animation_data.drivers or any(not t.mute for t in keys.animation_data.nla_tracks)):
            raise ValueError('形态动画暂不导出驱动器或 NLA，请先烘焙为独立 Action')
        if clip.end<=clip.start or not math.isfinite(clip.speed) or clip.speed<=0: raise ValueError('动画范围或速度无效：'+clip.label)
        fps=bpy.context.scene.render.fps/bpy.context.scene.render.fps_base
        duration=(clip.end-clip.start)/fps;count=max(2,math.ceil(duration*clip.sample_rate)+1)
        found=shape_curves(obj,clip);channels={}
        for item in clip.channels:
            if not item.enabled: continue
            if item.name not in found: raise ValueError('动画频道不在所选 Action 中：'+item.name)
            key=keys.key_blocks[item.name];control=next((c for c in obj.data.eiem_shape_controls if c.shape==item.name),None)
            if control is None:control=addon.add_shape_control(obj,item.name)
            if not control.enabled:raise ValueError('动画频道的形态控制已禁用：'+item.name)
            values=[float(found[item.name].evaluate(clip.start+(clip.end-clip.start)*i/(count-1))) for i in range(count)]
            if not all(math.isfinite(v) and control.minimum-1e-6<=v<=control.maximum+1e-6 for v in values): raise ValueError('动画权重超出形态键范围：'+item.name)
            channels[addon.shape_channel_name(obj,key)]=values
        if not channels: raise ValueError('播放组没有启用的动画频道：'+clip.label)
        result.append(dict(id=clip.identity,label=clip.label,key=clip.hotkey if include_hotkeys else '',duration=duration,speed=clip.speed,loop=clip.loop,autoplay=clip.autoplay,channels=channels))
    return result

class EFF_PG_animation_channel(bpy.types.PropertyGroup):
    enabled: BoolProperty(name='参与播放',default=True)
class EFF_PG_shape_animation(bpy.types.PropertyGroup):
    identity: StringProperty()
    label: StringProperty(name='名称',default='形态动画',update=sync_settings)
    enabled: BoolProperty(name='导出',default=True,update=sync_settings)
    action: PointerProperty(name='形态 Action',type=bpy.types.Action,poll=compatible_action)
    slot_handle: IntProperty(name='Action 插槽')
    start: FloatProperty(name='起始帧',default=1,update=sync_settings)
    end: FloatProperty(name='结束帧',default=60,update=sync_settings)
    loop: BoolProperty(name='循环播放',default=True,update=sync_settings)
    autoplay: BoolProperty(name='初始自动播放',default=False,update=sync_settings)
    speed: FloatProperty(name='默认倍率',default=1,min=.001,update=sync_settings)
    sample_rate: FloatProperty(name='采样率',default=60,min=1,update=sync_settings)
    advanced: BoolProperty(name='更多设置',default=False)
    expanded: BoolProperty(name='展开网格频道',default=True)
    hotkey: StringProperty(name='游戏快捷键',update=sync_settings)
    channels: CollectionProperty(type=EFF_PG_animation_channel)

class EFF_OT_shape_animation(bpy.types.Operator):
    bl_idname='eiem.shape_animation';bl_label='从选中网格动画创建播放组';bl_options={'REGISTER','UNDO'}
    remove: BoolProperty(default=False)
    refresh: BoolProperty(default=False)
    use_range: BoolProperty(default=False)
    target_name: StringProperty()
    def execute(self,context):
        try:
            clip=active_clip(context)
            if self.remove:
                stop_preview(context)
                if not clip: raise ValueError('请先选择播放组')
                remove_group(clip.identity);groups=all_groups();context.scene.eiem_animation_group=groups[0].identity if groups else ''
            elif self.use_range:
                if not clip: raise ValueError('请先选择播放组')
                frames=[p.co.x for member,c in group_members(clip.identity) for curve in shape_curves(member,c).values() for p in curve.keyframe_points]
                if not frames:raise ValueError('所选动作没有形态关键帧')
                clip.start=min(frames);clip.end=max(max(frames),clip.start+1)
            elif self.refresh:
                if not clip: raise ValueError('请先选择播放组')
                obj=next((o for o,c in group_members(clip.identity) if o.name==self.target_name),None) if self.target_name else context.object
                if not obj or obj.type!='MESH': raise ValueError('请选择要读取频道的组内网格')
                local=member_clip(clip.identity,obj)
                if not local: raise ValueError('网格不在当前播放组')
                found=shape_curves(obj,local);local.channels.clear()
                for name in found: item=local.channels.add();item.name=name
            else:
                stop_preview(context)
                clip=create_group(context.selected_objects);set_active_group(context,clip.identity)
            return {'FINISHED'}
        except (ValueError,IndexError,AttributeError) as error: self.report({'ERROR'},str(error));return {'CANCELLED'}

class EFF_OT_animation_key(bpy.types.Operator):
    bl_idname='eiem.animation_key';bl_label='录制动画按键';bl_options={'REGISTER','UNDO'}
    def invoke(self,context,event):
        clip=active_clip(context)
        if not clip: return {'CANCELLED'}
        self.identity=clip.identity;context.window_manager.modal_handler_add(self);context.workspace.status_text_set(text='按一个游戏快捷键；Esc 取消');return {'RUNNING_MODAL'}
    def modal(self,context,event):
        if event.value!='PRESS' or event.type in {'LEFT_CTRL','RIGHT_CTRL','LEFT_ALT','RIGHT_ALT','LEFT_SHIFT','RIGHT_SHIFT'}:return {'RUNNING_MODAL'}
        if event.type=='ESC':context.workspace.status_text_set(text=None);return {'CANCELLED'}
        try:
            key=_addon.switch_key_from_event(event);key=_addon.validate_switch_key(key)
            clip=next((c for c in all_groups() if c.identity==self.identity),None)
            if not clip: context.workspace.status_text_set(text=None);return {'CANCELLED'}
            clip.hotkey=key
        except (ValueError,IndexError) as error:self.report({'WARNING'},str(error));return {'RUNNING_MODAL'}
        context.workspace.status_text_set(text=None);return {'FINISHED'}

class EFF_UL_shape_animations(bpy.types.UIList):
    def draw_item(self,context,layout,data,item,icon,active_data,active_propname,index):layout.prop(item,'label',text='',emboss=False,icon='ACTION')

class EFF_OT_animation_group_select(bpy.types.Operator):
    bl_idname='eiem.animation_group_select';bl_label='选择形态动画组'
    identity: StringProperty()
    def execute(self,context):
        try:set_active_group(context,self.identity);return {'FINISHED'}
        except ValueError as error:self.report({'ERROR'},str(error));return {'CANCELLED'}

class EFF_OT_animation_members(bpy.types.Operator):
    bl_idname='eiem.animation_members';bl_label='形态动画组网格';bl_options={'REGISTER','UNDO'}
    operation: EnumProperty(items=[('ADD','加入选中网格',''),('SELECT','选中全部成员',''),('EDIT','编辑此网格',''),('REMOVE','移出播放组','')])
    target_name: StringProperty()
    def execute(self,context):
        try:
            clip=active_clip(context)
            if not clip: raise ValueError('请先选择播放组')
            members=group_members(clip.identity)
            target=next((o for o,c in members if o.name==self.target_name),None)
            if self.operation=='ADD':
                stop_preview(context)
                count=add_members(clip,context.selected_objects)
                self.report({'INFO'},'已加入 '+str(count)+' 个网格；共用当前播放范围')
            elif self.operation=='SELECT':
                targets={o.data.as_pointer() for o,c in members}
                for o in context.view_layer.objects: o.select_set(o.type=='MESH' and o.data.as_pointer() in targets)
            elif self.operation=='EDIT':
                if not target or target.name not in context.view_layer.objects: raise ValueError('该网格不在当前视图层')
                target.select_set(True);context.view_layer.objects.active=target
                target.data.eiem_shape_animation_index=next(i for i,c in enumerate(target.data.eiem_shape_animations) if c.identity==clip.identity)
            elif self.operation=='REMOVE':
                if not target: raise ValueError('播放组网格已删除')
                stop_preview(context);remove_member(target,clip.identity)
            return {'FINISHED'}
        except (ValueError,IndexError,AttributeError,StopIteration) as error: self.report({'ERROR'},str(error));return {'CANCELLED'}

def stop_preview(context):
    cls=EFF_OT_animation_preview;saved=cls._saved
    if not saved: return
    if context.screen and context.screen.is_animation_playing: bpy.ops.screen.animation_cancel(restore_frame=False)
    for record in saved['members']:
        keys=record['keys'];anim=keys.animation_data
        if anim:
            anim.action=record['action']
            if record['action'] and record['slot']: anim.action_slot=record['slot']
        if not record['had_animation']: keys.animation_data_clear()
    scene=saved['scene'];scene.frame_start=saved['start'];scene.frame_end=saved['end'];scene.frame_set(saved['frame'],subframe=saved['subframe'])
    for record in saved['members']:
        for key,value in record['values']: key.value=value
    bpy.data.actions.remove(saved['preview']);cls._saved=None

def start_preview(context,clip):
    cls=EFF_OT_animation_preview
    if cls._saved: raise ValueError('请先停止当前插件预览')
    if clip.end<=clip.start or not math.isfinite(clip.speed) or clip.speed<=0: raise ValueError('动画范围或速度无效')
    members=group_members(clip.identity);planned=[];seen=set()
    # Check every member before replacing the first Action. An invalid slot or
    # removed channel must not leave a partial preview in the author's scene.
    for obj,member in members:
        keys=obj.data.shape_keys;found=shape_curves(obj,member);selected=[i.name for i in member.channels if i.enabled]
        if not selected or any(n not in found for n in selected): raise ValueError(obj.name+'：请先选择有效动画频道')
        if keys.as_pointer() in seen: raise ValueError('多个动画成员共用 Shape Keys 数据，请先建立独立副本')
        seen.add(keys.as_pointer());planned.append((keys,found,selected))
    if not planned: raise ValueError('播放组没有网格')
    preview=bpy.data.actions.new('EFF 形态动画组预览');scene=context.scene
    saved=dict(scene=scene,start=scene.frame_start,end=scene.frame_end,frame=scene.frame_current,subframe=scene.frame_subframe,preview=preview,members=[])
    cls._saved=saved
    try:
        strip=preview.layers.new('预览').strips.new(type='KEYFRAME')
        count=max(2,math.ceil((clip.end-clip.start)/clip.speed)+1)
        for keys,found,selected in planned:
            had_animation=bool(keys.animation_data);keys.animation_data_create();anim=keys.animation_data
            saved['members'].append(dict(keys=keys,action=anim.action,slot=getattr(anim,'action_slot',None),had_animation=had_animation,values=[(k,k.value) for k in keys.key_blocks]))
            slot=preview.slots.new(id_type='KEY',name=keys.name);bag=strip.channelbag(slot,ensure=True)
            for name in selected:
                curve=bag.fcurves.new(data_path=keys.key_blocks[name].path_from_id('value'),index=0);curve.keyframe_points.add(count)
                for i,p in enumerate(curve.keyframe_points):
                    p.co=(1+(clip.end-clip.start)/clip.speed*i/(count-1),found[name].evaluate(clip.start+(clip.end-clip.start)*i/(count-1)));p.interpolation='LINEAR'
                curve.update()
            anim.action=preview;anim.action_slot=slot
        scene.frame_start=1;scene.frame_end=math.ceil(1+(clip.end-clip.start)/clip.speed);scene.frame_set(1)
        if context.screen and not context.screen.is_animation_playing: bpy.ops.screen.animation_play()
    except Exception:
        stop_preview(context);raise

class EFF_OT_animation_preview(bpy.types.Operator):
    bl_idname='eiem.animation_preview';bl_label='预览形态动画'
    stop: BoolProperty(default=False)
    _saved=None
    def execute(self,context):
        try:
            if self.stop:
                stop_preview(context)
                return {'FINISHED'}
            clip=active_clip(context)
            if not clip: raise ValueError('请先选择播放组')
            start_preview(context,clip)
            return {'FINISHED'}
        except (ValueError,IndexError,AttributeError,StopIteration) as error:self.report({'ERROR'},str(error));return {'CANCELLED'}

class EFF_PT_shape_animation(bpy.types.Panel):
    bl_label='形态动画组管理';bl_idname='EFF_PT_shape_animation';bl_space_type='VIEW_3D';bl_region_type='UI';bl_category='EFF'
    @classmethod
    def poll(cls,context):return context.scene is not None
    def draw(self,context):
        l=self.layout;l.operator('eiem.shape_animation',icon='ADD');groups=all_groups();c=active_clip(context)
        listing=l.box();listing.label(text='全部播放组（'+str(len(groups))+'）')
        if not c: listing.label(text='多选带形态动画的网格，创建播放组');return
        for group in groups:
            row=listing.row(align=True)
            op=row.operator('eiem.animation_group_select',text=group.label,icon='ACTION',depress=group.identity==c.identity);op.identity=group.identity
            row.label(text=str(len(group_members(group.identity)))+' 网格')
            row.label(text=group.hotkey or '无按键')
        l.prop(c,'label');l.prop(c,'enabled')
        members=group_members(c.identity);box=l.box();box.label(text='本组网格与形态频道（'+str(len(members))+'）')
        for member,local in members:
            detail=box.box();row=detail.row(align=True);row.prop(local,'expanded',text=member.name,icon='TRIA_DOWN' if local.expanded else 'TRIA_RIGHT',emboss=False)
            op=row.operator('eiem.animation_members',text='',icon='GREASEPENCIL');op.operation='EDIT';op.target_name=member.name
            op=row.operator('eiem.animation_members',text='',icon='X');op.operation='REMOVE';op.target_name=member.name
            if not local.expanded: continue
            detail.prop(local,'action')
            if local.action and hasattr(local.action,'slots'):
                for slot in local.action.slots:
                    if slot.target_id_type=='KEY':
                        row=detail.row();row.label(text=slot.name_display)
                        op=row.operator('eiem.animation_slot',text='使用此插槽',depress=slot.handle==local.slot_handle);op.handle=slot.handle;op.target_name=member.name
            op=detail.operator('eiem.shape_animation',text='读取动作频道',icon='FILE_REFRESH');op.refresh=True;op.target_name=member.name
            for item in local.channels: detail.prop(item,'enabled',text=item.name)
            overlaps=[]
            channels={i.name for i in local.channels if i.enabled}
            for other in member.data.eiem_shape_animations:
                if other.identity!=c.identity and other.enabled and channels&{i.name for i in other.channels if i.enabled}: overlaps.append(other.label)
            if overlaps: detail.label(text='同频道组互斥：'+', '.join(overlaps),icon='INFO')
        row=box.row(align=True);row.operator('eiem.animation_members',text='加入选中网格',icon='ADD').operation='ADD';row.operator('eiem.animation_members',text='选中全部成员',icon='RESTRICT_SELECT_OFF').operation='SELECT'
        r=l.row(align=True);r.prop(c,'start');r.prop(c,'end');l.operator('eiem.shape_animation',text='使用动作范围').use_range=True
        l.separator();l.label(text='全组共用播放设置')
        l.prop(c,'hotkey');l.operator('eiem.animation_key');l.prop(c,'loop');l.prop(c,'autoplay');l.prop(c,'advanced')
        if c.advanced:l.prop(c,'speed');l.prop(c,'sample_rate')
        r=l.row();r.operator('eiem.animation_preview',text='预览',icon='PLAY');r.operator('eiem.animation_preview',text='停止',icon='PAUSE').stop=True
        l.operator('eiem.shape_animation',text='删除播放组',icon='REMOVE').remove=True
        l.label(text='网格可加入多组；同频道播放新组时替换旧组')
class EFF_OT_animation_slot(bpy.types.Operator):
    bl_idname='eiem.animation_slot';bl_label='选择形态动画插槽';bl_options={'REGISTER','UNDO'}
    handle:IntProperty()
    target_name:StringProperty()
    def execute(self,context):
        clip=active_clip(context)
        obj=(next((o for o,c in group_members(clip.identity) if o.name==self.target_name),None) if self.target_name else context.object) if clip else None
        local=member_clip(clip.identity,obj) if obj and obj.type=='MESH' and clip else None
        if not local:return {'CANCELLED'}
        local.slot_handle=self.handle;return {'FINISHED'}

_classes=(EFF_PG_animation_channel,EFF_PG_shape_animation,EFF_OT_shape_animation,EFF_OT_animation_key,EFF_UL_shape_animations,EFF_OT_animation_group_select,EFF_OT_animation_members,EFF_OT_animation_preview,EFF_OT_animation_slot,EFF_PT_shape_animation)
def register(addon):
    global _addon
    _addon=addon
    for cls in _classes:bpy.utils.register_class(cls)
    bpy.types.Mesh.eiem_shape_animations=CollectionProperty(type=EFF_PG_shape_animation)
    bpy.types.Mesh.eiem_shape_animation_index=IntProperty(default=0,min=0)
    bpy.types.Scene.eiem_animation_group=StringProperty(name='当前形态动画组')
def unregister():
    if EFF_OT_animation_preview._saved:
        bpy.ops.eiem.animation_preview(stop=True)
    del bpy.types.Mesh.eiem_shape_animation_index;del bpy.types.Mesh.eiem_shape_animations
    del bpy.types.Scene.eiem_animation_group
    for cls in reversed(_classes):bpy.utils.unregister_class(cls)

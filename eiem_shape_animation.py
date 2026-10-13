"""Shape Keys Action authoring and offline weight sampling."""
import math
import re
import uuid
import bpy
from bpy.props import BoolProperty, CollectionProperty, FloatProperty, IntProperty, PointerProperty, StringProperty

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
    keys=obj.data.shape_keys;animation=keys.animation_data if keys else None
    if not animation or not animation.action: raise ValueError('请先给形态键权重插入关键帧')
    clip=obj.data.eiem_shape_animations.add();clip.identity=uuid.uuid4().hex[:16];clip.action=animation.action
    clip.slot_handle=getattr(getattr(animation,'action_slot',None),'handle',0)
    clip.label=clip.action.name;clip.name=clip.label
    try:
        found=shape_curves(obj,clip)
        if not found: raise ValueError('当前 Action 没有形态键权重曲线；物体变换动画不能用于此处')
        frames=[p.co.x for c in found.values() for p in c.keyframe_points]
        if not frames: raise ValueError('动作没有关键帧')
        clip.start=min(frames);clip.end=max(frames)
        if clip.end<=clip.start: clip.end=clip.start+1
        for name in found: item=clip.channels.add();item.name=name;item.enabled=True
        obj.data.eiem_shape_animation_index=len(obj.data.eiem_shape_animations)-1
        return clip
    except Exception:
        obj.data.eiem_shape_animations.remove(len(obj.data.eiem_shape_animations)-1);raise

def export_clips(obj, addon, include_hotkeys=True):
    result=[];occupied=set();keys=obj.data.shape_keys
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
            if item.name in occupied: raise ValueError('多个播放组控制同一频道：'+item.name)
            occupied.add(item.name);key=keys.key_blocks[item.name];control=next((c for c in obj.data.eiem_shape_controls if c.shape==item.name),None)
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
    label: StringProperty(name='名称',default='形态动画')
    enabled: BoolProperty(name='导出',default=True)
    action: PointerProperty(name='形态 Action',type=bpy.types.Action,poll=compatible_action)
    slot_handle: IntProperty(name='Action 插槽')
    start: FloatProperty(name='起始帧',default=1)
    end: FloatProperty(name='结束帧',default=60)
    loop: BoolProperty(name='循环播放',default=True)
    autoplay: BoolProperty(name='初始自动播放',default=False)
    speed: FloatProperty(name='默认倍率',default=1,min=.001)
    sample_rate: FloatProperty(name='采样率',default=60,min=1)
    advanced: BoolProperty(name='更多设置',default=False)
    hotkey: StringProperty(name='游戏快捷键')
    channels: CollectionProperty(type=EFF_PG_animation_channel)

class EFF_OT_shape_animation(bpy.types.Operator):
    bl_idname='eiem.shape_animation';bl_label='从当前动画创建播放组';bl_options={'REGISTER','UNDO'}
    remove: BoolProperty(default=False)
    refresh: BoolProperty(default=False)
    use_range: BoolProperty(default=False)
    def execute(self,context):
        obj=context.object
        try:
            if self.remove: obj.data.eiem_shape_animations.remove(obj.data.eiem_shape_animation_index)
            elif self.use_range:
                clip=obj.data.eiem_shape_animations[obj.data.eiem_shape_animation_index];frames=[p.co.x for c in shape_curves(obj,clip).values() for p in c.keyframe_points]
                if not frames:raise ValueError('所选动作没有形态关键帧')
                clip.start=min(frames);clip.end=max(frames)
            elif self.refresh:
                clip=obj.data.eiem_shape_animations[obj.data.eiem_shape_animation_index];clip.channels.clear()
                for name in shape_curves(obj,clip): item=clip.channels.add();item.name=name
            else: add_current(obj)
            return {'FINISHED'}
        except (ValueError,IndexError,AttributeError) as error: self.report({'ERROR'},str(error));return {'CANCELLED'}

class EFF_OT_animation_key(bpy.types.Operator):
    bl_idname='eiem.animation_key';bl_label='录制动画按键';bl_options={'REGISTER','UNDO'}
    def invoke(self,context,event):
        self.mesh=context.object.data;self.index=self.mesh.eiem_shape_animation_index;context.window_manager.modal_handler_add(self);context.workspace.status_text_set(text='按一个游戏快捷键；Esc 取消');return {'RUNNING_MODAL'}
    def modal(self,context,event):
        if event.value!='PRESS' or event.type in {'LEFT_CTRL','RIGHT_CTRL','LEFT_ALT','RIGHT_ALT','LEFT_SHIFT','RIGHT_SHIFT'}:return {'RUNNING_MODAL'}
        if event.type=='ESC':context.workspace.status_text_set(text=None);return {'CANCELLED'}
        try:
            key=_addon.switch_key_from_event(event);key=_addon.validate_switch_key(key)
            self.mesh.eiem_shape_animations[self.index].hotkey=key
        except (ValueError,IndexError) as error:self.report({'WARNING'},str(error));return {'RUNNING_MODAL'}
        context.workspace.status_text_set(text=None);return {'FINISHED'}

class EFF_UL_shape_animations(bpy.types.UIList):
    def draw_item(self,context,layout,data,item,icon,active_data,active_propname,index):layout.prop(item,'label',text='',emboss=False,icon='ACTION')

class EFF_OT_animation_preview(bpy.types.Operator):
    bl_idname='eiem.animation_preview';bl_label='预览形态动画'
    stop: BoolProperty(default=False)
    _saved=None
    def execute(self,context):
        cls=type(self)
        try:
            if self.stop:
                if cls._saved:
                    if context.screen.is_animation_playing:bpy.ops.screen.animation_cancel(restore_frame=False)
                    keys,action,slot,start,end,frame,values,preview=cls._saved;keys.animation_data.action=action
                    if action and slot:keys.animation_data.action_slot=slot
                    context.scene.frame_start=start;context.scene.frame_end=end;context.scene.frame_set(frame)
                    for key,value in values:key.value=value
                    bpy.data.actions.remove(preview)
                    cls._saved=None
                if context.screen.is_animation_playing:bpy.ops.screen.animation_cancel(restore_frame=False)
                return {'FINISHED'}
            if cls._saved:raise ValueError('请先停止当前插件预览')
            obj=context.object;keys=obj.data.shape_keys;clip=obj.data.eiem_shape_animations[obj.data.eiem_shape_animation_index];shape_curves(obj,clip)
            selected=[i.name for i in clip.channels if i.enabled];found=shape_curves(obj,clip)
            if not selected or any(n not in found for n in selected):raise ValueError('请先选择有效动画频道')
            keys.animation_data_create();anim=keys.animation_data;preview=bpy.data.actions.new('EFF 形态动画预览')
            cls._saved=(keys,anim.action,getattr(anim,'action_slot',None),context.scene.frame_start,context.scene.frame_end,context.scene.frame_current,[(k,k.value) for k in keys.key_blocks],preview)
            slot=preview.slots.new(id_type='KEY',name=keys.name);bag=preview.layers.new('预览').strips.new(type='KEYFRAME').channelbag(slot,ensure=True)
            count=max(2,math.ceil((clip.end-clip.start)/clip.speed)+1)
            for name in selected:
                curve=bag.fcurves.new(data_path=keys.key_blocks[name].path_from_id('value'),index=0);curve.keyframe_points.add(count)
                for i,p in enumerate(curve.keyframe_points):p.co=(1+(clip.end-clip.start)/clip.speed*i/(count-1),found[name].evaluate(clip.start+(clip.end-clip.start)*i/(count-1)));p.interpolation='LINEAR'
                curve.update()
            anim.action=preview;anim.action_slot=slot
            context.scene.frame_start=1;context.scene.frame_end=math.ceil(1+(clip.end-clip.start)/clip.speed);context.scene.frame_set(1)
            if not context.screen.is_animation_playing:bpy.ops.screen.animation_play()
            return {'FINISHED'}
        except (ValueError,IndexError,AttributeError,StopIteration) as error:self.report({'ERROR'},str(error));return {'CANCELLED'}

class EFF_PT_shape_animation(bpy.types.Panel):
    bl_label='形态动画';bl_idname='EFF_PT_shape_animation';bl_space_type='VIEW_3D';bl_region_type='UI';bl_category='EFF'
    @classmethod
    def poll(cls,context):return bool(context.object and context.object.type=='MESH' and context.object.data.get('eiem_section'))
    def draw(self,context):
        l=self.layout;obj=context.object;mesh=obj.data;l.label(text='目标网格：'+obj.name);l.operator('eiem.shape_animation',icon='ADD')
        l.template_list('EFF_UL_shape_animations','',mesh,'eiem_shape_animations',mesh,'eiem_shape_animation_index',rows=3)
        if not 0<=mesh.eiem_shape_animation_index<len(mesh.eiem_shape_animations):return
        c=mesh.eiem_shape_animations[mesh.eiem_shape_animation_index];l.prop(c,'enabled');l.prop(c,'action')
        if c.action and hasattr(c.action,'slots'):
            for slot in c.action.slots:
                if slot.target_id_type=='KEY':
                    row=l.row();row.label(text=slot.name_display);op=row.operator('eiem.animation_slot',text='使用此插槽',depress=slot.handle==c.slot_handle);op.handle=slot.handle
        r=l.row(align=True);r.prop(c,'start');r.prop(c,'end');l.operator('eiem.shape_animation',text='使用动作范围').use_range=True
        l.operator('eiem.shape_animation',text='读取动作频道',icon='FILE_REFRESH').refresh=True
        for item in c.channels:l.prop(item,'enabled',text=item.name)
        l.prop(c,'hotkey');l.operator('eiem.animation_key');l.prop(c,'loop');l.prop(c,'autoplay');l.prop(c,'advanced')
        if c.advanced:l.prop(c,'speed');l.prop(c,'sample_rate')
        r=l.row();r.operator('eiem.animation_preview',text='预览',icon='PLAY');r.operator('eiem.animation_preview',text='停止',icon='PAUSE').stop=True
        l.operator('eiem.shape_animation',text='删除播放组',icon='REMOVE').remove=True
        l.label(text='游戏中按键播放/停止；Ins 可随时调速')
class EFF_OT_animation_slot(bpy.types.Operator):
    bl_idname='eiem.animation_slot';bl_label='选择形态动画插槽';bl_options={'REGISTER','UNDO'}
    handle:IntProperty()
    def execute(self,context):context.object.data.eiem_shape_animations[context.object.data.eiem_shape_animation_index].slot_handle=self.handle;return {'FINISHED'}

_classes=(EFF_PG_animation_channel,EFF_PG_shape_animation,EFF_OT_shape_animation,EFF_OT_animation_key,EFF_UL_shape_animations,EFF_OT_animation_preview,EFF_OT_animation_slot,EFF_PT_shape_animation)
def register(addon):
    global _addon
    _addon=addon
    for cls in _classes:bpy.utils.register_class(cls)
    bpy.types.Mesh.eiem_shape_animations=CollectionProperty(type=EFF_PG_shape_animation)
    bpy.types.Mesh.eiem_shape_animation_index=IntProperty(default=0,min=0)
def unregister():
    if EFF_OT_animation_preview._saved:
        bpy.ops.eiem.animation_preview(stop=True)
    del bpy.types.Mesh.eiem_shape_animation_index;del bpy.types.Mesh.eiem_shape_animations
    for cls in reversed(_classes):bpy.utils.unregister_class(cls)

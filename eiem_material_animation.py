"""Material parameter keyframes sampled offline, separate from shape groups."""
import math
import uuid
import hashlib
import bpy
from bpy.props import BoolProperty,CollectionProperty,EnumProperty,FloatProperty,FloatVectorProperty,IntProperty,StringProperty

_items=[]
def parameter_items(self,context):
    global _items
    material=getattr(context,'material',None) or (getattr(context.object,'active_material',None) if context.object else None)
    _items=[]
    if material:
        for key in sorted(material.keys()):
            if key.startswith(('eiem_float.','eiem_color.','eiem_value4.')):
                name=key.split('.',1)[1]
                if name.startswith(('_Use','_Enable')) or name in {'_ZWrite','_ZTest','_Cull','_SrcBlend','_DstBlend','_AlphaSrcBlend','_AlphaDstBlend','_SurfaceType','_QueueOffset','_AlphaClip'}:continue
                _items.append((key,name,key))
    return _items

def values(material,key):
    value=material[key]
    if key.startswith('eiem_float.'):return [float(value)]
    if isinstance(value,str):value=[float(x.strip()) for x in value.split(',')]
    result=list(value);assert len(result)==4
    return result

class EFF_PG_material_animation(bpy.types.PropertyGroup):
    identity:StringProperty()
    label:StringProperty(name='名称',default='材质动画',update=lambda self,context:setattr(self,'name',self.label))
    enabled:BoolProperty(name='导出',default=True)
    property:StringProperty(name='Shader 参数')
    kind:EnumProperty(items=(('FLOAT','数值',''),('COLOR','颜色',''),('VECTOR','四维向量','')))
    value:FloatProperty(name='动画值',description='在此数值上按 I 插入关键帧')
    vector:FloatVectorProperty(name='动画值',size=4,description='在此数值上按 I 插入关键帧')
    start:FloatProperty(name='起始帧',default=1)
    end:FloatProperty(name='结束帧',default=60)
    loop:BoolProperty(name='循环',default=True)
    autoplay:BoolProperty(name='初始自动播放',default=True)
    hotkey:StringProperty(name='游戏快捷键',description='例如 F6；播放/停止')
    speed:FloatProperty(name='默认倍率',default=1,min=.001)
    sample_rate:FloatProperty(name='采样率',default=60,min=1)

class EFF_OT_material_animation(bpy.types.Operator):
    bl_idname='eiem.material_animation';bl_label='添加材质参数动画';bl_options={'UNDO'}
    parameter:EnumProperty(name='现有参数',items=parameter_items)
    remove:BoolProperty(default=False)
    def invoke(self,context,event):
        if self.remove:return self.execute(context)
        return context.window_manager.invoke_props_dialog(self)
    def execute(self,context):
        material=getattr(context,'material',None) or context.object.active_material
        try:
            if self.remove:
                index=material.eiem_material_animation_index
                if material.animation_data and material.animation_data.action:raise ValueError('删除播放组前请先移除其关键帧，避免列表索引改变后驱动其他参数')
                material.eiem_material_animations.remove(index);material.eiem_material_animation_index=max(0,index-1)
            else:
                if self.parameter not in material:raise ValueError('请选择来源材质已声明的数值或颜色参数')
                prop=self.parameter.split('.',1)[1]
                if any(c.property==prop for c in material.eiem_material_animations):raise ValueError('该参数已有播放组，请编辑现有组')
                v=values(material,self.parameter);clip=material.eiem_material_animations.add();clip.identity=uuid.uuid4().hex;clip.label=prop;clip.property=prop
                clip.kind='FLOAT' if len(v)==1 else 'COLOR' if self.parameter.startswith('eiem_color.') else 'VECTOR'
                if len(v)==1:clip.value=v[0]
                else:clip.vector=v
                clip.start=context.scene.frame_start;clip.end=context.scene.frame_end;material.eiem_material_animation_index=len(material.eiem_material_animations)-1
            return {'FINISHED'}
        except Exception as e:self.report({'ERROR'},str(e));return {'CANCELLED'}

def export_clips(material,mid,addon,include_hotkeys=True):
    clips=[(i,c) for i,c in enumerate(material.eiem_material_animations) if c.enabled]
    if not clips:return []
    animation=material.animation_data
    if not animation or not animation.action:raise ValueError(material.name+' 的材质动画没有 Action，请先对动画值插入关键帧')
    scene=bpy.context.scene;fps=scene.render.fps/scene.render.fps_base;result=[]
    action=animation.action
    if hasattr(action,'fcurves'):curves=list(action.fcurves)
    else:
        slot=animation.action_slot
        if not slot or slot.target_id_type!='MATERIAL':raise ValueError('材质动画缺少当前材质的 Action 插槽')
        curves=[]
        for layer in action.layers:
            for strip in layer.strips:
                if hasattr(strip,'channelbag'):
                    bag=strip.channelbag(slot)
                    if bag:curves.extend(bag.fcurves)
    for index,c in clips:
        cid='mat_'+hashlib.sha256((mid+'\n'+c.identity).encode('utf-8')).hexdigest()[:32]
        if not math.isfinite(c.end-c.start) or c.end<=c.start:raise ValueError(c.label+' 的帧范围无效')
        path='eiem_material_animations[%d].%s'%(index,'value' if c.kind=='FLOAT' else 'vector')
        count=max(2,math.ceil((c.end-c.start)/fps*c.sample_rate)+1);tracks=[];channels=1 if c.kind=='FLOAT' else 4
        for component in range(channels):
            curve=next((f for f in curves if f.data_path==path and f.array_index==component),None)
            if curve is None:raise ValueError(c.label+' 缺少动画频道 %d 的关键帧'%component)
            samples=[float(curve.evaluate(c.start+(c.end-c.start)*i/(count-1))) for i in range(count)]
            if not all(math.isfinite(v) and abs(v)<=3.402823466e38 for v in samples):raise ValueError(c.label+' 存在无效数值')
            tracks.append(dict(variable='$'+cid+'_'+str(component),material=mid,property=c.property,kind=c.kind,component=component,samples=samples))
        result.append(dict(id=cid,label=c.label,key=c.hotkey if include_hotkeys else '',duration=(c.end-c.start)/fps,speed=c.speed,loop=c.loop,autoplay=c.autoplay,targets=tracks))
    return result

class EFF_PT_material_animation(bpy.types.Panel):
    bl_label='EFF 动态材质参数';bl_idname='EFF_PT_material_animation';bl_space_type='PROPERTIES';bl_region_type='WINDOW';bl_context='material'
    @classmethod
    def poll(cls,context):return context.material is not None and bool(context.material.get('eiem_source'))
    def draw(self,context):
        m=context.material;l=self.layout;l.operator('eiem.material_animation',icon='ADD');l.template_list('UI_UL_list','eiem_material_clips',m,'eiem_material_animations',m,'eiem_material_animation_index',rows=2)
        if not m.eiem_material_animations:return
        c=m.eiem_material_animations[min(m.eiem_material_animation_index,len(m.eiem_material_animations)-1)]
        l.prop(c,'label');l.label(text=c.property);l.prop(c,'value' if c.kind=='FLOAT' else 'vector');l.label(text='在动画值上按 I 插帧；使用当前材质 Action')
        l.label(text='功能开关和 Shader 关键字需先在静态材质中启用')
        r=l.row(align=True);r.prop(c,'start');r.prop(c,'end');l.prop(c,'loop');l.prop(c,'autoplay');l.prop(c,'hotkey');l.prop(c,'speed');l.prop(c,'enabled');l.operator('eiem.material_animation',text='删除播放组',icon='REMOVE').remove=True

CLASSES=(EFF_PG_material_animation,EFF_OT_material_animation,EFF_PT_material_animation)
def register():
    for cls in CLASSES:bpy.utils.register_class(cls)
    bpy.types.Material.eiem_material_animations=CollectionProperty(type=EFF_PG_material_animation)
    bpy.types.Material.eiem_material_animation_index=IntProperty(min=0)
def unregister():
    del bpy.types.Material.eiem_material_animation_index;del bpy.types.Material.eiem_material_animations
    for cls in reversed(CLASSES):bpy.utils.unregister_class(cls)

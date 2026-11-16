"""One action list and one channel editor for every EFF animation type."""
import bpy
from bpy.props import EnumProperty,StringProperty

_animation=None


class EFF_OT_animation(bpy.types.Operator):
    bl_idname='eiem.animation'
    bl_label='EFF 动作'
    bl_options={'REGISTER','UNDO'}
    operation:EnumProperty(items=(('CREATE','新建动作','从所选对象的关键帧创建动作'),
        ('ADD','添加所选通道','将所选对象的关键帧加入当前动作'),
        ('IMPORT','加入 Action','使用已有 Action 的实际绑定对象'),
        ('REMOVE','移除动作','保留源 Action 与关键帧，移除其 EFF 导出配置'),
        ('REMOVE_CHANNEL','移除通道','从当前动作移除此通道')))
    action_name:StringProperty(options={'HIDDEN'})
    kind:StringProperty(options={'HIDDEN'})
    owner_name:StringProperty(options={'HIDDEN'})
    channel_name:StringProperty(options={'HIDDEN'})

    def execute(self,context):
        try:
            model=_animation
            model.stop_preview(context)
            clip=model.active_clip(context)
            if self.operation in {'CREATE','IMPORT'}:
                action=bpy.data.actions.get(self.action_name) if self.operation=='IMPORT' else None
                if self.operation=='IMPORT' and action is None:raise ValueError('Action 已删除')
                objects=model.action_source_objects(action) if action else context.selected_objects
                clip=model.create_action(objects,source_action=action)
                model.set_active_group(context,clip.identity)
            elif self.operation=='ADD':
                if clip is None:raise ValueError('请先选择动作')
                count=model.add_action_channels(clip,context.selected_objects)
                self.report({'INFO'},'已添加 '+str(count)+' 条动画通道')
            elif self.operation=='REMOVE':
                if clip is None:raise ValueError('请先选择动作')
                model.remove_group(clip.identity)
                groups=model.all_groups()
                context.scene.eiem_animation_group=groups[0].identity if groups else ''
            elif self.operation=='REMOVE_CHANNEL':
                if clip is None:raise ValueError('请先选择动作')
                model.remove_action_channel(clip.identity,self.kind,self.owner_name,self.channel_name)
            return {'FINISHED'}
        except (ValueError,IndexError,AttributeError,StopIteration) as error:
            self.report({'ERROR'},str(error));return {'CANCELLED'}


def draw(layout,context,model):
    groups=model.all_groups();current=model.active_clip(context)
    channels={clip.identity:model.action_channels(clip.identity) for clip in groups}
    bound={action.as_pointer() for clip in groups for action in model.source_actions(clip.identity)}
    preview=model.EFF_OT_animation_preview._saved
    raw=[action for action in bpy.data.actions
         if action.as_pointer() not in bound and (not preview or action!=preview['preview'])]
    listing=layout.box()
    listing.label(text='Action 动作（'+str(len(groups)+len(raw))+'）',icon='ACTION')
    for clip in groups:
        row=listing.row(align=True)
        op=row.operator('eiem.animation_group_select',text=clip.label,icon='ACTION',
                        depress=current is not None and clip.identity==current.identity)
        op.identity=clip.identity
        row.label(text=str(len(channels[clip.identity]))+' 通道')
    for action in raw:
        row=listing.row(align=True)
        row.label(text=action.name,icon='ACTION')
        op=row.operator('eiem.animation',text='加入',icon='ADD')
        op.operation='IMPORT';op.action_name=action.name
    if not groups and not raw:listing.label(text='尚无 Action，请先给属性插入关键帧')
    row=layout.row(align=True)
    row.operator('eiem.animation',text='从所选创建动作',icon='ADD').operation='CREATE'
    if current:row.operator('eiem.animation',text='',icon='REMOVE').operation='REMOVE'
    if current is None:return

    layout.separator()
    row=layout.row(align=True);row.prop(current,'label',text='动作');row.prop(current,'enabled',text='导出')
    row=layout.row(align=True);row.prop(current,'start');row.prop(current,'end')
    row=layout.row(align=True);row.prop(current,'hotkey',text='切换按键')
    row.operator('eiem.animation_key',text='',icon='EVENT_K')
    row=layout.row(align=True);row.prop(current,'loop');row.prop(current,'autoplay')
    row=layout.row(align=True);row.prop(current,'speed');row.prop(current,'sample_rate')

    # Older projects did not persist Material Action ownership. Expose the
    # source only when it cannot be recovered without guessing.
    for material,member in model.material_group_members(current.identity):
        if not member.action and not member.source_bound:
            row=layout.row(align=True)
            row.label(text=material.name)
            row.prop(member,'action',text='源 Action')

    rows=channels[current.identity]
    box=layout.box();header=box.row(align=True)
    header.label(text='Action 通道（'+str(len(rows))+'）')
    header.operator('eiem.animation',text='添加所选对象',icon='ADD').operation='ADD'
    box.label(text='自动导出 Action 内全部受支持通道；在 Action 中增删曲线')
    labels={'SHAPE':'形态键','BONE':'变换','MATERIAL':'材质'}
    for track in rows:
        row=box.row(align=True)
        row.label(text=labels[track['kind']]+' · '+track['owner'].name+' · '+track['name'])
        source=track['action']
        box.label(text='Action: '+(source.name if source else '缺少 Action'),icon='ACTION')


def register(animation):
    global _animation
    _animation=animation
    bpy.utils.register_class(EFF_OT_animation)


def unregister():
    bpy.utils.unregister_class(EFF_OT_animation)

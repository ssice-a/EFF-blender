"""Typed material controls over the existing, portable EFF author properties."""
import math
import re

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       FloatVectorProperty, IntProperty, StringProperty)
from bpy_extras.io_utils import ImportHelper


GROUPS = (
    ('surface', '表面与颜色'), ('alpha', '透明与裁剪'), ('emission', '自发光与动态发光'),
    ('outline', '描边'), ('normal', '法线与高光'), ('vfx', '特效与溶解'),
    ('face', '脸部与表情'), ('environment', '环境效果'), ('other', '其他参数'),
    ('render', '渲染状态'),
)
MASTERS = {
    'emission': ('float._UseEmission', 'keyword._EMISSION'),
    'outline': ('float._EnableOutline',),
    'normal': (),
    'vfx': (),
    'face': (),
    'environment': (),
}
BOOLEAN_NAMES = {
    '_ZWrite', '_TransparentDepthWrite', '_AlphaClip', '_AlphaPremultiply',
    '_BackFaceNormalFlip', '_EmissionAlphaBrightBreath', '_OutlineAverageNormal',
    '_OutlineTransparent', '_ResponsiveTransparency', '_SkinRimOff',
    '_DissolveUseViewUV', '_DisableRainEffectOnMaterial', '_AvatarCustomizeEnable',
}
RENDER_NAMES = {'_Cull', '_ZTest', '_ZWrite', '_SrcBlend', '_DstBlend', '_AlphaSrcBlend',
                '_AlphaDstBlend', '_MVSrcBlend', '_MVDstBlend', '_QueueOffset',
                '_characterRenderQueue', '_TransparentSortPriority', '_TransparentDepthWrite'}
_SLOT_ITEMS = {}  # Blender retains callback enum strings; keep their owners alive.
_PENDING_MATERIALS = {}
_PARAMETER_PREFIXES = ('eiem_float.', 'eiem_int.', 'eiem_value4.', 'eiem_color.',
                       'eiem_keyword.', 'eiem_tag.')
_TEXTURE_PREFIXES = ('eiem_texture.', 'eiem_texture_scale.', 'eiem_texture_offset.')
_INFO_KEYS = ('eiem_source', 'eiem_shader', 'eiem_section')


def enabled(value):
    text = str(value).strip().lower()
    if text in ('true', 'false'):
        return text == 'true'
    try:
        return float(text) != 0
    except ValueError:
        return True  # Unknown encodings must not hide their settings.


def property_kind(key, value):
    kind, _, name = key.removeprefix('eiem_').partition('.')
    if kind == 'keyword':
        return 'BOOL'
    if key == 'eiem_render_queue' or kind == 'int':
        return 'INT'
    if kind == 'float':
        if (name in BOOLEAN_NAMES or re.match(r'^_(Enable|Use)[A-Z]', name)) and str(value).lower() in ('0','1','0.0','1.0','true','false'):
            return 'BOOL'
        return 'FLOAT'
    if kind in ('color', 'value4'):
        return 'COLOR' if kind == 'color' or re.search('color|tint', name, re.I) else 'VECTOR4'
    if kind in ('texture_scale', 'texture_offset'):
        return 'VECTOR2'
    return 'TEXT'


def group_for(key):
    prop = key.removeprefix('eiem_')
    name = prop.partition('.')[2]
    lower = name.lower()
    if prop == 'render_queue' or name in RENDER_NAMES or prop.startswith('tag.'):
        return 'render'
    if 'emission' in lower or 'emissive' in lower:
        return 'emission'
    if 'alpha' in lower or 'surface' in lower or 'blendmode' in lower or 'transparen' in lower:
        return 'alpha'
    if 'outline' in lower:
        return 'outline'
    if any(x in lower for x in ('dissolve', 'vfx', 'dither', 'vat', 'cutoff', 'puppet')):
        return 'vfx'
    if any(x in lower for x in ('face', 'emotion', 'sdf', 'skinrim')):
        return 'face'
    if any(x in lower for x in ('rain', 'wet', 'wind')):
        return 'environment'
    if any(x in lower for x in ('bump', 'normal', 'metallic', 'smooth', 'rough', 'specular', 'clearcoat', 'specramp')):
        return 'normal'
    if any(x in lower for x in ('base', 'albedo', 'color', 'shadow', 'difframp')):
        return 'surface'
    return 'other'


def feature_active(material, group):
    present = [material['eiem_' + k] for k in MASTERS.get(group, ()) if 'eiem_' + k in material]
    # Unknown shaders/absent switches stay visible. Do not guess that a
    # non-emissive template texture is unused just because a keyword is off.
    return not present or any(enabled(v) for v in present)


def texture_slots(material):
    slots = set()
    for key in material.keys():
        for prefix in ('eiem_texture.', 'eiem_texture_scale.', 'eiem_texture_offset.'):
            if key.startswith(prefix):
                slots.add(key[len(prefix):])
    return sorted(slots)


def _get(row):
    return row.id_data.get(row.key, '')


def _put(row, value):
    row.id_data[row.key] = value


def _number(row, integer=False):
    try:
        value = float(_get(row))
        return int(value) if integer else value
    except (TypeError, ValueError, OverflowError):
        return 0 if integer else 0.0


def _vector(row, size):
    try:
        values = tuple(float(v) for v in str(_get(row)).split(','))
        return values if len(values) == size and all(math.isfinite(v) for v in values) else (0.0,) * size
    except (TypeError, ValueError):
        return (0.0,) * size


def _set_vector(row, values):
    _put(row, ','.join(format(v, '.9g') for v in values))


class EFF_PG_material_value(bpy.types.PropertyGroup):
    key: StringProperty()
    boolean: BoolProperty(get=lambda s: enabled(_get(s)), set=lambda s,v: _put(s, '1' if v else '0'))
    integer: IntProperty(get=lambda s: _number(s, True), set=lambda s,v: _put(s, str(v)))
    scalar: FloatProperty(precision=4, get=lambda s: _number(s), set=lambda s,v: _put(s, format(v, '.9g')))
    vector2: FloatVectorProperty(size=2, precision=4, get=lambda s: _vector(s,2), set=_set_vector)
    vector4: FloatVectorProperty(size=4, precision=4, get=lambda s: _vector(s,4), set=_set_vector)
    color: FloatVectorProperty(size=4, subtype='COLOR', precision=4, min=0.0,
                               get=lambda s: _vector(s,4), set=_set_vector)
    text: StringProperty(get=lambda s: str(_get(s)), set=_put)


class EFF_PG_material_group(bpy.types.PropertyGroup):
    name: StringProperty()
    expanded: BoolProperty(default=False)


def _row(material, key):
    rows = material.eiem_editor_values
    row = rows.get(key)
    if row is None:
        row = rows.add()
        row.name = key
    if row.key != key:
        row.key = key
    return row


def _schema(material):
    keys = [key for key in material.keys()
            if key.startswith(_PARAMETER_PREFIXES + _TEXTURE_PREFIXES)
            or key == 'eiem_render_queue' or key in _INFO_KEYS]
    groups = {group_for(key) for key in keys
              if key.startswith(_PARAMETER_PREFIXES) or key == 'eiem_render_queue'}
    return keys, groups


def _prepare_material(material):
    # RNA collections cannot be created or changed from Panel.draw. Prepare
    # all controls outside drawing, including currently collapsed sections.
    for collection in (material.eiem_editor_values, material.eiem_editor_groups):
        for index in range(len(collection) - 1, -1, -1):
            if not collection[index].name:
                collection.remove(index)
    keys, groups = _schema(material)
    for key in keys:
        _row(material, key)
    for group in groups:
        if material.eiem_editor_groups.get(group) is None:
            state = material.eiem_editor_groups.add()
            state.name = group
            state.expanded = group in ('surface', 'alpha', 'emission', 'normal')


def _prepare_pending():
    pending = tuple(_PENDING_MATERIALS.values())
    _PENDING_MATERIALS.clear()
    for material in pending:
        try:
            _prepare_material(material)
        except ReferenceError:
            continue  # Material was deleted before the deferred callback.
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'PROPERTIES':
                area.tag_redraw()
    return None


def _controls_ready(material):
    keys, groups = _schema(material)
    if (all((row := material.eiem_editor_values.get(key)) is not None and row.key == key for key in keys)
            and all(material.eiem_editor_groups.get(group) is not None for group in groups)):
        return True
    _PENDING_MATERIALS[material.as_pointer()] = material
    if not bpy.app.timers.is_registered(_prepare_pending):
        bpy.app.timers.register(_prepare_pending, first_interval=0.0)
    return False


def _value_layout(layout, label):
    split = layout.split(factor=0.52)
    caption = split.row()
    caption.alignment = 'LEFT'
    caption.label(text=label)
    value = split.row(align=True)
    value.use_property_split = False
    return value


def draw_value(layout, material, key):
    row = material.eiem_editor_values.get(key)
    kind = property_kind(key, material[key])
    field = {'BOOL':'boolean','INT':'integer','FLOAT':'scalar','VECTOR2':'vector2',
             'VECTOR4':'vector4','COLOR':'color','TEXT':'text'}[kind]
    label = key.removeprefix('eiem_').partition('.')[2] or '渲染队列'
    _value_layout(layout, label).prop(row, field, text='')


def context_material(context):
    return getattr(context, 'material', None) or getattr(getattr(context,'object',None),'active_material',None)


def empty_slot_items(self, context):
    material = context_material(context)
    if not material:
        return []
    return [_SLOT_ITEMS.setdefault(name,(name, name, '为 Shader 已声明的空贴图槽添加图片')) for name in texture_slots(material)
            if not str(material.get('eiem_texture.' + name, '')).strip()]


class EFF_OT_material_texture_add(bpy.types.Operator):
    bl_idname = 'eiem.material_texture_add'
    bl_label = '添加空贴图槽'
    bl_options = {'REGISTER', 'UNDO'}
    slot: EnumProperty(name='贴图槽', items=empty_slot_items)

    def invoke(self, context, event):
        if not empty_slot_items(self, context):
            self.report({'INFO'}, '没有空贴图槽')
            return {'CANCELLED'}
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        material = context_material(context)
        if material is None or self.slot not in texture_slots(material):
            return {'CANCELLED'}
        bpy.ops.eiem.material_texture_file('INVOKE_DEFAULT', slot=self.slot)
        return {'FINISHED'}


class EFF_OT_material_texture_file(ImportHelper, bpy.types.Operator):
    bl_idname = 'eiem.material_texture_file'
    bl_label = '选择材质贴图'
    bl_options = {'REGISTER', 'UNDO'}
    slot: StringProperty(options={'HIDDEN'})
    filter_glob: StringProperty(default='*.png;*.tga;*.jpg;*.jpeg;*.tif;*.tiff;*.bmp;*.exr', options={'HIDDEN'})

    def execute(self, context):
        material = context_material(context)
        if material is None or self.slot not in texture_slots(material):
            self.report({'ERROR'}, 'Shader 没有声明此贴图槽')
            return {'CANCELLED'}
        material['eiem_texture.' + self.slot] = self.filepath
        return {'FINISHED'}


def draw(layout, material):
    layout.use_property_split = False
    layout.use_property_decorate = False
    if not _controls_ready(material):
        layout.label(text='正在初始化材质控件…')
        return
    layout.prop(material, 'eiem_editor_search', text='', icon='VIEWZOOM')
    query = material.eiem_editor_search.lower().strip()
    texture_box = layout.box()
    texture_box.label(text='贴图')
    for name in texture_slots(material):
        key = 'eiem_texture.' + name
        # All assigned source/author textures remain visible even with the
        # corresponding feature off. Only never-assigned slots are compact.
        if key not in material or (not str(material[key]).strip()):
            continue
        if query and query not in name.lower():
            continue
        line = _value_layout(texture_box, name)
        line.prop(material.eiem_editor_values.get(key), 'text', text='')
        line.operator('eiem.material_texture_file', text='', icon='FILE_FOLDER').slot = name
    texture_box.operator('eiem.material_texture_add', icon='ADD')
    groups = {group:[] for group,_ in GROUPS}
    for key in material.keys():
        if (key.startswith(_PARAMETER_PREFIXES)
            or key == 'eiem_render_queue'):
            if not query or query in key.lower():
                groups[group_for(key)].append(key)
    for group, label in GROUPS:
        keys = groups[group]
        if not keys:
            continue
        state = material.eiem_editor_groups.get(group)
        box = layout.box()
        header = box.row(align=True)
        header.prop(state, 'expanded', text=label, icon='TRIA_DOWN' if state.expanded else 'TRIA_RIGHT', emboss=False)
        masters = {'eiem_' + k for k in MASTERS.get(group, ())}
        switches = [k for k in keys if k in masters]
        for key in switches:
            draw_value(box, material, key)
        if not (state.expanded or query):
            continue
        active = feature_active(material, group)
        if not active and not query:
            box.label(text='开启后显示详细参数')
            continue
        for key in keys:
            if key not in switches:
                draw_value(box, material, key)
    transforms = layout.box()
    transforms.prop(material, 'eiem_editor_uv_expanded', text='贴图变换',
                    icon='TRIA_DOWN' if material.eiem_editor_uv_expanded else 'TRIA_RIGHT', emboss=False)
    if material.eiem_editor_uv_expanded:
        for name in texture_slots(material):
            if not str(material.get('eiem_texture.' + name, '')).strip():
                continue
            for kind in ('texture_scale.', 'texture_offset.'):
                key='eiem_'+kind+name
                if key in material:
                    draw_value(transforms,material,key)
    info = layout.box()
    info.prop(material, 'eiem_editor_info_expanded', text='资源信息',
              icon='TRIA_DOWN' if material.eiem_editor_info_expanded else 'TRIA_RIGHT', emboss=False)
    if material.eiem_editor_info_expanded:
        for key,label in (('eiem_source','源材质'),('eiem_shader','Shader'),('eiem_section','资源段')):
            if key in material:
                _value_layout(info, label).prop(material.eiem_editor_values.get(key), 'text', text='')


CLASSES = (EFF_PG_material_value, EFF_PG_material_group, EFF_OT_material_texture_add, EFF_OT_material_texture_file)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Material.eiem_editor_values = CollectionProperty(type=EFF_PG_material_value)
    bpy.types.Material.eiem_editor_groups = CollectionProperty(type=EFF_PG_material_group)
    bpy.types.Material.eiem_editor_search = StringProperty()
    bpy.types.Material.eiem_editor_uv_expanded = BoolProperty()
    bpy.types.Material.eiem_editor_info_expanded = BoolProperty()


def unregister():
    if bpy.app.timers.is_registered(_prepare_pending):
        bpy.app.timers.unregister(_prepare_pending)
    _PENDING_MATERIALS.clear()
    for name in ('eiem_editor_info_expanded','eiem_editor_uv_expanded','eiem_editor_search',
                 'eiem_editor_groups','eiem_editor_values'):
        delattr(bpy.types.Material,name)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)

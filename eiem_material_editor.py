"""Typed material controls over the existing, portable EFF author properties."""
import math
import re
import importlib.util
from pathlib import Path

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       FloatVectorProperty, IntProperty, StringProperty)
from bpy_extras.io_utils import ImportHelper

if __package__:
    from . import eiem_material_features as features
else:
    _feature_spec = importlib.util.spec_from_file_location(
        'EFFMaterialFeatures', Path(__file__).with_name('eiem_material_features.py'))
    features = importlib.util.module_from_spec(_feature_spec)
    _feature_spec.loader.exec_module(features)


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
    '_ClearCoat', '_SpecRampIridescentMode', '_ParallaxUseNormal',
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
    return features.texture_slots(material)


def _get(row):
    return row.id_data.get(row.key, '')


def _put(row, value):
    if (row.key.startswith('eiem_float.') and property_kind(row.key, value) == 'BOOL'
            and features.profile(row.id_data)):
        features.set_switch(row.id_data, features.property_name(row.key), enabled(value))
        _prepare_material(row.id_data)
    else:
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


def _set_uv_speed(row, values):
    _set_vector(row, tuple(values) + _vector(row, 4)[2:])


_CHOICES = {
    '_ClearCoatNormalMode': (('VERTEX', '顶点法线', '', 0), ('TEXTURE', '法线贴图', '', 1)),
    '_Cull': (('BOTH', '双面', '', 0), ('BACK', '只显示背面', '', 1), ('FRONT', '只显示正面', '', 2)),
    '_ZTest': (('NEVER', '不通过', '', 1), ('LESS', '小于', '', 2), ('EQUAL', '等于', '', 3),
               ('LEQUAL', '小于等于', '', 4), ('GREATER', '大于', '', 5), ('NEQUAL', '不等于', '', 6),
               ('GEQUAL', '大于等于', '', 7), ('ALWAYS', '总是通过', '', 8)),
}


def _choices(row, context):
    return _CHOICES.get(features.property_name(row.key), ())


def _set_enabled(row, value):
    features.set_feature(row.id_data, row.key, value)
    _prepare_material(row.id_data)
    if value:
        row.id_data.eiem_editor_groups[row.key].expanded = True


def _get_enabled(row):
    item = features.get_feature(row.id_data, row.key)
    return bool(item and features.active(row.id_data, item))


def _set_surface(material, value):
    features.set_surface(material, value)
    _prepare_material(material)


class EFF_PG_material_value(bpy.types.PropertyGroup):
    key: StringProperty()
    boolean: BoolProperty(get=lambda s: enabled(_get(s)), set=lambda s,v: _put(s, '1' if v else '0'))
    integer: IntProperty(get=lambda s: _number(s, True), set=lambda s,v: _put(s, str(v)))
    scalar: FloatProperty(precision=4, get=lambda s: _number(s), set=lambda s,v: _put(s, format(v, '.9g')))
    vector2: FloatVectorProperty(size=2, precision=4, get=lambda s: _vector(s,2), set=_set_vector)
    vector4: FloatVectorProperty(size=4, precision=4, get=lambda s: _vector(s,4), set=_set_vector)
    uv_speed: FloatVectorProperty(size=2, precision=4, get=lambda s: _vector(s,4)[:2], set=_set_uv_speed)
    choice: EnumProperty(items=_choices, get=lambda s: _number(s, True), set=lambda s,v: _put(s,str(v)))
    color: FloatVectorProperty(size=4, subtype='COLOR', precision=4, min=0.0,
                               get=lambda s: _vector(s,4), set=_set_vector)
    text: StringProperty(get=lambda s: str(_get(s)), set=_put)
    uv_expanded: BoolProperty()


class EFF_PG_material_feature(bpy.types.PropertyGroup):
    key: StringProperty()
    enabled: BoolProperty(get=_get_enabled, set=_set_enabled,
                          description='同时同步此功能的材质参数与 Shader 关键字；关闭保留贴图和数值')


class EFF_PG_material_group(bpy.types.PropertyGroup):
    name: StringProperty()
    expanded: BoolProperty(default=False)
    details_expanded: BoolProperty(default=False)


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
    # Empty slots already declared by the source get a UI row, without adding
    # a native texture override or any material data from Panel.draw.
    keys = list(dict.fromkeys(keys + ['eiem_texture.' + slot for slot in texture_slots(material)]))
    groups = {group_for(key) for key in keys
              if key.startswith(_PARAMETER_PREFIXES) or key == 'eiem_render_queue'}
    groups.update(item['key'] for item in features.features(material))
    groups.update(('surface', 'extras', 'render'))
    return keys, groups


def _prepare_material(material):
    # RNA collections cannot be created or changed from Panel.draw. Prepare
    # all controls outside drawing, including currently collapsed sections.
    for collection in (material.eiem_editor_values, material.eiem_editor_groups, material.eiem_editor_features):
        for index in range(len(collection) - 1, -1, -1):
            if not collection[index].name:
                collection.remove(index)
    keys, groups = _schema(material)
    for key in keys:
        _row(material, key)
    for item in features.features(material):
        if material.eiem_editor_features.get(item['key']) is None:
            state = material.eiem_editor_features.add()
            state.name = state.key = item['key']
    for group in groups:
        if material.eiem_editor_groups.get(group) is None:
            state = material.eiem_editor_groups.add()
            state.name = group
            item = features.get_feature(material, group)
            state.expanded = features.active(material, item) if item else group in ('surface', 'alpha', 'emission', 'normal')


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
            and all(material.eiem_editor_groups.get(group) is not None for group in groups)
            and all(material.eiem_editor_features.get(item['key']) is not None for item in features.features(material))):
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


def draw_value(layout, material, key, simple=False):
    row = material.eiem_editor_values.get(key)
    kind = property_kind(key, material[key])
    field = {'BOOL':'boolean','INT':'integer','FLOAT':'scalar','VECTOR2':'vector2',
             'VECTOR4':'vector4','COLOR':'color','TEXT':'text'}[kind]
    name = features.property_name(key)
    if simple and name in _CHOICES:
        field = 'choice'
    elif simple and name in ('_BaseMapUVSpeed', '_EmissionMapUVSpeed', '_MainTexUVSpeed', '_BlendTexUVSpeed', '_MaskTexUVSpeed'):
        field = 'uv_speed'
    label = features.label(key) if simple else name or '渲染队列'
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


def _draw_raw(layout, material):
    layout.use_property_split = False
    layout.use_property_decorate = False
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
        active = feature_active(material, group) if not material.eiem_editor_advanced else True
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


def _draw_info(layout, material):
    info = layout.box()
    info.prop(material, 'eiem_editor_info_expanded', text='资源信息',
              icon='TRIA_DOWN' if material.eiem_editor_info_expanded else 'TRIA_RIGHT', emboss=False)
    if material.eiem_editor_info_expanded:
        for key,label in (('eiem_source','源材质'),('eiem_shader','Shader'),('eiem_section','资源段')):
            if key in material:
                _value_layout(info, label).prop(material.eiem_editor_values.get(key), 'text', text='')


def _matches(material, key):
    query = material.eiem_editor_search.strip().casefold()
    return not query or query in key.casefold() or query in features.label(key).casefold()


def _draw_texture(layout, material, slot):
    key = 'eiem_texture.' + slot
    if not _matches(material, key):
        return
    row = material.eiem_editor_values[key]
    line = _value_layout(layout, features.label(key))
    line.prop(row, 'text', text='')
    line.operator('eiem.material_texture_file', text='', icon='FILE_FOLDER').slot = slot
    transforms = [kind + slot for kind in ('eiem_texture_scale.', 'eiem_texture_offset.') if kind + slot in material]
    if transforms:
        line.prop(row, 'uv_expanded', text='', icon='TRIA_DOWN' if row.uv_expanded else 'TRIA_RIGHT', emboss=False)
        if row.uv_expanded:
            for transform in transforms:
                _value_layout(layout, '平铺 U/V' if 'scale.' in transform else '偏移 U/V').prop(
                    material.eiem_editor_values[transform], 'vector2', text='')


def _draw_textures(layout, material, items):
    box = layout.box()
    box.label(text='贴图')
    declared = texture_slots(material)
    visible = {slot for slot in declared if str(material.get('eiem_texture.' + slot, '')).strip()}
    visible.update(slot for item in items for slot in features.visible_slots(material, item))
    if features.profile(material) == 'character' and '_BaseMap' in declared:
        visible.add('_BaseMap')
    # Keep all bound paths together, including disabled features. Enabling a
    # feature adds its declared empty rows here instead of inside its panel.
    for slot in declared:
        if slot in visible:
            _draw_texture(box, material, slot)
    box.operator('eiem.material_texture_add', icon='ADD')


def _draw_surface(layout, material, ownership):
    state = material.eiem_editor_groups['surface']
    box = layout.box()
    box.prop(state, 'expanded', text='表面、颜色与透明', icon='TRIA_DOWN' if state.expanded else 'TRIA_RIGHT', emboss=False)
    if not state.expanded:
        return
    if features.profile(material) == 'character':
        box.prop(material, 'eiem_editor_surface', text='表面模式')
        if features.surface_mode(material) == 1 and 'eiem_float._AlphaClipThreshold' in material:
            draw_value(box, material, 'eiem_float._AlphaClipThreshold', True)
    for key in material.keys():
        if key.startswith(_PARAMETER_PREFIXES) and ownership.get(key) == 'surface' and _matches(material, key):
            draw_value(box, material, key, True)


def _draw_feature(layout, material, item, ownership):
    keys = [key for key in material.keys() if key.startswith(_PARAMETER_PREFIXES)
            and ownership.get(key) == item['key']]
    query = material.eiem_editor_search.strip().casefold()
    if (query and query not in item['label'].casefold()
            and not any(_matches(material, key) for key in keys)
            and not any(_matches(material, 'eiem_texture.' + slot) for slot in item['slots'])):
        return
    state = material.eiem_editor_groups[item['key']]
    box = layout.box()
    header = box.row(align=True)
    header.prop(state, 'expanded', text='', icon='TRIA_DOWN' if state.expanded else 'TRIA_RIGHT', emboss=False)
    if item['switch']:
        header.prop(material.eiem_editor_features[item['key']], 'enabled', text=item['label'])
    else:
        header.label(text=item['label'])
    active = features.active(material, item)
    if (not active and item['key'] != 'pbr') or not (state.expanded or query):
        return
    if item['key'] == 'pbr':
        box.label(text='P 图：R 金属、G 高光、B 阴影、A 光滑度' if active else '未使用 P 图，直接编辑表面数值')
    if item['key'] == 'vfx' and features.profile(material) == 'character':
        box.label(text='空贴图沿用来源／Shader 默认值；选择文件后才导出覆盖')
    if item['key'] == 'breath':
        box.label(text='E 图 Alpha 控制呼吸参与度；开启时联动角色 VFX')
    primary = features.PRIMARY.get(item['key'], ())
    keys.sort(key=lambda key: primary.index(features.property_name(key)) if features.property_name(key) in primary else len(primary))
    secondary = []
    for key in keys:
        name = features.property_name(key)
        if (name == item['switch'] or key.startswith('eiem_keyword.')
                or (name == '_ClearCoatMask' and key.startswith('eiem_float.'))):
            continue
        if item['key'] == 'pbr' and active and name in ('_Metallic', '_Specular', '_Smoothness'):
            continue
        if _matches(material, key):
            if name in primary or query:
                draw_value(box, material, key, True)
            else:
                secondary.append(key)
    for child in features.features(material):
        if child['parent'] == item['key']:
            _draw_feature(box, material, child, ownership)
    if secondary:
        box.prop(state, 'details_expanded', text='更多此功能参数',
                 icon='TRIA_DOWN' if state.details_expanded else 'TRIA_RIGHT', emboss=False)
        if state.details_expanded:
            for key in secondary:
                draw_value(box, material, key, True)


_SURFACE_STATE = {'_SurfaceType', '_EnableAlphaTest', '_BlendMode', '_AlphaPremultiply',
                  '_SrcBlend', '_DstBlend', '_AlphaSrcBlend', '_AlphaDstBlend', '_ZTest',
                  '_AlphaClipThreshold', '_AlphaClip', '_Surface', '_Blend'}


def _draw_remaining(layout, material, ownership):
    remaining = [key for key in material.keys() if key.startswith(_PARAMETER_PREFIXES)
                 and not key.startswith(('eiem_keyword.', 'eiem_tag.'))
                 and ownership.get(key) is None and features.property_name(key) not in _SURFACE_STATE]
    state = material.eiem_editor_groups['extras']
    box = layout.box()
    box.prop(state, 'expanded', text='更多参数与渲染状态', icon='TRIA_DOWN' if state.expanded else 'TRIA_RIGHT', emboss=False)
    if state.expanded:
        for key in remaining:
            if _matches(material, key):
                draw_value(box, material, key, True)
        for name in ('_Cull', '_ZWrite', '_ZTest', '_TransparentDepthWrite'):
            key = 'eiem_float.' + name
            if key in material and key not in remaining and _matches(material, key):
                draw_value(box, material, key, True)
        for key in ('eiem_render_queue', 'eiem_tag.RenderType'):
            if key in material and key not in remaining:
                draw_value(box, material, key, True)


def draw(layout, material):
    layout.use_property_split = False
    layout.use_property_decorate = False
    if not _controls_ready(material):
        layout.label(text='正在初始化材质控件…')
        return
    header = layout.row(align=True)
    header.prop(material, 'eiem_editor_search', text='', icon='VIEWZOOM')
    header.prop(material, 'eiem_editor_advanced', text='原始参数', toggle=True)
    if material.eiem_editor_advanced or not features.profile(material):
        if not features.profile(material):
            layout.label(text='此 Shader 使用原始参数编辑，保留来源功能')
        _draw_raw(layout, material)
    else:
        items = features.features(material)
        ownership = {key: features.owner(material, key, items) for key in material.keys()}
        ownership.update({key: features.owner(material, key, items)
                          for key in ('eiem_texture.' + slot for slot in texture_slots(material)) if key not in ownership})
        _draw_textures(layout, material, items)
        _draw_surface(layout, material, ownership)
        for item in items:
            if not item['parent']:
                _draw_feature(layout, material, item, ownership)
        _draw_remaining(layout, material, ownership)
    _draw_info(layout, material)


CLASSES = (EFF_PG_material_value, EFF_PG_material_feature, EFF_PG_material_group,
           EFF_OT_material_texture_add, EFF_OT_material_texture_file)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Material.eiem_editor_values = CollectionProperty(type=EFF_PG_material_value)
    bpy.types.Material.eiem_editor_groups = CollectionProperty(type=EFF_PG_material_group)
    bpy.types.Material.eiem_editor_features = CollectionProperty(type=EFF_PG_material_feature)
    bpy.types.Material.eiem_editor_surface = EnumProperty(items=features.SURFACE_ITEMS,
        get=features.surface_mode, set=_set_surface, description='同步透明参数、Shader 关键字和实际渲染队列；保留贴图和深度写入')
    bpy.types.Material.eiem_editor_advanced = BoolProperty(description='查看全部原始字段，包括关闭功能的参数和贴图')
    bpy.types.Material.eiem_editor_search = StringProperty()
    bpy.types.Material.eiem_editor_uv_expanded = BoolProperty()
    bpy.types.Material.eiem_editor_info_expanded = BoolProperty()


def unregister():
    if bpy.app.timers.is_registered(_prepare_pending):
        bpy.app.timers.unregister(_prepare_pending)
    _PENDING_MATERIALS.clear()
    for name in ('eiem_editor_advanced','eiem_editor_surface','eiem_editor_features',
                 'eiem_editor_info_expanded','eiem_editor_uv_expanded','eiem_editor_search',
                 'eiem_editor_groups','eiem_editor_values'):
        delattr(bpy.types.Material,name)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)

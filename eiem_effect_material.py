"""Adapt effect bindings to the existing EFF Material editor and native writer.

The Material's eiem_* author properties are the editing surface, just as for
ordinary .mat imports. Empty fields remain only for saved-project migration.
"""
import json
import importlib
from pathlib import Path

import bpy

_MODULE_PREFIX = (__package__ + ".") if __package__ else ""


def _module(name):
    return importlib.import_module(_MODULE_PREFIX + name)


document = _module("eiem_effect_document")
material_editor = _module("eiem_material_editor")
native_export = _module("eiem_native_export")
format_io = _module("eiem_format")
type_tree = _module("nativepack.type_tree")

_SCHEMAS = {}
PREFIXES = ('eiem_float.', 'eiem_int.', 'eiem_value4.', 'eiem_color.',
            'eiem_keyword.', 'eiem_tag.', 'eiem_texture_scale.', 'eiem_texture_offset.')


def material_for(author):
    if author is None:return None
    material = author.eff_effect_material
    if material and material.eff_effect_author == author and not material.get('eff_preview_instance'):
        return material
    return next((m for m in bpy.data.materials if m.eff_effect_author == author
                 and not m.get('eff_preview_instance')), None)


def ensure_material(root, edit, identity):
    """Keep author materials owned by their resource, including billboards.

    Per-emitter preview instances occupy mesh slots; the editable shared
    material may have none. The resource's forward ID pointer makes Blender
    save it, instead of dropping its paths and controls on the next load.
    """
    assets = _module("eiem_effect_assets")
    author = assets.material_object(root, identity)
    if author is None:return None
    material = material_for(author)
    if material is None:
        material = bpy.data.materials.new(edit.objects[identity].get('name') or author.name)
        material.eff_effect_author = author
    setup(material, root, edit, identity)
    return material


def _source(edit, identity):
    spec = edit.objects.get(identity, {})
    return next((r['path'] for r in spec.get('logicalPaths') or [] if r.get('path')), '')


def properties(edit, identity):
    """Same portable property names used by load_material and .mat export."""
    preview = _module("eiem_effect_preview")
    data = edit.value(identity)
    result = {'eiem_' + key: value for key, value in format_io.material_snapshot_properties(data).items()}
    for index, row in enumerate(data.get('m_SavedProperties', {}).get('m_TexEnvs', [])):
        name = row['first']
        target = preview._target(edit, identity, ['m_SavedProperties', 'm_TexEnvs', index, 'second', 'm_Texture'])
        field = '$.m_SavedProperties.m_TexEnvs[' + str(index) + '].second.m_Texture'
        binding = next((r.get('gameLogical') for r in edit.objects[identity].get('references', [])
                        if r.get('field') == field and r.get('gameLogical')), None)
        result['eiem_texture.' + name] = binding['path'] if binding else _source(edit, target) if target else ''
    return result


def setup(material, root, edit, identity):
    if material.eff_effect_author and material.eff_effect_author.eff_effect_material != material:
        material.eff_effect_author.eff_effect_material = material
    if material.get('eff_material_editor_version') == 1:return
    assets = _module("eiem_effect_assets")
    preview = _module("eiem_effect_preview")
    values = properties(edit, identity)
    # Preserve legacy selected images, including packed/painted buffers. They
    # remain an export fallback; new editing always uses the ordinary path UI.
    author = material.eff_effect_author
    for slot in author.eff_effect_textures:
        if slot.image:
            image = slot.image
            if image.filepath and not image.packed_file and not image.is_dirty:
                values['eiem_texture.' + slot.name] = bpy.path.abspath(image.filepath, library=image.library)
    for key, value in values.items():material[key] = value
    spec = edit.objects[identity]
    material['eiem_source'] = _source(edit, identity) or edit.state['catalog']['prefab']
    material['eiem_name'] = spec.get('name') or material.name
    material['eiem_section'] = 'Material' + root['eff_effect_id'] + '_' + identity.rsplit(':', 1)[-1].replace('-', 'n')
    shader = preview._target(edit, identity, ['m_Shader'])
    if shader:
        try:
            shader_name = edit.objects[shader].get('shaderName')
            if not shader_name:
                shader_name = assets.NativeAssets(edit, root['eff_effect_id']).reader.shader_name(shader)
            material['eiem_shader'] = shader_name
        except (OSError, ValueError):
            # Game-owned shader snapshots only improve offline preview.
            # Portable logical bindings remain exportable without the donor.
            pass
    material['eff_material_source_textures'] = document.dumps({k: v for k, v in properties(edit, identity).items() if k.startswith('eiem_texture.')})
    material['eff_material_last'] = document.dumps(values)
    material['eff_material_editor_version'] = 1
    material_editor._prepare_material(material)


def signature(author):
    material = material_for(author)
    if not material:return ()
    values = tuple((k, str(material[k])) for k in material.keys() if k.startswith(PREFIXES + ('eiem_texture.',)) or k == 'eiem_render_queue')
    sources = json.loads(material.get('eff_material_source_textures', '{}'))
    files = []
    for key, value in values:
        if not key.startswith('eiem_texture.') or not value or value == sources.get(key):continue
        try:
            stat = Path(bpy.path.abspath(value, library=material.library)).stat()
            stamp = stat.st_mtime_ns, stat.st_size
        except OSError:
            stamp = None
        files.append((key, stamp))
    return values, tuple(files)


def _schema(edit, identity):
    if edit.objects[identity].get('gameSource'):
        return _module('eiem_effect_native').Reader(edit).tree(identity)[0].schema
    path = Path(edit.state['source']) / edit.objects[identity]['schema']
    stat = path.stat()
    stamp = stat.st_mtime_ns, stat.st_size
    cached = _SCHEMAS.get(str(path))
    if cached is None or cached[0] != stamp:
        cached = stamp, type_tree.Schema.compile(json.loads(path.read_bytes())['nodes'])
        _SCHEMAS[str(path)] = cached
    return cached[1]


def _json_value(value):
    if value.schema.type == 'bool':return bool(value.data)
    if value.schema.type == 'Array':return [_json_value(item) for item in value.items()]
    if isinstance(value.data, dict):
        if list(value.data) == ['Array']:return _json_value(value.data['Array'])
        return {k: _json_value(v) for k, v in value.data.items()}
    return value.data


def synchronize(root, edit, api):
    """Common author properties -> the exact same native material_fields writer."""
    for material in bpy.data.materials:
        author = material.eff_effect_author
        if not author or api.root_of(author) != root:continue
        identity = next((i for i in json.loads(author.get('eff_effect_components', '[]')) if edit.objects[i]['classId'] == 21), None)
        if not identity:continue
        setup(material, root, edit, identity)
        previous = json.loads(material['eff_material_last'])
        for slot in author.eff_effect_textures:
            key = 'eiem_texture.' + slot.name
            if key in previous and str(material.get(key, '')) != previous[key] and slot.image:
                slot.image = None
        resources = _module('eiem_effect_game_resources')
        for index, row in enumerate(edit.value(identity).get('m_SavedProperties', {}).get('m_TexEnvs', [])):
            key = 'eiem_texture.' + row['first']
            value = str(material.get(key, '')).strip()
            if resources.valid_logical_path(value) and value != previous.get(key):
                resources.bind_logical(edit, identity,
                    ['m_SavedProperties', 'm_TexEnvs', index, 'second', 'm_Texture'], value, expected='Texture')
        native = properties(edit, identity)
        overrides = {k[5:]: str(material[k]) for k in material.keys()
                     if (k.startswith(PREFIXES) or k == 'eiem_render_queue') and str(material[k]) != previous.get(k, native.get(k))}
        if overrides:
            tree = type_tree.from_data(_schema(edit, identity), edit.value(identity))
            native_export.material_fields(tree, overrides, type_tree.encode)
            edit.state['values'][identity] = _json_value(tree)
            # Legacy hidden fields must not overwrite the new canonical data
            # on the following sync or after saving/reopening an old project.
            api.populate(author, edit, [identity])
            native = properties(edit, identity)
        for key, value in native.items():
            if key.startswith('eiem_texture.'):continue
            # Disabled keywords have no native entry; keep their explicit 0.
            if material.get(key) != value:material[key] = value
        for key in previous:
            if key.startswith('eiem_keyword.') and key not in native and key in material:material[key] = '0'
        # A newly introduced switch can create its keyword in the same action.
        keys = set(previous) | set(native) | {k for k in material.keys() if k.startswith(PREFIXES)}
        last = {k: str(material[k]) for k in keys if k in material}
        encoded = document.dumps(last)
        if material.get('eff_material_last') != encoded:material['eff_material_last'] = encoded


def texture_path(root, identity, slot):
    assets = _module("eiem_effect_assets")
    material = material_for(assets.material_object(root, identity))
    if not material:return None
    key = 'eiem_texture.' + slot
    value = str(material.get(key, '')).strip()
    sources = json.loads(material.get('eff_material_source_textures', '{}'))
    if not value or value == sources.get(key, ''):return None
    resources = _module('eiem_effect_game_resources')
    if resources.valid_logical_path(value):return None
    path = Path(bpy.path.abspath(value, library=material.library))
    document.require(path.is_file(), '贴图文件不存在：' + str(path))
    return path.resolve()


def image(root, identity, slot):
    path = texture_path(root, identity, slot)
    if not path:return None
    # File paths are authoritative, as in ordinary .mat export. Keep a lazy
    # preview buffer separate from any Image the author may be painting, and
    # reread externally edited files even when the chosen path is unchanged.
    key = str(path)
    stat = path.stat()
    stamp = str((stat.st_mtime_ns, stat.st_size))
    loaded = next((image for image in bpy.data.images if image.get('eff_material_file') == key), None)
    if loaded is None:
        loaded = bpy.data.images.load(key, check_existing=False)
        loaded['eff_material_file'] = key
    elif loaded.get('eff_material_file_stamp') != stamp:
        loaded.reload()
    if loaded.get('eff_material_file_stamp') != stamp:loaded['eff_material_file_stamp'] = stamp
    return loaded


def draw(layout, author):
    material = material_for(author)
    if not material:
        layout.label(text='材质尚未初始化，请重新加载特效资源', icon='ERROR')
        return
    material_editor.draw(layout, material)


def draw_sources(layout, material):
    author = material.eff_effect_author
    if not author:return
    ui = _module("eiem_effect_ui")
    header, body = layout.panel('eff_texture_provenance_' + material.get('eiem_section', ''), default_closed=True)
    header.label(text='特效贴图来源 / 用途',icon='TEXTURE')
    if body is None:return
    roles={'_PositionTexture':'VAT 位置 / 偏移数据（浮点；含义取决于原 Shader）',
           '_RotationTexture':'VAT 旋转 / 朝向数据', '_NormalMap':'表面法线；可随 UV 速度流动',
           '_MaskTex':'透明度 / 覆盖范围遮罩', '_DissolveTex':'溶解形状与边缘',
           '_MainTex':'主颜色 / 透明度', '_DisturbTex':'UV 扰动数据'}
    for slot in author.eff_effect_textures:
        data=json.loads(slot.source_metadata or '{}')
        if not data.get('identity'):continue
        box=body.box()
        features = _module("eiem_material_features")
        box.label(text=features.label('eiem_texture.'+slot.name))
        box.label(text=slot.name+' → '+data.get('name',''))
        ui.hint(box,roles.get(slot.name,'按原 Shader 采样；具体通道用途请结合原参数核对'))
        if 'width' in data:
            box.label(text='%d × %d · TextureFormat %d · %s · %d mip'%(data['width'],data['height'],data['format'],
                'Linear' if data['colorSpace']==0 else 'sRGB',data['mipCount']))
        for path in data.get('logicalPaths',[]):ui.hint(box,'游戏逻辑路径：'+path)
        if not data.get('logicalPaths'):ui.hint(box,'来源未记录逻辑路径；使用报告中的 CAB / PathID，不能按名称猜路径。')
        if data.get('preview'):ui.hint(box,'显示用预览：'+data['preview'])
        if data.get('format') in (17,20):ui.hint(box,'PNG 仅供查看；VAT 预览从原生浮点流读取。',icon='INFO')

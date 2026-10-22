"""Adapt effect bindings to the existing EFF Material editor and native writer.

The Material's eiem_* author properties are the editing surface, just as for
ordinary .mat imports. Empty fields remain only for saved-project migration.
"""
import json
from pathlib import Path

import bpy

from . import eiem_effect_document as document
from . import eiem_material_editor as material_editor
from . import eiem_native_export as native_export
from .nativepack import type_tree

_SCHEMAS = {}
PREFIXES = ('eiem_float.', 'eiem_int.', 'eiem_value4.', 'eiem_color.',
            'eiem_keyword.', 'eiem_tag.', 'eiem_texture_scale.', 'eiem_texture_offset.')


def material_for(author):
    return next((m for m in bpy.data.materials if m.eff_effect_author == author), None)


def _text(value):
    return str(value) if isinstance(value, (int, str)) else repr(value)


def _source(edit, identity):
    spec = edit.objects.get(identity, {})
    return next((r['path'] for r in spec.get('logicalPaths', []) if r.get('path')), '')


def properties(edit, identity):
    """Same portable property names used by load_material and .mat export."""
    from . import eiem_effect_preview as preview
    data = edit.value(identity)
    result = {}
    for group, kind in (('m_Floats', 'float'), ('m_Ints', 'int'), ('m_Colors', 'value4')):
        for row in data.get('m_SavedProperties', {}).get(group, []):
            value = row['second']
            result['eiem_' + kind + '.' + row['first']] = ','.join(_text(value[k]) for k in 'rgba') if kind == 'value4' else _text(value)
    if 'm_CustomRenderQueue' in data:result['eiem_render_queue'] = str(data['m_CustomRenderQueue'])
    for row in data.get('stringTagMap', []):result['eiem_tag.' + row['first']] = row['second']
    keywords = data.get('m_ValidKeywords', data.get('m_ShaderKeywords', '').split())
    for name in keywords:result['eiem_keyword.' + name] = '1'
    for index, row in enumerate(data.get('m_SavedProperties', {}).get('m_TexEnvs', [])):
        name, slot = row['first'], row['second']
        for kind, field in (('texture_scale', 'm_Scale'), ('texture_offset', 'm_Offset')):
            result['eiem_' + kind + '.' + name] = ','.join(_text(slot[field][k]) for k in 'xy')
        target = preview._target(edit, identity, ['m_SavedProperties', 'm_TexEnvs', index, 'second', 'm_Texture'])
        result['eiem_texture.' + name] = _source(edit, target) if target else ''
    return result


def setup(material, root, edit, identity):
    if material.get('eff_material_editor_version') == 1:return
    from . import eiem_effect_assets as assets
    from . import eiem_effect_preview as preview
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
        tree, _ = assets.NativeAssets(edit, root['eff_effect_id']).tree(shader)
        material['eiem_shader'] = tree['m_ParsedForm']['m_Name'].data
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
    path = Path(edit.state['source']) / edit.objects[identity]['schema']
    stat = path.stat()
    stamp = stat.st_mtime_ns, stat.st_size
    cached = _SCHEMAS.get(str(path))
    if cached is None or cached[0] != stamp:
        cached = stamp, type_tree.Schema.compile(json.loads(path.read_bytes())['nodes'])
        _SCHEMAS[str(path)] = cached
    return cached[1]


def _json_value(value):
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
    from . import eiem_effect_assets as assets
    material = material_for(assets.material_object(root, identity))
    if not material:return None
    key = 'eiem_texture.' + slot
    value = str(material.get(key, '')).strip()
    sources = json.loads(material.get('eff_material_source_textures', '{}'))
    if not value or value == sources.get(key, ''):return None
    document.require(not value.replace('\\', '/').lower().startswith(('assets/', 'packages/')),
                     '替换贴图请选择本地文件；游戏逻辑引用请恢复来源值：' + slot)
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

"""Blender author export through the shared nativepack writer.

Exports publish the single supported self-describing Format 3 directory.
"""
import configparser
import base64
import ctypes
import hashlib
import io
import importlib.util
import math
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import struct
import tempfile
import time
import types
import contextlib
try:
    from . import eiem_shared_resources as shared_resources
    from . import eiem_texture_compression as texture_compression
    from . import eiem_format as format_io
except ImportError:
    import eiem_shared_resources as shared_resources
    import eiem_texture_compression as texture_compression
    import eiem_format as format_io


# Native partial exports used to rebuild the Blender snapshot on every click,
# even when the selected Mesh had not changed. Keep a bounded session cache;
# the add-on's content token includes vertices, weights, shapes, slots and ID
# revisions, so an edit naturally misses without trusting mtimes.
_NATIVE_SNAPSHOT_CACHE = {}
_NATIVE_SNAPSHOT_CACHE_LIMIT = 128
_NATIVE_MATERIAL_FIELDS_CACHE = {}
_NATIVE_IMAGE_CACHE = {}
_NATIVE_IMAGE_CACHE_BYTES = 128 * 1024 * 1024
_FORMAT3_WRITER_LIBRARIES = {}


class _TypedAuthorWriter:
    def __init__(self):
        self.data = bytearray()

    def integer(self, value):
        self.data.extend(int(value).to_bytes(8, 'little', signed=False))

    def signed_integer(self, value):
        self.data.extend(int(value).to_bytes(8, 'little', signed=True))

    def number(self, value):
        self.data.extend(struct.pack('<d', float(value)))

    def string(self, value):
        encoded = str(value).encode('utf-8', 'strict')
        self.integer(len(encoded))
        self.data.extend(encoded)

    def blob(self, value):
        value = bytes(value)
        self.integer(len(value))
        self.data.extend(value)

    def strings(self, values):
        values = list(values)
        self.integer(len(values))
        for value in values:
            self.string(value)


def _typed_author_input(mod_id, templates, meshes, textures):
    writer = _TypedAuthorWriter()
    writer.string('EFF_AUTHOR_TYPED_INPUT/1')
    writer.string(mod_id)
    template_indices = {}
    writer.integer(len(templates))
    for index, row in enumerate(templates):
        key = (row['kind'], row['path'], row['asset'])
        template_indices[key] = index
        for value in (row['kind'], row['path'], row['asset'], row['cab']):
            writer.string(value)
        writer.signed_integer(row['pathId'])
        writer.string(row['file'])
        writer.string(row['sha256'])
    writer.integer(len(meshes))
    for row in meshes:
        for value in (row['partId'], row['meshId'], row['path'], row['material'],
                      row['targetPath'], row['targetAsset']):
            writer.string(value)
        index = template_indices.get(('Mesh', row['targetPath'], row['targetAsset']))
        if index is None:
            raise ValueError('Format 3 Mesh template is not declared: ' + row['targetPath'] + ' / ' + row['targetAsset'])
        writer.integer(index)
        writer.integer(row['vertices'])
        writer.integer(row['indices'])
        writer.strings(row['bonePaths'])
        writer.strings(row['shapeNames'])
        fields = row['fieldData']
        writer.integer(len(fields))
        for name in sorted(fields):
            writer.string(name)
            writer.blob(fields[name])
        writer.blob(row['authorData'])
    texture_templates = [index for index, row in enumerate(templates)
                         if row['kind'] == 'Texture2D']
    writer.integer(len(textures))
    for row in textures:
        writer.string(row['id'])
        writer.string(row['path'])
        if not texture_templates:
            raise ValueError('Format 3 Texture2D template is absent')
        writer.integer(texture_templates[0])
    return bytes(writer.data)


def write_format3_native_assets(library_path, mod_id, templates, meshes,
                                textures, output_path):
    output_path = Path(output_path).resolve()
    if not output_path.is_dir() or not (output_path / 'mod.ini').is_file():
        raise ValueError('Format 3 typed writer requires a prepared candidate directory')
    manifest = configparser.ConfigParser(interpolation=None, delimiters=('=',), strict=True)
    manifest.optionxform = str
    with (output_path / 'mod.ini').open(encoding='utf-8-sig') as stream:
        manifest.read_file(stream)
    if (manifest.get('Mod', 'format', fallback='') != '3' or
            manifest.get('Mod', 'id', fallback='') != mod_id):
        raise ValueError('Format 3 typed writer candidate identity differs')
    path = str(Path(library_path).resolve())
    library = _FORMAT3_WRITER_LIBRARIES.get(path)
    if library is None:
        library = ctypes.CDLL(path)
        library.EFF_AuthorAssetsAbi.argtypes = []
        library.EFF_AuthorAssetsAbi.restype = ctypes.c_uint
        library.EFF_AuthorAssets_ExportTypedW.argtypes = [
            ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t,
            ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_size_t]
        library.EFF_AuthorAssets_ExportTypedW.restype = ctypes.c_int
        _FORMAT3_WRITER_LIBRARIES[path] = library
    if library.EFF_AuthorAssetsAbi() != 3:
        raise ValueError('Format 3 typed author writer ABI is unsupported')
    input_bytes = _typed_author_input(mod_id, templates, meshes, textures)
    input_buffer = ctypes.c_char_p(input_bytes)
    response = ctypes.create_string_buffer(65536)
    status = library.EFF_AuthorAssets_ExportTypedW(
        ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_ubyte)), len(input_bytes),
        str(output_path), response, len(response))
    message = response.value.decode('utf-8', 'replace')
    if status:
        raise ValueError(message or 'Format 3 native author DLL failed')
    return message


def format3_native_records(format3, source, parts, textures, rules):
    targets_by_part = {}
    for rule in rules:
        if rule['hidden']:
            continue
        for part_id in rule['parts']:
            targets_by_part.setdefault(part_id, set()).update(
                (target['sourcePath'], target['sourceAsset'])
                for target in rule['targets'])

    meshes = []
    for part in parts:
        part_id = part['partId']
        targets = sorted(targets_by_part.get(part_id, ()))
        if not targets:
            raise ValueError('Format 3 Mesh Part has no declared target: ' + part_id)
        typed_targets = []
        for path, asset in targets:
            template = source.resolve('Mesh', path, asset)
            typed_targets.append((path, asset, template['native']['typeHash']))
        expected_type = part['nativeTypeHash']
        if any(type_hash != expected_type for _, _, type_hash in typed_targets):
            raise ValueError('Format 3 Mesh Part is shared by incompatible native templates: ' + part_id)
        target_path, target_asset, _ = typed_targets[0]
        section = format3.part_resource_id(part_id)
        meshes.append(dict(
            partId=section,
            meshId=format3.identifier('Mesh_' + section),
            path='meshes/' + section + '.effres',
            material=format3.identifier(part['material']),
            targetPath=target_path,
            targetAsset=target_asset,
            vertices=part['vertices'],
            indices=part['indices'],
            bonePaths=part['bonePaths'],
            shapeNames=part['shapeNames'],
            fieldData=part['fieldData'],
            authorData=part['authorData']))

    textures_out = []
    for texture in textures:
        identity = format3.identifier(texture['id'])
        textures_out.append(dict(
            id=identity,
            path='textures/' + identity + '.effres',
        ))
    return meshes, textures_out


class _MeshSnapshot(dict):
    """Keep native encodings with the exact snapshot that produced them."""
    def __init__(self, values):
        super().__init__(values)
        self.native_fields = {}


def _cached_mesh_snapshot(addon, obj, skin_cache=None):
    options = dict(return_snapshot=True)
    if skin_cache is not None:
        options['skin_cache'] = skin_cache
    token_fn = getattr(addon, '_mesh_resource_token', None)
    if token_fn is None:
        return addon.write_mesh(None, obj, **options), False
    token = token_fn(obj)
    if token is None:
        # A lost tracker also makes older entries unsafe after it is restored.
        _NATIVE_SNAPSHOT_CACHE.clear()
        return addon.write_mesh(None, obj, **options), False
    key = (int(obj.as_pointer()), token)
    cached = _NATIVE_SNAPSHOT_CACHE.get(key)
    if cached is not None:
        return cached, True
    snapshot = _MeshSnapshot(addon.write_mesh(None, obj, **options))
    # Keep the current version of each object, rather than letting old edits
    # evict still-useful snapshots of all its unchanged siblings.
    for previous in list(_NATIVE_SNAPSHOT_CACHE):
        if previous[0] == key[0]:
            del _NATIVE_SNAPSHOT_CACHE[previous]
    _NATIVE_SNAPSHOT_CACHE[key] = snapshot
    while len(_NATIVE_SNAPSHOT_CACHE) > _NATIVE_SNAPSHOT_CACHE_LIMIT:
        _NATIVE_SNAPSHOT_CACHE.pop(next(iter(_NATIVE_SNAPSHOT_CACHE)))
    return snapshot, False


def native_mesh_fields(modules, source, target, author, snapshot, slot, name, encode):
    # Validate current source bytes even on a hit; a matching manifest string
    # must not hide a missing/corrupt source snapshot.
    source.file(target)
    source_hash = source.rows[target['native']['cab']]['snapshot']['sha256']
    key = (target['identity'], source_hash, target['native']['typeHash'], slot, name,
           modules['mesh'].native_mesh, encode)
    cache = getattr(snapshot, 'native_fields', None)
    if cache is not None and key in cache:
        return cache[key]
    mesh, _ = modules['mesh'].native_mesh(source.tree(target), author, name)
    fields = {field: encode(mesh[field]) for field in modules['delta_apply'].GEOMETRY_FIELDS}
    if cache is not None:
        cache[key] = fields
    return fields

def pack_modules():
    local = Path(__file__).parent / 'nativepack'
    canonical = Path(__file__).resolve().parents[1] / 'nativepack'
    root = canonical if (canonical / 'author_source.py').is_file() else local
    if not (root/'author_source.py').is_file():
        raise ValueError('缺少 EFF 原生导出模块，请安装完整插件包')
    # Blender loads this file as part of the add-on package, while small
    # diagnostics import it directly with ``spec_from_file_location``.
    # Keep both entry paths on the same canonical package object.
    name = (__package__ or '_eiem_blender_export') + '.nativepack'
    loaded = sys.modules.get(name)
    loaded_root = Path(next(iter(getattr(loaded, '__path__', (''))), '')).resolve() if loaded else None
    if loaded is not None and loaded_root != root.resolve():
        # A Blender reload can retain a previous package object. Remove that
        # package and its children before importing from the canonical root.
        for key in list(sys.modules):
            if key == name or key.startswith(name + '.'):
                del sys.modules[key]
        loaded = None
    if loaded is None:
        spec = importlib.util.spec_from_file_location(name, root/'__init__.py',
                                                      submodule_search_locations=[str(root)])
        # The canonical core is a namespace package, with no initializer.
        if not (root/'__init__.py').exists():
            module = types.ModuleType(name); module.__path__ = [str(root)]
        else:
            module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        sys.modules[name] = module
    from importlib import import_module
    modules = {n: import_module(name+'.'+n) for n in
               ('author_source', 'mesh', 'delta_apply', 'type_tree', 'author_format3', 'effect_native')}
    dll = shared_resources.native_tool_file('eff_author_core.dll')
    return modules, dll

def token(value):
    return re.sub(r'[^A-Za-z0-9_.-]', '_', str(value))


def mesh_part_id(name, slot, split_count):
    original = str(name)
    normalized = token(original)
    # Sanitizing Chinese/spaced names can merge distinct objects. Keep the
    # suffix tied to the original name so partial exports use the same ID.
    if normalized != original:
        normalized += '_' + hashlib.sha256(original.encode('utf-8')).hexdigest()[:16]
    identifier = 'Mesh' + normalized.removeprefix('Mesh')
    return identifier if split_count == 1 else identifier + '_mat' + str(slot)


def baseline_for(objects, configured, physics_objects=()):
    if configured:
        path = Path(configured).resolve()
        if not (path/'source/manifest.json').is_file():
            raise ValueError('原生来源目录缺少 source/manifest.json：'+str(path))
        return path
    sources = {Path(value).resolve() for value in
               (str(o.get('eiem_author_package', '')).strip() for o in objects) if value}
    if not sources and physics_objects:
        sources = {Path(value).resolve() for value in
                   (str(group.eiem_physics.rig.get('eiem_author_package', '')).strip()
                    for group in physics_objects if group.eiem_physics.rig) if value}
    if len(sources) != 1 or not (next(iter(sources))/'source/manifest.json').is_file():
        raise ValueError('旧工程未保留完整原生来源，请在导出窗口指定包含 source/manifest.json 的解包目录')
    return next(iter(sources))


def material_source(primary, material, cache):
    """A material imported from another package retains that package's schema."""
    owner = str(material.get('eiem_author_package', '')).strip()
    if not owner:
        return primary
    package = Path(owner).resolve()
    if package == primary.package:
        return primary
    if package not in cache:
        cache[package] = type(primary)(package, primary.core)
    return cache[package]


def split_material(author):
    # The snapshot already has one consistent corner/skin/shape vertex map.
    # Keeping unused vertices avoids inventing a second remapping algorithm.
    for slot, (_, start, count, _, _, _) in enumerate(author['submeshes']):
        if not count: continue
        part = dict(author)
        part['indices'] = author['indices'][start:start+count]
        part['index_count'] = count
        part['submeshes'] = [(0, 0, count, 0, 0, author['vertex_count'])]
        yield slot, part

def material_fields(tree, overrides, encode):
    before = {key: encode(tree[key]) for key in tree.data}
    for key, value in overrides.items():
        if key.startswith('texture.'): continue
        if key == 'render_queue':
            # Queue is a Material field, not a shader float. Updating an
            # editor helper such as _QueueOffset does not update this field.
            try:
                queue = int(str(value))
            except (TypeError, ValueError) as error:
                raise ValueError('渲染队列必须是整数：'+str(value)) from error
            if queue != -1 and not 0 <= queue <= 5000:
                raise ValueError('渲染队列须为 -1（使用 Shader 默认值）或 0 至 5000')
            tree.set('m_CustomRenderQueue', queue)
            continue
        kind, _, prop = key.partition('.')
        if kind in ('texture_scale', 'texture_offset'):
            entries = tree['m_SavedProperties']['m_TexEnvs'].items()
            entry = next((p for p in entries if p['first'].data == prop), None)
            if entry is None:
                raise ValueError('原生材质没有此贴图槽：'+prop)
            values = [float(x) for x in str(value).split(',')]
            if len(values) != 2 or not all(math.isfinite(x) for x in values):
                raise ValueError('贴图缩放和偏移需要两个有限分量：'+key)
            entry['second'].set('m_Scale' if kind == 'texture_scale' else 'm_Offset',
                                dict(zip(('x','y'), values)))
            continue
        if kind == 'keyword':
            enabled = str(value).strip().lower()
            if enabled not in ('0', '1', 'false', 'true'):
                raise ValueError('Shader keyword 必须为 0/1 或 false/true：'+key)
            if not prop or any(character.isspace() for character in prop):
                raise ValueError('Shader keyword 名称无效：'+key)
            enabled = enabled in ('1', 'true')
            if 'm_ValidKeywords' in tree.data:
                valid = tree['m_ValidKeywords']
                names = [item.data for item in valid.items()]
                updated = names + [prop] if enabled and prop not in names else names
                if not enabled:
                    updated = [name for name in names if name != prop]
                if updated != names:
                    valid.set_items(updated)
                if 'm_InvalidKeywords' in tree.data:
                    invalid = tree['m_InvalidKeywords']
                    names = [item.data for item in invalid.items()]
                    if prop in names:
                        invalid.set_items([name for name in names if name != prop])
            elif 'm_ShaderKeywords' in tree.data:
                names = tree['m_ShaderKeywords'].data.split()
                updated = names + [prop] if enabled and prop not in names else names
                if not enabled:
                    updated = [name for name in names if name != prop]
                if updated != names:
                    tree.set('m_ShaderKeywords', ' '.join(updated))
            else:
                raise ValueError('原生材质没有 Shader keyword 字段：'+key)
            continue
        if kind == 'tag':
            entries = tree['stringTagMap'].items()
            entry = next((item for item in entries if item['first'].data == prop), None)
            if entry is None:
                tree['stringTagMap'].set_items(entries + [dict(first=prop, second=str(value))])
            elif entry['second'].data != str(value):
                entry.set('second', str(value))
            continue
        maps = {'float': 'm_Floats', 'int': 'm_Ints', 'value4': 'm_Colors', 'color': 'm_Colors'}
        if kind not in maps:
            raise ValueError('尚未支持的材质字段修改：'+key)
        entries = tree['m_SavedProperties'][maps[kind]].items()
        entry = next((p for p in entries if p['first'].data == prop), None)
        if entry is None: raise ValueError('原生材质没有此属性：'+key)
        if kind in ('float', 'int'):
            entry.set('second', float(value) if kind == 'float' else int(value))
        else:
            values = [float(x) for x in value.split(',')]
            if len(values) != 4: raise ValueError('材质向量需要四个分量：'+key)
            entry.set('second', dict(zip(('r','g','b','a'), values)))
    changed = {}
    for key in tree.data:
        encoded = encode(tree[key])
        if encoded != before[key]:
            changed[key] = encoded
    return changed


def native_material_fields(source, native, overrides, encode):
    # Shader fields depend on exact source bytes and scalar/keyword settings.
    # Texture bindings are resolved separately, on every export.
    source.file(native)
    source_hash = source.rows[native['native']['cab']]['snapshot']['sha256']
    settings = tuple(sorted((key, value) for key, value in overrides.items()
                            if not key.startswith('texture.')))
    key = (native['identity'], source_hash, native['native']['typeHash'], settings, encode)
    cached = _NATIVE_MATERIAL_FIELDS_CACHE.get(key)
    if cached is not None:
        return cached
    fields = material_fields(source.tree(native), overrides, encode)
    _NATIVE_MATERIAL_FIELDS_CACHE[key] = fields
    while len(_NATIVE_MATERIAL_FIELDS_CACHE) > 128:
        _NATIVE_MATERIAL_FIELDS_CACHE.pop(next(iter(_NATIVE_MATERIAL_FIELDS_CACHE)))
    return fields

def material_id(material):
    stored = str(material.get('eiem_native_id', ''))
    return stored or 'Material'+token(material.get('eiem_section', material.name)).removeprefix('Material')


def material_ids(materials):
    """Keep source-local section names from aliasing distinct Blender materials.

    Normal imports retain their familiar section ID. A copied material or a
    second source package can carry the same section, so assign a stable ID
    once and keep it in the .blend for subsequent partial exports.
    """
    result, used = {}, {}
    for material in sorted(materials, key=lambda item: item.name):
        mid = material_id(material)
        if mid in used and used[mid] is not material:
            source = '|'.join(str(material.get(key, '')) for key in
                              ('eiem_author_package', 'eiem_source', 'eiem_target_asset'))
            suffix = hashlib.sha256((source + '|' + material.name).encode('utf-8')).hexdigest()[:12]
            mid = mid + '_' + suffix
        if mid in used:
            raise ValueError('不同材质的导出 ID 冲突：' + material.name)
        material['eiem_native_id'] = mid
        used[mid] = material
        result[material.as_pointer()] = mid
    return result


def shape_controls(addon, obj, include_hotkeys):
    """Serialize the validated author controls using their native channel names."""
    addon.sync_new_shape_controls(obj)
    result = []
    for control in obj.data.eiem_shape_controls:
        record = dict((key, getattr(control, key)) for key in
                      ('shape', 'enabled', 'identity', 'label', 'default',
                       'minimum', 'maximum', 'hotkey_increase',
                       'hotkey_decrease', 'hotkey_speed'))
        if not control.enabled:
            result.append(record)
            continue
        keys = obj.data.shape_keys
        key = keys.key_blocks.get(control.shape) if keys else None
        if key is None or key == keys.reference_key:
            raise ValueError('形态键控制缺少有效频道：' + obj.name + ' / ' + control.shape)
        record['shape'] = addon.shape_channel_name(obj, key)
        if control.automatic:
            record['default'], record['minimum'], record['maximum'] = (
                key.value, key.slider_min, key.slider_max)
        if not include_hotkeys:
            record['hotkey_increase'] = record['hotkey_decrease'] = ''
        result.append(record)
    return result

def _texture_image_signature(addon, image, source_color_space, source_format):
    """Identify one image snapshot for the duration of an export."""
    try:
        pointer = int(image.as_pointer())
    except (AttributeError, TypeError, ValueError):
        pointer = id(image)
    try:
        source = str(addon.image_absolute_path(image) or '')
    except (AttributeError, RuntimeError, TypeError, ValueError):
        source = str(getattr(image, 'filepath', '') or '')
    stamp = None
    if source:
        try:
            stat_result = Path(source).stat()
            stamp = (stat_result.st_mtime_ns, stat_result.st_size)
        except OSError:
            pass
    revision = getattr(addon, '_owner_revision', lambda value: None)(image)
    return (pointer, source, stamp, revision,
            str(image.get('eiem_mipmaps', 'true')),
            int(source_color_space),
            str(source_format),
            str(image.get('eiem_filter', '1')),
            str(image.get('eiem_wrap', '0')),
            str(image.get('eiem_aniso', '1')),
            str(image.get('eiem_mip_bias', '0')))


def texture_bitmap(path, cache_disk=False):
    """Decode exact captured file bytes; dirty/packed Blender images bypass reuse."""
    from PIL import Image
    if not cache_disk:
        with Image.open(path) as image:
            return image.convert('RGBA')
    encoded = Path(path).read_bytes()
    key = hashlib.sha256(encoded).digest()
    cached = _NATIVE_IMAGE_CACHE.pop(key, None)
    if cached is None:
        with Image.open(io.BytesIO(encoded)) as image:
            rgba = image.convert('RGBA')
            cached = (rgba.size, rgba.tobytes())
    if len(cached[1]) <= _NATIVE_IMAGE_CACHE_BYTES:
        _NATIVE_IMAGE_CACHE[key] = cached
        while (len(_NATIVE_IMAGE_CACHE) > 128 or
               sum(len(value[1]) for value in _NATIVE_IMAGE_CACHE.values()) > _NATIVE_IMAGE_CACHE_BYTES):
            _NATIVE_IMAGE_CACHE.pop(next(iter(_NATIVE_IMAGE_CACHE)))
    return Image.frombytes('RGBA', cached[0], cached[1])


def _texture_resource_id(section, image, color_space, target, width, height,
                         mip_count, settings):
    """Share one author image across bindings with identical sampling semantics."""
    section = str(image.get('eiem_section', '') or section).strip()
    owner = str(image.get('eiem_author_package', '')).replace('\\', '/').casefold()
    profile = '\0'.join((
        owner, section.casefold(), str(color_space), target['name'],
        str(width), str(height), str(mip_count), str(settings['filter']),
        str(settings['wrap']), str(settings['aniso']), str(settings['mipBias']),
    ))
    label = section.removeprefix('Texture') or Path(
        getattr(image, 'filepath', '') or image.name).stem or image.name
    suffix = hashlib.sha256(profile.encode('utf-8')).hexdigest()[:12]
    return 'Texture' + token(label)[:70] + '_' + suffix


def collect_material(addon, source, mat, images, textures, temporary, encode,
                     existing=None, texture_only=False, mid=None,
                     texture_cache=None, include_native_fields=True,
                     texture_inputs=None):
    texture_cache = texture_cache if texture_cache is not None else {}
    mid = mid or material_id(mat)
    matpath = str(mat.get('eiem_target_path', '') or mat.get('eiem_source', ''))
    matasset = str(mat.get('eiem_target_asset', '') or mat.get('eiem_name', mat.name))
    values, _ = addon.material_override_payload(mat, images, force=True)
    props = dict(line.split('=', 1) for line in values if '=' in line)
    overrides = {k: v for k, v in props.items() if k not in
                 ('format', 'version', 'overrides', 'source', 'name', 'shader')}
    # A published authored slot remains exportable even when Blender's baseline
    # now equals its path. Original inherited slots have no published binding.
    if texture_only and existing is not None and mid in existing:
        bindings = addon.parse_json_property(mat, 'eiem_texture_sections_json', {})
        for key in existing[mid]:
            if not key.startswith('texture.') or key in overrides:
                continue
            value = str(mat.get('eiem_' + key, '')).strip()
            if value and not value.replace('\\', '/').lower().startswith('assets/'):
                overrides[key] = addon.resolve_material_texture(mat, key, value, images, bindings)
    needs_texture_template = any(key.startswith('texture.') for key in overrides)
    native = None
    if (include_native_fields and not texture_only) or needs_texture_template:
        native = source.resolve('Material', matpath, matasset)
    edits = []
    for key, section in overrides.items():
        if not key.startswith('texture.'):
            continue
        prop = key.removeprefix('texture.')
        pointer = next((x['pointer'] for x in native['details']['textures'] if x['property'] == prop), None)
        if pointer is None:
            raise ValueError('原生材质没有此贴图槽：' + prop)
        if pointer['isNull']:
            # The slot exists; only its value is null. An already referenced
            # Texture2D supplies the native schema, not the new slot's pixels.
            pointer = next((entry['pointer'] for entry in native['details']['textures']
                            if not entry['pointer']['isNull'] and entry['pointer'].get('identity')), None)
            if pointer is None:
                raise ValueError('材质来源没有可用的 Texture2D 结构模板：' + prop)
        tex = next(r for r in source.manifest['resources'] if r['identity'] == pointer['identity'])
        details = tex.get('details') or {}
        color_space = details.get('colorSpace')
        source_format = details.get('format')
        if color_space not in (0, 1):
            raise ValueError('源贴图缺少有效的 sRGB/Linear 元数据：' + tex['logicalPath'])
        image = images[section]
        disk = Path(addon.image_absolute_path(image)) if hasattr(addon, 'image_absolute_path') else Path(image.filepath)
        is_dds = disk.suffix.casefold() == '.dds'
        if not is_dds and (not isinstance(source_format, str) or not source_format):
            raise ValueError('源贴图缺少 Texture2D 格式元数据：' + tex['logicalPath'])
        cache_key = _texture_image_signature(addon, image, color_space, source_format or '')
        payload = texture_cache.get(cache_key)
        if payload is None:
            settings = dict(colorSpace=color_space,
                            filter=int(image.get('eiem_filter',1)), wrap=int(image.get('eiem_wrap',0)),
                            aniso=int(image.get('eiem_aniso',1)), mipBias=float(image.get('eiem_mip_bias',0)))
            if is_dds:
                if disk.is_file():
                    dds_bytes = disk.read_bytes()
                    dds_source = disk
                else:
                    packed = getattr(image, 'packed_file', None)
                    if packed is None:
                        raise ValueError('DDS 文件不存在：' + str(disk))
                    try:
                        dds_bytes = bytes(packed.data)
                    except (AttributeError, TypeError, ValueError) as error:
                        raise ValueError('无法读取打包的 DDS 文件：' + str(disk)) from error
                    dds_hash = hashlib.sha256(dds_bytes).hexdigest().upper()
                    dds_source = Path(temporary) / 'dds-passthrough' / (dds_hash + '.dds')
                    dds_source.parent.mkdir(parents=True, exist_ok=True)
                    dds_source.write_bytes(dds_bytes)
                parsed = texture_compression.parse_source_dds(dds_bytes)
                dds_hash = hashlib.sha256(dds_bytes).hexdigest().upper()
                source_pixels_hash = hashlib.sha256(parsed['pixels']).hexdigest().upper()
                target = dict(name=parsed['format'], unity=parsed['formatId'])
                payload = dict(parsed, pixelLabel=token(disk.stem or image.name),
                               sourcePixelsHash=source_pixels_hash, ddsHash=dds_hash,
                               ddsSourcePath=str(dds_source), **settings)
            else:
                direct = disk.is_file() and not getattr(image, 'is_dirty', False) and not getattr(image, 'packed_file', None)
                png = disk if direct else Path(temporary)/(hashlib.sha256(section.encode()).hexdigest()+'.png')
                if not direct:
                    addon.write_texture(png, image)
                with texture_bitmap(png, direct) as mip:
                    width, height = mip.size
                    base_pixels = texture_compression.rgba_pixels_top_down(mip)
                source_pixels_hash = hashlib.sha256(base_pixels).hexdigest().upper()
                target = texture_compression.target_format(source_format, color_space)
                converted = texture_compression.compress_rgba(
                    base_pixels, width, height,
                    str(image.get('eiem_mipmaps', 'true')) == 'true', source_format,
                    color_space, shared_resources.native_tool_file('texconv.exe'), flip_y=False)
                payload = dict(converted, pixels=converted['pixels'],
                               pixelLabel=token(Path(image.filepath).stem or image.name),
                               sourcePixelsHash=source_pixels_hash, ddsHash=None,
                               ddsSourcePath=None, **settings)
            texture_cache[cache_key] = payload
        target = dict(name=payload['format'], unity=payload['formatId'])
        settings = dict(colorSpace=color_space,
                        filter=int(image.get('eiem_filter',1)), wrap=int(image.get('eiem_wrap',0)),
                        aniso=int(image.get('eiem_aniso',1)), mipBias=float(image.get('eiem_mip_bias',0)))
        tid = _texture_resource_id(section, image, color_space, target,
                                   payload['width'], payload['height'], payload['mipCount'], settings)
        # The game texture is a template, not the identity of an authored
        # override. One author image/profile is shared across all material bindings.
        record = dict(payload, id=tid, section=tid,
                      sourcePath=tex['logicalPath'], sourceAsset=tex['name'])
        if tid in textures and any(textures[tid][key] != record[key] for key in
                                   ('pixels', 'pixelLabel', 'format', 'formatId',
                                    'sourcePixelsHash', 'width', 'height', 'mipCount',
                                    'ddsHash', 'colorSpace', 'filter', 'wrap', 'aniso', 'mipBias')):
            raise ValueError('同一作者材质的纹理绑定在导出期间发生冲突：' + mid + ' / ' + prop)
        textures[tid] = record
        if texture_inputs is not None:
            inputs = texture_inputs.setdefault(tid, dict(images=[]))
            if not any(existing_image is image for existing_image in inputs['images']):
                inputs['images'].append(image)
            if payload.get('ddsSourcePath'):
                prior_hash = inputs.get('dds_hash')
                if prior_hash and prior_hash != payload['ddsHash']:
                    raise ValueError('同一作者贴图身份对应不同 DDS 文件：' + tid)
                inputs['dds_path'] = Path(payload['ddsSourcePath'])
                inputs['dds_hash'] = payload['ddsHash']
        edits.append(dict(property=prop, texture=tid))
    author_values = list(values)
    return dict(id=mid, sourcePath=matpath, sourceAsset=matasset,
                nativeTypeHash=(native['native']['typeHash'] if native else '00' * 16),
                textureEdits=edits,
                authorData=format_io.bind_effmat_texture_ids(
                    ('\n'.join(author_values) + '\n').encode('utf-8'), edits),
                fieldData=(native_material_fields(source, native, overrides, encode)
                           if include_native_fields and not texture_only else {}))


def _update_texture_image_paths(root, texture_inputs):
    root = Path(root).resolve()
    for texture_id, inputs in texture_inputs.items():
        relative = inputs.get('dds_relative')
        if not relative:
            raise ValueError('已发布的 Texture DDS 路径缺失：' + texture_id)
        path = (root / relative).resolve()
        if root not in path.parents or not path.is_file():
            raise ValueError('已发布的 Texture DDS 文件无效：' + str(path))
        for image in inputs['images']:
            image.filepath = str(path)

def _replace_published_file(source, destination):
    # ReplaceFile preserves the runtime reader's delete-shared old snapshot.
    # MoveFileEx (used by os.replace) can reject that pinned block-store file.
    if os.name == 'nt' and Path(destination).is_file():
        import ctypes
        from ctypes import wintypes
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.ReplaceFileW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR,
                                    wintypes.LPCWSTR, wintypes.DWORD,
                                    ctypes.c_void_p, ctypes.c_void_p]
        api.ReplaceFileW.restype = wintypes.BOOL
        if not api.ReplaceFileW(str(destination), str(source), None, 0, None, None):
            raise OSError(ctypes.get_last_error(), 'Mod 文件原子发布失败：' + str(destination))
    else:
        os.replace(source, destination)


def package_changes(staging, destination, previous, candidate):
    """Compare validated packages, including content-preserving hardlinks."""
    staging, destination = Path(staging), Path(destination)
    old_files = previous['_files'] if previous is not None else set()
    new_files = candidate['_files']

    def same_bytes(a, b):
        if not b.is_file():
            return False
        # Both packages have already passed checksum validation. An unchanged
        # staging hardlink names the same file, so rereading both adds no proof.
        if os.path.samefile(a, b):
            return True
        if a.stat().st_size != b.stat().st_size:
            return False
        with a.open('rb') as first, b.open('rb') as second:
            while True:
                chunk = first.read(1024*1024)
                if chunk != second.read(1024*1024):
                    return False
                if not chunk:
                    return True

    changed = [name for name in sorted(new_files) if not same_bytes(staging/name, destination/name)]
    removed = sorted(old_files - new_files)
    return changed, removed


def _prepare_publication_file(source, destination):
    """Stage an immutable resource by hardlink where the filesystem allows."""
    source, destination = Path(source), Path(destination)
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def publish(staging, destination, reader, previous=None, candidate=None):
    staging = Path(staging).resolve()
    destination = Path(destination).resolve()
    marker = destination/'mod.ini'
    if destination.exists() and any(destination.iterdir()):
        if not marker.is_file():
            raise ValueError('不能覆盖缺少 mod.ini 的目录，请选择新的 Mod 输出目录')
        if previous is None:previous = reader(destination)
    if candidate is None:candidate = reader(staging)
    changed, removed = package_changes(staging, destination, previous, candidate)
    if not changed and not removed:
        return previous if previous is not None else candidate
    release = getattr(reader, 'release_files', None)
    if release:
        release([root/name for root in (destination,staging) for name in changed + removed])
    # Keep the Mod directory identity: Windows readers/file browsers may retain
    # a handle which forbids renaming it. Prepare only changed payloads on the
    # same volume; replace each complete file atomically, then publish INI last.
    # Native generation publication still happens only after compilation.
    destination.mkdir(parents=True, exist_ok=True)
    pending = Path(tempfile.mkdtemp(prefix='.'+destination.name+'.publish-', dir=destination.parent))
    if pending.resolve().parent != destination.parent:
        raise ValueError('导出临时路径超出输出父目录')
    backup = pending/'backup'
    applied = []
    cleanup = True
    try:
        for name in changed:
            output = pending/'new'/name
            output.parent.mkdir(parents=True, exist_ok=True)
            _prepare_publication_file(staging/name, output)
        for name in changed:
            original = destination/name
            if original.is_file():
                saved = backup/name
                saved.parent.mkdir(parents=True, exist_ok=True)
                _prepare_publication_file(original, saved)
        for name in removed:
            saved = backup/name
            saved.parent.mkdir(parents=True, exist_ok=True)
            os.replace(destination/name, saved)
            applied.append(name)
        for name in sorted(changed, key=lambda value: value == 'mod.ini'):
            output = destination/name
            output.parent.mkdir(parents=True, exist_ok=True)
            _replace_published_file(pending/'new'/name, output)
            applied.append(name)
        return reader(destination)
    except Exception as error:
        try:
            for name in reversed(applied):
                saved = backup/name
                if saved.is_file():
                    _replace_published_file(saved, destination/name)
                else:
                    (destination/name).unlink()
        except Exception as rollback:
            cleanup = False
            raise RuntimeError('导出失败且回滚未完成，原文件备份保留在 '+str(backup)+
                               '：'+str(error)+'；'+str(rollback)) from error
        raise
    finally:
        if cleanup:
            shutil.rmtree(pending, ignore_errors=True)


@contextlib.contextmanager
def _export_temporary_directory(resource_cache, destination, **options):
    """Keep complete Format 3 staging separate from installed Mods."""
    package_parent = Path(destination).resolve().parent
    staging_parent = package_parent / '.eff-export-staging'
    cleanup_parent = False
    if package_parent.is_dir():
        staging_parent.mkdir(exist_ok=True)
        options['dir'] = str(staging_parent)
        cleanup_parent = True
    try:
        with tempfile.TemporaryDirectory(**options) as temporary:
            try:
                yield temporary
            finally:
                frozen = resource_cache.get('__frozen_files__', {})
                for stream, _signature, _result in frozen.values():
                    stream.close()
                frozen.clear()
    finally:
        if cleanup_parent:
            try:
                staging_parent.rmdir()
            except OSError:
                pass


def export_native_package(addon, destination, mesh_objects, *, armatures=None,
                          physics_objects=None, lod_levels=None,
                          include_switches=True, source_baseline='', effect_roots=None):
    cache = {'__frozen_files__': {}}
    try:
        objects = list(mesh_objects)
        primary = baseline_for(objects, source_baseline, physics_objects or ())
        packages = {primary}
        packages.update(Path(slot.material.get('eiem_author_package')).resolve()
                        for obj in objects for slot in obj.material_slots
                        if slot.material and slot.material.get('eiem_author_package'))
        if effect_roots is None and addon is not None:
            effect_roots = addon.effect_editor.export_roots((), objects, addon)
        modules, _ = pack_modules()
        for root in effect_roots or ():
            state = addon.effect_editor.synchronize(root).state
            packages.add(Path(state['source']).resolve())
        with contextlib.ExitStack() as locks:
            for package in sorted(packages, key=lambda p: str(p).casefold()):
                locks.enter_context(modules['author_source'].source_read_lock(package))
            return _export_native_package(
                addon, destination, objects, armatures=armatures,
                physics_objects=physics_objects, lod_levels=lod_levels,
                include_switches=include_switches, source_baseline=source_baseline,
                resource_cache=cache, effect_roots=effect_roots)
    finally:
        for stream, _signature, _result in cache['__frozen_files__'].values():
            stream.close()


def _export_native_package(addon, destination, mesh_objects, armatures=None,
                          physics_objects=None, lod_levels=None,
                          include_switches=True, source_baseline='', resource_cache=None,
                          effect_roots=None):
    import bpy
    started = time.perf_counter()
    detail = dict(meshSnapshot=0., materialTexture=0., meshEncoding=0., effect=0., effectAuthor=0., writeAuthor=0.)
    snapshot_hits = snapshot_misses = 0
    exported_physics = exported_skeletons = 0
    physics_resources = []
    # pack_modules() resolves the ABI-matched shared-tools vendor directory.
    # Do not prepend this add-on's bundled vendor: it may target another Python ABI.
    if bpy.context.mode != 'OBJECT': raise ValueError('请回到物体模式后导出')
    objects = list(mesh_objects)
    physics_objects = list(physics_objects or [])
    if not objects: raise ValueError('Format 3 导出需要至少一个 EFF Mesh')
    modules, dll = pack_modules()
    source = modules['author_source'].AuthorSource(baseline_for(objects, source_baseline, physics_objects),
                modules['author_source'].AuthorCore(dll))
    destination = Path(destination).resolve()
    resource_cache = resource_cache if resource_cache is not None else {}
    if (destination/'mod.ini').is_file():
        header = configparser.ConfigParser(interpolation=None, delimiters=('=',), strict=True)
        header.optionxform = str
        with (destination/'mod.ini').open(encoding='utf-8-sig') as stream:
            header.read_file(stream)
        if header.get('Mod', 'format', fallback='') != '3':
            raise ValueError('Blender 只读取或覆盖 Format 3 Mod；请先使用 Mod Manager 迁移旧格式。')
    if destination == source.package or source.package in destination.parents:
        raise ValueError('不能覆盖原生来源目录')
    bpy.context.view_layer.update()
    plan = addon.plan_switch_export(objects, include_switches=include_switches)
    if lod_levels is None:
        lod_levels = addon.lod_levels_for_export(objects, all_levels=True)
    if lod_levels:
        plan = addon.expand_lod_plan(plan, lod_levels)
    material_switch_by_object_slot = {}
    material_switch_definitions = []
    seen_material_switches = set()
    for family in plan['sources']:
        if not family:
            continue
        canonical = min(family, key=lambda obj: addon.mesh_lod_level(obj))
        original = addon.mesh_export_template(canonical)
        claimed_slots = set()
        for group in original.eiem_material_switches:
            definition = addon.material_switch_definition(group)
            if definition['identity'] in seen_material_switches:
                raise ValueError('材质切换组 ID 重复，请重新创建该组')
            if definition['slot'] in claimed_slots:
                raise ValueError(original.name + ' 的同一材质槽不能配置多个切换组')
            seen_material_switches.add(definition['identity'])
            claimed_slots.add(definition['slot'])
            definition['variable'] = '$material_' + definition['identity']
            definition['mesh'] = original
            material_switch_definitions.append(definition)
            for view in family:
                target_mesh = addon.mesh_export_template(view)
                if definition['slot'] >= len(target_mesh.material_slots):
                    raise ValueError(target_mesh.name + ' 缺少材质切换目标槽')
                material_switch_by_object_slot[(target_mesh.as_pointer(), definition['slot'])] = definition
    encode = modules['type_tree'].encode
    # Image sections are package-local.  Restrict lookup to the source package
    # being exported so a second imported Mod with the same section/path
    # cannot silently supply the first Mod's pixels.
    source_packages = {str(source.package).replace('\\', '/').casefold()}
    source_packages.update(
        str(o.get('eiem_author_package', '')).replace('\\', '/').casefold()
        for o in objects if o.get('eiem_author_package'))
    source_packages.update(
        str(slot.material.get('eiem_author_package', '')).replace('\\', '/').casefold()
        for obj in plan['objects'] for slot in addon.mesh_export_template(obj).material_slots
        if slot.material and slot.material.get('eiem_author_package'))
    source_packages.update(
        str(material.get('eiem_author_package', '')).replace('\\', '/').casefold()
        for switch in material_switch_definitions for material in switch['options']
        if material.get('eiem_author_package'))
    images = {}
    for image in bpy.data.images:
        section = str(image.get('eiem_section', ''))
        owner = str(image.get('eiem_author_package', '')).replace('\\', '/').casefold()
        if not section or (owner and owner not in source_packages):
            continue
        if section in images and owner != next(iter(source_packages), ''):
            continue
        images[section] = image
    selected_materials = set()
    for view in objects:
        original = addon.mesh_export_template(view)
        for slot in {polygon.material_index for polygon in original.data.polygons}:
            if slot >= len(original.data.materials) or original.data.materials[slot] is None:
                continue
            switch = material_switch_by_object_slot.get((original.as_pointer(), slot))
            if switch:
                selected_materials.update(addon.material_switch_export_options(switch,include_switches))
            else:
                selected_materials.add(original.data.materials[slot])
    material_sections = material_ids(selected_materials)
    parts, materials, textures, rules = [], {}, {}, []
    texture_inputs = {}
    texture_cache = {}
    material_sources = {}
    snapshots, part_cache, snapshot_rigs = {}, {}, {}
    skin_cache = {}
    switches = [dict(variable=var, label=group.name, key=key, stateCount=len(states),
                     stateValues=addon.switch_state_values(group), default=default)
                for group, states, default, key, var in plan['groups']]
    if include_switches:
        switches.extend(dict(variable=row['variable'], label=row['label'], key=row['key'],
                             stateCount=len(row['options']), stateValues=list(range(len(row['options']))),
                             default=row['default']) for row in material_switch_definitions)
    controls_by_object = {}
    clips_by_object = {}
    originals = {addon.mesh_export_template(view) for view in plan['objects']
                 if view not in plan['hidden']}
    addon.shape_animation.validate_export_members(originals)
    clips_by_object = {original.as_pointer(): addon.shape_animation.export_clips(
        original, addon, include_switches) for original in originals}
    _, _, hotkeys = addon.plan_shape_controls(list(originals),
                                             include_hotkeys=include_switches)
    controls_by_object = {original.as_pointer(): shape_controls(addon, original, include_switches)
                          for original in originals}
    with _export_temporary_directory(resource_cache, destination,
                                      prefix='eff-native-export-') as temporary:
        staging = Path(temporary)/'mod'
        staging.mkdir()
        resource_cache['__trusted_roots__'] = (staging.resolve(),)
        for group in plan['sources']:
            obj = group[0]
            path, asset, level = addon.lod.mesh_export_target(obj)
            target = source.resolve('Mesh', path, asset)
            members = []
            for view in group:
                if view in plan['hidden']: continue
                original = addon.mesh_export_template(view)
                identity = original.as_pointer()
                if identity not in snapshots:
                    phase = time.perf_counter()
                    snapshots[identity], hit = _cached_mesh_snapshot(addon, original, skin_cache)
                    detail['meshSnapshot'] += (time.perf_counter()-phase)*1000
                    snapshot_hits += int(hit);snapshot_misses += int(not hit)
                    snapshot_rigs[identity] = next((modifier.object for modifier in original.modifiers
                                                   if modifier.type == 'ARMATURE' and modifier.object), None)
                if identity not in part_cache:
                    cache = []
                    splits = list(split_material(snapshots[identity]))
                    for slot, author in splits:
                        mat = original.data.materials[slot] if slot < len(original.data.materials) else None
                        if mat is None: raise ValueError(original.name+' 缺少材质槽 '+str(slot))
                        switch = material_switch_by_object_slot.get((identity, slot))
                        variants = (addon.material_switch_export_options(switch,include_switches)
                                    if switch else [mat])
                        for variant in variants:
                            mid = material_sections[variant.as_pointer()]
                            if mid not in materials:
                                phase = time.perf_counter()
                                materials[mid] = collect_material(
                                    addon, material_source(source, variant, material_sources), variant,
                                    images, textures, temporary, encode, mid=mid,
                                    texture_cache=texture_cache,
                                    include_native_fields=False, texture_inputs=texture_inputs)
                                detail['materialTexture'] += (time.perf_counter()-phase)*1000
                        phase = time.perf_counter()
                        fields = native_mesh_fields(modules, source, target, author,
                                                    snapshots[identity], slot, asset, encode)
                        detail['meshEncoding'] += (time.perf_counter()-phase)*1000
                        pid = mesh_part_id(original.name, slot, len(splits))
                        controls = controls_by_object.get(identity, [])
                        common = dict(object=pid,vertices=author['vertex_count'],
                                      indices=len(author['indices']),bonePaths=author['bone_paths'],
                                      shapeNames=[c[0] for c in author['blend_channels']],shapeControls=controls,
                                      shapeOwner=str(original.data.get('eiem_control_id', '')),
                                      shapeAnimations=clips_by_object.get(identity, []),
                                      nativeTypeHash=target['native']['typeHash'],fieldData=fields)
                        bindings = list(plan['bindings'].get(view,[]))
                        if switch and include_switches:
                            for option_index, variant in enumerate(variants):
                                variant_id = pid + '_switch_' + switch['identity'] + '_' + str(option_index)
                                mid = material_sections[variant.as_pointer()]
                                parts.append(dict(common,partId=variant_id,material=mid,
                                    authorData=addon.format_io.encode_mesh(author),
                                    visibilityBindings=bindings+[dict(variable=switch['variable'],visible=[option_index])]))
                                cache.append(variant_id)
                        else:
                            selected_material=variants[0] if switch else mat
                            parts.append(dict(common,partId=pid,material=material_sections[selected_material.as_pointer()],
                                              authorData=addon.format_io.encode_mesh(author),
                                              visibilityBindings=bindings))
                            cache.append(pid)
                    part_cache[identity]=cache
                members.extend(part_cache[identity])
            key = tuple(members) if members else ('hidden', tuple(addon.mesh_export_template(v).as_pointer() for v in group))
            existing = next((r for r in rules if r['_key']==key),None)
            if existing:
                existing['targets'].append(dict(level=level,sourcePath=path,sourceAsset=asset))
            else:
                rules.append(dict(_key=key,sourcePath=path,sourceAsset=asset,hidden=not members,parts=members,
                                  targets=[dict(level=level,sourcePath=path,sourceAsset=asset)]))
        # Hide rules across LODs group by the exact original author's family.
        material_clips=None
        material_clips=[];seen_materials=set()
        for view in objects:
            for mat in sorted(selected_materials,
                              key=lambda material:material_sections[material.as_pointer()]):
                if mat and mat.as_pointer() not in seen_materials and mat.as_pointer() in material_sections:
                    seen_materials.add(mat.as_pointer());material_clips.extend(addon.material_animation.export_clips(mat,material_sections[mat.as_pointer()],addon,include_switches))
        phase = time.perf_counter()
        effects=addon.effect_editor.export_attachments(objects,parts,addon,effect_roots or ())
        detail['effect'] = (time.perf_counter()-phase)*1000
        for rule in rules:
            base=min(rule['targets'],key=lambda t:t['level'])
            rule['sourcePath'],rule['sourceAsset']=base['sourcePath'],base['sourceAsset']
            base['level']=0
        # Mesh weights determine which authored Physics chains belong in the
        # package; exporting them with the full author directory keeps the
        # dependency graph in one transaction.
        used_by_rig = {}
        for identity, snapshot in snapshots.items():
            rig = snapshot_rigs[identity]
            if rig is not None:
                used_by_rig.setdefault(rig, set()).update(
                    snapshot['bone_paths'][slot]
                    for weights, slots in snapshot['skin']
                    for weight, slot in zip(weights, slots) if weight > 0)
        bone_tables = {}
        for group in bpy.data.objects:
            if group.eiem_physics.kind != 'GROUP' or group in physics_objects:
                continue
            rig = group.eiem_physics.rig
            if rig not in used_by_rig:
                continue
            if rig not in bone_tables:
                author = sys.modules[addon.__package__ + '.eiem_physics_authoring']
                bone_tables[rig] = author.bones(rig)
            table = bone_tables[rig]
            paths = {table[node.bone_id][1][0] for node in group.eiem_physics.nodes
                     if node.role == 'MOVE' and node.bone_id in table}
            if paths & used_by_rig[rig]:
                physics_objects.append(group)
        if physics_objects:
            from importlib import import_module
            physics = import_module(modules['author_source'].__package__ + '.physics')
            author = sys.modules[addon.__package__ + '.eiem_physics_authoring']
            physics_document = sys.modules[addon.__package__ + '.eiem_physics_document']
            native_source = sys.modules[addon.__package__ + '.eiem_physics_source']
            evidence = native_source.read_evidence(source.package / 'physics/components.json')
            rigs = {obj.eiem_physics.rig for obj in physics_objects}
            for rig in rigs:
                groups = [obj for obj in physics_objects if obj.eiem_physics.rig == rig]
                document = author.author_document(groups, 'shared.skeleton')
                exported_physics += len(groups); exported_skeletons += 1
                skeleton = addon.skeleton_author_nodes(rig)
                mesh_bones = {identity: {snapshot['bone_paths'][slot]
                              for weights, slots in snapshot['skin']
                              for weight, slot in zip(weights, slots) if weight > 0}
                              for identity, snapshot in snapshots.items() if snapshot_rigs[identity] == rig}
                published_parts = {
                    identity: [modules['author_format3'].part_resource_id(part_id)
                               for part_id in part_ids]
                    for identity, part_ids in part_cache.items()}
                published_rules = [dict(rule, parts=[
                    modules['author_format3'].part_resource_id(part_id)
                    for part_id in rule['parts']]) for rule in rules]
                targets = {group['id']: physics.group_mesh_targets(
                               group, mesh_bones, published_rules, published_parts)
                           for group in document['groups']}
                data = physics.build_resource(source, evidence, document, skeleton, targets)
                resource_id = 'ResourcePhysics' + document['id']
                skeleton_bytes = addon.encode_skeleton(rig)
                document['skeleton'] = 'skeletons/physics_' + document['id'] + '.skeleton'
                author_data = addon.format_io.encode_format3_physics_snapshot(
                    physics_document.encode(document), skeleton_bytes)
                physics_resources.append(dict(
                    id=resource_id,
                    native=data,
                    author=author_data,
                    targets=sorted({target for rows in targets.values() for target in rows})))
        author_done = time.perf_counter()
        started_format3 = time.perf_counter()
        author_files = {}
        phase = time.perf_counter()
        roots_by_id = {root.get('eff_effect_id'): root for root in (effect_roots or ())}
        for attachment in effects or ():
            root = roots_by_id.get(attachment['id'])
            if root is None:
                raise ValueError('Format 3 Effect author root is missing: ' + attachment['id'])
            edit = addon.effect_editor.synchronize(root)
            relative = modules['author_format3'].identifier(attachment['id']) + '.effeffect'
            attachment_author = dict(attachment)
            owner = root.eff_effect.owner
            rig = addon.effect_editor.preview.mount_rig(owner) if owner else None
            if rig is not None:
                attachment_author['mountSkeleton'] = base64.b64encode(
                    addon.encode_skeleton(rig)).decode('ascii')
            author_files[relative] = addon.format_io.encode_format3_effect_source(
                edit.state['source'], edit.state, attachment_author)
        detail['effectAuthor'] = (time.perf_counter() - phase) * 1000
        source_packages = {source.package}
        source_packages.update(material_sources)
        templates = modules['author_format3'].resolve_templates(
            source_packages, rules, include_texture=bool(textures))
        native_meshes, native_textures = format3_native_records(
            modules['author_format3'], source, parts, textures.values(), rules)
        format3_staging = Path(temporary) / 'format3'
        format3_staging.mkdir()
        phase = time.perf_counter()
        modules['author_format3'].write_format3_directory(
            format3_staging, token(destination.name), parts, materials,
            list(textures.values()), rules, switches, material_clips, effects,
            physics=physics_resources, author_files=author_files)
        detail['writeAuthor'] = (time.perf_counter() - phase) * 1000
        tool = shared_resources.native_tool_file('eff_author_assets.dll')
        try:
            writer_report = write_format3_native_assets(
                tool, token(destination.name), templates, native_meshes,
                native_textures, format3_staging)
        except (OSError, ValueError) as error:
            raise ValueError('Format 3 native author writer could not complete: ' + str(error)) from error
        manifest = modules['author_format3'].read_format3_directory(format3_staging)
        format3_done = time.perf_counter()
        for texture_id in texture_inputs:
            texture_inputs[texture_id]['dds_relative'] = (
                'textures/' + modules['author_format3'].identifier(texture_id) + '.dds')
        source.assert_current()
        for material_baseline in material_sources.values():
            material_baseline.assert_current()

        def publication_reader(path):
            path = Path(path)
            if (path / 'mod.ini').is_file():
                header = configparser.ConfigParser(interpolation=None, delimiters=('=',), strict=True)
                header.optionxform = str
                with (path / 'mod.ini').open(encoding='utf-8-sig') as stream:
                    header.read_file(stream)
                if header.get('Mod', 'format', fallback='') != '3':
                    raise ValueError('Format 3 发布目标包含其他格式 Mod')
            return modules['author_format3'].read_format3_directory(path)

        package = publish(format3_staging, destination, publication_reader,
                          candidate=manifest)
        _update_texture_image_paths(destination, texture_inputs)
        publish_done = time.perf_counter()
        return dict(meshes=len(parts), materials=len(materials),
                    textures=len(native_textures), textureBindings=len(textures),
                    skeletons=exported_skeletons, physics=exported_physics, format=3,
                    rules=len(rules), nativeSubmission=True,
                    exportSeconds=time.perf_counter()-started,
                    effects=len(effects or []), exportDetailMs=detail,
                    meshSnapshotHits=snapshot_hits, meshSnapshotMisses=snapshot_misses,
                    exportCostsMs=dict(author=(author_done-started)*1000,
                        format3=(format3_done-started_format3)*1000,
                        publish=(publish_done-format3_done)*1000),
                    format3Writer=writer_report,
                    format3Files=len(package['_files']))

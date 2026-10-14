"""Blender format-2 export through the shared nativepack writer.

This entry only assembles author resources and publishes an offline candidate;
it does not call Unity or install a DLL. Geometry and material decoding remain
in the canonical nativepack modules and the native compiler.
"""
import hashlib
import importlib.util
import math
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
import time
import types


# Native partial exports used to rebuild the Blender snapshot on every click,
# even when the selected Mesh had not changed. Keep a bounded session cache;
# the add-on's content token includes vertices, weights, shapes, slots and ID
# revisions, so an edit naturally misses without trusting mtimes.
_NATIVE_SNAPSHOT_CACHE = {}
_NATIVE_SNAPSHOT_CACHE_LIMIT = 128


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
    root = local
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
               ('author_source', 'mesh', 'delta_apply', 'type_tree', 'ini_package', 'ini_reader')}
    dll = root/'eff_author_core.dll'
    if not dll.is_file():
        raise ValueError('缺少 eff_author_core.dll，请安装完整插件包')
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


def baseline_for(objects, configured):
    if configured:
        path = Path(configured).resolve()
        if not (path/'source/manifest.json').is_file():
            raise ValueError('原生来源目录缺少 source/manifest.json：'+str(path))
        return path
    sources = {Path(o.get('eiem_author_package', '')).resolve() for o in objects}
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

def _texture_image_signature(addon, image):
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
            str(image.get('eiem_linear', 'false')),
            str(image.get('eiem_filter', '1')),
            str(image.get('eiem_wrap', '0')),
            str(image.get('eiem_aniso', '1')),
            str(image.get('eiem_mip_bias', '0')))

def collect_material(addon, source, mat, images, textures, temporary, encode,
                     existing=None, texture_only=False, mid=None,
                     texture_cache=None, existing_package=None):
    from PIL import Image
    texture_cache = texture_cache if texture_cache is not None else {}
    mid = mid or material_id(mat)
    matpath = str(mat.get('eiem_target_path', '') or mat.get('eiem_source', ''))
    matasset = str(mat.get('eiem_target_asset', '') or mat.get('eiem_name', mat.name))
    native = source.resolve('Material', matpath, matasset)
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
        image = images[section]
        binding = hashlib.sha256((mid + '\0' + prop).encode('utf-8')).hexdigest()[:12]
        tid = 'Texture' + token(tex['name']) + '_' + binding
        cache_key = _texture_image_signature(addon, image)
        payload = texture_cache.get(cache_key)
        if payload is None:
            prior = next((t for t in (existing_package or {}).get('textures', []) if t['id'] == tid), None)
            disk = Path(addon.image_absolute_path(image)) if hasattr(addon, 'image_absolute_path') else Path(image.filepath)
            direct = disk.is_file() and not getattr(image, 'is_dirty', False) and not getattr(image, 'packed_file', None)
            png = disk if direct else Path(temporary)/(hashlib.sha256(section.encode()).hexdigest()+'.png')
            if not direct:
                addon.write_texture(png, image)
            with Image.open(png) as img:
                mip = img.convert('RGBA'); width, height = mip.size; raw = bytearray(); count = 0
                settings = dict(colorSpace=0 if str(image.get('eiem_linear', 'false')) == 'true' else 1,
                                filter=int(image.get('eiem_filter',1)), wrap=int(image.get('eiem_wrap',0)),
                                aniso=int(image.get('eiem_aniso',1)), mipBias=float(image.get('eiem_mip_bias',0)))
                expected_mips = max(width,height).bit_length() if str(image.get('eiem_mipmaps','true')) == 'true' else 1
                if prior and prior['width'] == width and prior['height'] == height and prior['mipCount'] == expected_mips and all(prior[k] == v for k,v in settings.items()):
                    resource = (existing_package or {})['_resources'][prior['file']]
                    pixels = resource['blocks']['pixels']
                    if pixels[:width*height*4] == mip.transpose(Image.Transpose.FLIP_TOP_BOTTOM).tobytes():
                        payload = dict(pixels=pixels, pixelLabel=Path(prior['file']).stem, format='RGBA32',
                                       width=width,height=height,mipCount=expected_mips,retained=True,**settings)
                while True:
                    if payload is not None:
                        break
                    raw.extend(mip.transpose(Image.Transpose.FLIP_TOP_BOTTOM).tobytes()); count += 1
                    if str(image.get('eiem_mipmaps', 'true')) != 'true' or mip.size == (1, 1):
                        break
                    mip = mip.resize((max(1, mip.width//2), max(1, mip.height//2)), Image.Resampling.BOX)
            if payload is None:
                payload = dict(pixels=bytes(raw), pixelLabel=token(Path(image.filepath).stem or image.name),
                          format='RGBA32', width=width, height=height, mipCount=count,
                          colorSpace=0 if str(image.get('eiem_linear', 'false')) == 'true' else 1,
                          filter=int(image.get('eiem_filter', 1)), wrap=int(image.get('eiem_wrap', 0)),
                          aniso=int(image.get('eiem_aniso', 1)), mipBias=float(image.get('eiem_mip_bias', 0)))
            texture_cache[cache_key] = payload
        # The game texture is a template, not the identity of an authored
        # override. Each material binding remains stable across pixel edits,
        # independent of other variants borrowing the same native texture.
        record = dict(payload, id=tid, section=tid,
                      sourcePath=tex['logicalPath'], sourceAsset=tex['name'])
        if tid in textures and textures[tid] != record:
            raise ValueError('同一作者材质的纹理绑定在导出期间发生冲突：' + mid + ' / ' + prop)
        textures[tid] = record
        edits.append(dict(property=prop, texture=tid))
    return dict(id=mid, sourcePath=matpath, sourceAsset=matasset,
                nativeTypeHash=native['native']['typeHash'], textureEdits=edits,
                fieldData={} if texture_only else material_fields(source.tree(native), overrides, encode))

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


def publish(staging, destination, reader):
    staging = Path(staging).resolve()
    destination = Path(destination).resolve()
    marker = destination/'mod.ini'
    previous = None
    if destination.exists() and any(destination.iterdir()):
        if not marker.is_file():
            raise ValueError('不能覆盖缺少 mod.ini 的目录，请选择新的 Mod 输出目录')
        previous = reader(destination)
    candidate = reader(staging)
    old_files = previous['_files'] if previous is not None else set()
    new_files = candidate['_files']

    def same_bytes(a, b):
        if not b.is_file() or a.stat().st_size != b.stat().st_size:
            return False
        # Both packages have already passed checksum validation. An unchanged
        # staging hardlink names the same file, so rereading both adds no proof.
        if os.path.samefile(a, b):
            return True
        with a.open('rb') as first, b.open('rb') as second:
            while True:
                chunk = first.read(1024*1024)
                if chunk != second.read(1024*1024):
                    return False
                if not chunk:
                    return True

    changed = [name for name in sorted(new_files) if not same_bytes(staging/name, destination/name)]
    removed = sorted(old_files - new_files)
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
            shutil.copy2(staging/name, output)
        for name in changed:
            original = destination/name
            if original.is_file():
                saved = backup/name
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(original, saved)
        for name in removed:
            saved = backup/name
            saved.parent.mkdir(parents=True, exist_ok=True)
            os.replace(destination/name, saved)
            applied.append(name)
        for name in sorted(changed, key=lambda value: (value == 'mod.ini', value)):
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


def _clone_staging_tree(source, destination):
    """Clone a partial-export input with same-volume hardlinks when possible."""
    source, destination = Path(source), Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    for path in source.rglob('*'):
        relative = path.relative_to(source)
        target = destination / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if not path.is_file():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if not path.stat().st_mode & stat.S_IWRITE:
            # Windows read-only attributes belong to the shared file, not one
            # link. A private writable copy lets cleanup/detach proceed without
            # changing the author's attributes.
            shutil.copy2(path, target)
            os.chmod(target, target.stat().st_mode | stat.S_IWRITE)
            continue
        try:
            os.link(path, target)
        except OSError:
            # A workspace and destination on different volumes cannot share
            # hardlinks; retain the old copy behavior for that case.
            shutil.copy2(path, target)


def _detach_staging_files(paths):
    """Break hardlinks before update_directory writes a staged resource."""
    for path in {Path(value) for value in paths}:
        if not path.is_file():
            continue
        fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.',
                                          suffix='.detach', dir=str(path.parent))
        os.close(fd)
        try:
            shutil.copy2(path, temporary)
            os.chmod(temporary, os.stat(temporary).st_mode | stat.S_IWRITE)
            _replace_published_file(temporary, path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def _partial_output_paths(staging, resource_scope, existing_ini, materials,
                          textures, parts, identifier, texture_paths=None):
    """Return every staged file that update_directory may write in place."""
    paths = [Path(staging) / 'mod.ini']
    if resource_scope in ('MATERIALS', 'TEXTURES'):
        for material in materials:
            if resource_scope == 'MATERIALS':
                section = identifier(material['id'])
                if section in existing_ini:
                    paths.append(Path(staging) / existing_ini[section]['path'])
        for texture in textures:
            relative = (texture_paths[texture['id']] if texture_paths is not None else
                        'textures/' + identifier(texture['pixelLabel']) + '.tex')
            path = Path(staging) / relative
            if texture.get('retained') and path.is_file():
                modules = pack_modules()[0]
                data = modules['ini_package'].encode_resource(
                    modules['ini_package'].PIXELS, '00' * 16,
                    modules['ini_package'].texture_blocks(texture))
                if path.read_bytes() == data:
                    continue
            paths.append(path)
    elif resource_scope == 'MESH':
        for part in parts:
            section = identifier(part['object'])
            if section in existing_ini:
                paths.append(Path(staging) / existing_ini[section]['path'])
    return paths

def export_native_package(*args, **kwargs):
    cache = {'__frozen_files__': {}}
    try:
        return _export_native_package(*args, resource_cache=cache, **kwargs)
    finally:
        for stream, _signature, _result in cache['__frozen_files__'].values():
            stream.close()


def _export_native_package(addon, destination, mesh_objects, armatures=None,
                          physics_objects=None, mesh_only=False, lod_levels=None,
                          include_switches=True, source_baseline='', resource_scope='ALL', resource_cache=None):
    import bpy
    started = time.perf_counter()
    vendor=Path(__file__).parent/'vendor'
    if vendor.is_dir() and str(vendor) not in sys.path: sys.path.insert(0,str(vendor))
    if resource_scope not in ('ALL', 'MESH', 'MATERIALS', 'TEXTURES'):
        raise ValueError('未知导出资源范围：' + resource_scope)
    if bpy.context.mode != 'OBJECT': raise ValueError('请回到物体模式后导出')
    objects = list(mesh_objects)
    physics_objects = list(physics_objects or [])
    if not objects: raise ValueError('没有选中的 EFF Mesh')
    if physics_objects and resource_scope != 'ALL': raise ValueError('新增 Physics 需要选择全部资源导出')
    modules, dll = pack_modules()
    source = modules['author_source'].AuthorSource(baseline_for(objects, source_baseline),
                modules['author_source'].AuthorCore(dll))
    destination = Path(destination).resolve()
    resource_cache = resource_cache if resource_cache is not None else {}
    def read_package(path):
        return modules['ini_reader'].read_package(path, resource_cache)
    def release_files(paths):
        identities = {(p.stat().st_dev,p.stat().st_ino) for p in paths if p.is_file()}
        for identity,(stream,_stamp,_result) in resource_cache.get('__frozen_files__',{}).items():
            if identity[:2] in identities:
                stream.close()
    read_package.release_files = release_files
    existing_ini = None
    existing_package = None
    if (destination/'mod.ini').is_file():
        existing_package = read_package(destination)
        existing_ini = modules['ini_package'].ini_document(destination)
    if resource_scope != 'ALL' and existing_ini is None:
        raise ValueError('部分导出需要已有的完整 Mod；首次请选择全部资源')
    if __package__:
        from .eiem_offline_reload import reload_context, compile_reload, prepare_static_sources, publish_reload
    else:
        from eiem_offline_reload import reload_context, compile_reload, prepare_static_sources, publish_reload
    offline_context = reload_context(destination)
    if offline_context is None:
        raise ValueError('完整原生导出需要离线编译目标：请设置 EFF_RELOAD_GAME，或直接导出到游戏 plugin/mods/<Mod>，并提供经过结构校验的静态来源输入')
    if destination == source.package or source.package in destination.parents:
        raise ValueError('不能覆盖原生来源目录')
    bpy.context.view_layer.update()
    plan = (addon.plan_mesh_only_export(objects) if mesh_only or resource_scope != 'ALL' else
            addon.plan_switch_export(objects, include_switches=include_switches))
    if resource_scope == 'ALL':
        if lod_levels is None:
            lod_levels = addon.lod_levels_for_export(objects, all_levels=True)
        if lod_levels:
            plan = addon.expand_lod_plan(plan, lod_levels)
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
            if slot < len(original.data.materials) and original.data.materials[slot] is not None:
                selected_materials.add(original.data.materials[slot])
    material_sections = material_ids(selected_materials)
    parts, materials, textures, rules = [], {}, {}, []
    texture_cache = {}
    material_sources = {}
    snapshots, part_cache, snapshot_rigs = {}, {}, {}
    skin_cache = {}
    switches = [dict(variable=var, key=key, stateCount=len(states),
                     stateValues=addon.switch_state_values(group), default=default)
                for group, states, default, key, var in plan['groups']]
    controls_by_object = {}
    clips_by_object = {}
    if resource_scope in {'ALL', 'MESH'}:
        originals = {addon.mesh_export_template(view) for view in plan['objects']
                     if view not in plan['hidden']}
        # This is the same author-side validator the controls panels use. It
        # validates shared channels and ranges before any
        # resource files are written.
        addon.shape_animation.validate_export_members(originals)
        clips_by_object = {original.as_pointer(): addon.shape_animation.export_clips(original, addon, include_switches) for original in originals}
        _, _, hotkeys = addon.plan_shape_controls(list(originals),
                                                 include_hotkeys=include_switches)
        controls_by_object = {original.as_pointer(): shape_controls(addon, original, include_switches)
                              for original in originals}
    temporary_parent = (str(destination.parent)
                        if resource_scope != 'ALL' and destination.parent.is_dir()
                        else None)
    with tempfile.TemporaryDirectory(prefix='eff-native-export-',
                                      dir=temporary_parent) as temporary:
        staging = Path(temporary)/'mod'
        if resource_scope != 'ALL':
            _clone_staging_tree(destination, staging)
        else:
            staging.mkdir()
        # Partial staging may share files with the live author through links.
        # It must hash current bytes even if a foreign writer preserves mtime.
        resource_cache['__trusted_roots__'] = (staging.resolve(),) if resource_scope == 'ALL' else ()
        if resource_scope in ('MATERIALS', 'TEXTURES'):
            for view in objects:
                original = addon.mesh_export_template(view)
                slots = {polygon.material_index for polygon in original.data.polygons}
                for slot in sorted(slots):
                    mat = original.data.materials[slot] if slot < len(original.data.materials) else None
                    if mat is None:
                        raise ValueError(original.name+' 缺少材质槽 '+str(slot))
                    mid = material_sections[mat.as_pointer()]
                    if mid not in materials:
                        materials[mid] = collect_material(addon, material_source(source, mat, material_sources), mat, images, textures, temporary,
                                                         encode, existing_ini, resource_scope == 'TEXTURES', mid,
                                                         texture_cache, existing_package)
        for group in plan['sources']:
            if resource_scope in ('MATERIALS', 'TEXTURES'):
                break
            obj = group[0]
            path = str(obj.data.get('eiem_target_path', '') or obj.data.get('eiem_source', ''))
            asset = str(obj.data.get('eiem_target_asset', '') or obj.data.get('eiem_asset', ''))
            target = source.resolve('Mesh', path, asset)
            level_match = re.search(r'_lod(\d+)$', asset)
            level = int(level_match.group(1)) if level_match else 0
            members = []
            for view in group:
                if view in plan['hidden']: continue
                original = addon.mesh_export_template(view)
                identity = original.as_pointer()
                if identity not in snapshots:
                    snapshots[identity], _ = _cached_mesh_snapshot(addon, original, skin_cache)
                    snapshot_rigs[identity] = next((modifier.object for modifier in original.modifiers
                                                   if modifier.type == 'ARMATURE' and modifier.object), None)
                if identity not in part_cache:
                    cache = []
                    splits = list(split_material(snapshots[identity]))
                    for slot, author in splits:
                        mat = original.data.materials[slot] if slot < len(original.data.materials) else None
                        if mat is None: raise ValueError(original.name+' 缺少材质槽 '+str(slot))
                        mid = material_sections[mat.as_pointer()]
                        if resource_scope == 'ALL' and mid not in materials:
                            materials[mid] = collect_material(addon, material_source(source, mat, material_sources), mat, images, textures, temporary,
                                                              encode, mid=mid, texture_cache=texture_cache,
                                                              existing_package=existing_package)
                        fields = native_mesh_fields(modules, source, target, author,
                                                    snapshots[identity], slot, asset, encode)
                        pid = mesh_part_id(original.name, slot, len(splits))
                        controls = controls_by_object.get(identity, [])
                        parts.append(dict(partId=pid,object=pid,material=mid,vertices=author['vertex_count'],
                                          indices=len(author['indices']),bonePaths=author['bone_paths'],
                                          shapeNames=[c[0] for c in author['blend_channels']],shapeControls=controls,
                                          shapeOwner=str(original.data.get('eiem_control_id', '')),
                                          shapeAnimations=clips_by_object.get(identity, []),
                                          visibilityBindings=plan['bindings'].get(view,[]),
                                          nativeTypeHash=target['native']['typeHash'],
                                          fieldData=fields))
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
        for rule in rules:
            base=min(rule['targets'],key=lambda t:t['level'])
            rule['sourcePath'],rule['sourceAsset']=base['sourcePath'],base['sourceAsset']
            base['level']=0
        if resource_scope == 'ALL':
            modules['ini_package'].export_directory(staging,mod_id=token(destination.name),parts=parts,
                        materials=list(materials.values()),textures=list(textures.values()),rules=rules,
                        switches=switches,resource_cache=resource_cache,
                        key_switch_enabled=modules['ini_reader'].key_switch_enabled(
                            existing_ini['Mod'].get('key_switch_enabled', '1')) if existing_ini else True)
            # A selected Mesh that actually weights a new chain needs that
            # chain's Skeleton/Physics resource in every matching prefab.
            # Selection of the visual Mesh already requests this dependency.
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
                native_source = sys.modules[addon.__package__ + '.eiem_physics_source']
                evidence = native_source.read_evidence(source.package / 'physics/components.json')
                rigs = {obj.eiem_physics.rig for obj in physics_objects}
                ini = modules['ini_package'].ini_document(staging)
                for rig in rigs:
                    groups = [obj for obj in physics_objects if obj.eiem_physics.rig == rig]
                    document = author.author_document(groups, 'shared.skeleton')
                    skeleton = addon.skeleton_author_nodes(rig)
                    mesh_bones = {identity: {snapshot['bone_paths'][slot]
                                  for weights, slots in snapshot['skin']
                                  for weight, slot in zip(weights, slots) if weight > 0}
                                  for identity, snapshot in snapshots.items() if snapshot_rigs[identity] == rig}
                    targets = {group['id']: physics.group_mesh_targets(group, mesh_bones, rules, part_cache)
                               for group in document['groups']}
                    data = physics.build_resource(source, evidence, document, skeleton, targets)
                    relative = 'resources/physics_' + document['id'] + '.bin'
                    path = staging / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
                    ini['ResourcePhysics' + document['id']] = {'path': relative}
                modules['ini_package'].save_ini(staging, ini)
                read_package(staging)
        else:
            texture_paths = modules['ini_package'].texture_output_paths(
                staging, existing_ini, list(textures.values()), list(materials.values()))
            _detach_staging_files(_partial_output_paths(
                staging, resource_scope, existing_ini, list(materials.values()),
                list(textures.values()), parts, modules['ini_package'].identifier,
                texture_paths))
            modules['ini_package'].update_directory(staging,parts=parts,materials=list(materials.values()),
                        textures=list(textures.values()),texture_only=resource_scope == 'TEXTURES',
                        resource_cache=resource_cache)
        author_done = time.perf_counter()
        _detach_staging_files([staging / 'compiled.bin', staging / 'source-inputs.bin'])
        source_index = prepare_static_sources(staging, source.package, offline_context, previous=destination)
        prior_compiled = destination / 'compiled.bin'
        if prior_compiled.is_file() and not (staging / 'compiled.bin').is_file():
            shutil.copyfile(prior_compiled, staging / 'compiled.bin')
        offline = compile_reload(staging, offline_context, previous=destination, source_index=source_index)
        compile_done = time.perf_counter()
        package, offline = publish_reload(staging, destination,
            lambda: publish(staging,destination,read_package), offline_context, source_index, offline)
        texture_paths = modules['ini_package'].ini_document(destination)
        written_texture_files = {texture_paths[section]['path'] for section in texture_paths
                                 if section in textures and textures[section].get('_written', True)}
        return dict(meshes=len(parts),materials=0 if resource_scope == 'TEXTURES' else len(materials),
                    textures=len(written_texture_files),textureBindings=len(textures),
                    skeletons=len({obj.eiem_physics.rig for obj in physics_objects or []}),physics=len(physics_objects or []),format=2,
                    rules=len(rules) if resource_scope == 'ALL' else 0,
                    resourceScope=resource_scope,nativeSubmission=False,
                    exportSeconds=time.perf_counter()-started,
                    exportCostsMs=dict(author=(author_done-started)*1000,
                                       compile=(compile_done-author_done)*1000,
                                       publish=(time.perf_counter()-compile_done)*1000), **offline)

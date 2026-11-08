"""Schema-backed effect author state. No bpy, game installation or simulation.

Changed source objects and declared emitter additions/removals are exported.
Native validation/reconstruction is performed by AnimeStudio --build-eff-effects
against the preserved baseline.
"""
import copy
import base64
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import zlib

EDITABLE = {4, 21, 114, 198, 199}
LOCKED = {'m_GameObject', 'm_Script', 'm_Father', 'm_Children', 'm_ObjectHideFlags',
          'm_CorrespondingSourceObject', 'm_PrefabInstance', 'm_PrefabAsset'}
MODULE_NAMES = {
    'InitialModule': '初始参数', 'ShapeModule': '发射形状', 'EmissionModule': '发射',
    'SizeModule': '大小随生命周期', 'RotationModule': '旋转随生命周期',
    'ColorModule': '颜色随生命周期', 'UVModule': '贴图帧动画',
    'VelocityModule': '速度随生命周期', 'InheritVelocityModule': '继承速度',
    'LifetimeByEmitterSpeedModule': '寿命随发射器速度', 'ForceModule': '力',
    'ExternalForcesModule': '外部力', 'ClampVelocityModule': '限制速度', 'NoiseModule': '噪声',
    'SizeBySpeedModule': '大小随速度', 'RotationBySpeedModule': '旋转随速度',
    'ColorBySpeedModule': '颜色随速度', 'CollisionModule': '碰撞', 'TriggerModule': '触发',
    'SubModule': '子发射器', 'LightsModule': '灯光', 'TrailModule': '拖尾',
    'CustomDataModule': '自定义数据',
}
INTS = {'SInt8': (-128,127), 'UInt8': (0,255), 'char': (0,255),
        'short': (-32768,32767), 'SInt16': (-32768,32767), 'UInt16': (0,65535),
        'unsigned short': (0,65535), 'int': (-2**31,2**31-1), 'SInt32': (-2**31,2**31-1),
        'UInt32': (0,2**32-1), 'unsigned int': (0,2**32-1), 'Type*': (0,2**32-1),
        'SInt64': (-2**63,2**63-1), 'long long': (-2**63,2**63-1),
        'UInt64': (0,2**64-1), 'unsigned long long': (0,2**64-1), 'FileSize': (0,2**64-1)}


def require(condition, message):
    if not condition:
        raise ValueError('特效：' + message)


def digest(data):
    return hashlib.sha256(data).hexdigest().upper()


def pointer_type_matches(expected, actual):
    return expected == 'Object' or expected == actual or (
        expected == 'Texture' and actual in {'Texture2D', 'Cubemap', 'Texture3D', 'Texture2DArray', 'CubemapArray'})


def resolve_logical_asset(objects, expected, path, asset=''):
    """Resolve an exact game asset identity inside this effect's source graph."""
    require(isinstance(path, str) and path.strip(), '游戏逻辑路径不能为空')
    normalized = path.strip().replace('\\', '/').casefold()
    asset = asset.strip().casefold() if isinstance(asset, str) else ''
    path_matches = {}
    for spec in objects:
        for row in spec.get('logicalPaths') or []:
            candidate_path = str(row.get('path', '')).replace('\\', '/').casefold()
            candidate_asset = str(row.get('name', '')).casefold()
            if candidate_path == normalized:
                path_matches[spec['identity']] = (spec, candidate_asset)
    require(path_matches, '当前 .efffx 来源依赖图中未包含此游戏资源，现有构建器不能新增跨 Bundle 依赖：' + path)
    matches = {identity: spec for identity, (spec, candidate_asset) in path_matches.items()
               if not asset or candidate_asset == asset}
    require(matches, '逻辑路径存在，但没有匹配的资产名：' + path + ((' / ' + asset) if asset else ''))
    typed = {identity: spec for identity, spec in matches.items()
             if pointer_type_matches(expected, spec.get('type', ''))}
    require(typed, '逻辑路径与资产名存在，但目标类型不兼容 PPtr<' + expected + '>：' + path)
    matches = typed
    require(len(matches) == 1, '逻辑路径与资产名匹配到多个来源对象，请检查资产名：' + path)
    return next(iter(matches.values()))


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def safe_path(directory, relative):
    require(isinstance(relative, str) and '\\' not in relative and ':' not in relative and
            all(p not in ('', '.', '..') for p in relative.split('/')), '来源路径无效')
    root = Path(directory).resolve()
    path = root.joinpath(*relative.split('/')).resolve()
    require(path.is_relative_to(root), '来源路径越界')
    return path


def compile_schema(nodes):
    stack, root = [], None
    for row in nodes:
        level = row['level']
        require(type(level) is int and 0 <= level <= len(stack), '类型层级无效')
        node = dict(row, children=[])
        stack = stack[:level]
        if stack:
            stack[-1]['children'].append(node)
        else:
            require(root is None, '类型有多个根')
            root = node
        stack.append(node)
    require(root is not None, '缺少类型')
    return root


def load_source(directory):
    # The add-on and AnimeStudio share this mutex; no watchers or frame polling.
    try:
        from .nativepack.author_source import source_read_lock
    except ImportError:
        try:
            from tools.nativepack.author_source import source_read_lock
        except ImportError:
            from nativepack.author_source import source_read_lock
    directory = str(Path(directory).resolve())
    with source_read_lock(directory):
        raw = safe_path(directory, 'effect.json').read_bytes()
        baseline = safe_path(directory, 'source/manifest.json').read_bytes()
        catalog = json.loads(raw)
        require(catalog.get('format') == 'EFF_EFFECT_SOURCE' and catalog.get('version') == 1,
                '请选择 .efffx 内的 effect.json')
        receipt = json.loads(safe_path(directory, 'source/generated-files.json').read_bytes())
        require(receipt.get('format') == 'EFF_GENERATED_FILES/1', '缺少完整导出记录')

        def read(relative):
            data = safe_path(directory, relative).read_bytes()
            require(digest(data) == receipt['files'].get(relative, '').upper(), '来源文件校验失败：' + relative)
            return data

        require(digest(raw) == receipt['files'].get('effect.json', '').upper() and
                digest(baseline) == receipt['files'].get('source/manifest.json', '').upper(), '来源清单已改变')
        values, schemas, hashes = {}, {}, {}
        for obj in catalog['objects']:
            identity = obj['identity']
            require(identity not in values, '重复对象身份')
            if obj['state'] != 'parsed':
                continue
            data = read(obj['data'])
            values[identity] = json.loads(data)
            hashes[identity] = digest(data)
            file = obj['schema']
            if file not in schemas:
                schemas[file] = json.loads(read(file))
        return dict(format='EFF_EFFECT_WORKSPACE', version=1, source=directory,
                    sourceManifestSha256=digest(baseline), sourceEffectSha256=digest(raw),
                    catalog=catalog, schemas=schemas, values=values,
                    original=copy.deepcopy(values), hashes=hashes)


def load_edit(path):
    """Read a Blender/AnimeStudio edit document without applying it."""
    path = Path(path).resolve()
    try:
        value = json.loads(path.read_text('utf-8-sig'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError('特效编辑文档无法读取') from error
    require(value.get('format') == 'EFF_EFFECT_EDIT' and value.get('version') == 1,
            '特效编辑文档格式或版本不受支持')
    require(isinstance(value.get('objects'), list) and
            all(isinstance(row, dict) for row in value['objects']),
            '特效编辑文档缺少对象编辑列表')
    source = Path(value.get('source', '')).resolve()
    require(source.is_dir(), '特效编辑文档的 .efffx 来源不存在：' + str(source))
    value['_document_path'] = str(path)
    return value


class Editor:
    def __init__(self, state):
        require(state.get('format') == 'EFF_EFFECT_WORKSPACE' and state.get('version') == 1,
                '编辑数据格式不支持')
        self.state = state
        self.objects = {o['identity']: o for o in state['catalog']['objects']}
        self._schemas = {}

    def emission_controllers(self, identities=None):
        """Known imported components that can overwrite particle emission."""
        for identity in self.objects if identities is None else identities:
            spec=self.objects[identity]
            kind=(spec.get('script') or {}).get('class') or spec.get('name')
            if spec['classId']==114 and kind=='VFXAutoEmissiveRate' and identity in self.state['values']:
                yield identity,self.value(identity)

    def use_particle_emission(self, identities=None):
        """Keep the source intact; explicitly disable its automatic rate rule."""
        changed=[]
        for identity,value in self.emission_controllers(identities):
            if value.get('m_Enabled'):
                value['m_Enabled']=0;changed.append(identity)
        return changed

    def value(self, identity, path=()):
        value = self.state['values'][identity]
        for part in path:
            value = value[part]
        return value

    def schema(self, identity, path=()):
        payload = self.state['schemas'][self.objects[identity]['schema']]
        key = self.objects[identity]['schema']
        if key not in self._schemas:
            self._schemas[key] = compile_schema(payload['nodes'])
        schema = self._schemas[key]
        value = self.state['values'][identity]
        parent = None
        for part in path:
            schema = self._unwrap(schema)
            if schema['type'] == 'ReferencedObjectData':
                schema = self._managed_schema(payload, parent)
            if schema['type'] == 'Array':
                schema = schema['children'][1]
            else:
                schema = next(c for c in schema['children'] if c['name'] == part)
            parent, value = value, value[part]
        schema = self._unwrap(schema)
        if schema['type'] == 'ReferencedObjectData':
            schema = self._managed_schema(payload, parent)
        return schema

    @staticmethod
    def _managed_schema(payload, parent):
        require(parent and parent.get('type'), '空的多态数据不可编辑')
        desc = parent['type']
        found = [t for t in payload['managedTypes'] if (t['class'], t['namespace'], t['assembly']) ==
                 (desc['class'], desc['ns'], desc['asm'])]
        require(len(found) == 1, '多态类型不明确')
        return compile_schema(found[0]['nodes'])

    @staticmethod
    def _unwrap(schema):
        if schema['type'] not in {'string', 'TypelessData'} and len(schema['children']) == 1 and schema['children'][0]['type'] == 'Array':
            return schema['children'][0]
        return schema

    def editable(self, identity, path):
        obj = self.objects[identity]
        if obj['classId'] not in EDITABLE or identity not in self.state['values'] or not path or path[0] in LOCKED:
            return False
        # Keep managed registry membership/type descriptors, allow its data fields.
        if 'RefIds' in path:
            idx = path.index('RefIds')
            if len(path) <= idx + 2 or path[idx + 2] != 'data':
                return False
        if 'm_BurstCount' in path:
            return False  # Derived from the editable burst list.
        for end in range(len(path)):
            if self.schema(identity, path[:end])['type'].startswith('PPtr<'):
                return False  # Pointers use one atomic target picker.
        return True

    def set_scalar(self, identity, path, value):
        require(self.editable(identity, path), '此字段是只读来源/结构信息')
        kind = self.schema(identity, path)['type']
        if kind == 'bool':
            require(type(value) is bool, '需要布尔值')
        elif kind in INTS:
            lo, hi = INTS[kind]
            require(type(value) is int and lo <= value <= hi, '整数超出原生范围')
        elif kind in ('float', 'double'):
            require(type(value) in (int, float) and math.isfinite(value) and
                    (kind != 'float' or abs(value) <= 3.4028234663852886e38), '需要有限数字')
        else:
            require(kind == 'string' and isinstance(value, str), '此字段需要专用编辑器')
        self.value(identity, path[:-1])[path[-1]] = value
        if path[-1] == 'time' and 'm_Curve' in path:
            curve_path = path[:path.index('m_Curve') + 1]
            self.value(identity, curve_path).sort(key=lambda key: key['time'])

    def set_pointer(self, identity, path, target):
        require(self.editable(identity, path), '此引用不可编辑')
        kind = self.schema(identity, path)['type']
        require(kind.startswith('PPtr<'), '不是资源引用')
        current = self.value(identity, path)
        if target is None:
            current.update(m_FileID=0, m_PathID=0)
            return
        require(target in self.objects, '目标不在已解析来源图中')
        expected = kind[5:-1]
        require(pointer_type_matches(expected, self.objects[target]['type']), '目标资源类型不匹配')
        owner = self.objects[identity]['native']['cab'].casefold()
        obj = self.objects[target]
        target_cab = obj['native']['cab'].casefold()
        if owner == target_cab:
            file_id = 0
        else:
            # Exported typed references prove the native external index exactly.
            refs = [r for o in self.objects.values() if o['native']['cab'].casefold() == owner
                    for r in o['references'] if (r.get('cab') or '').casefold() == target_cab and r['fileId'] > 0]
            ids = {r['fileId'] for r in refs}
            require(len(ids) == 1, '目标不在原生依赖表中；不能猜测外部引用编号')
            file_id = ids.pop()
        current.update(m_FileID=file_id, m_PathID=obj['native']['pathId'])

    def set_pointer_by_logical(self, identity, path, logical_path, asset=''):
        kind = self.schema(identity, path)['type']
        require(kind.startswith('PPtr<'), '不是资源引用')
        target = resolve_logical_asset(self.objects.values(), kind[5:-1], logical_path, asset)
        self.set_pointer(identity, path, target['identity'])
        return target

    def array_change(self, identity, path, operation, index=0):
        require(self.editable(identity, path), '此列表是只读结构信息')
        items = self.value(identity, path)
        schema = self.schema(identity, path)
        require(schema['type'] == 'Array' and isinstance(items, list), '不是可编辑列表')
        require(operation in {'ADD', 'COPY', 'REMOVE'}, '未知列表操作')
        if operation in {'COPY', 'REMOVE'}:
            require(0 <= index < len(items), '列表项已改变')
        if operation == 'REMOVE':
            del items[index]
        else:
            require(len(items) < 100000, '列表过长')
            if operation == 'COPY':
                value = copy.deepcopy(items[index])
            elif items:
                value = copy.deepcopy(items[-1])
            else:
                value = self._default(schema['children'][1])
            items.append(value)
        if path[-1] == 'm_Curve':
            items.sort(key=lambda key: key['time'])
        if path[-1] == 'm_Bursts':
            self.value(identity, path[:-1])['m_BurstCount'] = len(items)

    @classmethod
    def _default(cls, schema):
        schema = cls._unwrap(schema)
        kind = schema['type']
        if kind == 'bool': return False
        if kind in INTS: return 0
        if kind in ('float', 'double'): return 0.0
        if kind == 'string': return ''
        if kind == 'Array': return []
        require(kind != 'ReferencedObjectData' and schema['children'], '该列表新增需要已存在的类型模板')
        result = {c['name']: cls._default(c) for c in schema['children']}
        # Explicit neutral defaults for native curve keys/burst entries only.
        if set(result) >= {'time', 'value', 'inSlope', 'outSlope', 'inWeight', 'outWeight'}:
            result.update(inWeight=1/3, outWeight=1/3)
        if set(result) >= {'time', 'countCurve', 'cycleCount', 'repeatInterval', 'probability'}:
            result.update(cycleCount=1, repeatInterval=0.01, probability=1.0)
            result['countCurve'].update(minMaxState=0, scalar=1.0)
        return result

    def export(self):
        if __package__:from . import eiem_effect_topology as topology
        else:import eiem_effect_topology as topology
        changes = []
        deleted = {i for n in self.state.get('source_hierarchy', []) if n['identity'] in self.state.get('removed_emitters', [])
                   for i in [n['identity']] + n['components']}
        for identity, value in self.state['values'].items():
            if self.objects[identity].get('template') or identity in deleted:continue
            value = copy.deepcopy(value)
            # Native ownership changes are rebuilt by the declared emitter
            # operations, never exposed as unrestricted raw-field edits.
            if self.objects[identity]['classId'] == 4:
                require(value['m_Children'] == topology.expected_children(self,identity), '节点子级与声明的发射器增删不一致')
                value['m_Children'] = copy.deepcopy(self.state['original'][identity]['m_Children'])
            if value == self.state['original'][identity]:
                continue
            require(self.objects[identity]['classId'] in EDITABLE, '只读对象被修改')
            changes.append(dict(identity=identity, sourceDataSha256=self.state['hashes'][identity], data=copy.deepcopy(value)))
        result = dict(format='EFF_EFFECT_EDIT', version=1, sourceManifestSha256=self.state['sourceManifestSha256'],
                    sourceEffectSha256=self.state['sourceEffectSha256'], source=self.state['source'], objects=changes)
        operations = topology.export(self)
        if operations:result['emitters'] = operations
        return result


def apply_edit(state, edit):
    """Apply an edit document, including authored resources and emitter topology."""
    require(edit.get('sourceManifestSha256', '').upper() ==
            state.get('sourceManifestSha256', '').upper(), '特效来源 manifest 摘要不一致')
    require(edit.get('sourceEffectSha256', '').upper() ==
            state.get('sourceEffectSha256', '').upper(), '特效来源 effect 摘要不一致')
    require(isinstance(edit.get('objects', []), list), '特效编辑对象列表无效')
    candidate = copy.deepcopy(state)
    editor = Editor(candidate)
    seen = set()
    for row in edit.get('objects', []):
        identity = row.get('identity')
        require(identity and identity not in seen, '特效编辑文档包含重复对象')
        seen.add(identity)
        require(identity in editor.objects and identity in candidate['values'],
                '特效编辑对象不在当前来源中：' + str(identity))
        require(row.get('sourceDataSha256', '').upper() ==
                candidate['hashes'].get(identity, '').upper(),
                '特效编辑对象来源摘要不一致：' + str(identity))
        require(editor.objects[identity].get('classId') in EDITABLE,
                '特效编辑对象不可写：' + str(identity))
        require(isinstance(row.get('data'), dict), '特效编辑对象数据不是对象：' + str(identity))
        try:
            from .nativepack import type_tree
        except ImportError:
            from nativepack import type_tree
        schema = type_tree.Schema.compile(
            candidate['schemas'][editor.objects[identity]['schema']]['nodes'])
        type_tree.from_data(schema, _native_input(row['data']))
        candidate['values'][identity] = copy.deepcopy(row['data'])

    _apply_authored_assets(candidate, edit.get('assets', []), editor)
    _apply_emitter_edits(candidate, edit.get('emitters'), editor)
    state.clear()
    state.update(candidate)
    return state


def _native_plain(value):
    if isinstance(value.data, bytes):
        return value.data
    if value.schema.type == 'Array':
        return [_native_plain(item) for item in value.items()]
    if isinstance(value.data, dict):
        if list(value.data) == ['Array']:
            return _native_plain(value.data['Array'])
        return {key: _native_plain(item) for key, item in value.data.items()}
    return value.data


def _native_input(value):
    if isinstance(value, dict):
        if value.get('encoding') == 'base64':
            require(set(value) == {'encoding', 'bytes'} and isinstance(value['bytes'], str),
                    '原生字节数组编码无效')
            try:
                return base64.b64decode(value['bytes'], validate=True)
            except (ValueError, base64.binascii.Error) as error:
                raise ValueError('原生字节数组编码无效') from error
        return {key: _native_input(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_native_input(item) for item in value]
    return value


def _workspace_plain(value):
    """Keep author bytes in the edit payload, not duplicated in Blender JSON state."""
    if isinstance(value, bytes):
        return {'encoding': 'author-payload', 'size': len(value)}
    if isinstance(value, dict):
        return {key: _workspace_plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_workspace_plain(item) for item in value]
    return value


def _identity_from_pointer(cab, pointer):
    if not isinstance(pointer, dict):
        return None
    try:
        file_id, path_id = int(pointer.get('m_FileID', 0)), int(pointer.get('m_PathID', 0))
    except (TypeError, ValueError):
        return None
    return cab + ':' + str(path_id) if file_id == 0 and path_id else None


def _json_path(value, path):
    current = value
    for field, index in re.findall(r'\.([^\.\[\]]+)|\[(\d+)\]', path):
        current = current[int(index)] if index else current[field]
    return current


def _references_for_data(spec, data, objects):
    """Keep source pointer metadata aligned with a cloned TypeTree value."""
    result = []
    for source in spec.get('references', []):
        field = source.get('field', '')
        try:
            pointer = _json_path(data, field)
            file_id, path_id = int(pointer.get('m_FileID', 0)), int(pointer.get('m_PathID', 0))
        except (AttributeError, KeyError, IndexError, TypeError, ValueError):
            continue
        cab = spec['native']['cab'] if file_id == 0 else (
            source.get('cab') if int(source.get('fileId', -1)) == file_id else None)
        identity = cab + ':' + str(path_id) if cab and path_id else None
        target = next((obj for key, obj in objects.items()
                       if identity and key.casefold() == identity.casefold()), None)
        result.append(dict(field=field, fileId=file_id, pathId=path_id, cab=cab,
                           isNull=not path_id or file_id < 0,
                           resolved=bool(target), identity=target.get('identity') if target else identity,
                           type=target.get('type') if target else source.get('type')))
    return result


def _decode_preview_png(row, class_id):
    encoded = row.get('previewPngBase64')
    if encoded is None:
        return None
    require(class_id == 28 and isinstance(encoded, str) and
            len(encoded) <= 44 * 1024 * 1024,
            '仅 Texture2D 作者资源可以携带 PNG 预览')
    try:
        payload = base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error) as error:
        raise ValueError('作者贴图 PNG 预览编码无效') from error
    require(33 <= len(payload) <= 32 * 1024 * 1024 and
            payload[:8] == b'\x89PNG\r\n\x1a\n' and
            struct.unpack('>I', payload[8:12])[0] == 13 and
            payload[12:16] == b'IHDR',
            '作者贴图 PNG 预览头无效或超过 32 MiB')
    width, height = struct.unpack('>II', payload[16:24])
    require(0 < width <= 4096 and 0 < height <= 4096 and
            width * height <= 16 * 1024 * 1024,
            '作者贴图 PNG 预览尺寸无效')
    offset, saw_idat, saw_iend = 8, False, False
    while offset + 12 <= len(payload):
        size = struct.unpack('>I', payload[offset:offset + 4])[0]
        end = offset + 12 + size
        require(end <= len(payload), '作者贴图 PNG 区块越界')
        kind = payload[offset + 4:offset + 8]
        data = payload[offset + 8:offset + 8 + size]
        checksum = struct.unpack('>I', payload[offset + 8 + size:end])[0]
        require(zlib.crc32(kind + data) == checksum, '作者贴图 PNG 校验值无效')
        if kind == b'IDAT':
            saw_idat = True
        elif kind == b'IEND':
            require(size == 0, '作者贴图 PNG 结束区块无效')
            saw_iend = True
            offset = end
            break
        offset = end
    require(saw_idat and saw_iend and offset == len(payload),
            '作者贴图 PNG 缺少图像数据或结束区块')
    return payload


def _apply_authored_assets(state, rows, editor):
    require(isinstance(rows, list) and len(rows) <= 4096, '作者资源列表无效')
    if not rows:
        return
    try:
        from . import eiem_effect_native
        from .nativepack import type_tree
    except ImportError:
        import eiem_effect_native
        from nativepack import type_tree
    reader = eiem_effect_native.Reader(editor)
    objects = editor.objects
    known = {identity.casefold() for identity in objects}
    additions, total = [], 0
    for row in rows:
        require(isinstance(row, dict), '作者资源记录无效')
        identity, template = row.get('identity'), row.get('template')
        require(isinstance(identity, str) and isinstance(template, str) and
                template in objects and identity.casefold() not in known,
                '作者资源缺少独立身份或模板')
        source = objects[template]
        require(source.get('state') in ('parsed', 'raw-backed') and
                source.get('classId') in (21, 28, 43) and
                not source.get('template'), '作者资源模板或类型不受支持')
        native = source['native']
        identity_cab, separator, path_id_text = identity.rpartition(':')
        require(separator and identity_cab.casefold() == native['cab'].casefold(),
                '作者资源必须使用模板的源文件')
        try:
            path_id = int(path_id_text)
        except (TypeError, ValueError) as error:
            raise ValueError('作者资源 PathID 无效') from error
        require(path_id != 0, '作者资源 PathID 无效')
        source_file = next(file for file in reader.manifest['serializedFiles']
                           if file['cab'].casefold() == native['cab'].casefold())
        source_ids = {int(item['pathId']) for item in source_file.get('objects', [])}
        require(path_id not in source_ids and
                all(item[0]['native']['pathId'] != path_id for item in additions
                    if item[0]['native']['cab'].casefold() == native['cab'].casefold()),
                '作者资源 PathID 与源对象冲突')
        tree, raw = reader.tree(template)
        require(hashlib.sha256(raw).hexdigest().upper() ==
                str(row.get('sourceObjectSha256', '')).upper(),
                '作者资源模板数据摘要不一致')
        encoded = row.get('payloadBase64')
        require(isinstance(encoded, str) and len(encoded) <= 384 * 1024 * 1024,
                '作者资源超过大小限制')
        try:
            payload = base64.b64decode(encoded, validate=True)
        except (ValueError, base64.binascii.Error) as error:
            raise ValueError('作者资源数据格式无效') from error
        total += len(payload)
        preview_png = _decode_preview_png(row, source['classId'])
        if preview_png is not None:
            total += len(preview_png)
        require(total <= 256 * 1024 * 1024, '作者资源合计超过 256 MiB')
        decoded = type_tree.decode(tree.schema, payload)
        require(type_tree.encode(decoded) == payload, '作者资源数据无法往返')
        data = _native_plain(decoded)
        type_tree.from_data(tree.schema, data)
        data = _workspace_plain(data)
        spec = copy.deepcopy(source)
        spec['identity'] = identity
        spec['state'] = 'parsed'
        spec['native'] = dict(native, pathId=path_id)
        spec['template'] = template
        spec['owner'] = None
        spec['name'] = str(data.get('m_Name', source.get('name', '')))
        spec['logicalPaths'] = []
        spec['references'] = []
        state['catalog']['objects'].append(spec)
        state['values'][identity] = data
        state['original'][identity] = copy.deepcopy(data)
        state['hashes'][identity] = hashlib.sha256(dumps(data).encode('utf-8')).hexdigest().upper()
        objects[identity] = spec
        known.add(identity.casefold())
        additions.append((spec, data))
    for spec, data in additions:
        spec['references'] = _references_for_data(objects[spec['template']], data, objects)
    state['author_assets'] = copy.deepcopy(rows)


def _apply_emitter_edits(state, operations, editor):
    if operations is None:
        return
    require(isinstance(operations, dict) and set(operations) == {'added', 'removed'} and
            isinstance(operations['added'], list) and isinstance(operations['removed'], list) and
            len(operations['added']) <= 4096 and len(operations['removed']) <= 1024,
            '发射器操作数据无效')
    try:
        from . import eiem_effect_topology as topology
        from . import eiem_effect_native
        from .nativepack import type_tree
    except ImportError:
        import eiem_effect_topology as topology
        import eiem_effect_native
        from nativepack import type_tree
    hierarchy = state['catalog'].get('hierarchy')
    require(isinstance(hierarchy, list) and hierarchy, '发射器来源缺少有效树形结构')
    state.setdefault('source_hierarchy', copy.deepcopy(hierarchy))
    for identity in operations['removed']:
        require(identity in editor.objects, '要删除的发射器不在来源中')
        topology.remove(editor, identity)

    declarations = operations['added']
    known = {identity.casefold() for identity in editor.objects}
    source_reader = eiem_effect_native.Reader(editor)
    pending, path_ids = [], set()
    for row in declarations:
        require(isinstance(row, dict), '发射器新增记录无效')
        identity, template, data = row.get('identity'), row.get('template'), row.get('data')
        require(isinstance(identity, str) and isinstance(template, str) and
                isinstance(data, dict) and template in editor.objects and
                identity.casefold() not in known,
                '发射器新增身份或模板无效')
        source = editor.objects[template]
        require(source.get('state') == 'parsed' and not source.get('template') and
                source.get('classId') in (1, 4, 114, 198, 199),
                '发射器仅支持可解析的来源组件模板')
        native = source['native']
        cab, separator, text_id = identity.rpartition(':')
        require(separator and cab.casefold() == native['cab'].casefold(),
                '发射器新增必须使用模板的源文件')
        try:
            path_id = int(text_id)
        except (TypeError, ValueError) as error:
            raise ValueError('发射器新增 PathID 无效') from error
        source_file = next(file for file in source_reader.manifest['serializedFiles']
                           if file['cab'].casefold() == native['cab'].casefold())
        source_ids = {int(item['pathId']) for item in source_file.get('objects', [])}
        require(path_id != 0 and (cab.casefold(), path_id) not in path_ids,
                '发射器新增 PathID 重复或无效')
        require(path_id not in source_ids, '发射器新增 PathID 与源对象冲突')
        require((cab.casefold(), path_id) not in {
            (obj['native']['cab'].casefold(), int(obj['native']['pathId']))
            for obj in editor.objects.values() if obj.get('template')},
            '发射器新增 PathID 与作者资源冲突')
        schema_data = state['schemas'][source['schema']]
        schema = type_tree.Schema.compile(schema_data['nodes'])
        data = _native_plain(type_tree.from_data(schema, data))
        spec = copy.deepcopy(source)
        spec['identity'] = identity
        spec['native'] = dict(native, pathId=path_id)
        spec['template'] = template
        spec['name'] = str(data.get('m_Name', source.get('name', '')))
        spec['owner'] = None
        spec['references'] = []
        editor.objects[identity] = spec
        state['catalog']['objects'].append(spec)
        state['values'][identity] = data
        state['original'][identity] = copy.deepcopy(data)
        state['hashes'][identity] = hashlib.sha256(dumps(data).encode('utf-8')).hexdigest().upper()
        pending.append((identity, template, data, spec))
        known.add(identity.casefold())
        path_ids.add((cab.casefold(), path_id))

    if not pending:
        return

    def pointer_identity(owner, pointer):
        identity = _identity_from_pointer(editor.objects[owner]['native']['cab'], pointer)
        return next((key for key in editor.objects if identity and
                     key.casefold() == identity.casefold()), None)

    by_owner = {}
    for identity, template, data, spec in pending:
        if spec['classId'] == 1:
            continue
        owner = pointer_identity(identity, data.get('m_GameObject'))
        require(owner and editor.objects[owner]['classId'] == 1 and
                editor.objects[owner].get('template'), '发射器组件必须挂在新增 GameObject 上')
        spec['owner'] = owner
        by_owner.setdefault(owner, []).append((identity, template, data, spec))

    source_nodes = {node['identity']: node for node in state['catalog']['hierarchy']}
    new_nodes = []
    for go_identity, go_template, go_data, go_spec in pending:
        if go_spec['classId'] != 1:
            continue
        source_node = source_nodes.get(go_template)
        require(source_node is not None, '发射器模板必须是来源树形节点')
        components = by_owner.get(go_identity, [])
        component_templates = [template for _, template, _, _ in components]
        require(len(component_templates) == len(source_node['components']) and
                set(component_templates) == set(source_node['components']) and
                len(set(component_templates)) == len(component_templates),
                '新增发射器必须复制完整组件')
        require(sum(spec['classId'] == 4 for _, _, _, spec in components) == 1 and
                sum(spec['classId'] == 198 for _, _, _, spec in components) == 1 and
                sum(spec['classId'] == 199 for _, _, _, spec in components) == 1,
                '新增发射器缺少 Transform/ParticleSystem/Renderer')
        component_by_template = {template: identity for identity, template, _, _ in components}
        declared = [pointer_identity(go_identity, item.get('component'))
                    for item in go_data.get('m_Component', [])]
        expected = [component_by_template.get(template) for template in source_node['components']]
        require(declared == expected, '新增 GameObject 的组件指针不一致')
        transform_identity = next(identity for identity, _, _, spec in components
                                  if spec['classId'] == 4)
        new_nodes.append(dict(identity=go_identity, name=go_data.get('m_Name', ''),
                              transform=transform_identity, parent=None, children=[],
                              components=[component_by_template[t] for t in source_node['components']]))

    node_by_transform = {node['transform']: node for node in hierarchy}
    node_by_transform.update((node['transform'], node) for node in new_nodes)
    for node in new_nodes:
        transform = state['values'][node['transform']]
        parent = pointer_identity(node['transform'], transform.get('m_Father'))
        require(parent in node_by_transform, '发射器父 Transform 不在有效 Prefab 中')
        node['parent'] = parent
        for child_identity, _, child_data, child_spec in pending:
            if child_spec['classId'] == 4 and pointer_identity(
                    child_identity, child_data.get('m_Father')) == node['transform']:
                node['children'].append(child_identity)
    for node in new_nodes:
        visited = {node['transform']}
        parent = node['parent']
        while parent in node_by_transform:
            require(parent not in visited, '发射器骨架不能形成循环')
            visited.add(parent)
            parent = node_by_transform[parent]['parent']
    for node in new_nodes:
        parent_node = node_by_transform[node['parent']]
        parent_node['children'].append(node['transform'])
        parent_children = state['values'][node['parent']].get('m_Children')
        require(isinstance(parent_children, list), '发射器父 Transform 缺少子节点列表')
        parent_children.append(dict(m_FileID=0,
                                    m_PathID=int(node['transform'].rsplit(':', 1)[1])))
    hierarchy.extend(new_nodes)
    for _, template, data, spec in pending:
        spec['references'] = _references_for_data(editor.objects[template], data, editor.objects)


def write_edits(state, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = dumps(Editor(state).export())
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(data, encoding='utf-8')
    temporary.replace(path)
    return len(json.loads(data)['objects'])

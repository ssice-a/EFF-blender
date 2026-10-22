"""Schema-backed effect author state. No bpy, game installation or simulation.

Changed source objects and declared emitter additions/removals are exported.
Native validation/reconstruction is performed by AnimeStudio --build-eff-effects
against the preserved baseline.
"""
import copy
import hashlib
import json
import math
from pathlib import Path

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


def write_edits(state, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = dumps(Editor(state).export())
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(data, encoding='utf-8')
    temporary.replace(path)
    return len(json.loads(data)['objects'])

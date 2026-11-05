"""Read-only sampling of source AnimationClip tracks, without editing the scene.

Stream coefficients are Unity's cubic polynomial in seconds. Binding hashes
follow AnimeStudio's CustomCurveResolver (CRC32 / CRC28 for RendererMaterial).
Animator transitions, scripts and ACL buffers with unsupported layouts remain
explicit diagnostics; selecting a clip never invents a complete skill sequence.
"""
import bisect
import copy
import math
import struct
import zlib

if __package__:
    from . import eiem_effect_document as document
    from .eiem_effect_native import plain
else:
    import eiem_effect_document as document
    from eiem_effect_native import plain


def crc(value):
    return zlib.crc32(value.encode('utf-8'))


def streamed_curves(data, count):
    raw = struct.pack('<%dI' % len(data), *data)
    curves = [[] for _ in range(count)]
    offset = 0
    while offset < len(raw):
        document.require(offset + 8 <= len(raw), '动画流帧头不完整')
        time, keys = struct.unpack_from('<fi', raw, offset)
        offset += 8
        document.require(0 <= keys <= count and offset + keys * 20 <= len(raw), '动画流关键帧范围无效')
        for _ in range(keys):
            index, a, b, c, d = struct.unpack_from('<i4f', raw, offset)
            offset += 20
            document.require(0 <= index < count, '动画流轨道索引越界')
            # The -FLT_MAX first frame seeds values; +Infinity is a sentinel.
            if math.isfinite(time):
                curves[index].append((time, (a, b, c, d)))
    return curves


def sample_stream(keys, time):
    if not keys:
        return None
    index = max(0, bisect.bisect_right(keys, time, key=lambda row: row[0]) - 1)
    start, (a, b, c, d) = keys[index]
    if start < -1e30 or a == b == c == 0:
        return d
    delta = max(0., time - start)
    return ((a * delta + b) * delta + c) * delta + d


class Clip:
    def __init__(self, data):
        self.name = data['m_Name']
        muscle = data['m_MuscleClip']
        self.start, self.stop = muscle['m_StartTime'], muscle['m_StopTime']
        self.loop = bool(muscle['m_LoopTime'])
        self.rate = data['m_SampleRate']
        self.bindings = data['m_ClipBindingConstant']['genericBindings']
        clip = muscle['m_Clip']['data']
        streamed = clip['m_StreamedClip']
        self.streams = streamed_curves(streamed['data'], streamed['curveCount'])
        self.dense = clip['m_DenseClip']
        self.constants = clip.get('m_ConstantClip', {}).get('data', [])
        self.warnings = []
        acl = data.get('m_AclCompressedBuffer', {})
        if clip.get('m_CompressedCurveCount') or any(acl.get(k) for k in ('TransformBufferData', 'FloatBufferData')):
            self.warnings.append('ACL 压缩轨道尚未解码；不套用不确定的绑定偏移')
        dense = self.dense
        if dense.get('m_ACLType') and not dense.get('m_SampleArray'):
            self.warnings.append('ACL Dense 轨道尚未解码')
        self.total = len(self.streams) + dense['m_CurveCount'] + len(self.constants)
        dimensions = sum(self.dimension(b) for b in self.bindings)
        if dimensions != self.total:
            self.warnings.append('绑定标量数与已解码轨道数不同：%d / %d' % (dimensions, self.total))
        self.safe_bindings = dimensions == self.total and not any('绑定偏移' in w for w in self.warnings)

    @staticmethod
    def dimension(binding):
        if binding['typeID'] == 4 and binding['attribute'] in (1, 2, 3, 4):
            return 4 if binding['attribute'] == 2 else 3
        return 1

    def sample(self, time, loop=False):
        duration = self.stop - self.start
        t = self.start + ((time % duration) if loop and duration > 0 else max(0, min(duration, time)))
        values = [sample_stream(keys, t) for keys in self.streams]
        dense = self.dense
        count, frames = dense['m_CurveCount'], dense['m_FrameCount']
        samples = dense.get('m_SampleArray', [])
        if count and len(samples) == count * frames:
            frame = max(0., min(frames - 1, (t - dense['m_BeginTime']) * dense['m_SampleRate']))
            lo = int(frame); hi = min(frames - 1, lo + 1); u = frame - lo
            values += [samples[lo * count + i] * (1 - u) + samples[hi * count + i] * u for i in range(count)]
        else:
            values += [None] * count
        return values + self.constants


def paths(edit):
    hierarchy = edit.state['catalog']['hierarchy']
    by_transform = {row['transform']: row for row in hierarchy}
    def path(row):
        if not row['parent']:
            return ''
        parent = path(by_transform[row['parent']])
        return (parent + '/' if parent else '') + row['name']
    result = {}
    for row in hierarchy:
        result.setdefault(crc(path(row)), []).append(row)
    return result


def material_property(data, attribute):
    fields = data.get('m_SavedProperties', {})
    candidates = {row['first']: ('m_Floats', i) for i, row in enumerate(fields.get('m_Floats', []))}
    candidates.update({row['first']: ('m_Colors', i) for i, row in enumerate(fields.get('m_Colors', []))})
    candidates.update({row['first'] + '_ST': ('m_TexEnvs', i) for i, row in enumerate(fields.get('m_TexEnvs', []))})
    matches = [(name, *target) for name, target in candidates.items() if crc(name) & 0xfffffff == attribute & 0xfffffff]
    return matches[0] if len(matches) == 1 else None


class Timeline:
    def __init__(self, edit, reader):
        self.clips = {}
        self.warnings = []
        for identity, spec in edit.objects.items():
            if spec['classId'] != 74:
                continue
            try:
                self.clips[identity] = Clip(plain(reader.tree(identity)[0]))
            except (KeyError, ValueError, struct.error) as error:
                self.warnings.append((spec.get('name') or identity) + '：' + str(error))
        self.paths = paths(edit)

    def evaluate(self, edit, identity, time, loop=False):
        """Return native Transform and per-Renderer Material overlays only."""
        clip = self.clips.get(identity)
        transforms, renderers = {}, {}
        warnings = list(self.warnings)
        if not clip:
            return transforms, renderers, warnings
        warnings.extend(clip.warnings)
        if not clip.safe_bindings:
            return transforms, renderers, warnings
        values = clip.sample(time, loop)
        offset = 0
        for binding in clip.bindings:
            size = clip.dimension(binding)
            sampled = values[offset:offset + size]; offset += size
            rows = self.paths.get(binding['path'], [])
            if len(rows) != 1 or any(v is None for v in sampled):
                warnings.append('动画目标路径未唯一解析：%s' % binding['path'])
                continue
            row = rows[0]
            if binding.get('isPPtrCurve'):
                warnings.append('PPtr 动画轨道尚未回放')
                continue
            if binding['typeID'] == 4 and binding['attribute'] in (1, 2, 3):
                transform = transforms.setdefault(row['transform'], copy.deepcopy(edit.value(row['transform'])))
                field = {1: 'm_LocalPosition', 2: 'm_LocalRotation', 3: 'm_LocalScale'}[binding['attribute']]
                transform[field] = dict(zip('xyzw', sampled))
            elif binding['customType'] == 22:
                ids = [i for i in row['components'] if edit.objects[i]['classId'] == binding['typeID']]
                for renderer in ids:
                    materials = renderers.setdefault(renderer, {})
                    spec = edit.objects[renderer]
                    mids = {r['identity'] for r in spec.get('references', []) if r.get('type') == 'Material' and r.get('identity')}
                    for mid in mids:
                        target = material_property(edit.value(mid), binding['attribute'])
                        if not target:
                            warnings.append('动画材质属性未解析：%s' % binding['attribute'])
                            continue
                        data = materials.setdefault(mid, copy.deepcopy(edit.value(mid)))
                        name, group, index = target
                        value = data['m_SavedProperties'][group][index]
                        component = (binding['attribute'] >> 28) & 3
                        if binding['attribute'] & 0x80000000 and group == 'm_Floats':
                            value['second'] = sampled[0]
                        elif group == 'm_Colors':
                            value['second']['rgba'[component]] = sampled[0]
                        elif group == 'm_TexEnvs':
                            value['second']['m_Scale' if component < 2 else 'm_Offset']['xy'[component % 2]] = sampled[0]
                        else:
                            warnings.append('动画属性类型尚未支持：' + name)
            else:
                warnings.append('动画绑定暂未回放：type=%s attribute=%s' % (binding['typeID'], binding['attribute']))
        return transforms, renderers, sorted(set(warnings))


def overlay(edit, values):
    state = dict(edit.state)
    state['values'] = dict(edit.state['values'], **values)
    state['_preview_base_values'] = edit.state['values']
    return document.Editor(state)

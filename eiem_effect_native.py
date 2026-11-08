"""Bounded reads of preserved effect resources for offline inspection.

No game/runtime dependency. Float VAT data must never pass through the PNG
display conversion. Native snapshots stay authoritative and are not modified.
"""
import hashlib
import base64
import json
from pathlib import Path

if __package__:
    from . import eiem_effect_document as document
    from .nativepack import type_tree
else:
    import eiem_effect_document as document
    from nativepack import type_tree


def plain(value):
    if isinstance(value.data, bytes):
        return value.data
    if value.schema.type == 'Array':
        return [plain(item) for item in value.items()]
    if isinstance(value.data, dict):
        if list(value.data) == ['Array']:
            return plain(value.data['Array'])
        return {key: plain(item) for key, item in value.data.items()}
    return value.data


class Reader:
    def __init__(self, edit):
        self.edit = edit
        self.source = Path(edit.state['source'])
        self.manifest = json.loads((self.source / 'source/manifest.json').read_bytes())
        self.receipt = json.loads((self.source / 'source/generated-files.json').read_bytes())['files']
        self.verified = set()
        self.schemas = {}

    def snapshot(self, spec):
        path = document.safe_path(self.source, spec['file'])
        if spec['file'] not in self.verified:
            with path.open('rb') as stream:
                signature = hashlib.file_digest(stream, 'sha256').hexdigest().upper()
            document.require(signature == spec['sha256'].upper(), '原生底稿摘要不匹配：' + spec['file'])
            self.verified.add(spec['file'])
        return path

    def tree(self, identity):
        spec = self.edit.objects[identity]
        authored = next((row for row in self.edit.state.get('author_assets', [])
                         if row.get('identity', '').casefold() == identity.casefold()), None)
        if authored:
            source_tree, _ = self.tree(authored['template'])
            payload = base64.b64decode(authored['payloadBase64'], validate=True)
            tree = type_tree.decode(source_tree.schema, payload)
            document.require(type_tree.encode(tree) == payload,
                             'Authored resource payload does not round-trip')
            return tree, payload
        if spec.get('template'):
            spec = self.edit.objects[spec['template']]
        native = spec['native']
        row = next(r for r in self.manifest['serializedFiles'] if r['cab'].lower() == native['cab'].lower())
        path = self.snapshot(row['snapshot'])
        document.require(0 <= native['offset'] <= path.stat().st_size - native['size'] and native['size'] > 0,
                         '原生对象范围越界')
        with path.open('rb') as stream:
            stream.seek(native['offset'])
            raw = stream.read(native['size'])
        if spec['schema'] not in self.schemas:
            data = document.safe_path(self.source, spec['schema']).read_bytes()
            document.require(document.digest(data) == self.receipt[spec['schema']].upper(), '资源结构摘要不匹配')
            self.schemas[spec['schema']] = type_tree.Schema.compile(json.loads(data)['nodes'])
        tree = type_tree.decode(self.schemas[spec['schema']], raw)
        document.require(type_tree.encode(tree) == raw, '原生资源未修改往返失败')
        return tree, raw

    def stream(self, identity, info):
        member = info['path'].replace('\\', '/').rsplit('/', 1)[-1]
        ref = next((r for r in self.manifest['streamReferences'] if r['identity'] == identity and
                    r['member'].lower() == member.lower() and r['offset'] == info['offset'] and
                    r['size'] == info['size']), None)
        document.require(ref is not None, '原生流引用不在来源清单中')
        row = next(r for r in self.manifest['rawStreams'] if r['member'].lower() == member.lower())
        path = self.snapshot(row['snapshot'])
        document.require(0 <= info['offset'] <= path.stat().st_size - info['size'], '原生流范围越界')
        with path.open('rb') as stream:
            stream.seek(info['offset'])
            return stream.read(info['size'])

    def float_texture(self, identity):
        import numpy as np
        tree, _ = self.tree(identity)
        data = plain(tree)
        width, height, fmt = data['m_Width'], data['m_Height'], data['m_TextureFormat']
        document.require(fmt in (17, 20), 'VAT 数据纹理需要 RGBAHalf / RGBAFloat，不能使用 PNG 近似')
        document.require(data['m_ImageCount'] == 1 and data['m_TextureDimension'] == 2,
                         'VAT 预览当前只支持二维数据纹理')
        document.require(0 < width * height <= 8 * 1024 * 1024 and width > 0 and height > 0,
                         'VAT 单张浮点预览超过 8M 像素预算；原始数据未修改')
        raw = data['image data'] or self.stream(identity, data['m_StreamData'])
        dtype = '<f2' if fmt == 17 else '<f4'
        size = width * height * 4 * np.dtype(dtype).itemsize
        document.require(len(raw) >= size, 'VAT 数据纹理载荷不足')
        pixels = np.frombuffer(raw, dtype=dtype, count=width * height * 4).reshape(height, width, 4).copy()
        document.require(np.isfinite(pixels).all(), 'VAT 数据纹理含非有限值')
        return pixels, dict(width=width, height=height, format=fmt, colorSpace=data['m_ColorSpace'],
                            stream=data['m_StreamData'], settings=data['m_TextureSettings'])

    def mesh(self, identity):
        """Current uncompressed Unity vertex streams, including UV1..7 and colors."""
        import numpy as np
        if __package__:from .nativepack.mesh import unpack_frames
        else:from nativepack.mesh import unpack_frames
        tree, _ = self.tree(identity)
        mesh = plain(tree)
        vertex = mesh['m_VertexData']
        count = vertex['m_VertexCount']
        document.require(0 < count <= 4000000, 'Mesh 顶点数超过离线预览预算')
        channels = vertex['m_Channels']
        raw = vertex['m_DataSize']
        if not raw:
            raw = self.stream(identity, mesh['m_StreamData'])
        # Unity VertexFormat, not TextureFormat.
        types = ('<f4', '<f2', 'u1', 'i1', '<u2', '<i2', 'u1', 'i1', '<u2', '<i2', '<u4', '<i4')
        widths = [np.dtype(t).itemsize for t in types]
        streams = max((c['stream'] for c in channels if c['dimension'] & 15), default=0) + 1
        strides = [0] * streams
        starts = [0] * streams
        for ch in channels:
            dim = ch['dimension'] & 15
            if dim:
                document.require(ch['format'] < len(types), '预览不支持该顶点格式')
                strides[ch['stream']] += dim * widths[ch['format']]
        size = 0
        for index, stride in enumerate(strides):
            starts[index] = size
            size = (size + count * stride + 15) & ~15
        document.require(len(raw) >= size, '预览顶点流范围不足（压缩 Mesh 尚未支持）')
        arrays = {}
        for index, ch in enumerate(channels):
            dim, fmt, stream = ch['dimension'] & 15, ch['format'], ch['stream']
            if not dim:
                continue
            values = np.ndarray((count, dim), dtype=types[fmt], buffer=raw,
                                offset=starts[stream] + ch['offset'], strides=(strides[stream], widths[fmt])).copy()
            if fmt in (2, 3, 4, 5):
                values = values.astype(np.float32) / (255 if fmt == 2 else 127 if fmt == 3 else 65535 if fmt == 4 else 32767)
                if fmt in (3, 5):
                    values = np.maximum(values, -1)
            arrays[index] = values
        if len(channels) > 1 and channels[1]['dimension'] == 0x31:
            ch = channels[1]
            words = np.ndarray((count,), dtype='<u4', buffer=raw,
                               offset=starts[ch['stream']] + ch['offset'], strides=(strides[ch['stream']],)).copy()
            arrays[1], arrays[2] = unpack_frames(words)
        indices = np.frombuffer(mesh['m_IndexBuffer'], dtype='<u2' if mesh['m_IndexFormat'] == 0 else '<u4')
        index_size = indices.dtype.itemsize
        submeshes = []
        for sub in mesh['m_SubMeshes']:
            document.require(sub['topology'] == 0 and sub['indexCount'] % 3 == 0, '特效预览当前支持三角形子网格')
            start = sub['firstByte'] // index_size
            faces = indices[start:start + sub['indexCount']].astype(np.int64) + sub.get('baseVertex', 0)
            document.require(len(faces) == sub['indexCount'] and (not len(faces) or faces.max() < count), '子网格索引越界')
            submeshes.append(faces.reshape(-1, 3))
        return dict(vertices=arrays[0], normals=arrays.get(1), tangents=arrays.get(2), colors=arrays.get(3),
                    uvs={i - 4: value for i, value in arrays.items() if 4 <= i <= 11}, submeshes=submeshes)

"""Softbody VAT vertex adapter recovered from VFXWaterRefract's vertex program.

The data contract is UV1 lookup, RGBAHalf/Float offset and rotation textures,
integer displayFrame and _frameCount. This does not emulate the pixel shader,
rigid/fluid VAT, particle custom streams, or a skinned Renderer.
"""
import math
import numpy as np

if __package__:from . import eiem_effect_document as document
else:import eiem_effect_document as document


def sample_texture(pixels, uv, settings):
    height, width = pixels.shape[:2]
    xy = np.asarray(uv, dtype=np.float64) * (width, height)
    def indices(values, length, mode):
        values = values.astype(np.int64)
        if mode == 0:
            return values % length
        if mode == 2:
            folded = values % (2 * length)
            return np.where(folded < length, folded, 2 * length - 1 - folded)
        return np.clip(values, 0, length - 1)
    def lookup(x, y):
        return pixels[indices(y, height, settings.get('m_WrapV', 0)),
                      indices(x, width, settings.get('m_WrapU', 0))].astype(np.float32)
    if settings.get('m_FilterMode', 0) == 0:
        return lookup(np.floor(xy[:, 0]), np.floor(xy[:, 1]))
    xy -= .5
    low = np.floor(xy); u = xy - low
    a = lookup(low[:, 0], low[:, 1]) * (1 - u[:, 0:1]) + lookup(low[:, 0] + 1, low[:, 1]) * u[:, 0:1]
    b = lookup(low[:, 0], low[:, 1] + 1) * (1 - u[:, 0:1]) + lookup(low[:, 0] + 1, low[:, 1] + 1) * u[:, 0:1]
    return a * (1 - u[:, 1:2]) + b * u[:, 1:2]


def softbody(mesh, floats, position, rotation, time=0.):
    document.require(1 in mesh['uvs'] and mesh['uvs'][1].shape[1] >= 2, 'Softbody VAT 缺少原生 UV1 查找坐标')
    document.require(not floats.get('_HoudiniVATInParticle', 0), '粒子自定义 VAT 顶点流尚未回放')
    document.require(floats.get('_frameCount', 0) > 0, 'VAT 缺少有效 _frameCount')
    count = floats['_frameCount']
    if floats.get('_B_autoPlayback', 0):
        normalized = floats.get('_houdiniFPS', 60) / (count - .01) * (time - floats.get('_gameTimeAtFirstFrame', 0)) * floats.get('_playbackSpeed', 1)
        display = math.floor((normalized % 1) * count) + 1
    else:
        display = math.floor(floats.get('_displayFrame', 1))
    phase = math.fmod(display - 1, count) / count
    lookup = mesh['uvs'][1][:, :2].astype(np.float64).copy()
    # Houdini stores optional packed texture padding in the bound decimals.
    max_x, min_z = floats.get('_boundMaxX', 1), floats.get('_boundMinZ', 0)
    padding_y = math.floor(-10 * max_x) + 10 * max_x + 1
    padding_x = 10 * min_z - math.ceil(10 * min_z) + 1
    uv = np.column_stack((lookup[:, 0] * padding_x,
                          1 - padding_y * (phase + 1 - lookup[:, 1])))
    pos_pixels, pos_meta = position
    offsets = sample_texture(pos_pixels, uv, pos_meta['settings'])[:, :3]
    if not floats.get('_TextureFormat', 0):
        low = np.array([floats.get('_boundMin' + k, 0) for k in 'XYZ'])
        high = np.array([floats.get('_boundMax' + k, 1) for k in 'XYZ'])
        offsets = offsets * (high - low) + low
    vertices = mesh['vertices'] + offsets
    vertices[lookup[:, 1] <= .1] = 0  # source shader's padding/unused-vertex sentinel
    normals = mesh.get('normals')
    if rotation is not None:
        pixels, meta = rotation
        quat = sample_texture(pixels, uv, meta['settings'])
        if not floats.get('_TextureFormat', 0):
            quat = quat * 2 - 1
        # Original water shader rotates the fixed +Y normal, not the source normal.
        xyz, w = quat[:, :3], quat[:, 3:4]
        axis = np.zeros_like(xyz); axis[:, 1] = 1
        normals = axis + 2 * np.cross(xyz, np.cross(xyz, axis) + w * axis)
        lengths = np.linalg.norm(normals, axis=1, keepdims=True)
        normals = normals / np.maximum(lengths, 1e-20)
    return vertices, normals, display

"""Offline DDS/BCn encoding for native Texture2D payloads."""

from pathlib import Path
import struct
import subprocess
import tempfile


UNITY_FORMATS = {
    'RGBA32': dict(unity=4, block_bytes=4),
    'BC5': dict(unity=27, dxgi='BC5_UNORM', dxgi_id=83, block_bytes=16),
    'BC7': dict(unity=25, dxgi='BC7_UNORM', dxgi_id=98, block_bytes=16),
}


def target_format(source_format, color_space):
    """Retain BC5 normals; use BC7 for all other author images."""
    if color_space not in (0, 1):
        raise ValueError('来源贴图缺少有效的 sRGB/Linear 元数据')
    text = str(source_format).upper()
    name = 'BC5' if text in ('BC5', '27') else 'BC7'
    result = dict(UNITY_FORMATS[name])
    if name == 'BC7' and color_space == 1:
        result.update(dxgi='BC7_UNORM_SRGB', dxgi_id=99)
    result['name'] = name
    return result


def texture_metadata(resource, _values=None):
    metadata = resource.get('blocks', {}).get('@texture')
    if metadata is None:
        return dict(format='RGBA32', sourcePixelsHash=None)
    if len(metadata) == 20:
        version, _width, _height, format_id, _mips = struct.unpack('<5I', metadata)
        if version == 1 and format_id == 4:
            return dict(format='RGBA32', sourcePixelsHash=None)
    if len(metadata) == 52:
        version, _width, _height, format_id, _mips = struct.unpack_from('<5I', metadata)
        if version == 2 and format_id in (25, 27):
            return dict(format='BC7' if format_id == 25 else 'BC5',
                        sourcePixelsHash=metadata[20:].hex().upper())
    raise ValueError('作者 Texture 格式元数据无效')


def _mip_extent(width, height, count, block_bytes):
    total = 0
    for _ in range(count):
        total += max(1, (width + 3) // 4) * max(1, (height + 3) // 4) * block_bytes
        width, height = max(1, width // 2), max(1, height // 2)
    return total


def parse_source_dds(data):
    """Read an authored DDS container and expose its existing mip blocks."""
    data = bytes(data)
    if (len(data) < 128 or data[:4] != b'DDS '
            or struct.unpack_from('<I', data, 4)[0] != 124
            or struct.unpack_from('<I', data, 76)[0] != 32):
        raise ValueError('DDS 头无效')
    flags = struct.unpack_from('<I', data, 8)[0]
    if (flags & 0x1007) != 0x1007 or struct.unpack_from('<I', data, 112)[0] or struct.unpack_from('<I', data, 24)[0]:
        raise ValueError('DDS 必须是普通 2D 贴图')
    height, width = struct.unpack_from('<II', data, 12)
    mips = struct.unpack_from('<I', data, 28)[0] or 1
    pixel_flags, fourcc = struct.unpack_from('<II', data, 80)
    offset, format_id = 128, 0
    if pixel_flags & 4 and fourcc == int.from_bytes(b'DX10', 'little'):
        if len(data) < 148:
            raise ValueError('DDS DX10 扩展头不完整')
        dxgi, dimension, misc_flag, array_size, alpha_mode = struct.unpack_from('<5I', data, 128)
        if (dimension, misc_flag, array_size) != (3, 0, 1) or alpha_mode > 4:
            raise ValueError('DDS DX10 数组、贴图维度或 Alpha 模式无效')
        format_id = {28: 4, 29: 4, 83: 27, 98: 25, 99: 25}.get(dxgi, 0)
        offset = 148
    elif pixel_flags & 4 and fourcc in (
            int.from_bytes(b'ATI2', 'little'), int.from_bytes(b'BC5U', 'little')):
        format_id = 27
    elif (pixel_flags & 0x41) == 0x41 and struct.unpack_from('<5I', data, 88) == (
            32, 0xff, 0xff00, 0xff0000, 0xff000000):
        format_id = 4
    names = {4: 'RGBA32', 25: 'BC7', 27: 'BC5'}
    format_name = names.get(format_id)
    if format_name is None:
        raise ValueError('DDS 格式不受支持（支持 RGBA8、BC5、BC7）')
    if not 0 < width <= 16384 or not 0 < height <= 16384:
        raise ValueError('DDS 尺寸无效')
    if not 0 < mips <= max(width, height).bit_length():
        raise ValueError('DDS mip 数量无效')
    if format_id == 4:
        expected = sum(max(1, width >> i) * max(1, height >> i) * 4 for i in range(mips))
    else:
        expected = sum(max(1, (max(1, width >> i) + 3) // 4) *
                       max(1, (max(1, height >> i) + 3) // 4) * 16
                       for i in range(mips))
    if offset + expected != len(data):
        raise ValueError('DDS mip 数据长度无效')
    return dict(width=width, height=height, mipCount=mips, format=format_name,
                formatId=format_id, pixels=data[offset:])


def unpack_dds(data, width, height, mip_count, target):
    """Validate DirectXTex output and remove the DDS container header."""
    if len(data) < 148 or data[:4] != b'DDS ':
        raise ValueError('texconv 未生成有效的 DX10 DDS')
    header_size, flags, actual_height, actual_width = struct.unpack_from('<4I', data, 4)
    actual_mips = struct.unpack_from('<I', data, 28)[0]
    pixel_format_size, pixel_flags, fourcc = struct.unpack_from('<3I', data, 76)
    dxgi_format, dimension, misc_flag, array_size, misc_flags2 = struct.unpack_from('<5I', data, 128)
    if (header_size != 124 or pixel_format_size != 32 or fourcc != int.from_bytes(b'DX10', 'little')
            or actual_width != width or actual_height != height or actual_mips != mip_count
            or (dxgi_format, dimension, misc_flag, array_size) != (target['dxgi_id'], 3, 0, 1)
            or misc_flags2 not in (0, 1, 2, 3, 4)):
        raise ValueError('texconv DDS 元数据与目标 Texture2D 不一致')
    payload = data[148:]
    expected = _mip_extent(width, height, mip_count, target['block_bytes'])
    if len(payload) != expected:
        raise ValueError('texconv DDS mip 数据长度不正确')
    return payload


def compress_rgba(pixels, width, height, mipmaps, source_format, color_space,
                  texconv, flip_y=False):
    """Compress top-down RGBA pixels to validated BCn blocks using texconv."""
    from PIL import Image

    if (type(width) is not int or type(height) is not int or width <= 0 or height <= 0
            or width > 16384 or height > 16384 or len(pixels) != width * height * 4):
        raise ValueError('贴图像素长度或尺寸无效')
    target = target_format(source_format, color_space)
    image = Image.frombytes('RGBA', (width, height), bytes(pixels))
    if flip_y:
        image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    mip_count = max(width, height).bit_length() if mipmaps else 1
    with tempfile.TemporaryDirectory(prefix='eff-texture-bcn-') as temporary:
        root = Path(temporary)
        source = root / 'source.png'
        image.save(source, format='PNG', optimize=False)
        command = [str(Path(texconv)), '-nologo', '-y', '-dx10', '-f', target['dxgi'],
                   '-m', str(mip_count), '-if', 'BOX', '-o', str(root), str(source)]
        result = subprocess.run(command, capture_output=True, text=True, errors='replace', timeout=300)
        output = root / 'source.dds'
        if result.returncode or not output.is_file():
            detail = (result.stderr or result.stdout or 'texconv 未生成输出').strip()
            raise RuntimeError('DDS 压缩失败：' + detail[-2000:])
        payload = unpack_dds(output.read_bytes(), width, height, mip_count, target)
    return dict(pixels=payload, format=target['name'], formatId=target['unity'],
                width=width, height=height, mipCount=mip_count)

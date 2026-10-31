"""DDS container parsing and BCn payload contract tests."""
import hashlib
import struct
import unittest

from eiem_texture_compression import (
    parse_source_dds, target_format, texture_metadata, unpack_dds,
)


def dds_fixture(width, height, mip_count, fmt, payload, alpha_mode=0):
    data = bytearray(148)
    data[:4] = b'DDS '
    struct.pack_into('<7I', data, 4, 124, 0x2100F, height, width, 0, 0, mip_count)
    struct.pack_into('<3I', data, 76, 32, 4, int.from_bytes(b'DX10', 'little'))
    struct.pack_into('<5I', data, 128, fmt, 3, 0, 1, alpha_mode)
    data.extend(payload)
    return bytes(data)


class TextureCompressionTests(unittest.TestCase):
    def test_source_dds_returns_existing_mip_payload_without_reencoding(self):
        payload = bytes(range(80))
        parsed = parse_source_dds(dds_fixture(5, 7, 2, 99, payload))
        self.assertEqual((parsed['width'], parsed['height'], parsed['mipCount']), (5, 7, 2))
        self.assertEqual((parsed['format'], parsed['formatId']), ('BC7', 25))
        self.assertEqual(parsed['pixels'], payload)

    def test_color_space_selects_bc7_variant_without_changing_unity_format(self):
        srgb = target_format('RGBA32', 1)
        linear = target_format('RGBA32', 0)
        self.assertEqual((srgb['unity'], srgb['dxgi'], srgb['dxgi_id']),
                         (25, 'BC7_UNORM_SRGB', 99))
        self.assertEqual((linear['unity'], linear['dxgi'], linear['dxgi_id']),
                         (25, 'BC7_UNORM', 98))

    def test_bc5_source_format_is_preserved_for_linear_normal_maps(self):
        bc5 = target_format('BC5', 0)
        self.assertEqual((bc5['unity'], bc5['dxgi'], bc5['dxgi_id']),
                         (27, 'BC5_UNORM', 83))

    def test_dds_header_is_removed_and_odd_mips_are_checked_by_block_extent(self):
        target = target_format('RGBA32', 0)
        # 5x7, 2x3, 1x1 each occupy one or four 4x4 blocks in BC7.
        payload = bytes(range(96))
        dds = dds_fixture(5, 7, 3, 98, payload)
        self.assertEqual(unpack_dds(dds, 5, 7, 3, target), payload)
        opaque_dds = dds_fixture(5, 7, 3, 98, payload, alpha_mode=3)
        self.assertEqual(unpack_dds(opaque_dds, 5, 7, 3, target), payload)

    def test_rejects_reserved_dds_alpha_mode(self):
        target = target_format('RGBA32', 0)
        with self.assertRaisesRegex(ValueError, '元数据'):
            unpack_dds(dds_fixture(4, 4, 1, 98, bytes(16), alpha_mode=5), 4, 4, 1, target)

    def test_rejects_wrong_srgb_dxgi_format_and_wrong_mip_extent(self):
        target = target_format('RGBA32', 1)
        payload = bytes(48)
        with self.assertRaisesRegex(ValueError, '元数据'):
            unpack_dds(dds_fixture(4, 2, 3, 98, payload), 4, 2, 3, target)
        with self.assertRaisesRegex(ValueError, '数据长度'):
            unpack_dds(dds_fixture(4, 2, 3, 99, payload[:-1]), 4, 2, 3, target)

    def test_texture_resource_metadata_preserves_uncompressed_source_pixel_hash(self):
        digest = hashlib.sha256(b'authored base image').hexdigest().upper()
        metadata = struct.pack('<5I', 2, 256, 128, 25, 9) + bytes.fromhex(digest)
        self.assertEqual(texture_metadata({'blocks': {'@texture': metadata}}),
                         {'format': 'BC7', 'sourcePixelsHash': digest})


if __name__ == '__main__':
    unittest.main()

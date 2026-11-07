"""Pure export contracts; these tests never start Blender or export a scene."""
import ast
import hashlib
import importlib.util
import inspect
import json
import os
import shutil
import struct
import types
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
import zlib
import numpy as np
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
ADDON = Path(__file__).with_name('eiem_blender_addon.py')


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def source_stub(native, resources, **attributes):
    source = SimpleNamespace(manifest=dict(resources=resources), **attributes)
    source.resolve = lambda *args: native
    source.resolve_identity = lambda identity: next(
        row for row in source.manifest['resources'] if row['identity'] == identity)
    return source


exporter = load_module('eiem_native_export_core_test',
                       Path(__file__).with_name('eiem_native_export.py'))


def bundled_mesh_module():
    name = '_eiem_shipped_nativepack'
    if name not in sys.modules:
        package = types.ModuleType(name)
        package.__path__ = [str(Path(__file__).with_name('nativepack'))]
        sys.modules[name] = package
    return importlib.import_module(name + '.mesh')


class Material(dict):
    def __init__(self, name, values=None):
        super().__init__(values or {})
        self.name = name

    def as_pointer(self):
        return id(self)


class MaterialCollection(list):
    def new(self, name):
        result = Material(name)
        self.append(result)
        return result


def dds_bc7(width, height, payload, mip_count=1, dxgi=98):
    data = bytearray(148)
    data[:4] = b'DDS '
    struct.pack_into('<7I', data, 4, 124, 0x2100F, height, width, 0, 0, mip_count)
    struct.pack_into('<3I', data, 76, 32, 4, int.from_bytes(b'DX10', 'little'))
    struct.pack_into('<5I', data, 128, dxgi, 3, 0, 1, 0)
    return bytes(data) + payload


def addon_function(name, namespace, path=ADDON):
    """Execute the real function with tiny Blender-shaped dependencies."""
    tree = ast.parse(path.read_text('utf-8'))
    function = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == name)
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(ADDON), 'exec'),
         namespace)
    return namespace[name]


class NativeExportCoreTests(unittest.TestCase):
    def test_all_resource_identity_paths_share_assets_and_packages_namespaces(self):
        format3 = exporter.pack_modules()[0]['author_format3']
        for path in (
                'assets/characters/body.mesh',
                'packages/com.hg.game/runtime/weapons/sword.mesh',
                'packages/com.hg.render-pipelines/runtime/renderpipelineresources/material/defaultmaterial.mat'):
            self.assertTrue(format3.is_logical_resource_path(path), path)
        for path in ('packages/../assets/body.mesh', 'packages\\game\\body.mesh',
                     'C:/game/body.mesh', 'assets//body.mesh'):
            self.assertFalse(format3.is_logical_resource_path(path), path)

    def test_texture_only_export_preserves_package_texture_identity(self):
        source_path = 'packages/com.hg.render-pipelines/runtime/renderpipelineresources/texture/body.tex'
        material = Material('MaterialBody', {'eiem_texture._BaseMap': source_path})
        addon = SimpleNamespace(
            material_override_payload=lambda *args, **kwargs: (['format=EFFMAT', 'version=1'], None),
            parse_json_property=lambda *args, **kwargs: {},
            resolve_material_texture=lambda *args, **kwargs: self.fail('game identity treated as image path'))
        result = exporter.collect_material(addon, object(), material, {}, {}, '.', None,
            existing={'MaterialBody': {'texture._BaseMap'}}, texture_only=True,
            mid='MaterialBody')
        self.assertEqual(result['textureEdits'], [])

    def setUp(self):
        def compress(pixels, width, height, mipmaps, source_format, color_space, texconv, flip_y=False):
            target = exporter.texture_compression.target_format(source_format, color_space)
            mips = max(width, height).bit_length() if mipmaps else 1
            size = exporter.texture_compression._mip_extent(width, height, mips, target['block_bytes'])
            seed = hashlib.sha256(bytes(pixels) + target['name'].encode()).digest()
            payload = (seed * ((size + len(seed) - 1) // len(seed)))[:size]
            return dict(pixels=payload, format=target['name'], formatId=target['unity'],
                        width=width, height=height, mipCount=mips)
        self.texture_compressor = patch.object(exporter.texture_compression, 'compress_rgba', side_effect=compress)
        self.texture_compressor_mock = self.texture_compressor.start()
        self.addCleanup(self.texture_compressor.stop)
        self.texture_cache_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.texture_cache_directory.cleanup)
        self.texture_cache_root = patch.object(
            exporter, '_texture_compression_cache_root',
            return_value=Path(self.texture_cache_directory.name))
        self.texture_cache_root.start()
        self.addCleanup(self.texture_cache_root.stop)
        self.texture_tool = patch.object(exporter.shared_resources, 'native_tool_file', return_value=Path('texconv.exe'))
        self.texture_tool.start()
        self.addCleanup(self.texture_tool.stop)

    def test_exporter_has_one_complete_format3_entry(self):
        self.assertFalse(hasattr(exporter, 'validate_format3_export_scope'))
        self.assertNotIn('resource_scope', inspect.signature(exporter.export_native_package).parameters)

    def test_shipped_bundle_mesh_writer_preserves_authored_uv_values(self):
        mesh = bundled_mesh_module()
        channels = [[.1, .2, .8, .9]] + [[]] * 7
        converted, tangents = mesh.game_texture_coordinates(
            channels, [1., 0., 0., 1., 0., 1., 0., -1.], 2)
        np.testing.assert_allclose(converted[0], [[.1, .2], [.8, .9]])
        np.testing.assert_array_equal(tangents.reshape(2, 4)[:, 3], [1., -1.])

    def test_shipped_bundle_writer_restores_blender_author_uvs_and_tangents(self):
        mesh = bundled_mesh_module()
        channels = [[.1, .2, .8, .9], [.3, .4, .7, .6]] + [[]] * 6
        converted, tangents = mesh.game_texture_coordinates(
            channels, [1., 0., 0., -1., 0., 1., 0., 1.], 2,
            coordinate='Blender')
        np.testing.assert_allclose(converted[0], [[.1, .8], [.8, .1]], atol=3e-8)
        np.testing.assert_allclose(converted[1], [[.3, .6], [.7, .4]])
        np.testing.assert_array_equal(tangents.reshape(2, 4)[:, 3], [1., -1.])

    def test_typed_author_input_preserves_signed_unity_path_ids(self):
        template = dict(kind='Mesh', path='assets/test.asset', asset='MeshTest',
                        cab='CAB-test', pathId=-123456789012345,
                        file='/source/CAB-test.bytes', sha256='A' * 64)
        encoded = exporter._typed_author_input('Smoke', [template], [], [])
        offset = 0

        def text():
            nonlocal offset
            size = struct.unpack_from('<Q', encoded, offset)[0]
            offset += 8
            value = encoded[offset:offset + size].decode('utf-8')
            offset += size
            return value

        self.assertEqual(text(), 'EFF_AUTHOR_TYPED_INPUT/1')
        self.assertEqual(text(), 'Smoke')
        self.assertEqual(struct.unpack_from('<Q', encoded, offset)[0], 1)
        offset += 8
        self.assertEqual([text() for _ in range(4)],
                         ['Mesh', 'assets/test.asset', 'MeshTest', 'CAB-test'])
        self.assertEqual(struct.unpack_from('<q', encoded, offset)[0], template['pathId'])

    def test_typed_texture_input_does_not_copy_dds_payload_into_author_dll(self):
        template = dict(kind='Texture2D', path='assets/body_d', asset='Body_D',
                        cab='CAB-texture', pathId=-17,
                        file='/source/CAB-texture.bytes', sha256='B' * 64)
        pixels = b'large-dds-payload' * 200_000
        texture = dict(id='TextureBody', path='textures/TextureBody.effres',
                       dds='textures/TextureBody.dds', pixels=pixels,
                       width=4096, height=4096, mipCount=13, formatId=25,
                       colorSpace=1, filter=1, wrap=0, aniso=1, mipBias=0.0)
        encoded = exporter._typed_author_input('Smoke', [template], [], [texture])
        self.assertNotIn(pixels[:4096], encoded)
        self.assertLess(len(encoded), 1024)

    def test_typed_texture_record_ends_with_template_index(self):
        template = dict(kind='Texture2D', path='assets/body_d', asset='Body_D',
                        cab='CAB-texture', pathId=-17,
                        file='/source/CAB-texture.bytes', sha256='B' * 64)
        texture = dict(id='TextureBody', path='textures/TextureBody.effres',
                       dds='textures/TextureBody.dds')
        encoded = exporter._typed_author_input('Smoke', [template], [], [texture])
        self.assertEqual(encoded[-8:], (0).to_bytes(8, 'little'))

    def test_typed_writer_requires_a_matching_format3_candidate(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'candidate'
            with self.assertRaisesRegex(ValueError, 'prepared candidate directory'):
                exporter.write_format3_native_assets(
                    Path(folder) / 'missing.dll', 'Smoke', [], [], [], output)
            output.mkdir()
            (output / 'mod.ini').write_text('[Mod]\nformat=2\nid=Smoke\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'candidate identity differs'):
                exporter.write_format3_native_assets(
                    Path(folder) / 'missing.dll', 'Smoke', [], [], [], output)

            (output / 'mod.ini').write_text('[Mod]\nformat=3\nid=Other\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'candidate identity differs'):
                exporter.write_format3_native_assets(
                    Path(folder) / 'missing.dll', 'Smoke', [], [], [], output)

    def test_typed_format3_records_reject_shared_part_with_different_mesh_schemas(self):
        class Format3:
            @staticmethod
            def part_resource_id(value): return 'Part' + value.removeprefix('Mesh')
            @staticmethod
            def identifier(value): return value

        class Source:
            @staticmethod
            def resolve(kind, path, asset):
                return {'native': {'typeHash': 'TYPE-A' if asset == 'A' else 'TYPE-B'}}

        part = dict(partId='MeshBody', material='MaterialBody', nativeTypeHash='TYPE-A',
                    vertices=3, indices=3, bonePaths=[], shapeNames=[], fieldData={},
                    authorData=b'editable mesh')
        rules = [dict(hidden=False, parts=['MeshBody'], targets=[
            dict(sourcePath='assets/a.mesh', sourceAsset='A'),
            dict(sourcePath='assets/b.mesh', sourceAsset='B')])]
        with self.assertRaisesRegex(ValueError, 'incompatible native templates'):
            exporter.format3_native_records(Format3, Source(), [part], [], rules)

    def test_typed_format3_records_match_format3_manifest_names(self):
        class Format3:
            @staticmethod
            def part_resource_id(value): return 'Part' + value.removeprefix('Mesh')
            @staticmethod
            def identifier(value): return value

        class Source:
            @staticmethod
            def resolve(kind, path, asset):
                return {'native': {'typeHash': 'TYPE-A'}}

        part = dict(partId='MeshBody', material='MaterialBody', nativeTypeHash='TYPE-A',
                    vertices=3, indices=3, bonePaths=['Root'], shapeNames=['Smile'],
                    fieldData={'m_Name': b'Mesh'}, authorData=b'editable mesh')
        rules = [dict(hidden=False, parts=['MeshBody'], targets=[
            dict(sourcePath='assets/body.mesh', sourceAsset='Body')])]
        meshes, textures = exporter.format3_native_records(Format3, Source(), [part], [], rules)
        self.assertEqual(textures, [])
        self.assertEqual(meshes[0]['partId'], 'PartBody')
        self.assertEqual(meshes[0]['meshId'], 'Mesh_PartBody')
        self.assertEqual(meshes[0]['path'], 'meshes/PartBody.effres')
        self.assertEqual(meshes[0]['targetAsset'], 'Body')

    def test_typed_format3_records_allow_mesh_only_parts(self):
        class Format3:
            @staticmethod
            def part_resource_id(value): return 'Part' + value.removeprefix('Mesh')
            @staticmethod
            def identifier(value): return value

        class Source:
            @staticmethod
            def resolve(kind, path, asset):
                return {'native': {'typeHash': 'TYPE-A'}}

        part = dict(partId='MeshBody', nativeTypeHash='TYPE-A', vertices=3,
                    indices=3, bonePaths=[], shapeNames=[], fieldData={},
                    authorData=b'editable mesh')
        rules = [dict(hidden=False, parts=['MeshBody'], targets=[dict(
            sourcePath='assets/body.mesh', sourceAsset='Body')])]
        meshes, _ = exporter.format3_native_records(Format3, Source(), [part], [], rules)
        self.assertEqual(meshes[0]['material'], '')

    def test_dds_author_image_is_not_compressed_and_is_registered_for_passthrough(self):
        payload = bytes(range(16))
        raw_dds = dds_bc7(4, 4, payload)
        native = dict(details=dict(textures=[dict(property='_BaseMap', pointer=dict(
            isNull=False, identity='native-texture'))]), native=dict(typeHash='12' * 16))
        source = source_stub(native, [dict(
            identity='native-texture', logicalPath='assets/body_d', name='Body_D',
            details=dict(colorSpace=1))])
        addon = SimpleNamespace(image_absolute_path=lambda image: image.filepath,
            material_override_payload=lambda *args, **kwargs:
                (['texture._BaseMap=slot'], None))
        with tempfile.TemporaryDirectory() as folder:
            dds_path = Path(folder) / 'replacement.dds'
            dds_path.write_bytes(raw_dds)
            image = Material('Replacement', {'eiem_section': 'TextureReplacement'})
            image.filepath = str(dds_path)
            textures, texture_inputs = {}, {}
            material = Material('Body', {'eiem_source': 'assets/body', 'eiem_name': 'Body'})
            record = exporter.collect_material(addon, source, material, {'slot': image},
                textures, folder, None, texture_only=True, texture_inputs=texture_inputs)

        self.texture_compressor_mock.assert_not_called()
        texture_id = record['textureEdits'][0]['texture']
        self.assertEqual(textures[texture_id]['pixels'], payload)
        self.assertEqual(textures[texture_id]['format'], 'BC7')
        self.assertEqual(texture_inputs[texture_id]['dds_path'], dds_path)
        self.assertEqual(texture_inputs[texture_id]['dds_hash'], hashlib.sha256(raw_dds).hexdigest().upper())

    def test_png_author_image_is_compressed_and_published_as_dds(self):
        from PIL import Image
        native = dict(details=dict(textures=[dict(property='_BaseMap', pointer=dict(
            isNull=False, identity='native-texture'))]), native=dict(typeHash='12' * 16))
        source = source_stub(native, [dict(
            identity='native-texture', logicalPath='assets/body_d', name='Body_D',
            details=dict(format='BC7', colorSpace=1))])
        addon = SimpleNamespace(image_absolute_path=lambda image: image.filepath,
            material_override_payload=lambda *args, **kwargs:
                (['texture._BaseMap=slot'], None))
        with tempfile.TemporaryDirectory() as folder:
            png_path = Path(folder) / 'replacement.png'
            Image.new('RGBA', (4, 4), (80, 120, 160, 255)).save(png_path)
            image = Material('Replacement', {'eiem_section': 'TextureReplacement'})
            image.filepath = str(png_path)
            textures, texture_inputs = {}, {}
            material = Material('Body', {
                'eiem_source': 'assets/body', 'eiem_name': 'Body',
                'eiem_texture._BaseMap': str(png_path),
            })
            record = exporter.collect_material(addon, source, material, {'slot': image},
                textures, folder, None, texture_only=True, texture_inputs=texture_inputs)
            self.texture_compressor_mock.assert_called_once()
            texture_id = record['textureEdits'][0]['texture']
            self.assertEqual(texture_inputs[texture_id], {})
            self.assertNotIn('dds_path', texture_inputs[texture_id])

            root = Path(folder) / 'published'
            root.mkdir()
            format3 = exporter.pack_modules()[0]['author_format3']
            format3.write_format3_directory(
                root, 'Smoke', [], {}, list(textures.values()), [], [], [], [])
            published_dds = root / 'textures' / (texture_id + '.dds')
            self.assertEqual(exporter.texture_compression.parse_source_dds(
                published_dds.read_bytes())['format'], 'BC7')
            self.assertEqual(Path(image.filepath), png_path)
            self.assertEqual(Path(material['eiem_texture._BaseMap']), png_path)

            next_textures, next_inputs = {}, {}
            exporter.collect_material(addon, source, material, {'slot': image},
                next_textures, folder, None, texture_only=True,
                texture_inputs=next_inputs)
            self.assertEqual(self.texture_compressor_mock.call_count, 1,
                             'unchanged source PNG should reuse compressed BCn blocks')

            stamp = png_path.stat()
            Image.new('RGBA', (4, 4), (32, 96, 224, 255)).save(png_path)
            self.assertEqual(png_path.stat().st_size, stamp.st_size)
            os.utime(png_path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            next_textures, next_inputs = {}, {}
            exporter.collect_material(addon, source, material, {'slot': image},
                next_textures, folder, None, texture_only=True,
                texture_inputs=next_inputs)
            self.assertEqual(self.texture_compressor_mock.call_count, 2)
            self.assertEqual(Path(material['eiem_texture._BaseMap']), png_path)

    def test_format3_passthrough_copies_exact_dds(self):
        payload = bytes(range(16))
        raw_dds = dds_bc7(4, 4, payload, dxgi=99)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'published'
            root.mkdir()
            (root / 'mod.ini').write_text(
                '[Mod]\nformat=3\n[TextureExample]\ndds=textures/TextureExample.dds\n',
                encoding='utf-8')
            source_dds = Path(folder) / 'source.dds'
            source_dds.write_bytes(raw_dds)
            parsed_source = exporter.texture_compression.parse_source_dds(raw_dds)
            texture = dict(parsed_source, id='TextureExample', colorSpace=1,
                           filter=1, wrap=0, aniso=1, mipBias=0.0,
                           ddsSourcePath=str(source_dds),
                           ddsHash=hashlib.sha256(raw_dds).hexdigest().upper())
            texture_inputs = {'TextureExample': {
                'dds_path': source_dds,
                'dds_hash': hashlib.sha256(raw_dds).hexdigest().upper(),
            }}

            exporter.pack_modules()[0]['author_format3'].write_format3_directory(
                root, 'Smoke', [], {}, [texture], [], [], [], [])
            output_dds = root / 'textures/TextureExample.dds'
            self.assertEqual(output_dds.read_bytes(), raw_dds)
            parsed = exporter.texture_compression.parse_source_dds(output_dds.read_bytes())
            self.assertEqual(parsed['pixels'], payload)

    def test_export_pipeline_has_no_published_path_rewrite(self):
        self.assertFalse(hasattr(exporter, '_update_texture_image_paths'))
        self.assertNotIn('_update_texture_image_paths',
                         inspect.getsource(exporter.export_native_package))

    def test_material_switch_definition_validates_user_selected_materials(self):
        validate = addon_function('material_switch_definition', {
            're': __import__('re'), 'validate_switch_key': lambda key: key},
            Path(__file__).with_name('eiem_blender_controls.py'))
        first = Material('Body', {'eiem_section': 'MaterialBody'})
        alternate = Material('BodyAlt', {'eiem_section': 'MaterialBodyAlt'})
        group = SimpleNamespace(identity='0123456789abcdef', label='裙子材质', key='F7',
                                 slot_index=1, default_option=1,
                                 options=[SimpleNamespace(material=first),
                                          SimpleNamespace(material=alternate)])
        definition = validate(group)
        self.assertEqual((definition['slot'], definition['default'], definition['key']), (1, 1, 'F7'))
        self.assertEqual(definition['options'], [first, alternate])
        options = addon_function('material_switch_export_options', {},
                                 Path(__file__).with_name('eiem_blender_controls.py'))
        self.assertEqual(options(definition, True), [first, alternate])
        self.assertEqual(options(definition, False), [alternate])
        group.options[1].material = first
        with self.assertRaisesRegex(ValueError, '不能重复'):
            validate(group)

    def shape_frame_snapshot(self, metadata, names):
        class Keys(list):
            def get(self, name):
                return next((key for key in self if key.name == name), None)

        basis = SimpleNamespace(name='Basis')
        keys = Keys([basis] + [SimpleNamespace(name=name, relative_key=basis, vertex_group='') for name in names])
        mesh = Material('Shape source', {'eiem_blend_shapes_json': metadata})
        mesh.vertices = []
        mesh.shape_keys = SimpleNamespace(reference_key=basis, key_blocks=keys, use_relative=True)
        function = addon_function('export_blend_shapes', dict(
            parse_json_property=lambda owner, key, default: owner.get(key, default), zlib=zlib))
        vertices, frames, channels, weights, additional = function(SimpleNamespace(data=mesh), tuple, [])
        author = dict(vertex_count=0, blend_vertices=vertices, blend_frames=frames,
                      blend_channels=channels, blend_weights=weights)
        modules, _ = exporter.pack_modules()
        class Array:
            def set_items(self, values): self.values = values
        shapes = Material('Shapes', {key: Array() for key in ('vertices', 'shapes', 'channels', 'fullWeights')})
        shapes.data = shapes
        modules['mesh'].write_shapes({'m_Shapes': shapes}, author)
        return author, shapes

    def test_imported_shape_frames_without_native_names_use_their_channel(self):
        metadata = [dict(name='Native brow', hash=1234,
                         frames=[dict(key='Native brow', source_name='', weight=100.)])]
        author, shapes = self.shape_frame_snapshot(metadata, ['Native brow'])
        self.assertEqual(author['blend_frames'][0][0], 'Native brow')
        self.assertEqual(shapes['channels'].values, [dict(name='Native brow', nameHash=1234, frameIndex=0, frameCount=1)])
        self.assertEqual(shapes['fullWeights'].values, [100.])

    def test_imported_multi_frame_labels_keep_channel_hash_range_and_weights(self):
        metadata = [dict(name='Native smile', hash=5678, frames=[
            dict(key='Smile@50', source_name='different legacy frame label', weight=50.),
            dict(key='Smile@100', weight=100.)])]
        author, _ = self.shape_frame_snapshot(metadata, ['Smile@50', 'Smile@100', 'Author key'])
        self.assertEqual([frame[0] for frame in author['blend_frames']], ['Native smile', 'Native smile', 'Author key'])
        self.assertEqual(author['blend_channels'][0], ('Native smile', 5678, 0, 2))
        self.assertEqual(author['blend_channels'][1], ('Author key', zlib.crc32(b'Author key') & 0xffffffff, 2, 1))
        self.assertEqual(author['blend_weights'], [50., 100., 100.])

    def test_deleted_imported_shape_frames_do_not_shift_following_channel_identity(self):
        metadata = [dict(name='Deleted', frames=[dict(key='Removed', source_name='')]),
                    dict(name='Survivor', hash=42, frames=[dict(key='Keep', source_name='')])]
        author, _ = self.shape_frame_snapshot(metadata, ['Keep'])
        self.assertEqual(author['blend_channels'], [('Survivor', 42, 0, 1)])
        self.assertEqual(author['blend_frames'][0][0], 'Survivor')

    @unittest.skipUnless(os.name == 'nt', 'Windows pinned runtime snapshot')
    def test_atomic_publication_keeps_pinned_old_runtime_file_readable(self):
        import ctypes
        from ctypes import wintypes
        api=ctypes.WinDLL('kernel32',use_last_error=True)
        api.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
        api.CreateFileW.restype=wintypes.HANDLE
        api.ReadFile.argtypes=[wintypes.HANDLE,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(wintypes.DWORD),ctypes.c_void_p]
        api.CloseHandle.argtypes=[wintypes.HANDLE]
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);old=root/'compiled.bin';new=root/'new.bin'
            old.write_bytes(b'old pinned PFB');new.write_bytes(b'new author and PFB')
            handle=api.CreateFileW(str(old),0x80000000,7,None,3,0x80,None)
            self.assertNotEqual(handle,ctypes.c_void_p(-1).value)
            try:
                exporter._replace_published_file(new,old)
                self.assertEqual(old.read_bytes(),b'new author and PFB')
                data=ctypes.create_string_buffer(64);count=wintypes.DWORD()
                self.assertTrue(api.ReadFile(handle,data,64,ctypes.byref(count),None))
                self.assertEqual(data.raw[:count.value],b'old pinned PFB')
            finally:
                api.CloseHandle(handle)

    def test_mesh_part_ids_keep_unicode_names_distinct_and_stable(self):
        names = ['MeshS_actor_liino_cloth_02_lod0_3.丝袜上',
                 'MeshS_actor_liino_cloth_02_lod0_3.丝袜下']
        self.assertEqual(exporter.token(names[0]), exporter.token(names[1]))
        ids = [exporter.mesh_part_id(name, slot, 2)
               for name in names for slot in (1, 2)]
        self.assertEqual(len(set(ids)), 4)
        self.assertEqual(ids, [exporter.mesh_part_id(name, slot, 2)
                              for name in names for slot in (1, 2)])
        self.assertNotEqual(exporter.mesh_part_id('MeshCloth left', 0, 1),
                            exporter.mesh_part_id('MeshCloth_left', 0, 1))
        self.assertEqual(exporter.mesh_part_id('MeshCloth.001', 0, 1),
                         'MeshCloth.001')
        self.assertEqual(exporter.mesh_part_id('MeshCloth.001', 1, 2),
                         'MeshCloth.001_mat1')

    def test_empty_point_attribute_returns_an_empty_stream(self):
        function = addon_function('get_point_attribute', {})

        class EmptyData(list):
            def foreach_get(self, member, values):
                assert values == []

        data = EmptyData()
        attribute = SimpleNamespace(domain='POINT', data=data)
        mesh = SimpleNamespace(attributes={'SourceNormal': attribute}, vertices=[])
        self.assertEqual(function(mesh, 'SourceNormal', 'vector'), [])
        self.assertEqual(function(mesh, 'SourceNormal', 'value'), [])

    def test_generated_tangent_batch_preserves_scalar_float32_frames(self):
        function = addon_function('_orthogonalize_missing_tangents', {'np': np})
        rng = np.random.default_rng(173)
        tangents = rng.normal(size=(4096, 4))
        tangents[:, 3] = np.where(tangents[:, 3] < 0, -1, 1)
        normals = rng.normal(size=(4096, 3))
        expected = []
        for value, normal in zip(tangents, normals):
            tx, ty, tz, sign = map(float, value)
            nx, ny, nz = map(float, normal)
            dot = (tx*nx + ty*ny + tz*nz) / (nx*nx + ny*ny + nz*nz)
            tx -= nx*dot; ty -= ny*dot; tz -= nz*dot
            scale = (tx*tx + ty*ty + tz*tz) ** -.5
            expected.append((tx*scale, ty*scale, tz*scale, sign))
        actual = function('cloth', tangents, normals)
        self.assertEqual(np.asarray(actual, dtype='<f4').tobytes(),
                         np.asarray(expected, dtype='<f4').tobytes())

    def test_generated_tangent_batch_reports_first_invalid_corner(self):
        function = addon_function('_orthogonalize_missing_tangents', {'np': np})
        tangents = [(1, 0, 0, -1)] * 4
        normals = [(0, 0, 1)] * 4
        normals[1] = (1, 0, 0)
        tangents[3] = (0, 0, 0, 0)
        with self.assertRaisesRegex(ValueError, 'cloth corner 1 tangent is parallel'):
            function('cloth', tangents, normals)
        with self.assertRaisesRegex(ValueError, 'cloth corner 0 cannot generate'):
            function('cloth', [(float('nan'), 0, 0, 1)], [(0, 0, 1)])

    def test_material_keywords_and_tags_are_serialized_with_shader_edits(self):
        codec = exporter.pack_modules()[0]['type_tree']
        string = lambda name: codec.Schema('string', name, True)
        strings = lambda name: codec.Schema('vector', name, False, [
            codec.Schema('Array', 'Array', False, [
                codec.Schema('int', 'size', False), string('data')])])
        tags = codec.Schema('map', 'stringTagMap', False, [
            codec.Schema('Array', 'Array', False, [
                codec.Schema('int', 'size', False),
                codec.Schema('pair', 'data', False, [string('first'), string('second')])])])
        schema = codec.Schema('Material', 'Base', False, [
            strings('m_ValidKeywords'), strings('m_InvalidKeywords'), tags,
            codec.Schema('int', 'm_CustomRenderQueue', False)])
        tree = codec.from_data(schema, dict(
            m_ValidKeywords=['_NORMALMAP', '_ALPHATEST_ON'],
            m_InvalidKeywords=['OTHER', '_EMISSION'],
            stringTagMap=[dict(first='RenderType', second='Opaque'),
                          dict(first='OtherTag', second='unchanged')],
            m_CustomRenderQueue=2000))
        edits = {'keyword._EMISSION': '1', 'keyword._ALPHABLEND_ON': 'true',
                 'keyword._ALPHATEST_ON': '0', 'tag.RenderType': 'Transparent',
                 'render_queue': '3000'}
        changed = exporter.material_fields(tree, edits, codec.encode)
        self.assertEqual([x.data for x in tree['m_ValidKeywords'].items()],
                         ['_NORMALMAP', '_EMISSION', '_ALPHABLEND_ON'])
        self.assertEqual([x.data for x in tree['m_InvalidKeywords'].items()], ['OTHER'])
        self.assertEqual(tree['stringTagMap'].items()[0]['second'].data, 'Transparent')
        self.assertEqual(tree['stringTagMap'].items()[1]['second'].data, 'unchanged')
        self.assertEqual(set(changed), {'m_ValidKeywords', 'm_InvalidKeywords',
                                       'stringTagMap', 'm_CustomRenderQueue'})
        self.assertEqual(exporter.material_fields(tree, edits, codec.encode), {})

    def test_material_legacy_keyword_string_and_invalid_toggle(self):
        codec = exporter.pack_modules()[0]['type_tree']
        schema = codec.Schema('Material', 'Base', False, [
            codec.Schema('string', 'm_ShaderKeywords', True)])
        tree = codec.from_data(schema, dict(m_ShaderKeywords='_NORMALMAP _ALPHATEST_ON'))
        exporter.material_fields(tree, {'keyword._EMISSION': '1',
                                       'keyword._ALPHATEST_ON': 'false'}, codec.encode)
        self.assertEqual(tree['m_ShaderKeywords'].data, '_NORMALMAP _EMISSION')
        with self.assertRaisesRegex(ValueError, '0/1'):
            exporter.material_fields(tree, {'keyword._EMISSION': 'banana'}, codec.encode)

    def test_material_render_queue_changes_native_queue_not_shader_properties(self):
        codec = exporter.pack_modules()[0]['type_tree']
        schema = codec.Schema('Material', 'Base', False, [
            codec.Schema('int', 'm_CustomRenderQueue', False),
            codec.Schema('int', 'm_LightmapFlags', False),
        ])
        tree = codec.from_data(schema, dict(m_CustomRenderQueue=3000, m_LightmapFlags=4))
        self.assertEqual(exporter.material_fields(tree, {'render_queue': '3000'}, codec.encode), {})
        changed = exporter.material_fields(tree, {'render_queue': '2999'}, codec.encode)
        self.assertEqual(changed, {'m_CustomRenderQueue': struct.pack('<i', 2999)})
        self.assertEqual(tree['m_LightmapFlags'].plain(), 4)
        self.assertEqual(exporter.material_fields(tree, {'render_queue': '-1'}, codec.encode),
                         {'m_CustomRenderQueue': struct.pack('<i', -1)})

    def test_material_render_queue_rejects_invalid_values(self):
        codec = exporter.pack_modules()[0]['type_tree']
        schema = codec.Schema('Material', 'Base', False, [
            codec.Schema('int', 'm_CustomRenderQueue', False),
        ])
        for value in ('2999.5', 2999.5, 'invalid', '-2', '5001'):
            with self.subTest(value=value):
                tree = codec.from_data(schema, dict(m_CustomRenderQueue=3000))
                with self.assertRaisesRegex(ValueError, '渲染队列'):
                    exporter.material_fields(tree, {'render_queue': value}, codec.encode)
                self.assertEqual(tree['m_CustomRenderQueue'].plain(), 3000)

    def test_material_texture_transforms_preserve_native_pointer(self):
        codec = exporter.pack_modules()[0]['type_tree']
        scalar = lambda name: codec.Schema('float', name, False)
        vector = lambda name: codec.Schema('Vector2f', name, False, [scalar('x'), scalar('y')])
        pointer = codec.Schema('PPtr<Texture>', 'm_Texture', False, [
            codec.Schema('int', 'm_FileID', False), codec.Schema('SInt64', 'm_PathID', False)])
        envs = codec.Schema('map', 'm_TexEnvs', False, [codec.Schema('Array', 'Array', False, [
            codec.Schema('int', 'size', False), codec.Schema('pair', 'data', False, [
                codec.Schema('string', 'first', True), codec.Schema('UnityTexEnv', 'second', False,
                    [pointer, vector('m_Scale'), vector('m_Offset')])])])])
        schema = codec.Schema('Material', 'Base', False, [codec.Schema('UnityPropertySheet',
                              'm_SavedProperties', False, [envs])])
        tree = codec.from_data(schema, dict(m_SavedProperties=dict(m_TexEnvs=[dict(
            first='_BaseMap', second=dict(m_Texture=dict(m_FileID=3, m_PathID=123),
            m_Scale=dict(x=1,y=1), m_Offset=dict(x=0,y=0)))])))
        edits={'texture_scale._BaseMap':'2,3', 'texture_offset._BaseMap':'0.2,-0.3'}
        changed=exporter.material_fields(tree, edits, codec.encode)
        env=tree['m_SavedProperties']['m_TexEnvs'].items()[0]['second']
        self.assertEqual(env['m_Texture'].plain(),dict(m_FileID=3,m_PathID=123))
        self.assertEqual(env['m_Scale'].plain(),dict(x=2,y=3))
        self.assertEqual(set(changed),{'m_SavedProperties'})
        self.assertEqual(exporter.material_fields(tree, edits, codec.encode),{})
        with self.assertRaisesRegex(ValueError,'有限分量'):
            exporter.material_fields(tree, {'texture_offset._BaseMap':'nan,0'},codec.encode)

    def test_material_render_queue_flows_from_blender_override(self):
        namespace = dict(json=json, parse_json_property=lambda material, key, default:
                         json.loads(material.get(key, json.dumps(default))))
        namespace['material_editor'] = SimpleNamespace(features=SimpleNamespace(
            actual_render_queue=lambda material, baseline: int(material.get('eiem_render_queue', 2000))))
        payload = addon_function('material_override_payload', namespace)
        material = Material('Inner', dict(eiem_source='assets/source.mat',
                            eiem_baseline_json=json.dumps({'source': 'assets/source.mat'}),
                            eiem_render_queue=2999))
        values, images = payload(material, {}, force=True)
        self.assertIn('render_queue=2999', values)
        self.assertEqual(images, set())
        unchanged = Material('Inherited', dict(eiem_source='assets/source.mat',
            eiem_baseline_json=json.dumps({'source': 'assets/source.mat',
                                            'float._Gloss': '.4', 'render_queue': 2999}),
            eiem_render_queue=2999))
        unchanged['eiem_float._Gloss'] = '.4'
        values, images = payload(unchanged, {}, force=False)
        self.assertIsNone(values)
        self.assertEqual(images, set())

    def test_native_texture_template_allows_two_material_variants(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as folder:
            images = {}
            for name, color in [('bar', (255, 0, 0, 255)), ('cloth', (0, 0, 255, 255))]:
                path = Path(folder) / (name + '.png')
                Image.new('RGBA', (2, 2), color).save(path)
                image = Material(name)
                image.filepath = str(path)
                images[name] = image
            native = dict(details=dict(textures=[dict(property='_BaseMap',
                          pointer=dict(isNull=False, identity='native-tex'))]),
                          native=dict(typeHash='12' * 16))
            source = source_stub(native, [
                dict(identity='native-tex', logicalPath='assets/shared_d', name='Shared_D',
                     details=dict(format='BC7', colorSpace=1))])
            addon = SimpleNamespace(image_absolute_path=lambda image: image.filepath,
                write_texture=lambda path, image: shutil.copyfile(image.filepath, path),
                material_override_payload=lambda mat, *args, **kwargs:
                    (['texture._BaseMap=' + mat['image']], None))
            textures, cache, records = {}, {}, []
            for name in ('bar', 'cloth'):
                mat = Material('Material' + name, dict(eiem_source='assets/shared_mat',
                               eiem_name='SharedMaterial', image=name))
                records.append(exporter.collect_material(addon, source, mat, images,
                    textures, folder, None, texture_only=True, texture_cache=cache))
            first, second = (row['textureEdits'][0]['texture'] for row in records)
            self.assertNotEqual(first, second)
            self.assertEqual(len(textures), 2)
            self.assertNotEqual(textures[first]['pixels'], textures[second]['pixels'])
            self.assertEqual(textures[first]['sourceAsset'], textures[second]['sourceAsset'])
            self.assertEqual(textures[first]['section'], first)
            # A later partial export keeps the binding ID while pixels change.
            Image.new('RGBA', (2, 2), (0, 255, 0, 255)).save(Path(images['bar'].filepath))
            mat = Material('Materialbar', dict(eiem_source='assets/shared_mat',
                           eiem_name='SharedMaterial', image='bar'))
            updated = exporter.collect_material(addon, source, mat, images, {},
                folder, None, texture_only=True, texture_cache={})
            self.assertEqual(updated['textureEdits'][0]['texture'], first)

    def test_shared_author_image_uses_one_texture_resource_across_materials(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'shared.png'
            Image.new('RGBA', (2, 2), (40, 80, 120, 255)).save(path)
            image = Material('SharedImage', {'eiem_section': 'TextureShared'})
            image.filepath = str(path)
            native = dict(details=dict(textures=[dict(property='_BaseMap',
                          pointer=dict(isNull=False, identity='native-tex'))]),
                          native=dict(typeHash='12' * 16))
            source = source_stub(native, [
                dict(identity='native-tex', logicalPath='assets/shared_d', name='Shared_D',
                     details=dict(format='BC7', colorSpace=1))])
            addon = SimpleNamespace(image_absolute_path=lambda owner: owner.filepath,
                write_texture=lambda output, owner: shutil.copyfile(owner.filepath, output),
                material_override_payload=lambda *args, **kwargs:
                    (['texture._BaseMap=shared'], None))
            textures, cache, records = {}, {}, []
            for mid in ('MaterialVariantA', 'MaterialVariantB'):
                material = Material(mid, dict(eiem_source='assets/shared_mat', eiem_name='SharedMaterial'))
                records.append(exporter.collect_material(
                    addon, source, material, {'shared': image}, textures, folder, None,
                    texture_only=True, mid=mid, texture_cache=cache))
            first, second = (row['textureEdits'][0]['texture'] for row in records)
            self.assertEqual(first, second)
            self.assertEqual(len(textures), 1)

    def test_source_texture_png_is_compressed_when_exported(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            texture_path = root / 'textures' / 'source.png'
            texture_path.parent.mkdir()
            Image.new('RGBA', (2, 2), (40, 80, 120, 255)).save(texture_path)
            image = Material('SourceTexture', {'eiem_section': 'TextureSource'})
            image.filepath = str(texture_path)
            native = dict(details=dict(textures=[dict(property='_BaseMap',
                          pointer=dict(isNull=False, identity='native-tex'))]),
                          native=dict(typeHash='12' * 16))
            source = source_stub(native, [dict(identity='native-tex',
                    logicalPath='assets/game/source', name='Source',
                    authorFile='textures/source.png',
                    details=dict(format='BC7', colorSpace=1))], package=root)
            addon = SimpleNamespace(
                image_absolute_path=lambda owner: owner.filepath,
                material_override_payload=lambda *args, **kwargs: (
                    ['format=EFFMAT', 'version=1', 'overrides=true',
                     'source=assets/game/material', 'name=Material',
                     'texture._BaseMap=TextureSource'], None))
            textures = {}
            record = exporter.collect_material(addon, source,
                Material('Material', {'eiem_source': 'assets/game/material'}),
                {'TextureSource': image}, textures, folder, None,
                texture_only=True, include_native_fields=False)
            self.texture_compressor_mock.assert_called_once()
            self.assertEqual(len(textures), 1)
            self.assertEqual(len(record['textureEdits']), 1)
            self.assertIn('texture._BaseMap', record['authorData'].decode())

    def test_collect_material_preserves_complete_effmat_author_snapshot(self):
        values = ["format=EFFMAT", "version=1", "overrides=true",
                  "source=assets/game/body.mat", "name=Body",
                  "float._Gloss=.4"]
        addon = SimpleNamespace(material_override_payload=lambda *args, **kwargs: (values, None))
        source = SimpleNamespace(resolve=lambda *args: dict(
            native=dict(typeHash="12" * 16), details=dict(textures=[])))
        material = Material("MaterialBody", dict(eiem_source="assets/game/body.mat"))
        with tempfile.TemporaryDirectory() as folder:
            record = exporter.collect_material(addon, source, material, {}, {}, folder,
                                               None, texture_only=True)
        self.assertEqual(record["authorData"], ("\n".join(values) + "\n").encode("utf-8"))

    def test_collect_material_omits_unchanged_inherited_source(self):
        addon = SimpleNamespace(material_override_payload=lambda *args, **kwargs: (None, set()))
        source = SimpleNamespace(resolve=lambda *args: self.fail(
            "an inherited source Material must not be cloned"))
        material = Material("MaterialBody", dict(eiem_source="assets/game/body.mat"))
        with tempfile.TemporaryDirectory() as folder:
            record = exporter.collect_material(addon, source, material, {}, {}, folder,
                                               None, force=False)
        self.assertIsNone(record)

    def test_format3_collect_material_skips_native_material_snapshot(self):
        values = ["format=EFFMAT", "version=1", "overrides=true",
                  "source=assets/game/body.mat", "name=Body", "float._Gloss=.4"]
        addon = SimpleNamespace(material_override_payload=lambda *args, **kwargs: (values, None))
        def unexpected_source_lookup(*args):
            raise AssertionError("scalar-only Format 3 export should not read source Material bytes")
        source = SimpleNamespace(resolve=unexpected_source_lookup)
        material = Material("MaterialBody", dict(eiem_source="assets/game/body.mat",
                                                   eiem_name="Body"))
        with tempfile.TemporaryDirectory() as folder, patch.object(
                exporter, "native_material_fields",
                side_effect=AssertionError("full native Material snapshot was requested")):
            record = exporter.collect_material(addon, source, material, {}, {}, folder,
                                               None, include_native_fields=False)
        self.assertEqual(record["fieldData"], {})
        self.assertEqual(record["nativeTypeHash"], "00" * 16)
        self.assertEqual(record["sourcePath"], "assets/game/body.mat")
        self.assertEqual(record["sourceAsset"], "Body")
        self.assertEqual(record["authorData"], ("\n".join(values) + "\n").encode("utf-8"))

    def test_mesh_snapshots_share_only_the_current_exports_skin_context(self):
        calls=[]
        addon=SimpleNamespace(write_mesh=lambda path,obj,**options:
            calls.append((obj,options)) or dict(owner=obj))
        first,second=object(),object()
        cache={}
        exporter._cached_mesh_snapshot(addon,first,cache)
        exporter._cached_mesh_snapshot(addon,second,cache)
        self.assertIs(calls[0][1]['skin_cache'],cache)
        self.assertIs(calls[1][1]['skin_cache'],cache)
        self.assertIsNot(calls[0][0],calls[1][0])
        next_export={}
        exporter._cached_mesh_snapshot(addon,first,next_export)
        self.assertIs(calls[2][1]['skin_cache'],next_export)
        self.assertIsNot(calls[2][1]['skin_cache'],cache)

    def test_native_mesh_encoding_reuses_snapshot_and_validates_source_before_hit(self):
        calls, checked = [], []
        owner = SimpleNamespace(as_pointer=lambda: 42, token='first', vertices=[1, 2, 3])
        addon = SimpleNamespace(_mesh_resource_token=lambda obj: obj.token,
                               write_mesh=lambda *args, **kwargs: {'vertices': owner.vertices})
        def native_mesh(tree, author, name):
            calls.append((author['vertices'], name))
            return {'field': bytes(author['vertices'])}, {}
        modules = {'mesh': SimpleNamespace(native_mesh=native_mesh),
                   'delta_apply': SimpleNamespace(GEOMETRY_FIELDS=['field'])}
        source = SimpleNamespace(rows={'CAB': {'snapshot': {'sha256': 'proof'}}},
                                 file=lambda target: checked.append(target),
                                 tree=lambda target: object())
        target = {'identity': 'mesh', 'native': {'cab': 'CAB', 'typeHash': 'schema'}}
        exporter._NATIVE_SNAPSHOT_CACHE.clear()
        snapshot, _ = exporter._cached_mesh_snapshot(addon, owner)
        encode = lambda value: value
        first = exporter.native_mesh_fields(modules, source, target, snapshot,
                                            snapshot, 0, 'Mesh', encode)
        self.assertEqual(exporter.native_mesh_fields(modules, source, target, snapshot,
                         snapshot, 0, 'Mesh', encode), first)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(checked), 2)
        owner.token = 'edited'; owner.vertices = [4, 5, 6]
        edited, _ = exporter._cached_mesh_snapshot(addon, owner)
        self.assertNotEqual(exporter.native_mesh_fields(modules, source, target, edited,
                            edited, 0, 'Mesh', encode), first)
        self.assertEqual(len(calls), 2)
        source.rows['CAB']['snapshot']['sha256'] = 'changed-source'
        exporter.native_mesh_fields(modules, source, target, edited, edited, 0, 'Mesh', encode)
        self.assertEqual(len(calls), 3)
        def invalid(target):
            raise ValueError('source snapshot hash')
        source.file = invalid
        with self.assertRaisesRegex(ValueError, 'source snapshot hash'):
            exporter.native_mesh_fields(modules, source, target, edited, edited, 0, 'Mesh', encode)

    def test_material_fields_reuse_unchanged_parameters_and_validate_current_source(self):
        exporter._NATIVE_MATERIAL_FIELDS_CACHE.clear()
        checked = []
        source = SimpleNamespace(rows={'CAB': {'snapshot': {'sha256': 'proof'}}},
                                 file=lambda target: checked.append(target),
                                 tree=lambda target: object())
        native = {'identity': 'material', 'native': {'cab': 'CAB', 'typeHash': 'schema'}}
        encode = lambda value: value
        with patch.object(exporter, 'material_fields', side_effect=lambda tree, values, fn:
                          {'field': values['float._AlphaClip'].encode()}) as build:
            first = exporter.native_material_fields(source, native, {'float._AlphaClip': '0'}, encode)
            self.assertEqual(exporter.native_material_fields(source, native,
                {'float._AlphaClip': '0', 'texture._MainTex': 'new-pixels'}, encode), first)
            self.assertEqual(build.call_count, 1)
            self.assertNotEqual(exporter.native_material_fields(source, native,
                {'float._AlphaClip': '1'}, encode), first)
            self.assertEqual(build.call_count, 2)
            source.rows['CAB']['snapshot']['sha256'] = 'updated-game-source'
            exporter.native_material_fields(source, native, {'float._AlphaClip': '0'}, encode)
            self.assertEqual(build.call_count, 3)
            self.assertEqual(len(checked), 4)
            def invalid(target):
                raise ValueError('source snapshot hash')
            source.file = invalid
            with self.assertRaisesRegex(ValueError, 'source snapshot hash'):
                exporter.native_material_fields(source, native, {'float._AlphaClip': '0'}, encode)

    def test_disk_texture_cache_checks_bytes_even_when_size_and_timestamp_match(self):
        from PIL import Image
        exporter._NATIVE_IMAGE_CACHE.clear()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'pixels.bmp'
            Image.new('RGBA', (2, 2), (255, 0, 0, 255)).save(path)
            stamp, size = path.stat().st_mtime_ns, path.stat().st_size
            with exporter.texture_bitmap(path, True) as first:
                self.assertEqual(first.getpixel((0, 0)), (255, 0, 0, 255))
            Image.new('RGBA', (2, 2), (0, 255, 0, 255)).save(path)
            os.utime(path, ns=(stamp, stamp))
            self.assertEqual(path.stat().st_size, size)
            with exporter.texture_bitmap(path, True) as changed:
                self.assertEqual(changed.getpixel((0, 0)), (0, 255, 0, 255))
            # A dirty/packed image's temporary export is always read again.
            with exporter.texture_bitmap(path, False) as uncached:
                self.assertEqual(uncached.tobytes(), changed.tobytes())

    def test_texture_compression_cache_key_tracks_pixels_and_settings(self):
        base = exporter._texture_compression_cache_key(
            'A' * 64, 256, 128, True, 'BC7', 1, 'texconv.exe', flip_y=False)
        variants = (
            exporter._texture_compression_cache_key(
                'B' * 64, 256, 128, True, 'BC7', 1, 'texconv.exe', flip_y=False),
            exporter._texture_compression_cache_key(
                'A' * 64, 256, 128, False, 'BC7', 1, 'texconv.exe', flip_y=False),
            exporter._texture_compression_cache_key(
                'A' * 64, 256, 128, True, 'BC7', 0, 'texconv.exe', flip_y=False),
            exporter._texture_compression_cache_key(
                'A' * 64, 256, 128, True, 'BC5', 1, 'texconv.exe', flip_y=False),
            exporter._texture_compression_cache_key(
                'A' * 64, 256, 128, True, 'BC7', 1, 'texconv.exe', flip_y=True),
        )
        self.assertTrue(all(key != base for key in variants))

    def test_texture_compression_cache_validates_payload_checksum(self):
        key = 'c' * 64
        payload = b'compressed BCn blocks'
        exporter._write_texture_compression_cache(key, payload)
        path = Path(self.texture_cache_directory.name) / (key + '.bcncache')
        self.assertEqual(exporter._read_texture_compression_cache(key, len(payload)), payload)
        path.write_bytes(exporter._NATIVE_TEXTURE_COMPRESSION_CACHE_MAGIC + b'X' * 32 + payload)
        self.assertIsNone(exporter._read_texture_compression_cache(key, len(payload)))
        self.assertFalse(path.exists())

    def test_texture_compression_cache_prunes_oldest_entries_to_limit(self):
        root = Path(self.texture_cache_directory.name)
        root.mkdir(parents=True, exist_ok=True)
        oldest = root / 'old.bcncache'
        newest = root / 'new.bcncache'
        oldest.write_bytes(b'A' * 12)
        newest.write_bytes(b'B' * 12)
        os.utime(oldest, ns=(1_000_000_000, 1_000_000_000))
        os.utime(newest, ns=(2_000_000_000, 2_000_000_000))
        with patch.object(exporter, '_NATIVE_TEXTURE_COMPRESSION_CACHE_BYTES', 12):
            exporter._prune_texture_compression_cache()
        self.assertFalse(oldest.exists())
        self.assertTrue(newest.exists())

    def test_reextracted_source_rebinds_objects_and_rejects_mid_export_change(self):
        module = exporter.pack_modules()[0]['author_source']
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder);(root/'source').mkdir()
            path = root/'source/manifest.json'
            row = dict(type='Mesh', logicalPath='assets/body.mesh', name='Body',
                       identity='old:1', native=dict(cab='old', pathId=1))
            manifest = dict(format='EFFSOURCE', version=1,
                            serializedFiles=[], resources=[row])
            path.write_text(json.dumps(manifest))
            original = module.AuthorSource(root, None)
            self.assertEqual(original.resolve('Mesh', 'assets/body.mesh', 'Body')['native']['pathId'], 1)
            self.assertEqual(original.resolve_identity('old:1')['native']['pathId'], 1)
            row.update(identity='new:2', native=dict(cab='new', pathId=2))
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, '导出期间发生更新'):
                original.assert_current()
            refreshed = module.AuthorSource(root, None)
            self.assertEqual(refreshed.resolve('Mesh', 'assets/body.mesh', 'Body')['native'], dict(cab='new', pathId=2))
            self.assertEqual(refreshed.resolve_identity('new:2')['native']['pathId'], 2)
            refreshed.assert_current()

    def test_source_target_index_preserves_duplicate_target_rejection(self):
        module = exporter.pack_modules()[0]['author_source']
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root/'source').mkdir()
            rows = [
                dict(type='Mesh', logicalPath='assets/body.mesh', name='Body',
                     identity='cab-a:1'),
                dict(type='Mesh', logicalPath='assets/body.mesh', name='Body',
                     identity='cab-b:2'),
            ]
            (root/'source/manifest.json').write_text(json.dumps(dict(
                format='EFFSOURCE', version=1, serializedFiles=[], resources=rows)))
            source = module.AuthorSource(root, None)
            with self.assertRaisesRegex(ValueError, '2 matches'):
                source.resolve('Mesh', 'assets/body.mesh', 'Body')
            self.assertEqual(source.resolve_identity('cab-a:1')['identity'], 'cab-a:1')

    def test_material_uses_its_own_source_and_reuses_loaded_package(self):
        class Source:
            def __init__(self, package, core):
                self.package, self.core = Path(package).resolve(), core
        main = Source('/sources/main', object())
        local = Material('Local')
        foreign = Material('Foreign', dict(eiem_author_package='/sources/foreign'))
        cache = {}
        self.assertIs(exporter.material_source(main, local, cache), main)
        resolved = exporter.material_source(main, foreign, cache)
        self.assertEqual(resolved.package, Path('/sources/foreign').resolve())
        self.assertIs(resolved.core, main.core)
        self.assertIs(exporter.material_source(main, foreign, cache), resolved)
        local['eiem_author_package'] = str(main.package)
        self.assertIs(exporter.material_source(main, local, cache), main)

    def test_mod_material_uses_selected_baseline_instead_of_mod_as_native_source(self):
        module = exporter.pack_modules()[0]['author_source']
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline = root / 'source-package'
            (baseline / 'source').mkdir(parents=True)
            (baseline / 'source/manifest.json').write_text(json.dumps(dict(
                format='EFFSOURCE', version=1, serializedFiles=[], resources=[])))
            mod = root / 'runtime-mod'
            mod.mkdir()
            (mod / 'mod.ini').write_text('[Mod]\nformat=3\n')
            primary = module.AuthorSource(baseline, None)
            material = Material('ModMaterial', dict(eiem_author_package=str(mod)))
            cache = {}
            self.assertIs(exporter.material_source(primary, material, cache), primary)
            self.assertEqual(cache, {})

    def test_export_material_sources_are_validated_before_publication(self):
        calls = []

        class Source:
            package = Path('/sources/main')

            def resolve(self, kind, path, asset):
                calls.append((kind, path, asset))
                if asset != 'ExistingMaterial':
                    raise ValueError('missing source')
                return object()

        valid = Material('Material', dict(eiem_source='assets/game/material.mat',
                                          eiem_name='ExistingMaterial'))
        exporter.validate_export_material_sources(Source(), [valid], {})
        self.assertEqual(calls, [('Material', 'assets/game/material.mat', 'ExistingMaterial')])

        invalid = Material('Material', dict(eiem_source='assets/game/missing.mat',
                                            eiem_name='MissingMaterial'))
        with self.assertRaisesRegex(ValueError, 'complete Prefab EFFSOURCE'):
            exporter.validate_export_material_sources(Source(), [invalid], {})

    def test_shared_texture_conversion_reuses_pixels_and_preserves_conflicts(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'image.png'
            Image.new('RGBA', (4, 4), (20, 40, 60, 255)).save(path)
            image = Material('Image', {})
            image.filepath = str(path)
            calls = []
            def write_texture(output, owner):
                calls.append(owner)
                shutil.copyfile(owner.filepath, output)
            addon = SimpleNamespace(image_absolute_path=lambda owner: owner.filepath,
                _owner_revision=lambda owner: owner.get('revision', 0), write_texture=write_texture,
                material_override_payload=lambda *args, **kwargs: (
                    ['texture._BaseMap=slot', 'texture._DetailMap=slot'], None))
            tex = dict(identity='native-tex', logicalPath='assets/body_d', name='Body_D',
                       details=dict(format='BC7', colorSpace=1))
            native = dict(details=dict(textures=[dict(property=prop, pointer=dict(
                isNull=False, identity='native-tex')) for prop in ('_BaseMap', '_DetailMap')]),
                native=dict(typeHash='12' * 16))
            source = SimpleNamespace(resolve=lambda *args: native,
                resolve_identity=lambda identity: next(
                    row for row in source.manifest['resources'] if row['identity'] == identity),
                manifest=dict(resources=[tex]))
            material = Material('MaterialBody', dict(eiem_source='assets/body', eiem_name='Body'))
            textures, cache = {}, {}
            def collect():
                return exporter.collect_material(addon, source, material, {'slot': image},
                    textures, folder, None, texture_only=True, texture_cache=cache)
            first_record = collect(); collect()
            first = first_record['textureEdits'][0]['texture']
            self.assertEqual(len(calls), 0)  # Clean disk image bypasses Blender PNG writing.
            self.assertEqual(textures[first]['mipCount'], 3)
            # Different native templates can share one Blender image. Pixels
            # are converted once, but each target keeps its own source identity.
            source.manifest['resources'].append(dict(identity='native-detail',
                logicalPath='assets/detail_d', name='Detail_D', details=dict(format='BC5', colorSpace=0)))
            native['details']['textures'][1]['pointer']['identity'] = 'native-detail'
            detail_record = collect()
            detail = detail_record['textureEdits'][1]['texture']
            self.assertEqual(len(calls), 0)
            self.assertEqual(textures[detail]['sourceAsset'], 'Detail_D')
            self.assertEqual(textures[detail]['sourcePath'], 'assets/detail_d')
            self.assertEqual(textures[first]['colorSpace'], 1)
            self.assertEqual(textures[detail]['colorSpace'], 0)
            self.assertEqual(textures[first]['format'], 'BC7')
            self.assertEqual(textures[detail]['format'], 'BC5')
            self.assertEqual(textures[detail]['sourcePixelsHash'], textures[first]['sourcePixelsHash'])
            self.assertNotEqual(textures[detail]['pixels'], textures[first]['pixels'])
            image['eiem_filter'] = 2
            reconfigured = collect()
            self.assertNotEqual(reconfigured['textureEdits'][0]['texture'], first)
            self.assertNotEqual(reconfigured['textureEdits'][1]['texture'], detail)
            self.assertEqual(len(calls), 0)
            image.pop('eiem_filter')
            stamp = path.stat()
            Image.new('RGBA', (4, 4), (90, 20, 10, 255)).save(path)
            os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1000000000))
            with self.assertRaisesRegex(ValueError, '冲突'):
                collect()
            self.assertEqual(len(calls), 0)

    def test_empty_native_texture_slot_uses_schema_and_png_is_always_compressed(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'emission.png'
            Image.new('RGBA',(2,2),(255,0,0,255)).save(path)
            image=Material('Emission',{'eiem_linear':'true'}); image.filepath=str(path)
            native=dict(details=dict(textures=[dict(property='_EmissionMap',pointer=dict(isNull=True)),
                         dict(property='_BaseMap',pointer=dict(isNull=False,identity='base'))]),native=dict(typeHash='12'*16))
            source=SimpleNamespace(resolve=lambda *args:native,
                resolve_identity=lambda identity: next(
                    row for row in source.manifest['resources'] if row['identity'] == identity),
                manifest=dict(resources=[dict(identity='base',name='Base_D',logicalPath='assets/base',details=dict(format='BC7',colorSpace=1))]))
            addon=SimpleNamespace(material_override_payload=lambda *args,**kwargs:(['texture._EmissionMap=image'],None))
            mat=Material('MaterialNew',dict(eiem_source='assets/mat',eiem_name='Template'))
            textures={}
            record=exporter.collect_material(addon,source,mat,{'image':image},textures,folder,None,texture_only=True)
            self.assertEqual(record['textureEdits'][0]['property'],'_EmissionMap')
            texture=textures[record['textureEdits'][0]['texture']]
            self.assertEqual(texture['sourceAsset'],'Base_D')
            self.assertEqual(texture['colorSpace'],1)
            self.texture_compressor_mock.assert_called_once()
            self.texture_compressor_mock.reset_mock()
            reused={}
            exporter.collect_material(addon,source,mat,{'image':image},reused,folder,None,
                                      texture_only=True)
            self.texture_compressor_mock.assert_not_called()
            self.assertFalse(next(iter(reused.values())).get('retained', False))
            image.is_dirty=True
            writes=[]
            addon.write_texture=lambda output,owner:(writes.append(owner),Image.new(
                'RGBA',(2,2),(0,255,0,255)).save(output))
            exporter.collect_material(addon,source,mat,{'image':image},{},folder,None,texture_only=True)
            self.assertEqual(len(writes),1)
            self.texture_compressor_mock.assert_called_once()
            self.assertTrue(self.texture_compressor_mock.call_args.kwargs['flip_y'])

    def test_dirty_dds_recompresses_current_blender_pixels(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as folder:
            dds = Path(folder) / 'edited.dds'
            dds.write_bytes(dds_bc7(4, 4, b'D' * 16))
            image = Material('EditedDDS', {'eiem_linear': 'false'})
            image.filepath = str(dds)
            image.is_dirty = True
            addon = SimpleNamespace(
                image_absolute_path=lambda owner: owner.filepath,
                write_texture=lambda output, owner: Image.new(
                    'RGBA', (2, 2), (17, 33, 65, 255)).save(output),
                material_override_payload=lambda *args, **kwargs: (
                    ['texture._BaseMap=slot'], None))
            texture = dict(identity='source-texture', logicalPath='assets/body_d', name='Body_D',
                           details=dict(format='BC7', colorSpace=1))
            native = dict(details=dict(textures=[dict(property='_BaseMap', pointer=dict(
                isNull=False, identity='source-texture'))]), native=dict(typeHash='12' * 16))
            source = SimpleNamespace(resolve=lambda *args: native,
                resolve_identity=lambda identity: texture,
                manifest=dict(resources=[texture]))
            material = Material('MaterialBody', dict(eiem_source='assets/body', eiem_name='Body'))
            textures = {}

            record = exporter.collect_material(
                addon, source, material, {'slot': image}, textures, folder, None,
                texture_only=True)

            exported = textures[record['textureEdits'][0]['texture']]
            self.assertEqual(exported['width'], 2)
            self.assertEqual(exported['height'], 2)
            self.assertIsNone(exported['ddsHash'])
            self.assertIsNone(exported['ddsSourcePath'])
            self.assertEqual(self.texture_compressor_mock.call_args.args[0],
                             bytes((17, 33, 65, 255)) * 4)
            self.assertTrue(self.texture_compressor_mock.call_args.kwargs['flip_y'])

    def test_export_staging_closes_frozen_resources_before_cleanup(self):
        class Stream:
            closed = False
            def close(self): self.closed = True

        stream = Stream()
        cache = {'__frozen_files__': {('device', 'inode', 3): (stream, (), {})}}
        events = []
        with tempfile.TemporaryDirectory() as folder:
            destination_parent = Path(folder) / 'mods' / 'chen'
            destination_parent.mkdir(parents=True)
            destination = destination_parent / 'mod.ini'

            class TemporaryDirectory:
                def __enter__(self): return 'staging-root'
                def __exit__(self, *_): events.append(('cleanup', stream.closed))

            with patch.object(exporter.tempfile, 'TemporaryDirectory',
                              return_value=TemporaryDirectory()):
                with self.assertRaisesRegex(RuntimeError, 'export failed'):
                    with exporter._export_temporary_directory(
                            cache, destination, prefix='test-export-'):
                        raise RuntimeError('export failed')

        self.assertEqual(events, [('cleanup', True)])
        self.assertEqual(cache['__frozen_files__'], {})

    def test_source_checkout_imports_canonical_nativepack(self):
        modules, _ = exporter.pack_modules()
        self.assertEqual(Path(modules['author_source'].__file__).resolve().parent,
                         (ROOT / 'tools/nativepack').resolve())
        self.assertIn('effect_native', modules)
        self.assertNotIn('runtime_controls', modules)

    @unittest.skip('Format 2 package publication belongs to Mod Manager')
    def test_parent_export_preserves_nested_mods(self):
        import nativepack.ini_package as writer
        import nativepack.ini_reader as reader
        fixture_path=ROOT/'tools/nativepack/test_directory_package.py'
        fixture=load_module('nested_export_fixture',fixture_path).fixture
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'lx';root.mkdir()
            writer.export_directory(root,**fixture())
            child=root/'effects'/'花瓣';child.mkdir(parents=True)
            writer.export_directory(child,**dict(fixture(),mod_id='Petals'))
            hashes={p.relative_to(child).as_posix():p.read_bytes() for p in child.rglob('*') if p.is_file()}
            staging=Path(directory)/'staging'
            exporter._clone_staging_tree(root,staging)
            self.assertFalse((staging/'effects').exists())
            ini=writer.ini_document(staging)
            ini['Mod']['key_switch_enabled']='0'
            exporter._detach_staging_files([staging/'mod.ini'])
            writer.save_ini(staging,ini)
            exporter.publish(staging,root,reader.read_package)
            self.assertEqual({p.relative_to(child).as_posix():p.read_bytes() for p in child.rglob('*') if p.is_file()},hashes)
            self.assertFalse(reader.read_package(root)['keySwitchEnabled'])

    def test_material_ids_do_not_alias_sources_and_remain_stable(self):
        first = Material('MaterialBody', dict(eiem_section='MaterialBody',
                         eiem_author_package='/source/first'))
        second = Material('MaterialBody.001', dict(eiem_section='MaterialBody',
                          eiem_author_package='/source/second'))
        ids = exporter.material_ids([second, first])
        self.assertEqual(ids[first.as_pointer()], 'MaterialBody')
        self.assertNotEqual(ids[first.as_pointer()], ids[second.as_pointer()])
        self.assertEqual(exporter.material_ids([first, second]), ids)
        # The stored ID lets a later partial export select only the second.
        self.assertEqual(exporter.material_ids([second])[second.as_pointer()],
                         ids[second.as_pointer()])

    def test_material_import_keeps_package_ownership(self):
        collection = MaterialCollection()
        namespace = dict(bpy=SimpleNamespace(data=SimpleNamespace(materials=collection)),
                         Path=Path, json=json)
        load_material = addon_function('load_material', namespace)
        first = load_material('/source/first', 'MaterialBody', {'source': 'first'})
        second = load_material('/source/second', 'MaterialBody', {'source': 'second'})
        self.assertIsNot(first, second)
        self.assertEqual(first['eiem_source'], 'first')
        self.assertEqual(second['eiem_source'], 'second')
        self.assertIs(load_material('/source/first', 'MaterialBody', {'source': 'edited'}), first)
        self.assertEqual(second['eiem_source'], 'second')

    def test_automatic_shape_control_uses_live_channel_and_range(self):
        control = SimpleNamespace(shape='DisplayKey', enabled=True, identity='0123456789abcdef',
                                  label='Shape', default=0, minimum=0, maximum=1,
                                  hotkey_increase='K', hotkey_decrease='J', hotkey_speed=1,
                                  automatic=True)
        key = SimpleNamespace(value=0.6, slider_min=-0.5, slider_max=1.5)
        obj = SimpleNamespace(name='Mesh', data=SimpleNamespace(
            eiem_shape_controls=[control], shape_keys=SimpleNamespace(
                key_blocks={'DisplayKey': key}, reference_key=object())))
        addon = SimpleNamespace(sync_new_shape_controls=lambda obj: None,
                                shape_channel_name=lambda obj, key: 'NativeChannel')
        record, = exporter.shape_controls(addon, obj, False)
        self.assertEqual(record['shape'], 'NativeChannel')
        self.assertEqual((record['default'], record['minimum'], record['maximum']),
                         (0.6, -0.5, 1.5))
        self.assertEqual((record['hotkey_increase'], record['hotkey_decrease']), ('', ''))

    def test_public_export_facade_restores_revision_gate_on_failure(self):
        calls = []
        context = SimpleNamespace(mode='OBJECT', scene=SimpleNamespace(eiem_source_baseline='source'),
                                  view_layer=SimpleNamespace(update=lambda: calls.append('update')))
        namespace = dict(bpy=SimpleNamespace(context=context), sys=sys,
                         __name__=__name__, _EXPORT_IN_PROGRESS=False, _EXPORT_SKELETON_TOKENS=None,
                         )

        def fail(*args, **kwargs):
            self.assertTrue(namespace['_EXPORT_IN_PROGRESS'])
            self.assertEqual(kwargs['source_baseline'], 'source')
            raise ValueError('bad author')

        namespace['native_export'] = SimpleNamespace(export_native_package=fail)
        export_package = addon_function('export_package', namespace)
        with self.assertRaisesRegex(ValueError, 'bad author'):
            export_package('output', [object()], [], [])
        self.assertFalse(namespace['_EXPORT_IN_PROGRESS'])
        self.assertIsNone(namespace['_EXPORT_SKELETON_TOKENS'])
        self.assertEqual(calls, ['update', 'update'])

    def test_physics_only_export_resolves_source_from_selected_rig(self):
        with tempfile.TemporaryDirectory() as folder:
            package = Path(folder)
            (package / 'source').mkdir()
            (package / 'source/manifest.json').write_text('{}', encoding='utf-8')
            rig = {'eiem_author_package': str(package)}
            group = SimpleNamespace(eiem_physics=SimpleNamespace(rig=rig))
            self.assertEqual(exporter.baseline_for([], '', [group]), package.resolve())

if __name__ == '__main__':
    unittest.main()

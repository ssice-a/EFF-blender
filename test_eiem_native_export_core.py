"""Pure export contracts; these tests never start Blender or export a scene."""
import ast
import importlib.util
import json
import os
import shutil
import struct
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


exporter = load_module('eiem_native_export_core_test',
                       Path(__file__).with_name('eiem_native_export.py'))
bundler = load_module('eiem_bundle_core_test',
                     ROOT / 'tools/diagnostics/bundle_blender_native_export.py')


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


def addon_function(name, namespace, path=ADDON):
    """Execute the real function with tiny Blender-shaped dependencies."""
    tree = ast.parse(path.read_text('utf-8'))
    function = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == name)
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(ADDON), 'exec'),
         namespace)
    return namespace[name]


class NativeExportCoreTests(unittest.TestCase):
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
        payload = addon_function('material_override_payload', namespace)
        material = Material('Inner', dict(eiem_source='assets/source.mat',
                            eiem_baseline_json=json.dumps({'source': 'assets/source.mat'}),
                            eiem_render_queue=2999))
        values, images = payload(material, {}, force=True)
        self.assertIn('render_queue=2999', values)
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
            source = SimpleNamespace(resolve=lambda *args: native, manifest=dict(resources=[
                dict(identity='native-tex', logicalPath='assets/shared_d', name='Shared_D')]))
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

    def test_reextracted_source_rebinds_objects_and_rejects_mid_export_change(self):
        module = exporter.pack_modules()[0]['author_source']
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder);(root/'source').mkdir()
            path = root/'source/manifest.json'
            row = dict(type='Mesh', logicalPath='assets/body.mesh', name='Body',
                       identity='old:1', native=dict(cab='old', pathId=1))
            manifest = dict(format='EFFSOURCE', serializedFiles=[], resources=[row])
            path.write_text(json.dumps(manifest))
            original = module.AuthorSource(root, None)
            self.assertEqual(original.resolve('Mesh', 'assets/body.mesh', 'Body')['native']['pathId'], 1)
            row.update(identity='new:2', native=dict(cab='new', pathId=2))
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, '导出期间发生更新'):
                original.assert_current()
            refreshed = module.AuthorSource(root, None)
            self.assertEqual(refreshed.resolve('Mesh', 'assets/body.mesh', 'Body')['native'], dict(cab='new', pathId=2))
            refreshed.assert_current()

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
            tex = dict(identity='native-tex', logicalPath='assets/body_d', name='Body_D')
            native = dict(details=dict(textures=[dict(property=prop, pointer=dict(
                isNull=False, identity='native-tex')) for prop in ('_BaseMap', '_DetailMap')]),
                native=dict(typeHash='12' * 16))
            source = SimpleNamespace(resolve=lambda *args: native, manifest=dict(resources=[tex]))
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
                logicalPath='assets/detail_d', name='Detail_D'))
            native['details']['textures'][1]['pointer']['identity'] = 'native-detail'
            detail_record = collect()
            detail = detail_record['textureEdits'][1]['texture']
            self.assertEqual(len(calls), 0)
            self.assertEqual(textures[detail]['sourceAsset'], 'Detail_D')
            self.assertEqual(textures[detail]['sourcePath'], 'assets/detail_d')
            self.assertIs(textures[detail]['pixels'], textures[first]['pixels'])
            image['eiem_filter'] = 2
            with self.assertRaisesRegex(ValueError, '冲突'):
                collect()
            self.assertEqual(len(calls), 0)
            image.pop('eiem_filter')
            stamp = path.stat()
            Image.new('RGBA', (4, 4), (90, 20, 10, 255)).save(path)
            os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1000000000))
            with self.assertRaisesRegex(ValueError, '冲突'):
                collect()
            self.assertEqual(len(calls), 0)

    def test_empty_native_texture_slot_uses_an_existing_schema(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'emission.png'
            Image.new('RGBA',(2,2),(255,0,0,255)).save(path)
            image=Material('Emission'); image.filepath=str(path)
            native=dict(details=dict(textures=[dict(property='_EmissionMap',pointer=dict(isNull=True)),
                         dict(property='_BaseMap',pointer=dict(isNull=False,identity='base'))]),native=dict(typeHash='12'*16))
            source=SimpleNamespace(resolve=lambda *args:native,manifest=dict(resources=[dict(identity='base',name='Base_D',logicalPath='assets/base')]))
            addon=SimpleNamespace(material_override_payload=lambda *args,**kwargs:(['texture._EmissionMap=image'],None))
            mat=Material('MaterialNew',dict(eiem_source='assets/mat',eiem_name='Template'))
            textures={}
            record=exporter.collect_material(addon,source,mat,{'image':image},textures,folder,None,texture_only=True)
            self.assertEqual(record['textureEdits'][0]['property'],'_EmissionMap')
            texture=textures[record['textureEdits'][0]['texture']]
            self.assertEqual(texture['sourceAsset'],'Base_D')
            prior=dict(texture,file='textures/emission.tex')
            existing=dict(textures=[prior],_resources={'textures/emission.tex':dict(blocks=dict(pixels=texture['pixels']))})
            with patch.object(Image.Image,'resize',side_effect=AssertionError('unchanged texture regenerated mipmaps')):
                reused={}
                exporter.collect_material(addon,source,mat,{'image':image},reused,folder,None,
                                          texture_only=True,existing_package=existing)
            self.assertTrue(next(iter(reused.values()))['retained'])
            image.is_dirty=True
            writes=[]
            addon.write_texture=lambda output,owner:(writes.append(owner),shutil.copyfile(owner.filepath,output))
            exporter.collect_material(addon,source,mat,{'image':image},{},folder,None,texture_only=True)
            self.assertEqual(len(writes),1)

    def test_partial_staging_hardlinks_are_detached_before_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source'
            staging = Path(folder) / 'staging'
            source.mkdir()
            original = source / 'textures' / 'body.tex'
            original.parent.mkdir()
            original.write_bytes(b'original')
            exporter._clone_staging_tree(source, staging)
            staged = staging / 'textures' / 'body.tex'
            exporter._detach_staging_files([staged])
            staged.write_bytes(b'updated')
            self.assertEqual(original.read_bytes(), b'original')
            self.assertEqual(staged.read_bytes(), b'updated')

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

    @unittest.skipUnless(os.name == 'nt', 'Windows resource file handle semantics')
    def test_export_staging_removes_directory_after_frozen_texture_read(self):
        resource_file = exporter.pack_modules()[0]['ini_reader']
        resource_file = __import__(resource_file.__package__ + '.resource_file', fromlist=['*'])
        with tempfile.TemporaryDirectory() as parent:
            cache = {'__frozen_files__': {}}
            mods = Path(parent) / 'mods'
            package = mods / 'chen'
            package.mkdir(parents=True)
            destination = package / 'mod.ini'
            staging = None
            with self.assertRaisesRegex(RuntimeError, 'export failed'):
                with exporter._export_temporary_directory(
                        cache, destination, prefix='eff-export-test-') as temporary:
                    staging = Path(temporary)
                    self.assertEqual(staging.parent.name, '.eff-export-staging')
                    texture = staging / 'mod' / 'textures' / 'clothes_P.tex'
                    texture.parent.mkdir(parents=True)
                    texture.write_bytes(resource_file.encode_resource(
                        resource_file.PIXELS, '00' * 16, {'pixels': b'pixels'}))
                    resource_file.read_resource(texture, resource_file.PIXELS, cache)
                    self.assertTrue(next(iter(cache['__frozen_files__'].values()))[0].closed is False)
                    raise RuntimeError('export failed')

            self.assertFalse(staging.exists())
            self.assertFalse((mods / '.eff-export-staging').exists())
            self.assertEqual(cache['__frozen_files__'], {})

    def test_source_checkout_imports_canonical_nativepack(self):
        modules, _ = exporter.pack_modules()
        self.assertEqual(Path(modules['author_source'].__file__).resolve().parent,
                         (ROOT / 'tools/nativepack').resolve())

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
                         export_game_directory=lambda root, configured: 'selected game')

        def fail(*args, **kwargs):
            self.assertTrue(namespace['_EXPORT_IN_PROGRESS'])
            self.assertEqual(kwargs['source_baseline'], 'source')
            self.assertEqual(kwargs['resource_scope'], 'TEXTURES')
            self.assertEqual(kwargs['game_directory'], 'selected game')
            raise ValueError('bad author')

        namespace['native_export'] = SimpleNamespace(export_native_package=fail)
        export_package = addon_function('export_package', namespace)
        with self.assertRaisesRegex(ValueError, 'bad author'):
            export_package('output', [object()], [], [], resource_scope='TEXTURES')
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

    def test_bundle_includes_hashing_and_rejects_label_unaware_layouts(self):
        self.assertIn('hashing', bundler.MODULES)
        with tempfile.TemporaryDirectory() as folder:
            compiler = Path(folder) / 'eff_resource_pack.exe'
            compiler.write_bytes(b'EFF_NATIVE_PACK_V35_1 EFF_NATIVE_PACK_V36_ASSEMBLY_1')
            protocol = dict(format='EFF_OFFLINE_PACKAGE_PROTOCOL', compiler='EFF_NATIVE_PACK_V36_ASSEMBLY_1', abi=36)
            with patch.object(bundler._protocol_module.subprocess,'run',return_value=SimpleNamespace(
                    returncode=0,stdout=json.dumps(protocol),stderr='')):
                with self.assertRaisesRegex(ValueError,'数据结构不兼容'):
                    bundler._protocol(compiler)
            with patch.object(bundler._protocol_module.subprocess,'run',return_value=SimpleNamespace(
                    returncode=1,stdout='',stderr='query failed')):
                with self.assertRaisesRegex(ValueError,'协议查询失败'):
                    bundler._protocol(compiler)

    def test_new_compiler_protocol_is_accepted(self):
        protocol = dict(format='EFF_OFFLINE_PACKAGE_PROTOCOL', version=1, abi=35,
                        compiler='EFF_NATIVE_PACK_V35_1', authorFormat=2, resourceVersion=2,
                        structures=dict(bundler._protocol_module.STRUCTURES))
        result = SimpleNamespace(returncode=0, stdout=json.dumps(protocol), stderr='')
        with patch.object(bundler._protocol_module.subprocess, 'run', return_value=result):
            self.assertEqual(bundler._protocol('compiler.exe'), protocol)

    def test_same_abi_with_wrong_resource_structure_is_rejected(self):
        protocol = dict(format='EFF_OFFLINE_PACKAGE_PROTOCOL', version=1, abi=35,
                        compiler='EFF_NATIVE_PACK_V35_1', authorFormat=2, resourceVersion=2,
                        structures=dict(bundler._protocol_module.STRUCTURES,
                                        resource='different-block-encoding'))
        result = SimpleNamespace(returncode=0, stdout=json.dumps(protocol), stderr='')
        with patch.object(bundler._protocol_module.subprocess, 'run', return_value=result):
            with self.assertRaisesRegex(ValueError, '结构'):
                bundler._protocol('compiler.exe')


if __name__ == '__main__':
    unittest.main()

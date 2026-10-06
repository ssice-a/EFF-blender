"""Pure export contracts; these tests never start Blender or export a scene."""
import ast
import importlib.util
import json
import os
import shutil
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
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


def addon_function(name, namespace):
    """Execute the real function with tiny Blender-shaped dependencies."""
    tree = ast.parse(ADDON.read_text('utf-8'))
    function = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == name)
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(ADDON), 'exec'),
         namespace)
    return namespace[name]


class NativeExportCoreTests(unittest.TestCase):
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
            collect(); collect()
            self.assertEqual(len(calls), 1)
            self.assertEqual(textures['native-tex']['mipCount'], 3)
            # Different native templates can share one Blender image. Pixels
            # are converted once, but each target keeps its own source identity.
            source.manifest['resources'].append(dict(identity='native-detail',
                logicalPath='assets/detail_d', name='Detail_D'))
            native['details']['textures'][1]['pointer']['identity'] = 'native-detail'
            collect()
            self.assertEqual(len(calls), 1)
            self.assertEqual(textures['native-detail']['sourceAsset'], 'Detail_D')
            self.assertEqual(textures['native-detail']['sourcePath'], 'assets/detail_d')
            self.assertIs(textures['native-detail']['pixels'], textures['native-tex']['pixels'])
            image['eiem_filter'] = 2
            with self.assertRaisesRegex(ValueError, '冲突'):
                collect()
            self.assertEqual(len(calls), 2)
            image.pop('eiem_filter')
            stamp = path.stat()
            Image.new('RGBA', (4, 4), (90, 20, 10, 255)).save(path)
            os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1000000000))
            with self.assertRaisesRegex(ValueError, '冲突'):
                collect()
            self.assertEqual(len(calls), 3)

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

    def test_checkout_imports_canonical_nativepack(self):
        modules, _ = exporter.pack_modules()
        self.assertEqual(Path(modules['author_source'].__file__).resolve().parent,
                         (ROOT / 'tools/nativepack').resolve())

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
                         __name__=__name__, _EXPORT_IN_PROGRESS=False)

        def fail(*args, **kwargs):
            self.assertTrue(namespace['_EXPORT_IN_PROGRESS'])
            self.assertEqual(kwargs['source_baseline'], 'source')
            self.assertEqual(kwargs['resource_scope'], 'TEXTURES')
            raise ValueError('bad author')

        namespace['native_export'] = SimpleNamespace(export_native_package=fail)
        export_package = addon_function('export_package', namespace)
        with self.assertRaisesRegex(ValueError, 'bad author'):
            export_package('output', [object()], [], [], resource_scope='TEXTURES')
        self.assertFalse(namespace['_EXPORT_IN_PROGRESS'])
        self.assertEqual(calls, ['update', 'update'])

    def test_bundle_includes_hashing_and_accepts_known_layouts(self):
        self.assertIn('hashing', bundler.MODULES)
        with tempfile.TemporaryDirectory() as folder:
            compiler = Path(folder) / 'eff_resource_pack.exe'
            compiler.write_bytes(b'EFF_NATIVE_PACK_V35_1 EFF_NATIVE_PACK_V36_ASSEMBLY_1')
            protocol = dict(format='EFF_OFFLINE_PACKAGE_PROTOCOL', compiler='EFF_NATIVE_PACK_V36_ASSEMBLY_1', abi=36)
            with patch.object(bundler._protocol_module.subprocess,'run',return_value=SimpleNamespace(
                    returncode=0,stdout=json.dumps(protocol),stderr='')):
                self.assertEqual(bundler._protocol(compiler)['structures']['manifestEncoding'],'legacy-root-addresses/le64')
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

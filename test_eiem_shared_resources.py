"""Shared resource settings and local tool-cache discovery."""

import importlib.util
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
import unittest
import zipfile


MODULE_PATH = Path(__file__).with_name('eiem_shared_resources.py')
SPEC = importlib.util.spec_from_file_location('eiem_shared_resources_test', MODULE_PATH)
shared = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(shared)


class SharedResourceTests(unittest.TestCase):
    def test_resource_and_index_choices_share_one_settings_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            local = root / 'local'
            resources = root / 'resource-root'
            workspace = root / 'animestudio-workspace'
            index = workspace / 'index' / 'endfield_assets.eidx'
            index.parent.mkdir(parents=True)
            index.touch()
            with patch.dict(os.environ, {'LOCALAPPDATA': str(local)}, clear=True):
                shared.set_resource_root(resources)
                shared.set_index_workspace(workspace)
                data = json.loads(shared.settings_path().read_text(encoding='utf-8'))
                self.assertEqual(data['resourceRoot'], str(resources.resolve()))
                self.assertEqual(data['indexWorkspace'], str(workspace.resolve()))
                self.assertEqual(shared.resource_root(), resources.resolve())
                self.assertEqual(shared.index_workspace(), workspace.resolve())
                self.assertNotIn('EFF_RESOURCE_ROOT', os.environ)

    def test_resource_root_follows_a_change_made_by_another_client(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.dict(os.environ, {'LOCALAPPDATA': str(root / 'local')}, clear=True):
                first = shared.set_resource_root(root / 'first')
                self.assertEqual(shared.resource_root(), first)
                second = shared.set_resource_root(root / 'second')
                self.assertEqual(shared.resource_root(), second)

    def test_tools_are_discovered_from_selected_root_and_reused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = root / 'tools' / ('a' * 64)
            native = install / 'nativepack'
            native.mkdir(parents=True)
            (root / 'tools' / 'current.json').write_text(json.dumps({
                'format': shared.TOOLS_INSTALL_FORMAT,
                'sha256': 'a' * 64,
                'release': 'v-test',
            }), encoding='utf-8')
            for name in ('eff_resource_pack.exe', 'eff_author_assets.dll', 'eff_source_index.exe',
                         'eff_author_core.dll', 'package-protocol.json', 'texconv.exe', 'THIRD_PARTY_NOTICES'):
                (native / name).touch()
            vendor = install / 'vendor' / 'PIL'
            vendor.mkdir(parents=True)
            for name in shared.PIL_REQUIRED_FILES:
                (install / 'vendor' / name).touch()
            with patch.dict(os.environ, {'LOCALAPPDATA': str(root / 'local')}, clear=True):
                shared.set_resource_root(root)
                self.assertEqual(shared.find_native_tools(), install.resolve())
                self.assertEqual(shared.native_tool_file('eff_resource_pack.exe'),
                                 (native / 'eff_resource_pack.exe').resolve())

    def test_single_abi_cache_is_not_discovered(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = root / 'tools' / ('b' * 64)
            native = install / 'nativepack'
            native.mkdir(parents=True)
            for name in ('eff_resource_pack.exe', 'eff_author_assets.dll', 'eff_source_index.exe',
                         'eff_author_core.dll', 'package-protocol.json', 'texconv.exe', 'THIRD_PARTY_NOTICES'):
                (native / name).touch()
            pil = install / 'vendor' / 'PIL'
            pil.mkdir(parents=True)
            (pil / '__init__.py').touch()
            (pil / '_imaging.cp311-win_amd64.pyd').touch()
            (root / 'tools' / 'current.json').write_text(json.dumps({
                'format': shared.TOOLS_INSTALL_FORMAT,
                'sha256': 'b' * 64,
                'release': 'old-single-abi',
            }), encoding='utf-8')
            with patch.dict(os.environ, {'LOCALAPPDATA': str(root / 'local')}, clear=True):
                shared.set_resource_root(root)
                self.assertIsNone(shared.find_native_tools())

    def test_export_tool_lookup_never_starts_a_download(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.dict(os.environ, {'LOCALAPPDATA': str(root / 'local')}, clear=True):
                shared.set_resource_root(root / 'resources')
                with patch.object(shared, '_latest_tools_manifest', side_effect=AssertionError('network lookup')):
                    with self.assertRaisesRegex(ValueError, '检查并准备工具'):
                        shared.native_tool_file('eff_resource_pack.exe')

    def test_prepare_checks_release_but_reuses_matching_shared_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            digest = 'a' * 64
            install = root / 'tools' / digest
            native = install / 'nativepack'
            native.mkdir(parents=True)
            for name in ('eff_resource_pack.exe', 'eff_author_assets.dll', 'eff_source_index.exe',
                         'eff_author_core.dll', 'package-protocol.json', 'texconv.exe', 'THIRD_PARTY_NOTICES'):
                (native / name).touch()
            vendor = install / 'vendor' / 'PIL'
            vendor.mkdir(parents=True)
            for name in shared.PIL_REQUIRED_FILES:
                (install / 'vendor' / name).touch()
            (root / 'tools' / 'current.json').write_text(json.dumps({
                'format': shared.TOOLS_INSTALL_FORMAT,
                'sha256': digest,
                'release': 'v-test',
            }), encoding='utf-8')
            manifest = {'sha256': digest}
            with patch.dict(os.environ, {'LOCALAPPDATA': str(root / 'local')}, clear=True):
                shared.set_resource_root(root)
                with patch.object(shared, '_latest_tools_manifest', return_value=manifest), \
                        patch.object(shared, 'urlopen', side_effect=AssertionError('unexpected archive download')):
                    self.assertEqual(shared.ensure_native_tools(), install.resolve())

    def test_prepare_reuses_complete_cache_when_latest_release_has_no_tools_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            digest = 'd' * 64
            install = root / 'tools' / digest
            native = install / 'nativepack'
            native.mkdir(parents=True)
            for name in ('eff_resource_pack.exe', 'eff_author_assets.dll', 'eff_source_index.exe',
                         'eff_author_core.dll', 'package-protocol.json', 'texconv.exe', 'THIRD_PARTY_NOTICES'):
                (native / name).touch()
            vendor = install / 'vendor' / 'PIL'
            vendor.mkdir(parents=True)
            for name in shared.PIL_REQUIRED_FILES:
                (install / 'vendor' / name).touch()
            (root / 'tools' / 'current.json').write_text(json.dumps({
                'format': shared.TOOLS_INSTALL_FORMAT,
                'sha256': digest,
                'release': 'v-local',
            }), encoding='utf-8')
            with patch.dict(os.environ, {'LOCALAPPDATA': str(root / 'local')}, clear=True):
                shared.set_resource_root(root)
                with patch.object(shared, '_latest_tools_manifest',
                                  side_effect=ValueError('EFF 最新 Release 尚未提供共享工具清单')):
                    self.assertEqual(shared.ensure_native_tools(), install.resolve())

    def test_prepare_updates_shared_cache_when_release_digest_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old_digest = 'a' * 64
            old_install = root / 'tools' / old_digest
            old_native = old_install / 'nativepack'
            old_native.mkdir(parents=True)
            for name in ('eff_resource_pack.exe', 'eff_author_assets.dll', 'eff_source_index.exe',
                         'eff_author_core.dll', 'package-protocol.json', 'texconv.exe', 'THIRD_PARTY_NOTICES'):
                (old_native / name).touch()
            old_vendor = old_install / 'vendor' / 'PIL'
            old_vendor.mkdir(parents=True)
            for name in shared.PIL_REQUIRED_FILES:
                (old_install / 'vendor' / name).touch()
            (root / 'tools' / 'current.json').write_text(json.dumps({
                'format': shared.TOOLS_INSTALL_FORMAT,
                'sha256': old_digest,
                'release': 'v-old',
            }), encoding='utf-8')

            archive = io.BytesIO()
            with zipfile.ZipFile(archive, 'w') as package:
                for name in ('eff_resource_pack.exe', 'eff_author_assets.dll', 'eff_source_index.exe',
                             'eff_author_core.dll', 'package-protocol.json', 'texconv.exe', 'THIRD_PARTY_NOTICES'):
                    package.writestr('EFF_Native_Tools/nativepack/' + name, name.encode('ascii'))
                package.writestr('EFF_Native_Tools/vendor/PIL/__init__.py', b'')
                for name in shared.PIL_REQUIRED_FILES[1:]:
                    package.writestr('EFF_Native_Tools/vendor/' + name, b'pyd')
            payload = archive.getvalue()
            new_digest = hashlib.sha256(payload).hexdigest()
            manifest = {
                'url': 'https://github.com/ssice-a/EFF/releases/download/v-new/EFF_Shared_Native_Tools_v-new.zip',
                'bytes': len(payload),
                'sha256': new_digest,
                'release': 'v-new',
            }

            class Response:
                def __init__(self, data):
                    self.stream = io.BytesIO(data)

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    self.stream.close()

                def read(self, size=-1):
                    return self.stream.read(size)

            with patch.dict(os.environ, {'LOCALAPPDATA': str(root / 'local')}, clear=True):
                shared.set_resource_root(root)
                with patch.object(shared, '_latest_tools_manifest', return_value=manifest), \
                        patch.object(shared, 'urlopen', return_value=Response(payload)):
                    installed = shared.ensure_native_tools()

            self.assertEqual(installed, (root / 'tools' / new_digest).resolve())
            receipt = json.loads((root / 'tools' / 'current.json').read_text(encoding='utf-8'))
            self.assertEqual(receipt['sha256'], new_digest)
            self.assertTrue((installed / 'nativepack' / 'THIRD_PARTY_NOTICES').is_file())
            self.assertTrue(old_install.is_dir())

    def test_prepare_keeps_compatible_cache_when_release_lacks_blender_52_extension(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            digest = 'c' * 64
            install = root / 'tools' / digest
            native = install / 'nativepack'
            native.mkdir(parents=True)
            for name in ('eff_resource_pack.exe', 'eff_author_assets.dll', 'eff_source_index.exe',
                         'eff_author_core.dll', 'package-protocol.json', 'texconv.exe', 'THIRD_PARTY_NOTICES'):
                (native / name).touch()
            pil = install / 'vendor' / 'PIL'
            pil.mkdir(parents=True)
            for name in shared.PIL_REQUIRED_FILES:
                (install / 'vendor' / name).touch()
            (root / 'tools' / 'current.json').write_text(json.dumps({
                'format': shared.TOOLS_INSTALL_FORMAT,
                'sha256': digest,
                'release': 'local-dual-abi',
            }), encoding='utf-8')

            archive = io.BytesIO()
            with zipfile.ZipFile(archive, 'w') as package:
                for name in ('eff_resource_pack.exe', 'eff_author_assets.dll', 'eff_source_index.exe',
                             'eff_author_core.dll', 'package-protocol.json', 'texconv.exe', 'THIRD_PARTY_NOTICES'):
                    package.writestr('EFF_Native_Tools/nativepack/' + name, name.encode('ascii'))
                package.writestr('EFF_Native_Tools/vendor/PIL/__init__.py', b'')
                package.writestr('EFF_Native_Tools/vendor/PIL/_imaging.cp311-win_amd64.pyd', b'cp311')
            payload = archive.getvalue()
            manifest = {
                'url': 'https://github.com/ssice-a/EFF/releases/download/v-old/EFF_Shared_Native_Tools_v-old.zip',
                'bytes': len(payload),
                'sha256': hashlib.sha256(payload).hexdigest(),
                'release': 'v-old',
            }

            class Response:
                def __init__(self, data):
                    self.stream = io.BytesIO(data)

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    self.stream.close()

                def read(self, size=-1):
                    return self.stream.read(size)

            with patch.dict(os.environ, {'LOCALAPPDATA': str(root / 'local')}, clear=True):
                shared.set_resource_root(root)
                with patch.object(shared, '_latest_tools_manifest', return_value=manifest), \
                        patch.object(shared, 'urlopen', return_value=Response(payload)):
                    self.assertEqual(shared.ensure_native_tools(), install.resolve())

            receipt = json.loads((root / 'tools' / 'current.json').read_text(encoding='utf-8'))
            self.assertEqual(receipt['sha256'], digest)
            self.assertTrue((install / 'vendor' / 'PIL' / '_imaging.cp313-win_amd64.pyd').is_file())


if __name__ == '__main__':
    unittest.main()

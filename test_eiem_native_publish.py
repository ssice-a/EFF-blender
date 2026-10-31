"""Transactional publication tests for Format 3 author directories."""
import configparser
import importlib.util
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    'eiem_native_export', Path(__file__).with_name('eiem_native_export.py'))
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


def read_format3(root):
    root = Path(root)
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    with (root / 'mod.ini').open(encoding='utf-8') as stream:
        parser.read_file(stream)
    if parser.get('Mod', 'format', fallback='') != '3':
        raise ValueError('Format 3 manifest is absent')
    files = {'mod.ini'}
    for section in parser.sections():
        if section == 'Mod':
            continue
        for key in ('path', 'dds'):
            relative = parser[section].get(key)
            if not relative:
                continue
            path = (root / relative).resolve()
            if root.resolve() not in path.parents or not path.is_file():
                raise ValueError('Format 3 resource is missing or outside the Mod')
            files.add(Path(relative).as_posix())
    return {'_files': files, 'format': 3}


def write_package(root, mesh=b'old mesh', dds=b'old dds'):
    root = Path(root)
    (root / 'meshes').mkdir(parents=True, exist_ok=True)
    (root / 'textures').mkdir(parents=True, exist_ok=True)
    (root / 'meshes/body.effres').write_bytes(mesh)
    (root / 'textures/body.dds').write_bytes(dds)
    (root / 'mod.ini').write_text(
        '[Mod]\nformat=3\nid=PublishSmoke\n\n'
        '[MeshBody]\npath=meshes/body.effres\n\n'
        '[TextureBody]\npath=textures/body.effres\n'
        'dds=textures/body.dds\n', encoding='utf-8')
    (root / 'textures/body.effres').write_bytes(b'texture scaffold')


class Format3PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.destination = root / 'installed'
        self.staging = root / 'staging'
        write_package(self.destination)
        write_package(self.staging)

    def tearDown(self):
        self.temp.cleanup()

    def test_new_directory_publishes_resources_before_manifest(self):
        fresh = Path(self.temp.name) / 'new'
        replace = exporter._replace_published_file

        def verify_manifest_last(source, target):
            if Path(target) == fresh / 'mod.ini':
                self.assertTrue((fresh / 'meshes/body.effres').is_file())
                self.assertTrue((fresh / 'textures/body.dds').is_file())
            return replace(source, target)

        with patch.object(exporter, '_replace_published_file', side_effect=verify_manifest_last):
            result = exporter.publish(self.staging, fresh, read_format3)
        self.assertEqual(result['format'], 3)
        self.assertEqual(result['_files'], read_format3(self.staging)['_files'])

    def test_replaces_declared_resources_and_removes_retired_files(self):
        (self.staging / 'textures/old.dds').write_bytes(b'obsolete')
        text = (self.staging / 'mod.ini').read_text('utf-8')
        (self.staging / 'mod.ini').write_text(
            text.replace('textures/body.dds', 'textures/new.dds'), encoding='utf-8')
        (self.staging / 'textures/new.dds').write_bytes(b'new dds')
        (self.staging / 'textures/body.dds').unlink()
        exporter.publish(self.staging, self.destination, read_format3)
        self.assertEqual((self.destination / 'textures/new.dds').read_bytes(), b'new dds')
        self.assertFalse((self.destination / 'textures/body.dds').exists())

    def test_failed_manifest_commit_restores_resources_and_removals(self):
        before = {path.relative_to(self.destination): path.read_bytes()
                  for path in self.destination.rglob('*') if path.is_file()}
        (self.staging / 'meshes/body.effres').write_bytes(b'new mesh')
        text = (self.staging / 'mod.ini').read_text('utf-8')
        (self.staging / 'mod.ini').write_text(
            text.replace('textures/body.dds', 'textures/new.dds'), encoding='utf-8')
        (self.staging / 'textures/new.dds').write_bytes(b'new dds')
        (self.staging / 'textures/body.dds').unlink()
        replace = exporter._replace_published_file

        def fail_manifest(source, target):
            if Path(target) == self.destination / 'mod.ini':
                raise PermissionError('manifest is locked')
            return replace(source, target)

        with patch.object(exporter, '_replace_published_file', side_effect=fail_manifest):
            with self.assertRaisesRegex(PermissionError, 'manifest is locked'):
                exporter.publish(self.staging, self.destination, read_format3)
        after = {path.relative_to(self.destination): path.read_bytes()
                 for path in self.destination.rglob('*') if path.is_file()}
        self.assertEqual(after, before)

    def test_old_format_is_rejected_by_the_blender_reader(self):
        (self.staging / 'mod.ini').write_text('[Mod]\nformat=2\nid=Old\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Format 3 manifest'):
            read_format3(self.staging)

    def test_unchanged_directory_is_not_republished(self):
        replace = exporter._replace_published_file
        with patch.object(exporter, '_replace_published_file', side_effect=AssertionError('unexpected write')):
            result = exporter.publish(self.staging, self.destination, read_format3)
        self.assertEqual(result['_files'], read_format3(self.destination)['_files'])

    @unittest.skipUnless(os.name == 'nt', 'requires Windows sharing flags')
    def test_open_mod_directory_survives_resource_update(self):
        from ctypes import wintypes
        kernel = __import__('ctypes').WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateFileW(str(self.destination), 0x80000000, 7, None, 3,
                                    0x02000000, None)
        self.assertNotEqual(handle, wintypes.HANDLE(-1).value)
        try:
            (self.staging / 'meshes/body.effres').write_bytes(b'new mesh')
            exporter.publish(self.staging, self.destination, read_format3)
            self.assertEqual((self.destination / 'meshes/body.effres').read_bytes(), b'new mesh')
        finally:
            kernel.CloseHandle(handle)

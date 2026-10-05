"""Publish author resources while Windows readers retain the Mod directory."""
import ctypes
from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from nativepack.ini_package import export_directory, update_directory
from nativepack.ini_reader import read_package
fixture_spec = importlib.util.spec_from_file_location(
    'directory_fixture', Path(__file__).parents[1] / 'nativepack' / 'test_directory_package.py')
fixture_module = importlib.util.module_from_spec(fixture_spec)
fixture_spec.loader.exec_module(fixture_module)
fixture = fixture_module.fixture

spec = importlib.util.spec_from_file_location('eiem_native_export', Path(__file__).with_name('eiem_native_export.py'))
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


@contextmanager
def windows_reader(path, directory=False):
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                 wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    # Permit reads/writes of the directory, but forbid its rename/deletion.
    # The unchanged Mesh is deliberately read-only and cannot be overwritten.
    handle = kernel.CreateFileW(str(path), 0x80000000, 3 if directory else 1,
                               None, 3, 0x02000000 if directory else 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        kernel.CloseHandle(handle)


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'Mod'
        self.root.mkdir()
        self.data = fixture()
        export_directory(self.root, **self.data)
        self.stage = Path(self.temp.name) / 'stage'
        shutil.copytree(self.root, self.stage)

    def tearDown(self):
        self.temp.cleanup()

    def change_texture(self, rename=False):
        textures = [dict(t, pixels=b'\xff\x00\x00\xff',
                         pixelLabel='renamed' if rename else t['pixelLabel']) for t in self.data['textures']]
        update_directory(self.stage, textures=textures, texture_only=True)

    def hashes(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes()
                for p in self.root.rglob('*') if p.is_file()}

    @unittest.skipUnless(os.name == 'nt', 'requires Windows sharing flags')
    def test_texture_export_with_directory_and_unchanged_mesh_locked(self):
        before = self.hashes()
        original = read_package(self.root)
        mesh = self.root / next(p for p in original['_files'] if p.endswith('.mesh'))
        mesh_time = mesh.stat().st_mtime_ns
        (self.root / 'state.ini').write_bytes(b'live values')
        (self.root / 'textures' / 'body.png').write_bytes(b'editable source')
        self.change_texture()
        with windows_reader(self.root, directory=True), windows_reader(mesh):
            result = exporter.publish(self.stage, self.root, read_package)
        self.assertEqual(result['_files'], original['_files'])
        self.assertEqual(mesh.stat().st_mtime_ns, mesh_time)
        self.assertEqual((self.root / 'mod.ini').read_bytes(), before['mod.ini'])
        self.assertEqual((self.root / 'state.ini').read_bytes(), b'live values')
        self.assertEqual((self.root / 'textures' / 'body.png').read_bytes(), b'editable source')
        for relative, data in before.items():
            if not relative.startswith('textures/'):
                self.assertEqual((self.root / relative).read_bytes(), data)
        self.assertNotEqual(self.hashes()['textures/body.tex'], before['textures/body.tex'])

    def test_texture_rename_removes_obsolete_file_and_keeps_auxiliary(self):
        (self.root / 'textures' / 'body.png').write_bytes(b'editable source')
        self.change_texture(rename=True)
        result = exporter.publish(self.stage, self.root, read_package)
        self.assertIn('textures/renamed.tex', result['_files'])
        self.assertFalse((self.root / 'textures' / 'body.tex').exists())
        self.assertTrue((self.root / 'textures' / 'body.png').exists())

    def test_failed_ini_publication_restores_resources_and_deletions(self):
        before = self.hashes()
        self.change_texture(rename=True)
        replace = os.replace
        blocked = False

        def fail_ini_once(source, target):
            nonlocal blocked
            if Path(target) == self.root / 'mod.ini' and not blocked:
                blocked = True
                raise PermissionError('fixture INI publication denied')
            return replace(source, target)

        with patch.object(os, 'replace', side_effect=fail_ini_once):
            with self.assertRaisesRegex(PermissionError, 'INI publication denied'):
                exporter.publish(self.stage, self.root, read_package)
        self.assertEqual(self.hashes(), before)
        read_package(self.root)

    def test_first_export_is_complete(self):
        fresh = Path(self.temp.name) / 'new'
        result = exporter.publish(self.stage, fresh, read_package)
        self.assertEqual(result['_files'], read_package(self.stage)['_files'])


if __name__ == '__main__':
    unittest.main()

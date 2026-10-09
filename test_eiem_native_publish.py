"""Publish author resources while Windows readers retain the Mod directory."""
import ctypes
from contextlib import contextmanager
import importlib.util
import os
import stat
import struct
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
    def test_variant_texture_partial_update_detaches_the_planned_collision_path(self):
        data = fixture_module.DirectoryTests().variant_fixture(same_label=True)
        root = Path(self.temp.name) / 'variants'
        root.mkdir()
        original = export_directory(root, **data)
        before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
        stage = Path(self.temp.name) / 'variant-stage'
        exporter._clone_staging_tree(root, stage)
        ini_module = exporter.pack_modules()[0]['ini_package']
        ini = ini_module.ini_document(stage)
        textures = [dict(data['textures'][0], pixels=b'\x00\xff\x00\xff')]
        materials = [data['materials'][1]]
        planned = ini_module.texture_output_paths(stage, ini, textures, materials)
        exporter._detach_staging_files(exporter._partial_output_paths(stage, 'TEXTURES',
            ini, materials, textures, [], ini_module.identifier, planned))
        update_directory(stage, materials=materials, textures=textures, texture_only=True)
        self.assertEqual(before, {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()})
        exporter.publish(stage, root, read_package)
        updated = {t['id']: t for t in read_package(root)['textures']}
        second = next(t for t in original['textures'] if t['id'] == 'TextureVariantB')
        self.assertEqual((root / second['file']).read_bytes(), before[Path(second['file'])])
        self.assertNotEqual(updated['TextureVariantA']['sha256'],
                            next(t for t in original['textures'] if t['id'] == 'TextureVariantA')['sha256'])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'Mod'
        self.root.mkdir()
        self.data = fixture()
        export_directory(self.root, **self.data)
        self.stage = Path(self.temp.name) / 'stage'
        exporter._clone_staging_tree(self.root, self.stage)

    def tearDown(self):
        self.temp.cleanup()

    def change_texture(self, rename=False):
        textures = [dict(t, pixels=b'\xff\x00\x00\xff',
                         pixelLabel='renamed' if rename else t['pixelLabel']) for t in self.data['textures']]
        exporter._detach_staging_files(exporter._partial_output_paths(
            self.stage, 'TEXTURES', exporter.pack_modules()[0]['ini_package'].ini_document(self.stage),
            [], textures, [], exporter.pack_modules()[0]['ini_package'].identifier))
        update_directory(self.stage, textures=textures, texture_only=True)

    def hashes(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes()
                for p in self.root.rglob('*') if p.is_file()}

    def test_static_source_descriptor_published_with_author_payload(self):
        (self.root/'source-inputs.bin').write_bytes(b'previous sources')
        (self.stage/'source-inputs.bin').write_bytes(b'new sources')
        result=exporter.publish(self.stage,self.root,read_package)
        self.assertIn('source-inputs.bin',result['_files'])
        self.assertEqual((self.root/'source-inputs.bin').read_bytes(),b'new sources')

    def test_compiled_rules_are_published_on_first_and_repeated_export(self):
        (self.stage/'compiled.bin').write_bytes(b'new compiled rules')
        fresh = Path(self.temp.name) / 'first-compiled'
        result = exporter.publish(self.stage, fresh, read_package)
        self.assertIn('compiled.bin', result['_files'])
        self.assertEqual((fresh/'compiled.bin').read_bytes(), b'new compiled rules')
        (self.root/'compiled.bin').write_bytes(b'previous compiled rules')
        self.change_texture()
        result = exporter.publish(self.stage, self.root, read_package)
        self.assertIn('compiled.bin', result['_files'])
        self.assertEqual((self.root/'compiled.bin').read_bytes(), b'new compiled rules')
        self.assertEqual((self.root/'textures/body.tex').read_bytes(),
                         (self.stage/'textures/body.tex').read_bytes())

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
        with patch.object(shutil, 'copy2', wraps=shutil.copy2) as copies:
            result = exporter.publish(self.stage, self.root, read_package)
        self.assertNotIn(self.root / 'textures' / 'body.tex',
                         [Path(call.args[0]) for call in copies.call_args_list])
        self.assertIn('textures/renamed.tex', result['_files'])
        self.assertFalse((self.root / 'textures' / 'body.tex').exists())
        self.assertTrue((self.root / 'textures' / 'body.png').exists())

    def test_valid_author_without_generated_comment_can_be_updated(self):
        ini = self.root / 'mod.ini'
        ini.write_text('\n'.join(ini.read_text('utf-8').splitlines()[1:]) + '\n', 'utf-8')
        self.change_texture()
        result = exporter.publish(self.stage, self.root, read_package)
        self.assertEqual(result['_files'], read_package(self.stage)['_files'])
        self.assertEqual((self.root / 'textures' / 'body.tex').read_bytes(),
                         (self.stage / 'textures' / 'body.tex').read_bytes())

    def test_invalid_author_is_not_overwritten_even_with_generated_comment(self):
        self.change_texture()
        ini = self.root / 'mod.ini'
        ini.write_text(ini.read_text('utf-8').replace('[Mod]', '[BrokenMod]'), 'utf-8')
        before = self.hashes()
        with self.assertRaisesRegex(ValueError, 'Mod/Constants'):
            exporter.publish(self.stage, self.root, read_package)
        self.assertEqual(self.hashes(), before)

    def test_failed_ini_publication_restores_resources_and_deletions(self):
        (self.root/'compiled.bin').write_bytes(b'previous compiled rules')
        (self.stage/'compiled.bin').write_bytes(b'new compiled rules')
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

    def test_hardlinked_material_and_mesh_updates_keep_author_private(self):
        before = self.hashes()
        material = dict(self.data['materials'][0], fieldData={'m_CustomRenderQueue': struct.pack('<i', 2001)})
        part = dict(self.data['parts'][0], fieldData={key: b'updated-field' for key in self.data['parts'][0]['fieldData']})
        for scope, kwargs in (('MATERIALS', dict(materials=[material], textures=self.data['textures'])),
                              ('MESH', dict(parts=[part]))):
            ini_module = exporter.pack_modules()[0]['ini_package']
            exporter._detach_staging_files(exporter._partial_output_paths(
                self.stage, scope, ini_module.ini_document(self.stage),
                kwargs.get('materials', []), kwargs.get('textures', []), kwargs.get('parts', []), ini_module.identifier))
            update_directory(self.stage, **kwargs)
            self.assertEqual(self.hashes(), before)
            exporter.publish(self.stage, self.root, read_package)
            after = self.hashes()
            suffix = '.mat' if scope == 'MATERIALS' else '.mesh'
            changed = {key for key in before if before[key] != after[key]}
            self.assertEqual(len(changed), 1)
            self.assertTrue(next(iter(changed)).endswith(suffix))
            before = after

    @unittest.skipUnless(os.name == 'nt', 'requires Windows attributes')
    def test_readonly_source_is_copied_without_changing_author_attributes(self):
        mesh = next(self.root.glob('meshes/*.mesh'))
        mesh.chmod(mesh.stat().st_mode & ~stat.S_IWRITE)
        fresh = Path(self.temp.name) / 'readonly-stage'
        try:
            with windows_reader(mesh):
                exporter._clone_staging_tree(self.root, fresh)
                copied = fresh / mesh.relative_to(self.root)
                self.assertFalse(os.path.samefile(mesh, copied))
                self.assertTrue(copied.stat().st_mode & stat.S_IWRITE)
                shutil.rmtree(fresh)
            self.assertFalse(mesh.stat().st_mode & stat.S_IWRITE)
        finally:
            mesh.chmod(mesh.stat().st_mode | stat.S_IWRITE)

    @unittest.skipUnless(os.name == 'nt', 'requires Windows sharing flags')
    def test_locked_source_clone_can_be_cleaned_while_reader_is_open(self):
        mesh = next(self.root.glob('meshes/*.mesh'))
        fresh = Path(self.temp.name) / 'locked-stage'
        try:
            with windows_reader(mesh):
                exporter._clone_staging_tree(self.root, fresh)
                shutil.rmtree(fresh)
        finally:
            if fresh.exists():
                shutil.rmtree(fresh)

    def test_linked_corruption_with_preserved_stamp_is_rejected(self):
        from nativepack.resource_file import read_resource, MESH
        mesh = next(self.root.glob('meshes/*.mesh'))
        staged = self.stage / mesh.relative_to(self.root)
        cache = {}
        read_resource(staged, MESH, cache)
        stamp = mesh.stat()
        damaged = bytearray(mesh.read_bytes())
        damaged[-1] ^= 1
        mesh.write_bytes(damaged)
        os.utime(mesh, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        with self.assertRaisesRegex(ValueError, 'checksum'):
            read_resource(staged, MESH, cache)


if __name__ == '__main__':
    unittest.main()

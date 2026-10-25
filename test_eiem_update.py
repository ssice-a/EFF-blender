import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import eiem_release as updates
import eiem_update_worker as worker


REVISION = 'a' * 40


def package(root):
    files = ('__init__.py', 'eiem_blender_addon.py', 'eiem_release.py', 'eiem_update_worker.py',
             'nativepack/eff_author_core.dll', 'nativepack/eff_resource_pack.exe',
             'nativepack/eff_source_index.exe', 'nativepack/package-protocol.json',
             'nativepack/compiled_store.py', 'vendor/PIL/__init__.py')
    files += ('physics_presets/chen_soft_ribbon.json',)
    for name in files:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(('NEW ' + name).encode())
    manifest = dict(format='EFF_BLENDER_BUILD/1', revision=REVISION,
                    files={name: updates.digest(root / name) for name in files})
    (root / updates.BUILD_FILE).write_text(json.dumps(manifest))
    return root


def archive(root):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as output:
        for path in root.rglob('*'):
            if path.is_file():
                output.writestr('EFF_Blender/' + path.relative_to(root).as_posix(), path.read_bytes())
    return stream.getvalue()


def feed(data):
    return dict(format='EFF_BLENDER_UPDATE/1', revision=REVISION, version=[0, 46, 0],
                bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                url=updates.DOWNLOAD_PREFIX + 'continuous/EFF_Blender_main-' + REVISION + '.zip')


def installation(root):
    root.mkdir()
    for name in ('__init__.py', 'eiem_blender_addon.py', 'eiem_release.py', 'eiem_update_worker.py'):
        (root / name).write_text('OLD ' + name)
    (root / 'my-notes.txt').write_text('user data')
    return root


class Updates(unittest.TestCase):
    def test_same_version_uses_commit_not_version_number(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            body = feed(b'zip')
            def opener(request, timeout):
                payload = {'assets': [{'name': updates.FEED_FILE, 'browser_download_url': updates.DOWNLOAD_PREFIX + 'continuous/' + updates.FEED_FILE}]} if request.full_url == updates.CONTINUOUS_API else body
                return io.BytesIO(json.dumps(payload).encode())
            self.assertEqual(updates.check_repository(root, (0, 46, 0), opener=opener)['status'], 'available')
            self.assertEqual(updates.check_repository(root, (0, 46, 0), REVISION, opener=opener)['status'], 'ignored')
            (root / updates.BUILD_FILE).write_text(json.dumps(dict(format='EFF_BLENDER_BUILD/1', revision=REVISION)))
            self.assertEqual(updates.check_repository(root, (0, 46, 0), opener=opener)['status'], 'latest')
            self.assertEqual(updates.check_repository(root, (0, 47, 0), opener=opener)['status'], 'latest')

    def test_catalog_rejects_other_repositories_and_invalid_identity(self):
        for key, value in (('url', 'https://example.com/code.zip'), ('revision', '../x'), ('sha256', 'bad'), ('bytes', -1)):
            item = feed(b'zip'); item[key] = value
            with self.assertRaises(ValueError):
                updates.validate_feed(item)

    def test_prepare_preserves_live_install_then_installs_and_keeps_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = installation(root / 'InstalledName')
            data = archive(package(root / 'source'))
            job = updates.prepare_update(target, feed(data), opener=lambda *_a, **_k: io.BytesIO(data))
            self.assertEqual((target / '__init__.py').read_text(), 'OLD __init__.py')
            result = worker.install(job)
            self.assertEqual(result['status'], 'installed')
            self.assertEqual((target / '__init__.py').read_text(), 'NEW __init__.py')
            self.assertEqual((target / 'my-notes.txt').read_text(), 'user data')
            self.assertEqual((Path(result['backup']) / '__init__.py').read_text(), 'OLD __init__.py')
            self.assertEqual(updates.read_build(target)['revision'], REVISION)

    def test_bad_download_and_bad_candidate_preserve_live_install(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); target = installation(root / 'Installed')
            data = archive(package(root / 'source'))
            for payload in (data[:-1], data + b'x'):
                with self.assertRaises(ValueError):
                    updates.prepare_update(target, feed(data), opener=lambda *_a, **_k: io.BytesIO(payload))
            self.assertEqual((target / '__init__.py').read_text(), 'OLD __init__.py')
            self.assertFalse(list(root.glob('.eff-update-*')))
            job = updates.prepare_update(target, feed(data), opener=lambda *_a, **_k: io.BytesIO(data))
            candidate = Path(json.loads(job.read_text())['candidate'])
            (candidate / 'eiem_blender_addon.py').write_text('corrupt')
            with self.assertRaises(ValueError):
                worker.install(job)
            self.assertEqual((target / '__init__.py').read_text(), 'OLD __init__.py')

    def test_failed_directory_commit_rolls_back(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); target = installation(root / 'Installed')
            data = archive(package(root / 'source'))
            job = updates.prepare_update(target, feed(data), opener=lambda *_a, **_k: io.BytesIO(data))
            candidate = Path(json.loads(job.read_text())['candidate'])
            original = Path.rename
            def rename(path, destination):
                if path == candidate:
                    raise PermissionError('locked fixture')
                return original(path, destination)
            with patch.object(Path, 'rename', rename), self.assertRaises(PermissionError):
                worker.install(job)
            self.assertEqual((target / '__init__.py').read_text(), 'OLD __init__.py')

    def test_git_development_install_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            root = installation(Path(folder) / 'Installed')
            (root / '.git').write_text('gitdir: source')
            with self.assertRaisesRegex(ValueError, 'Git'):
                updates.check_install_target(root)

    def test_unsafe_zip_paths_and_symlinks_reject(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ('../outside', 'EFF_Blender/../../outside', 'C:/outside', 'EFF_Blender/a:b',
                         'EFF_Blender\\outside', 'EFF_Blender/.. /outside'):
                stream = io.BytesIO()
                with zipfile.ZipFile(stream, 'w') as output:
                    item = zipfile.ZipInfo('placeholder')
                    item.filename = name  # Keep hostile backslashes on Windows.
                    output.writestr(item, 'bad')
                stream.seek(0)
                with self.assertRaises(ValueError):
                    updates.extract_package(stream, root)

    def test_worker_waits_for_blender_without_closing_it(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); target = installation(root / 'Installed')
            data = archive(package(root / 'source'))
            job = updates.prepare_update(target, feed(data), opener=lambda *_a, **_k: io.BytesIO(data))
            with patch.object(worker, 'blender_processes', side_effect=[[100], []]), patch.object(worker.time, 'sleep') as sleep:
                worker.run(job)
                sleep.assert_called_once_with(1)
            self.assertEqual(json.loads(job.read_text())['status'], 'installed')


if __name__ == '__main__':
    unittest.main()

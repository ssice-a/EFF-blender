import copy
import hashlib
import io
import json
from pathlib import Path
import ssl
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
import zipfile

import eiem_release as updates
import eiem_update_worker as worker


REVISION = 'a' * 40
INSTALLED_REVISION = 'b' * 40


def package(root):
    files = ('__init__.py', 'eiem_blender_addon.py', 'eiem_release.py', 'eiem_update_worker.py',
             'eiem_shared_resources.py', 'nativepack/author_source.py',
             'nativepack/author_format3.py',
             'nativepack/effect_native.py', 'nativepack/mesh.py',
             'eiem_texture_compression.py')
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
            (root / updates.BUILD_FILE).write_text(json.dumps(dict(
                format='EFF_BLENDER_BUILD/1', revision=INSTALLED_REVISION)))
            def opener(request, timeout):
                if '/compare/' in request.full_url:
                    self.assertTrue(request.full_url.endswith(INSTALLED_REVISION + '...' + REVISION))
                    payload = {'status': 'ahead', 'base_commit': {'sha': INSTALLED_REVISION}}
                elif request.full_url == updates.CONTINUOUS_API:
                    payload = {'assets': [{'name': updates.FEED_FILE, 'browser_download_url': updates.DOWNLOAD_PREFIX + 'continuous/' + updates.FEED_FILE}]}
                else:
                    payload = body
                return io.BytesIO(json.dumps(payload).encode())
            self.assertEqual(updates.check_repository(root, (0, 46, 0), opener=opener)['status'], 'available')
            self.assertEqual(updates.check_repository(root, (0, 46, 0), REVISION, opener=opener)['status'], 'ignored')
            self.assertEqual(updates.check_repository(root, (0, 46, 0), REVISION, force=True, opener=opener)['status'], 'available')
            (root / updates.BUILD_FILE).write_text(json.dumps(dict(format='EFF_BLENDER_BUILD/1', revision=REVISION)))
            self.assertEqual(updates.check_repository(root, (0, 46, 0), opener=opener)['status'], 'latest')
            self.assertEqual(updates.check_repository(root, (0, 47, 0), opener=opener)['status'], 'latest')

    def test_old_continuous_package_cannot_replace_ahead_install_even_when_forced(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / updates.BUILD_FILE).write_text(json.dumps(dict(
                format='EFF_BLENDER_BUILD/1', revision=INSTALLED_REVISION)))
            responses = [
                {'assets': [{'name': updates.FEED_FILE, 'browser_download_url': updates.DOWNLOAD_PREFIX + 'continuous/' + updates.FEED_FILE}]},
                feed(b'zip'),
                {'status': 'behind', 'base_commit': {'sha': INSTALLED_REVISION}},
            ]
            for force in (False, True):
                with self.subTest(force=force):
                    values = iter(responses)
                    result = updates.check_repository(root, (0, 46, 0), force=force,
                        opener=lambda *_a, **_k: io.BytesIO(json.dumps(next(values)).encode()))
                    self.assertEqual(result['status'], 'latest')
                    self.assertIn('领先', result['message'])

    def test_unproven_revision_order_is_unknown_and_never_available(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for comparison in ({'status': 'diverged', 'base_commit': {'sha': INSTALLED_REVISION}},
                               {'status': 'ahead', 'base_commit': {'sha': 'c' * 40}},
                               {'status': 'identical', 'base_commit': {'sha': INSTALLED_REVISION}},
                               {'status': 'unexpected'}, {'status': 'ahead', 'base_commit': None}, None):
                with self.subTest(comparison=comparison):
                    (root / updates.BUILD_FILE).write_text(json.dumps(dict(
                        format='EFF_BLENDER_BUILD/1', revision=INSTALLED_REVISION)))
                    values = iter([
                        {'assets': [{'name': updates.FEED_FILE, 'browser_download_url': updates.DOWNLOAD_PREFIX + 'continuous/' + updates.FEED_FILE}]},
                        feed(b'zip'), comparison])
                    result = updates.check_repository(root, (0, 46, 0), force=True,
                        opener=lambda *_a, **_k: io.BytesIO(json.dumps(next(values)).encode()))
                    self.assertEqual(result['status'], 'unknown')
                    self.assertTrue(result['message'])

    def test_missing_build_identity_only_allows_a_higher_version(self):
        with tempfile.TemporaryDirectory() as folder:
            for current, expected in (((0, 46, 0), 'unknown'), ((0, 45, 0), 'available')):
                values = iter([
                    {'assets': [{'name': updates.FEED_FILE, 'browser_download_url': updates.DOWNLOAD_PREFIX + 'continuous/' + updates.FEED_FILE}]},
                    feed(b'zip')])
                result = updates.check_repository(Path(folder), current,
                    opener=lambda *_a, **_k: io.BytesIO(json.dumps(next(values)).encode()))
                self.assertEqual(result['status'], expected)

    def test_compare_api_failure_is_not_reported_as_latest_or_retried_for_403(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / updates.BUILD_FILE).write_text(json.dumps(dict(
                format='EFF_BLENDER_BUILD/1', revision=INSTALLED_REVISION)))
            calls = []
            def opener(request, timeout):
                calls.append(request.full_url)
                if '/compare/' in request.full_url:
                    raise HTTPError(request.full_url, 403, 'Forbidden', {'X-RateLimit-Remaining': '0'}, None)
                payload = ({'assets': [{'name': updates.FEED_FILE, 'browser_download_url': updates.DOWNLOAD_PREFIX + 'continuous/' + updates.FEED_FILE}]}
                           if request.full_url == updates.CONTINUOUS_API else feed(b'zip'))
                return io.BytesIO(json.dumps(payload).encode())
            sleeps = []
            with self.assertRaisesRegex(updates.UpdateNetworkError, '版本先后.*额度'):
                updates.check_repository(root, (0, 46, 0), opener=opener, sleep=sleeps.append)
            self.assertEqual(len(calls), 3)
            self.assertEqual(sleeps, [])

    def test_transient_check_network_failure_retries_then_succeeds(self):
        calls = []
        def opener(request, timeout):
            calls.append(request.full_url)
            if len(calls) == 1:
                raise URLError('temporary DNS failure')
            return io.BytesIO(b'{"ok": true}')
        sleeps = []
        self.assertEqual(updates._request_json('https://example.invalid', opener,
            stage='更新目录', sleep=sleeps.append), {'ok': True})
        self.assertEqual(len(calls), 2)
        self.assertEqual(sleeps, [1])

    def test_network_retries_are_bounded_and_classified(self):
        cases = (
            (TimeoutError('timed out'), 3, '超时'),
            (HTTPError('https://example.invalid', 503, 'Unavailable', {}, None), 3, '503'),
            (HTTPError('https://example.invalid', 429, 'Too Many Requests', {}, None), 3, '429'),
            (HTTPError('https://example.invalid', 403, 'Forbidden', {}, None), 1, '403'),
            (HTTPError('https://example.invalid', 404, 'Not Found', {}, None), 1, '404'),
            (URLError(ssl.SSLCertVerificationError('certificate verify failed')), 1, '证书'),
        )
        for error, count, message in cases:
            with self.subTest(error=error):
                calls, sleeps = [], []
                def opener(*_a, **_k):
                    calls.append(True)
                    raise error
                with self.assertRaisesRegex(updates.UpdateNetworkError, '更新目录.*' + message):
                    updates._request_json('https://example.invalid', opener,
                        stage='更新目录', sleep=sleeps.append)
                self.assertEqual(len(calls), count)
                self.assertEqual(sleeps, [1, 2] if count == 3 else [])

    def test_long_retry_after_does_not_keep_retrying_a_throttled_endpoint(self):
        calls, sleeps = [], []
        def opener(*_a, **_k):
            calls.append(True)
            raise HTTPError('https://example.invalid', 429, 'Too Many Requests',
                            {'Retry-After': '60'}, None)
        with self.assertRaises(updates.UpdateNetworkError):
            updates._request_json('https://example.invalid', opener, sleep=sleeps.append)
        self.assertEqual(len(calls), 1)
        self.assertEqual(sleeps, [])

    def test_interrupted_download_retries_from_empty_file_and_preserves_live_install(self):
        class Interrupted(io.BytesIO):
            def read(self, _size):
                if self.tell():
                    raise ConnectionResetError('connection reset fixture')
                return super().read(20)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = installation(root / 'Installed')
            data = archive(package(root / 'source'))
            streams = iter((Interrupted(data), io.BytesIO(data)))
            sleeps = []
            job = updates.prepare_update(target, feed(data),
                opener=lambda *_a, **_k: next(streams), sleep=sleeps.append)
            self.assertEqual((target / '__init__.py').read_text(), 'OLD __init__.py')
            self.assertEqual((job.parent / 'download.zip').read_bytes(), data)
            self.assertEqual(sleeps, [1])

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
                with self.assertRaises((ValueError, updates.UpdateNetworkError)):
                    updates.prepare_update(target, feed(data), opener=lambda *_a, **_k: io.BytesIO(payload), sleep=lambda _delay: None)
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

    def test_waiting_task_persists_processes_and_can_be_restored(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); target = installation(root / 'Installed')
            data = archive(package(root / 'source'))
            job = updates.prepare_update(target, feed(data), opener=lambda *_a, **_k: io.BytesIO(data))
            snapshots = []
            def observe(_seconds):
                persisted = json.loads(job.read_text('utf-8'))
                snapshots.append(persisted)
                restored = updates.read_install_status(target)
                self.assertEqual(restored['status'], 'waitingForBlenderExit')
                self.assertEqual(restored['waitingPids'], persisted['waitingPids'])
                self.assertEqual(Path(restored['log']), job.parent / 'install.log')
            with patch.object(worker, 'blender_processes', side_effect=[[100, 200], [200], []]), \
                    patch.object(worker.time, 'sleep', side_effect=observe):
                worker.run(job)
            self.assertEqual([item['waitingPids'] for item in snapshots], [[100, 200], [200]])
            self.assertTrue(all(item['updatedAt'] for item in snapshots))
            self.assertEqual(updates.read_install_status(target)['status'], 'installed')
            self.assertEqual(json.loads(job.read_text('utf-8'))['waitingPids'], [])

    def test_failed_worker_is_visible_after_restart_with_error_and_log(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); target = installation(root / 'Installed')
            data = archive(package(root / 'source'))
            job = updates.prepare_update(target, feed(data), opener=lambda *_a, **_k: io.BytesIO(data))
            with patch.object(worker, 'blender_processes', return_value=[]), \
                    patch.object(worker, 'install', side_effect=PermissionError('locked fixture')):
                with self.assertRaises(PermissionError): worker.run(job)
            restored = updates.read_install_status(target)
            self.assertEqual(restored['status'], 'failed')
            self.assertIn('locked fixture', restored['message'])
            self.assertEqual(Path(restored['log']), job.parent / 'install.log')
            self.assertTrue(restored['updatedAt'])
            self.assertEqual((target / '__init__.py').read_text(), 'OLD __init__.py')

    def test_recovered_success_checks_current_files_and_revision(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); target = installation(root / 'Installed')
            data = archive(package(root / 'source'))
            job = updates.prepare_update(target, feed(data), opener=lambda *_a, **_k: io.BytesIO(data))
            worker.install(job)
            self.assertEqual(updates.read_install_status(target)['status'], 'installed')
            original = (target / '__init__.py').read_bytes()
            (target / '__init__.py').write_text('changed after installation')
            self.assertEqual(updates.read_install_status(target)['status'], 'unknown')
            (target / '__init__.py').write_bytes(original)
            build = updates.read_build(target); build['revision'] = 'c' * 40
            (target / updates.BUILD_FILE).write_text(json.dumps(build))
            self.assertEqual(updates.read_install_status(target)['status'], 'superseded')
            (target / updates.BUILD_FILE).write_text('[]')
            self.assertEqual(updates.read_install_status(target)['status'], 'unknown')

    def test_restoration_selects_latest_matching_target_not_other_installations(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); target = installation(root / 'Installed')
            other = installation(root / 'Other')
            data = archive(package(root / 'source'))
            first = updates.prepare_update(target, feed(data), opener=lambda *_a, **_k: io.BytesIO(data))
            latest = updates.prepare_update(target, feed(data), opener=lambda *_a, **_k: io.BytesIO(data))
            unrelated = updates.prepare_update(other, feed(data), opener=lambda *_a, **_k: io.BytesIO(data))
            item = json.loads(latest.read_text()); item.update(status='failed', error='latest failure')
            worker.save_job(latest, item)
            import os
            for timestamp, path in enumerate((first, latest, unrelated), 100): os.utime(path, (timestamp, timestamp))
            restored = updates.read_install_status(target)
            self.assertEqual(restored['status'], 'failed')
            self.assertIn('latest failure', restored['message'])
            self.assertEqual(Path(restored['job']), latest)
            self.assertEqual(updates.read_install_status(other)['status'], 'prepared')

    def test_missing_legacy_fields_and_broken_explicit_job_do_not_break_startup(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); target = installation(root / 'Installed')
            self.assertEqual(updates.read_install_status(target), {})
            data = archive(package(root / 'source'))
            job = updates.prepare_update(target, feed(data), opener=lambda *_a, **_k: io.BytesIO(data))
            item = json.loads(job.read_text()); item.pop('updatedAt', None)
            item.update(status='waitingForBlenderExit'); item.pop('waitingPids', None)
            job.write_text(json.dumps(item))
            restored = updates.read_install_status(target)
            self.assertEqual(restored['status'], 'waitingForBlenderExit')
            self.assertEqual(restored['waitingPids'], [])
            item['waitingPids'] = None
            job.write_text(json.dumps(item))
            self.assertEqual(updates.read_install_status(target)['waitingPids'], [])
            job.write_text('{invalid')
            self.assertEqual(updates.read_install_status(target, job)['status'], 'unknown')
            self.assertEqual(updates.read_install_status(target), {})


if __name__ == '__main__':
    unittest.main()

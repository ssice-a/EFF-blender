"""Run update status recovery through real Blender registration and operators.

Blender --background --factory-startup --python this_file -- ADDON REPORT TEST_SOURCE
All tasks and installations are isolated in a temporary directory.
"""
import importlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import bpy

addon_path, report_path, test_source = sys.argv[sys.argv.index('--') + 1:]
sys.path.insert(0, str(Path(addon_path).resolve().parent))
sys.path.append(str(Path(test_source).resolve()))
addon = importlib.import_module(Path(addon_path).name)
gui = addon.eiem_blender_addon
fixtures = importlib.import_module('test_eiem_update')
updates = gui.release_check


class Layout:
    enabled = True

    def __init__(self): self.labels, self.operators = [], []
    def row(self, **_kwargs): return self
    def box(self): return self
    def prop(self, *_args, **_kwargs): pass
    def label(self, *, text='', **_kwargs): self.labels.append(text)
    def operator(self, name, **_kwargs):
        op = SimpleNamespace()
        self.operators.append((name, op))
        return op


checks = []
registered = False
with tempfile.TemporaryDirectory(prefix='eff-update-status-') as temporary:
    root = Path(temporary)
    target = fixtures.installation(root / 'Installed')
    data = fixtures.archive(fixtures.package(root / 'source'))
    job = fixtures.updates.prepare_update(target, fixtures.feed(data),
        opener=lambda *_a, **_k: fixtures.io.BytesIO(data))
    original_read = updates.read_install_status
    def restore(_directory, remembered=None):
        return original_read(target, remembered)
    with patch.object(updates, 'read_install_status', side_effect=restore):
        try:
            item = json.loads(job.read_text()); item.update(status='failed', error='locked fixture')
            updates.save_install_job(job, item)
            addon.register(); registered = True
            assert gui._update_install_state['status'] == 'failed'
            assert not bpy.app.timers.is_registered(gui._poll_update_install_status)
            layout = Layout(); gui.draw_update_status(layout)
            assert any('locked fixture' in label for label in layout.labels)
            assert any('install.log' in label for label in layout.labels)
            assert any(name == 'wm.path_open' for name, _op in layout.operators)
            checks.append('registration restores failed task, reason and log directory')

            addon.unregister(); registered = False
            item.update(status='waitingForBlenderExit', waitingPids=[100, 200])
            updates.save_install_job(job, item)
            addon.register(); registered = True
            assert bpy.app.timers.is_registered(gui._poll_update_install_status)
            layout = Layout(); gui.draw_update_status(layout)
            assert any('100, 200' in label for label in layout.labels)
            item.update(status='failed', error='later failure', waitingPids=[])
            updates.save_install_job(job, item)
            assert gui._poll_update_install_status() is None
            assert gui._update_install_state['status'] == 'failed'
            addon.unregister(); registered = False
            assert not bpy.app.timers.is_registered(gui._poll_update_install_status)
            checks.append('pending task resumes monitor, shows PIDs and stops on failure/unregister')

            fixtures.worker.install(job)
            addon.register(); registered = True
            assert gui._update_install_state['status'] == 'installed'
            assert not bpy.app.timers.is_registered(gui._poll_update_install_status)
            checks.append('registration verifies installed revision and files before success')

            job = fixtures.updates.prepare_update(target, fixtures.feed(data),
                opener=lambda *_a, **_k: fixtures.io.BytesIO(data))
            gui._update_install_state.clear()
            gui._update_state.update(status='available', channel='MAIN', feed=fixtures.feed(data))
            with patch.object(updates, 'check_install_target', return_value=target), \
                    patch.object(updates, 'prepare_update', return_value=job), \
                    patch.object(gui.subprocess, 'Popen', side_effect=PermissionError('helper launch fixture')):
                assert bpy.ops.eiem.install_update() == {'FINISHED'}
                gui._update_worker.join(10)
                assert not gui._update_worker.is_alive()
            persisted = json.loads(job.read_text())
            assert persisted['status'] == 'failed'
            assert 'helper launch fixture' in persisted['error']
            assert 'helper launch fixture' in (job.parent / 'install.log').read_text('utf-8')
            addon.unregister(); registered = False
            addon.register(); registered = True
            assert gui._update_install_state['status'] == 'failed'
            checks.append('real install operator persists helper launch failure across registration')
        finally:
            if registered: addon.unregister()
result = {'addon': str(Path(addon_path).resolve()), 'blender': bpy.app.version_string, 'checks': checks}
Path(report_path).parent.mkdir(parents=True, exist_ok=True)
Path(report_path).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print('EFF_UPDATE_STATUS_OK', len(checks))

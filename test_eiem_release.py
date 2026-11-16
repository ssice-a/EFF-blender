import ast
import io
import json
import tempfile
from types import SimpleNamespace
import unittest
from pathlib import Path

import eiem_release


class ReleaseCheckTests(unittest.TestCase):
    def test_package_and_update_metadata_match(self):
        root = Path(__file__).parent

        def read_info(name):
            tree = ast.parse((root / name).read_text(encoding="utf-8"))
            return next(ast.literal_eval(node.value) for node in tree.body
                        if isinstance(node, ast.Assign)
                        and any(isinstance(target, ast.Name) and target.id == "bl_info"
                                for target in node.targets))

        self.assertEqual(read_info("__init__.py"), read_info("eiem_blender_addon.py"))

    def test_format3_update_rejects_legacy_mod_io_modules(self):
        required = (
            '__init__.py', 'eiem_blender_addon.py', 'eiem_release.py',
            'eiem_update_worker.py', 'eiem_shared_resources.py',
            'nativepack/author_source.py', 'nativepack/author_format3.py',
            'nativepack/effect_native.py', 'nativepack/mesh.py',
            'eiem_texture_compression.py', 'physics_presets/chen_soft_ribbon.json',
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            files = {}
            for name in (*required, 'nativepack/ini_reader.py'):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'fixture')
                files[name] = eiem_release.digest(path)
            (root / eiem_release.BUILD_FILE).write_text(json.dumps({
                'format': 'EFF_BLENDER_BUILD/1',
                'revision': 'a' * 40,
                'files': files,
            }), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'legacy Mod'):
                eiem_release.verify_package(root)

    def test_new_latest_and_ignored(self):
        payload = {"tag_name": "v0.35.0", "html_url":
                   "https://github.com/ssice-a/EFF-blender/releases/tag/v0.35.0"}

        def opener(request, timeout):
            self.assertEqual(timeout, 5)
            return io.BytesIO(json.dumps(payload).encode())

        self.assertEqual(eiem_release.check_release((0, 34, 0), opener=opener)["status"],
                         "available")
        self.assertEqual(eiem_release.check_release((0, 34, 0), "v0.35.0",
                                                    opener=opener)["status"], "ignored")
        self.assertEqual(eiem_release.check_release((0, 34, 0), "v0.35.0",
                                                    force=True, opener=opener)["status"],
                         "available")
        self.assertEqual(eiem_release.check_release((0, 35, 0), opener=opener)["status"],
                         "latest")

    def test_invalid_release_is_rejected(self):
        with self.assertRaises(ValueError):
            eiem_release.parse_version("v0.35.0-beta")
        payload = {"tag_name": "v0.35.0", "html_url": "https://example.com/other"}
        with self.assertRaises(ValueError):
            eiem_release.check_release((0, 34, 0), opener=lambda *_args, **_kwargs:
                                       io.BytesIO(json.dumps(payload).encode()))

    def test_update_panel_explains_local_ahead_and_unproven_order_without_install_button(self):
        source = ast.parse((Path(__file__).with_name('eiem_blender_addon.py')).read_text('utf-8'))
        draw = next(node for node in source.body if isinstance(node, ast.FunctionDef)
                    and node.name == 'draw_update_status')
        class Layout:
            enabled = True
            def __init__(self):
                self.labels, self.operators = [], []
            def row(self, **_kwargs):
                return self
            def label(self, *, text, **_kwargs):
                self.labels.append(text)
            def operator(self, name, **_kwargs):
                self.operators.append(name)
                return SimpleNamespace()
        for status, message in (('latest', '本机已领先已发布的仓库更新，无需安装旧包'),
                                ('unknown', '无法确认更新包与本机的版本先后，已停止自动更新')):
            with self.subTest(status=status):
                namespace = dict(
                    _addon_preferences=lambda _context: None,
                    bpy=SimpleNamespace(context=None),
                    _update_state={'status': status, 'message': message},
                    _shared_tools_check_state={'status': 'idle'},
                    _update_install_state={},
                    draw_update_install_status=lambda _layout: None,
                    draw_shared_tools_update_status=lambda _layout: None,
                )
                exec(compile(ast.Module(body=[draw], type_ignores=[]), '<update-panel>', 'exec'), namespace)
                layout = Layout()
                namespace['draw_update_status'](layout)
                self.assertIn(message, layout.labels)
                self.assertNotIn('eiem.install_update', layout.operators)

    def test_install_panel_shows_waiting_processes_and_failure_log_separately(self):
        source = ast.parse(Path(__file__).with_name('eiem_blender_addon.py').read_text('utf-8'))
        draw = next(node for node in source.body if isinstance(node, ast.FunctionDef)
                    and node.name == 'draw_update_install_status')
        class Layout:
            def __init__(self): self.labels, self.operators = [], []
            def box(self): return self
            def label(self, *, text, **_kwargs): self.labels.append(text)
            def operator(self, name, **_kwargs):
                op = SimpleNamespace()
                self.operators.append((name, op))
                return op
        for state in (
                {'status': 'waitingForBlenderExit', 'message': '等待退出', 'waitingPids': [100, 200]},
                {'status': 'failed', 'message': '安装失败：locked fixture', 'waitingPids': []},
                {'status': 'installed', 'message': '更新已安装并校验', 'waitingPids': []}):
            with self.subTest(status=state['status']):
                state['log'] = str(Path('stage') / 'install.log')
                namespace = dict(_update_install_state=state, Path=Path)
                exec(compile(ast.Module(body=[draw], type_ignores=[]), '<install-panel>', 'exec'), namespace)
                layout = Layout(); namespace['draw_update_install_status'](layout)
                self.assertIn(state['message'], layout.labels)
                self.assertTrue(any('install.log' in text for text in layout.labels))
                self.assertEqual(layout.operators[0][0], 'wm.path_open')
                self.assertEqual(layout.operators[0][1].filepath, str(Path(state['log']).parent))
                if state['waitingPids']:
                    self.assertTrue(any('100' in text and '200' in text for text in layout.labels))


if __name__ == "__main__":
    unittest.main()

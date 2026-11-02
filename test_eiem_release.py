import ast
import io
import json
import tempfile
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


if __name__ == "__main__":
    unittest.main()

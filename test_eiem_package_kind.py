"""Package classification tests for Blender's source and runtime inputs."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

FORMAT_PATH = Path(__file__).with_name("eiem_format.py")
SPEC = importlib.util.spec_from_file_location("eiem_package_kind_format", FORMAT_PATH)
format_io = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(format_io)


class PackageKindTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_runtime_format3_mod_is_distinct(self):
        (self.root / "mod.ini").write_text("[Mod]\nformat=3\nid=New\n", encoding="utf-8")
        self.assertEqual(format_io.package_kind(self.root), "format3")

    def test_runtime_format2_mod_is_distinct(self):
        (self.root / "mod.ini").write_text("[Mod]\nformat=2\nid=Old\n", encoding="utf-8")
        self.assertEqual(format_io.package_kind(self.root), "format2")

    def test_animestudio_source_manifest_is_not_misclassified_as_legacy_mod(self):
        (self.root / "mod.ini").write_text(
            "[MeshBody]\npath=meshes/body.mesh\n", encoding="utf-8")
        manifest = self.root / "source" / "manifest.json"
        manifest.parent.mkdir()
        manifest.write_text('{"format":"EFFSOURCE","version":1}', encoding="utf-8")
        self.assertEqual(format_io.package_kind(self.root), "source")

    def test_unrecognized_or_incomplete_package_is_not_a_mod(self):
        (self.root / "mod.ini").write_text("[MeshBody]\npath=meshes/body.mesh\n", encoding="utf-8")
        self.assertEqual(format_io.package_kind(self.root), "unknown")
        self.assertEqual(format_io.package_kind(self.root / "missing"), "unknown")


if __name__ == "__main__":
    unittest.main()

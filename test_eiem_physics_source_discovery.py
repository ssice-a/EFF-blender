"""Recursive discovery of game-source and native physics files."""
import tempfile
import unittest
from pathlib import Path

import eiem_physics_source as source


class PhysicsSourceDiscoveryTests(unittest.TestCase):
    def test_finds_nested_physics_sources_without_matching_unrelated_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = root / "character.eff" / "physics" / "components.json"
            package = root / "other" / "cloth.physics"
            unrelated = root / "materials" / "manifest.json"
            ignored = root / ".cache" / "components.json"
            for path in (evidence, package, unrelated, ignored):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}", encoding="utf-8")

            self.assertEqual(
                {evidence.resolve(), package.resolve()},
                set(source.find_import_sources(root)),
            )

    def test_requires_a_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing"
            with self.assertRaisesRegex(ValueError, "select an extracted resource directory"):
                source.find_import_sources(missing)


if __name__ == "__main__":
    unittest.main()

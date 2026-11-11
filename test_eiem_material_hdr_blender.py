"""Run in factory-startup Blender: exercise the real color/HDR RNA controls."""
import argparse
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

import bpy


parser = argparse.ArgumentParser()
parser.add_argument('--package-root', type=Path, default=Path(__file__).parent)
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
root = args.package_root.resolve()
spec = importlib.util.spec_from_file_location(
    'EFFMaterialHDRTest', root / '__init__.py', submodule_search_locations=[str(root)])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
package.register()
editor = package.eiem_blender_addon.material_editor
TINT = 'eiem_value4._BlendTint'


class MaterialHDRTests(unittest.TestCase):
    def setUp(self):
        self.material = bpy.data.materials.new('HDR Controls Regression')
        self.material['eiem_source'] = 'assets/test.mat'
        self.material['eiem_shader'] = 'HGRP/CharacterNPR_VFX'
        self.material[TINT] = '8,2,4,0.4'
        editor._prepare_material(self.material)
        self.color = self.material.eiem_editor_values[TINT]

    def tearDown(self):
        bpy.data.materials.remove(self.material)

    def assertVector(self, actual, expected):
        self.assertEqual(len(actual), len(expected))
        for a, b in zip(actual, expected):
            self.assertAlmostEqual(a, b, places=5)

    def native(self):
        return tuple(float(value) for value in self.material[TINT].split(','))

    def test_opening_hdr_color_preserves_hue_without_writing(self):
        before = {key: str(value) for key, value in self.material.items()}
        self.assertVector(self.color.color, (1, 0.25, 0.5, 0.4))
        self.assertEqual(self.material.eiem_blend_brightness, 8)
        self.assertEqual(before, {key: str(value) for key, value in self.material.items()})

    def test_dragging_color_preserves_hdr_multiplier(self):
        self.color.color = (0.2, 0.4, 1, 0.4)
        self.assertEqual(self.material.eiem_blend_brightness, 8)
        self.assertVector(self.native(), (1.6, 3.2, 8, 0.4))

    def test_darker_color_and_hdr_are_independent(self):
        self.color.color = (0.1, 0.2, 0.3, 0.4)
        self.assertEqual(self.material.eiem_blend_brightness, 8)
        self.assertVector(self.color.color, (0.1, 0.2, 0.3, 0.4))
        self.material.eiem_blend_brightness = 15
        self.assertVector(self.color.color, (0.1, 0.2, 0.3, 0.4))
        self.assertVector(self.native(), (1.5, 3, 4.5, 0.4))
        self.assertEqual(self.material.eiem_blend_brightness, 15)

    def test_raising_hdr_does_not_whiten_color_or_change_alpha(self):
        self.material.eiem_blend_brightness = 64
        self.assertVector(self.color.color, (1, 0.25, 0.5, 0.4))
        self.assertVector(self.native(), (64, 16, 32, 0.4))

    def test_alpha_edit_keeps_rgb_and_brightness(self):
        rgba = list(self.color.color)
        rgba[3] = 0.7
        self.color.color = rgba
        self.assertVector(self.native(), (8, 2, 4, 0.7))
        self.assertEqual(self.material.eiem_blend_brightness, 8)

    def test_repeated_color_and_brightness_edits_do_not_drift(self):
        for index in range(1, 15):
            brightness = index * 2
            rgba = (0.1 + index * 0.01, 0.2, 0.8, 0.4)
            self.material.eiem_blend_brightness = brightness
            self.color.color = rgba
            self.assertEqual(self.material.eiem_blend_brightness, brightness)
            self.assertVector(self.color.color, rgba)

    def test_zero_brightness_retains_color_for_restore(self):
        self.material.eiem_blend_brightness = 0
        self.assertVector(self.native(), (0, 0, 0, 0.4))
        self.assertVector(self.color.color, (1, 0.25, 0.5, 0.4))
        self.color.color = (0.2, 0.6, 1, 0.4)
        self.assertEqual(self.material.eiem_blend_brightness, 0)
        self.material.eiem_blend_brightness = 5
        self.assertVector(self.native(), (1, 3, 5, 0.4))

    def test_black_color_does_not_reset_brightness(self):
        self.color.color = (0, 0, 0, 0.4)
        self.assertEqual(self.material.eiem_blend_brightness, 8)
        self.color.color = (0.2, 0.6, 1, 0.4)
        self.assertVector(self.native(), (1.6, 4.8, 8, 0.4))

    def test_raw_rgba_edit_invalidates_previous_ui_decomposition(self):
        self.color.color = (0.1, 0.2, 0.3, 0.4)
        self.color.vector4 = (3, 6, 1.5, 0.8)
        self.assertEqual(self.material.eiem_blend_brightness, 6)
        self.assertVector(self.color.color, (0.5, 1, 0.25, 0.8))

    def test_export_writes_effective_hdr_rgba_without_editor_metadata(self):
        self.color.color = (0.2, 0.4, 1, 0.4)
        values, _ = package.eiem_blender_addon.material_override_payload(
            self.material, {}, force=True)
        rgba = next(value.split('=', 1)[1] for value in values
                    if value.startswith('value4._BlendTint='))
        self.assertVector(tuple(float(value) for value in rgba.split(',')), (1.6, 3.2, 8, 0.4))
        self.assertFalse(any('editor_' in value for value in values))

    def test_blend_file_roundtrip_preserves_independent_values(self):
        self.color.color = (0.1, 0.2, 0.3, 0.4)
        self.material.eiem_blend_brightness = 15
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'hdr-controls.blend')
            bpy.data.libraries.write(path, {self.material})
            with bpy.data.libraries.load(path) as (source, destination):
                destination.materials = source.materials
            restored = destination.materials[0]
            try:
                self.assertEqual(restored.eiem_blend_brightness, 15)
                self.assertVector(restored.eiem_editor_values[TINT].color, (0.1, 0.2, 0.3, 0.4))
            finally:
                bpy.data.materials.remove(restored)


try:
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(MaterialHDRTests))
finally:
    package.unregister()
if not result.wasSuccessful():
    raise SystemExit(1)

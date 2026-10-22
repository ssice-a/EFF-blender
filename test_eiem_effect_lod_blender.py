"""Real root RNA/export coverage without touching the user's scene."""
import copy
import importlib
import json
from pathlib import Path
import sys

import bpy

addon_path, source = sys.argv[sys.argv.index('--') + 1:]
sys.path.insert(0, str(Path(addon_path).resolve().parent))
addon = importlib.import_module(Path(addon_path).name)
addon.register()
fx = addon.eiem_blender_addon.effect_editor
root = fx.import_source(bpy.context, source)
edit = fx.synchronize(root)
before = copy.deepcopy(edit.state)
assert root.eff_effect.all_lods is True
first = fx.assets.export_document(root, edit)
assert first['lodCoverage'] == 'all'
root.eff_effect.all_lods = False
second = fx.assets.export_document(root, edit)
assert second['lodCoverage'] == 'source'
assert {k: v for k, v in first.items() if k != 'lodCoverage'} == {
    k: v for k, v in second.items() if k != 'lodCoverage'}
assert edit.state == before, 'export policy changed native author parameters'
root.eff_effect.all_lods = True
assert fx.assets.export_document(root, edit) == first
print('PASS effect LOD root control: enabled by default, reversible, export/currentness and author data preserved')

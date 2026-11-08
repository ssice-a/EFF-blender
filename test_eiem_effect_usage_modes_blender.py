"""Check explicit Mesh-mount and game-Prefab replacement author modes."""
import importlib.util
import json
import sys
from pathlib import Path

import bpy


addon_dir = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "eiem_effect_usage_mode_test", addon_dir / "__init__.py",
    submodule_search_locations=[str(addon_dir)])
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()

editor = addon.eiem_blender_addon.effect_editor
root = bpy.data.objects.new("GameEffectReplacement", None)
bpy.context.scene.collection.objects.link(root)
root["eff_effect_id"] = "game_effect_replacement"
root["eff_effect_document"] = json.dumps({
    "catalog": {"prefab": "assets/effects/skill/water_burst.prefab"}
})

assert root.eff_effect.usage == editor.MESH_ATTACHMENT
root.eff_effect.usage = editor.REPLACE_GAME_PREFAB
assert root.eff_effect.replacement_prefab == "assets/effects/skill/water_burst.prefab"
row = editor.attachment(root)
assert row["mode"] == "replace_game_prefab"
assert row["replacementPrefab"] == "assets/effects/skill/water_burst.prefab"
assert row["targetPath"] == "" and row["targetAsset"] == ""
editor.require_publishable_effect_usage(root)

root.eff_effect.replacement_prefab = "assets/effects/../skill/water_burst.prefab"
try:
    editor.require_publishable_effect_usage(root)
except ValueError:
    pass
else:
    raise AssertionError("non-canonical replacement Prefab path was accepted")

print("PASS explicit effect usage selection, logical-path target, and publication validation")
addon.unregister()

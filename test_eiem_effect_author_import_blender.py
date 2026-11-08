"""Verify that a Format 3 effect-author import remains re-exportable.

Run in background Blender with ``-- ADDON FORMAT3_PACKAGE REPORT``.
The test deliberately accepts packages whose target game Mesh is not bundled;
the attachment declaration must still survive as a logical reference.
"""
import configparser
import importlib
import json
import sys
from pathlib import Path

import bpy

addon_path, package, report = map(Path, sys.argv[sys.argv.index("--") + 1:])
sys.path.insert(0, str(addon_path.resolve().parent))
addon = importlib.import_module(addon_path.name)
addon.register()
runtime = addon.eiem_blender_addon

parser = configparser.ConfigParser(interpolation=None, strict=False)
parser.optionxform = str
with (package / "mod.ini").open("r", encoding="utf-8-sig") as stream:
    parser.read_file(stream)

expected = {}
author_edits = {}
for section in parser.sections():
    if not section.startswith("EffectAttachment"):
        continue
    author_id = parser[section].get("author", "")
    author = parser[author_id]
    snapshot = runtime.format_io.read_format3_effect_author_resource(
        runtime.safe_path(package, author["path"]), author_id, author["path"])
    effect_id = str(snapshot["attachment"].get("id", ""))
    expected[effect_id] = snapshot["attachment"]
    author_edits[effect_id] = snapshot.get("edit", {})

runtime.import_package(package, clean=True)
roots = [obj for obj in bpy.data.objects if obj.get("eff_effect_document")]
assert len(roots) == len(expected), (len(roots), len(expected))
rows = []
for root in roots:
    attachment = json.loads(root["eff_effect_attachment"])
    effect_id = str(root.get("eff_effect_id", ""))
    assert effect_id in expected, effect_id
    assert attachment["targetPath"] == expected[effect_id]["targetPath"]
    assert attachment["targetAsset"] == expected[effect_id]["targetAsset"]
    row = runtime.effect_editor.attachment(root)
    assert row["id"] == effect_id
    assert row["targetPath"] == attachment["targetPath"]
    assert row["targetAsset"] == attachment["targetAsset"]
    edit = runtime.effect_editor.editor(root)
    expected_assets = author_edits[effect_id].get("assets", [])
    imported_assets = edit.state.get("author_assets", [])
    assert [item["identity"] for item in imported_assets] == [
        item["identity"] for item in expected_assets]
    mesh_assets = [item for item in expected_assets
                   if edit.objects[item["template"]]["classId"] == 43]
    for item in mesh_assets:
        mesh = addon.eiem_blender_addon.effect_editor.assets.source_meshes(root).get(item["identity"])
        assert mesh and len(mesh.data.polygons) > 0, item["identity"]
    texture_ids = {item["identity"] for item in expected_assets
                   if edit.objects[item["template"]]["classId"] == 28 and
                   item.get("previewPngBase64")}
    imported_previews = {slot.image.get("eff_effect_asset_identity")
                         for obj in root.children_recursive
                         if obj.get("eff_effect_resource")
                         for slot in obj.eff_effect_textures
                         if slot.image and slot.image.packed_file and
                         slot.image.get("eff_effect_asset_identity") in texture_ids}
    assert imported_previews == texture_ids
    exported = addon.eiem_blender_addon.effect_editor.assets.export_document(root, edit)
    assert [item["identity"] for item in exported.get("assets", [])] == [
        item["identity"] for item in expected_assets]
    rows.append(row)

    Path(report).write_text(json.dumps({"passed": True, "effects": rows},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
print("PASS effect author import round-trip seam", flush=True)
addon.unregister()


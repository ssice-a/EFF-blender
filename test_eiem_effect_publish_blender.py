"""Publish a real native effect build, then import its Format 3 author data.

Run in background Blender with:
  -- ADDON_PACKAGE SOURCE BUILD EDIT_JSON ATTACHMENT_PACKAGE DESTINATION REPORT
"""
import configparser
import copy
import importlib
import json
import sys
from pathlib import Path

import bpy

addon_package, source, build, edit_path, template_package, destination, report = map(
    Path, sys.argv[sys.argv.index("--") + 1:])
sys.path.insert(0, str(addon_package.resolve().parent))
addon = importlib.import_module(addon_package.name)
addon.register()
runtime = addon.eiem_blender_addon

try:
    parser = configparser.ConfigParser(interpolation=None, delimiters=("=",), strict=False)
    parser.optionxform = str
    with (template_package / "mod.ini").open("r", encoding="utf-8-sig") as stream:
        parser.read_file(stream)

    attachment_section = next(section for section in parser.sections()
                              if section.startswith("EffectAttachment"))
    author_id = parser[attachment_section].get("author", "")
    author_path = parser[author_id]["path"]
    template = runtime.format_io.read_format3_effect_author_resource(
        runtime.safe_path(template_package, author_path), author_id, author_path)
    attachment = dict(template["attachment"])
    attachment.update(id="00000000000000000000000000000002",
                      label=source.stem, part="")

    state = runtime.effect_editor.document.load_source(source)
    edit = json.loads(edit_path.read_text(encoding="utf-8-sig"))
    runtime.effect_editor.document.apply_edit(copy.deepcopy(state), edit)
    author_data = runtime.format_io.encode_format3_effect_author(
        source, state, attachment, edit)
    row = dict(attachment, build=str(build.resolve()), _source=str(source.resolve()),
               _authorData=author_data)

    destination = destination.resolve()
    if destination.exists():
        raise ValueError("integration destination must be a new temporary directory")
    effect_export = importlib.import_module(addon.__name__ + ".eiem_effect_export")
    effect_export.publish(destination, [row])
    modules, _ = runtime.native_export.pack_modules()
    manifest = modules["author_format3"].read_format3_directory(destination)
    runtime_files = [path for path in manifest["_files"]
                     if path.startswith("effects/") and "/author/" not in path]
    assert manifest["modType"] == "effect", manifest
    assert "controls.effres" in manifest["_files"]
    assert runtime_files, runtime_files

    runtime.import_package(destination, clean=True)
    roots = [obj for obj in bpy.data.objects if obj.get("eff_effect_document")]
    assert len(roots) == 1, len(roots)
    root = roots[0]
    restored = json.loads(root["eff_effect_attachment"])
    assert root.get("eff_effect_id") == attachment["id"]
    for field in ("targetPrefab", "rendererPath", "targetPath", "targetAsset"):
        assert restored[field] == attachment[field], (field, restored[field])
    editor = runtime.effect_editor.editor(root)
    exported = runtime.effect_editor.assets.export_document(root, editor)
    assert [item["identity"] for item in exported.get("assets", [])] == [
        item["identity"] for item in edit.get("assets", [])]

    result = dict(passed=True, modId=manifest["modId"],
                  effectId=root.get("eff_effect_id"),
                  runtimeResources=runtime_files,
                  authoredAssets=len(exported.get("assets", [])),
                  targetPath=restored["targetPath"],
                  destination=str(destination))
    report = report.resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("PASS effect publish and Format 3 import", flush=True)
finally:
    addon.unregister()

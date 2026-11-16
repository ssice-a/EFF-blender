"""Cold field projections and saved-project upgrades must reuse one document.

Run with factory startup: -- ADDON PROJECT OUTPUT. Never saves the fixture.
Uses a real multi-emitter scene to cover large native documents and legacy
angle controls, including the Blender load_post path on reopening the file.
"""
import importlib
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch

import bpy

addon_path, project, output = map(Path, sys.argv[sys.argv.index('--') + 1:])
sys.path.insert(0, str(addon_path.resolve().parent))
addon = importlib.import_module(addon_path.name)
addon.register()
editor = addon.eiem_blender_addon.effect_editor
# Inspect the fixture before its migration. No timer callbacks run inside
# this background script, and all production handlers return for the reopen.
bpy.app.handlers.load_post.remove(editor.upgrade_loaded)
bpy.ops.wm.open_mainfile(filepath=str(project))
bpy.app.handlers.load_post.append(editor.upgrade_loaded)
roots = [obj for obj in bpy.context.scene.objects if obj.get('eff_effect_document')]
assert len(roots) > 1
root = max(roots, key=lambda obj: len(obj['eff_effect_document']))
members = editor.objects(root)
target = max(members, key=lambda obj: len(obj.get('eff_effect_fields', '')))
editor.clear_cache()
with patch.object(editor, 'editor', wraps=editor.editor) as lookup:
    rows = editor.field_rows(target)
    assert sum(row['kind'] in ('color', 'gradient_time') for row in rows) > 100
    assert lookup.call_count == 1, \
        f'cold field initialization re-read the document {lookup.call_count} times for one object'
edit = editor.editor(root)
for row in rows:
    if row['kind'] in ('color', 'gradient_time'):
        value = edit.value(row['identity'], row['path'])
        expected = tuple(value[c] for c in 'rgba') if row['kind'] == 'color' else value / 65535
        assert row['_last'] == expected, row

# Force the legacy upgrade path without touching the fixture on disk.
for obj in members:
    if 'eff_effect_angles_version' in obj: del obj['eff_effect_angles_version']
angle = next(row for row in rows if editor.effect_ui.angle_unit(row['path']))
existing = target.eff_effect_angles.get(angle['key'])
if existing: target.eff_effect_angles.remove(target.eff_effect_angles.find(angle['key']))
baseline = {obj.name: obj['eff_effect_document'] for obj in roots}
editor.clear_cache()
start = time.perf_counter()
with patch.object(editor, 'editor', wraps=editor.editor) as lookup:
    editor.upgrade_loaded()
    upgrade_lookups = lookup.call_count
    assert upgrade_lookups <= len(roots) * 2, \
        f'load upgrade read documents {upgrade_lookups} times for {len(roots)} roots'
upgrade_ms = (time.perf_counter()-start)*1000
assert target.eff_effect_angles.get(angle['key']), 'legacy degree control was not restored'
assert baseline == {obj.name: obj['eff_effect_document'] for obj in roots}, 'migration changed native fields'

# Exercise the actual reopening hook with empty caches, not a warm API call.
start = time.perf_counter()
with patch.object(editor, 'editor', wraps=editor.editor) as lookup:
    bpy.ops.wm.open_mainfile(filepath=str(project))
    reopen_lookups = lookup.call_count
reopen_ms = (time.perf_counter()-start)*1000
assert reopen_lookups <= len(roots)*2, f'cold reopen repeated document reads: {reopen_lookups}'
roots = [obj for obj in bpy.context.scene.objects if obj.get('eff_effect_document')]
assert baseline == {obj.name: obj['eff_effect_document'] for obj in roots}
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(dict(passed=True, roots=len(roots), coldFieldDocumentReads=1,
    legacyUpgradeDocumentReads=upgrade_lookups, legacyUpgradeMs=upgrade_ms,
    reopenDocumentReads=reopen_lookups, reopenMs=reopen_ms,
    legacyAnglesRestored=True, nativeFieldsUnchanged=True), indent=2), 'utf-8')
addon.unregister()
print('PASS cold fields, legacy controls and real reopen reuse root documents; native fields unchanged')

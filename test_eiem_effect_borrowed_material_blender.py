"""Edited cross-source game Materials must export as local author resources.

Run with factory startup: -- ADDON SCENE OUTPUT.
"""
import base64
import copy
import importlib
import json
from pathlib import Path
import sys

import bpy

addon_path, scene, output, *compilers = map(Path, sys.argv[sys.argv.index('--') + 1:])
output.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(addon_path.resolve().parent))
addon = importlib.import_module(addon_path.name)
addon.register()
fx = addon.eiem_blender_addon.effect_editor
bpy.ops.wm.open_mainfile(filepath=str(scene), load_ui=False, use_scripts=False)
root = bpy.context.view_layer.objects.active
edit = fx.synchronize(root)
mid = next(i for i, o in edit.objects.items()
           if o.get('classId') == 21 and o.get('gameSource') and
           edit.value(i) != edit.state['original'][i])
original = copy.deepcopy(edit.state)
payload = fx.assets.export_document(root, edit)
assert edit.state == original, 'export mutated the live author state'
assert payload == fx.assets.export_document(root, edit), 'export is not deterministic'
local = next(a for a in payload['assets']
             if edit.objects[a['template']]['classId'] == 21 and
             edit.objects[a['template']]['native']['cab'].casefold() !=
             edit.objects[mid]['native']['cab'].casefold() and
             a['identity'] not in {x['identity'] for x in edit.state.get('author_assets', [])})
native = fx.assets.NativeAssets(edit, root['eff_effect_id'])
tree, _ = native.tree(local['template'])
tree = native.codec.decode(tree.schema, base64.b64decode(local['payloadBase64']))
assert next(v['second'].data for v in tree['m_SavedProperties']['m_Floats'].items()
            if v['first'].data == '_TintColorIntensity') == 50
refs = [r for r in payload['gameReferences'] if r['owner'] == local['identity']]
assert {r['type'] for r in refs} >= {'Shader', 'Texture2D'}
assert all(r['previewSource'] == edit.objects[mid]['gameSource'] for r in refs)
assert not any(edit.objects[a['template']].get('gameSource') for a in payload['assets']), \
    'borrowed game resource snapshots were repackaged'
assert not any(r['type'] == 'Material' for r in payload['gameReferences']), \
    'Renderer still points at the unedited game Material'

# Restoring and re-exporting must retain the clone and its logical dependencies.
restored = fx.document.load_source(edit.state['source'])
fx.document.apply_edit(restored, payload)
again = fx.document.Editor(restored).export()
assert all(r in again['gameReferences'] for r in refs)
assert not any(o['identity'] == local['identity'] for o in again['objects'])

# An unmodified cross-source game Material remains a reference. Selecting a
# local image alone also creates a local Material, with only that image owned.
edit.state['values'][mid] = copy.deepcopy(edit.state['original'][mid])
pristine = fx.assets.export_document(root, edit)
assert not any(a['identity'] == local['identity'] for a in pristine.get('assets', []))
assert any(r['type'] == 'Material' for r in pristine['gameReferences'])
author = fx.assets.material_object(root, mid)
image = bpy.data.images.new('Borrowed local image regression', width=2, height=2, alpha=True)
image.pixels[:] = [0.2, 0.4, 0.8, 0.5] * 4
author.eff_effect_textures['_MainTex'].image = image
image_only = fx.assets.export_document(root, edit)
assert any(a['identity'] == local['identity'] for a in image_only['assets'])
assert any(r['type'] == 'Shader' and r['owner'] == local['identity'] for r in image_only['gameReferences'])
assert not any(r['type'] == 'Texture2D' and r['owner'] == local['identity'] for r in image_only['gameReferences'])
author.eff_effect_textures['_MainTex'].image = None
bpy.data.images.remove(image)
edit.state['values'][mid] = original['values'][mid]
(output / 'edits.json').write_text(fx.document.dumps(payload), encoding='utf-8')
(output / 'expected.json').write_text(json.dumps(dict(
    source=edit.state['source'], material=local['identity'], brightness=50,
    gameReferences=refs), ensure_ascii=False, indent=2), encoding='utf-8')
print('PASS edited borrowed Material: local clone, brightness 50, game Shader/Texture references, deterministic export, roundtrip')
if compilers:
    root.eff_effect.compiler = str(compilers[0].resolve())
    first = output / 'mod-first'
    fx.ExportBatch([root], first).run()
    roots_before = {o.as_pointer() for o in bpy.context.scene.objects if o.get('eff_effect_document')}
    addon.eiem_blender_addon.import_package(first)
    restored_root = next(o for o in bpy.context.scene.objects
                         if o.get('eff_effect_document') and o.as_pointer() not in roots_before)
    restored_root.eff_effect.compiler = str(compilers[0].resolve())
    restored_edit = fx.synchronize(restored_root)
    assert next(v['second'] for v in restored_edit.value(local['identity'])['m_SavedProperties']['m_Floats']
                if v['first'] == '_TintColorIntensity') == 50
    fx.ExportBatch([restored_root], output / 'mod-roundtrip').run()
    # Edit the now-authored Material again: its stored payload must update.
    material = fx.material_bridge.material_for(fx.assets.material_object(restored_root, local['identity']))
    material.eiem_editor_values['eiem_float._TintColorIntensity'].scalar = 60
    edited = fx.assets.export_document(restored_root, fx.synchronize(restored_root))
    asset = next(a for a in edited['assets'] if a['identity'] == local['identity'])
    tree = native.codec.decode(tree.schema, base64.b64decode(asset['payloadBase64']))
    assert next(v['second'].data for v in tree['m_SavedProperties']['m_Floats'].items()
                if v['first'].data == '_TintColorIntensity') == 60
    fx.ExportBatch([restored_root], output / 'mod-edited-again').run()
    print('PASS complete source -> native build -> Mod -> Blender -> export -> edit authored Material -> export')
addon.unregister()

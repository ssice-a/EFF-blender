"""Local author Material -> missing donor -> native rebuild, using real schemas."""
import importlib
import json
from pathlib import Path
import sys

import bpy

addon_path, fixture, output, compiler = map(Path, sys.argv[sys.argv.index('--') + 1:])
sys.path.insert(0, str(addon_path.resolve().parent))
addon = importlib.import_module(addon_path.name)
addon.register()
fx = addon.eiem_blender_addon.effect_editor
payload = json.loads(fixture.read_bytes())
output.mkdir(parents=True, exist_ok=True)
missing = str(output / 'donor-directory-does-not-exist')
for row in payload['gameReferences']:
    assert row.get('sourcePrefab') and row.get('sourceRootIdentity'), row
    row['previewSource'] = missing
state = fx.document.load_source(payload['source'])
fx.document.apply_edit(state, payload)
root = fx.import_workspace(bpy.context, state)
root.eff_effect.preview = False
edit = fx.synchronize(root)
exported = fx.assets.export_document(root, edit)
material = next(a for a in exported['assets'] if edit.objects[a['identity']]['classId'] == 21)
data = edit.value(material['identity'])
brightness = next(r['second'] for r in data['m_SavedProperties']['m_Floats'] if r['first'] == '_TintColorIntensity')
assert brightness == 50, brightness
assert all(r.get('sourcePrefab') and r.get('sourceRootIdentity') for r in exported['gameReferences'])
(output / 'missing-donor.edits.json').write_text(fx.document.dumps(exported), encoding='utf-8')
request = fx.effect_publish.Build(exported, compiler, output / 'cache', 'portable-test', fx.effect_codec().read_native_build)
native = request.run()
assert fx.effect_codec().read_native_build(native)['gameResources']
addon.unregister()
print('PASS missing donor -> Blender import/edit/export -> native build; authored Material brightness 50, game resource proofs retained')

"""Both existing material import routes retain reusable native bindings."""
import importlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
from unittest.mock import patch

import bpy

addon_path, source, output = map(Path, sys.argv[sys.argv.index('--') + 1:])
sys.path.insert(0, str(addon_path.parent))
addon = importlib.import_module(addon_path.name)
addon.register()
api = addon.eiem_blender_addon
bindings = importlib.import_module(addon.__name__ + '.eiem_effect_scene_material')
fx = api.effect_editor
output.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix='eff-material-reuse-') as temporary:
    package = Path(temporary) / 'source'
    ini = api.parse_ini(source)
    section = next(s for s in ini.sections() if s.lower().startswith('material') and
                   ini[s].get('path') == 'materials/M_fx_jsspsi_cloth_06_01.mat')
    values = api.format_io.read_package_material(source, section, dict(ini[section]), 'source')
    retained = {section} | {v for k,v in values.items() if k.startswith('texture.')}
    for s in list(ini.sections()):
        if s not in retained:
            ini.remove_section(s)
    shutil.copytree(source / 'source', package / 'source')
    for s in retained:
        relative = ini[s]['path']
        target = package / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, target)
    with (package / 'mod.ini').open('w', encoding='utf-8') as stream:
        ini.write(stream)
    assert api.import_package(package) == 0
    whole = next(m for m in bpy.data.materials if m.get('eiem_author_package') == str(package))
    target = bpy.data.objects.new('single material import', bpy.data.meshes.new('target'))
    bpy.context.scene.collection.objects.link(target)
    single = api.import_material_file(package / ini[section]['path'], target)
    assert whole[bindings.IMPORT_KEY] == single[bindings.IMPORT_KEY]
    for material in (whole, single):
        record = json.loads(material[bindings.IMPORT_KEY])
        assert record['bindings'] and record['textures']
        assert any(r['path'].endswith('characternpr_vfx.shader') for r in record['bindings'])
        assert all(p.get('importedSha256') for p in record['textures'].values())
    # The existing sparse Material resource carries its import record on roundtrip.
    lines, _ = api.material_override_payload(single, {}, force=True)
    raw = ('\n'.join(lines) + '\n').encode('utf-8')
    decoded = api.format_io.read_effmat_snapshot(raw)
    restored = api.load_material(package, 'MaterialRoundtrip', decoded)
    assert restored[bindings.IMPORT_KEY] == single[bindings.IMPORT_KEY]
    assert '_native_bindings' not in json.loads(restored['eiem_baseline_json'])

    with bpy.data.libraries.load(str(output.parent / 'author-fixture.blend')) as (src, dst):
        dst.objects = src.objects
    for obj in dst.objects:
        bpy.context.scene.collection.objects.link(obj)
    root = next(o for o in dst.objects if o.get('eff_effect_document'))
    edit = fx.synchronize(root)
    result = dict(gameReferences=[])
    native = fx.assets.NativeAssets(edit, root['eff_effect_id'])
    exporter = bindings.Materials(native, result, None)
    with patch.object(bindings.Import, 'source', side_effect=AssertionError('export reread source')):
        mid = exporter.export(restored)
    assert len(native.additions) == 1, 'untouched game textures were repackaged'
    refs = [r for r in result['gameReferences'] if r['owner'] == mid]
    assert len(refs) == len(record['bindings'])
    print('PASS existing whole-package and single-material imports, sparse Mod roundtrip, no export source reads, game textures stay references')
addon.unregister()

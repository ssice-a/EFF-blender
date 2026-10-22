"""Real one-click export/currentness regression against a scene snapshot."""
import bpy
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import shutil
import time

addon_path, scene, output = map(Path, sys.argv[sys.argv.index('--') + 1:])
sys.path.insert(0, str(addon_path.resolve().parent))
addon = importlib.import_module(addon_path.name)
addon.register()
fx = addon.eiem_blender_addon.effect_editor
bpy.ops.wm.open_mainfile(filepath=str(scene), load_ui=False, use_scripts=False)
output.mkdir(parents=True, exist_ok=True)
fx.effect_cache = lambda: str(output/'cache')
root = next(o for o in bpy.context.scene.objects if o.get('eff_effect_document'))
for obj in bpy.context.selected_objects:
    obj.select_set(False)
root.select_set(True)
bpy.context.view_layer.objects.active = root
codec = fx.effect_codec()
modules, _ = addon.eiem_blender_addon.native_export.pack_modules()
starts = []
original_start = fx.effect_publish.Build.start
def start(job):
    starts.append(job.key)
    return original_start(job)
fx.effect_publish.Build.start = start
destination = output/'petals-only'
def hashes():
    return {p.relative_to(destination).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in destination.rglob('*') if p.is_file()}
report = []
def export(label, expected_starts):
    began = time.perf_counter()
    bpy.context.scene.eiem_export_mod_name=destination.name
    result = bpy.ops.eiem.export_package('EXEC_DEFAULT', directory=str(destination.parent),filepath=str(destination))
    assert result == {'FINISHED'}, result
    assert len(starts) == expected_starts, (label, len(starts), expected_starts)
    package = modules['ini_reader'].read_package(destination)
    assert len(package['effects']) == 1 and not package['parts']
    report.append(dict(case=label, seconds=time.perf_counter()-began, builds=len(starts)))
    return hashes()
first = export('first', 1)
(destination/'state.ini').write_text('user-state=preserve', encoding='utf-8')
second = export('unchanged', 1)
assert all(second[k] == v for k, v in first.items())
assert (destination/'state.ini').read_text('utf-8') == 'user-state=preserve'
emitter = next(o for o in root.children_recursive
    if any(fx.editor(root).objects[i]['classId'] == 198
           for i in json.loads(o.get('eff_effect_components', '[]'))))
row = next(row for row in fx.field_rows(emitter)
    if row['path'][:2] == ['InitialModule', 'startSpeed'] and row['path'][-1] == 'scalar')
emitter[row['key']] = float(emitter[row['key']]) + .125
third = export('parameter', 2)
assert third != second, 'parameter edits exported stale resources'
root.location.x += .01
fourth = export('mount-only', 2)
assert fourth != third, 'mount offset not published'
particle = emitter.eff_effect.particle_mesh
particle.data.vertices[0].co.x += .01
particle.data.update()
fifth = export('particle-mesh', 3)
assert fifth != fourth, 'particle mesh edit exported stale resources'
assert (destination/'state.ini').read_text('utf-8') == 'user-state=preserve'
emitter[row['key']] = float(emitter[row['key']]) + .125
began = time.perf_counter()
request = fx.ensure_built(root)
assert not request.reused and len(starts) == 4, 'full export reused stale effect'
assert hashes() == fifth, 'building full-export attachment overwrote standalone Mod'
report.append(dict(case='full-export-currentness', seconds=time.perf_counter()-began, builds=len(starts)))
cached=fx.build_request(root)
assert cached.cached(), 'unchanged full-export build not reusable'
runtime=cached.output/'runtime.bin';broken=bytearray(runtime.read_bytes());broken[-1]^=1;runtime.write_bytes(broken)
assert not cached.cached(), 'corrupt cached runtime accepted'
cached.run()
assert len(starts)==5 and hashes()==fifth, 'cache repair changed published effect'
payload=fx.assets.export_document(root,fx.synchronize(root))
clone=output/'source-copy.efffx';shutil.copytree(Path(payload['source']),clone,dirs_exist_ok=True);payload['source']=str(clone)
fx.effect_publish.inputs_signature(payload,root.eff_effect.compiler)
baseline=json.loads((clone/'source/manifest.json').read_text('utf-8-sig'))
snapshot=clone/baseline['serializedFiles'][0]['snapshot']['file'];stamp=snapshot.stat()
bad=bytearray(snapshot.read_bytes());bad[-1]^=1;snapshot.write_bytes(bad);os.utime(snapshot,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
try:
    fx.effect_publish.inputs_signature(payload,root.eff_effect.compiler)
    raise AssertionError('same-stamp source corruption accepted')
except ValueError as error:
    assert '快照损坏' in str(error)
fake = output/'invalid-compiler.exe'
fake.write_bytes(b'not executable')
root.eff_effect.compiler = str(fake)
emitter[row['key']] = float(emitter[row['key']]) + .125
try:
    bpy.ops.eiem.export_package('EXEC_DEFAULT',directory=str(destination.parent),filepath=str(destination))
except RuntimeError:
    pass
assert hashes() == fifth, 'failed build changed published effect'
assert not fx._BUILD_RUNNING
fx.effect_publish.Build.start = original_start
(output/'report.json').write_text(json.dumps(dict(passed=True, cases=report,
    statePreserved=True, failedBuildPreservesPublished=True), ensure_ascii=False, indent=2), encoding='utf-8')
print('PASS effect fast export ' + json.dumps(report), flush=True)
addon.unregister()

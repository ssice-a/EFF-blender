"""Exercise the real unified selection/export entry with multiple effects."""
import ast
import bpy
import hashlib
import importlib
import json
from pathlib import Path
import sys
import time

addon_dir,scene,output=map(Path,sys.argv[sys.argv.index('--')+1:])
sys.path.insert(0,str(addon_dir.resolve().parent))
package=importlib.import_module(addon_dir.name);package.register()
addon=package.eiem_blender_addon;fx=addon.effect_editor
bpy.ops.wm.open_mainfile(filepath=str(scene),load_ui=False,use_scripts=False)
output.mkdir(parents=True,exist_ok=True)
fx.effect_cache=lambda:str(output/'cache')
first=next(o for o in bpy.context.scene.objects if o.get('eff_effect_document'))
first.eff_effect.preview=False
owner=first.eff_effect.owner
second=fx.import_source(bpy.context,fx.editor(first).state['source'])
second.eff_effect.owner=owner;second.eff_effect.bone=first.eff_effect.bone
second.eff_effect.preview=False
emitter=next(o for o in second.children_recursive if any(
    fx.editor(second).objects[i]['classId']==198 for i in json.loads(o.get('eff_effect_components','[]'))))
modules,_=addon.native_export.pack_modules()

def select(*objects):
    for obj in bpy.context.selected_objects:obj.select_set(False)
    for obj in objects:
        obj.hide_set(False);obj.select_set(True)
    bpy.context.view_layer.objects.active=objects[-1] if objects else None

def hashes(destination):
    return {p.relative_to(destination).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
        for p in destination.rglob('*') if p.is_file()}

target=output/'effects-only'
select(first,second,emitter)
meshes,rigs,roots=addon.selected_export_resources()
assert not meshes and len(roots)==2
addon.plan_mesh_only_export=lambda *a,**k:(_ for _ in ()).throw(AssertionError('effect-only touched geometry planning'))
real_native=addon.native_export.export_native_package
addon.native_export.export_native_package=lambda *a,**k:(_ for _ in ()).throw(AssertionError('effect-only called character compiler'))
bpy.context.scene.eiem_export_mod_name=target.name
began=time.perf_counter()
assert bpy.ops.eiem.export_package('EXEC_DEFAULT',directory=str(output),filepath=str(target))=={'FINISHED'}
duration=time.perf_counter()-began
read=modules['ini_reader'].read_package(target)
assert len(read['effects'])==2 and not read['parts'] and not (target/'compiled.bin').exists()
assert not any(e['part'] for e in read['effects'])
assert {e['id'] for e in read['effects']}=={first['eff_effect_id'],second['eff_effect_id']}
assert bpy.context.scene.eiem_export_last_directory==str(target.resolve())
(target/'state.ini').write_text('keep-user-state=1',encoding='utf-8')
before=hashes(target)
# The unified quick operation uses the last destination even if the next
# file-dialog folder name has since changed.
bpy.context.scene.eiem_export_mod_name='different-next-export'
assert bpy.ops.eiem.export_update('EXEC_DEFAULT')=={'FINISHED'}
assert before==hashes(target) and not (output/'different-next-export').exists()
# Selecting a child exports its owning effect once, without unrelated roots.
select(emitter)
single=output/'single-effect';bpy.context.scene.eiem_export_mod_name=single.name
assert bpy.ops.eiem.export_package('EXEC_DEFAULT',directory=str(output))=={'FINISHED'}
assert [e['id'] for e in modules['ini_reader'].read_package(single)['effects']]==[second['eff_effect_id']]
# A failed second effect must preserve the entire prior package.
select(first,second);bpy.context.scene.eiem_export_mod_name=target.name
compiler=second.eff_effect.compiler;second.eff_effect.compiler=str(output/'missing-compiler.exe')
try:bpy.ops.eiem.export_package('EXEC_DEFAULT',directory=str(output))
except RuntimeError:pass
else:raise AssertionError('invalid second effect succeeded')
assert hashes(target)==before and not fx._BUILD_RUNNING
second.eff_effect.compiler=compiler
# If parameters change while a native build is running, publish neither
# effect. Cancellation also releases the global build gate.
first_emitter=next(o for o in first.children_recursive if any(
    fx.editor(first).objects[i]['classId']==198 for i in json.loads(o.get('eff_effect_components','[]'))))
field=next(r for r in fx.field_rows(first_emitter)
    if r['path'][:2]==['InitialModule','startSpeed'] and r['path'][-1]=='scalar')
original_value=float(first_emitter[field['key']]);first_emitter[field['key']]=original_value+.117
job=fx.ExportBatch([first,second],target)
try:
    assert not job.poll() and fx._BUILD_RUNNING
    first_emitter[field['key']]=original_value+.118
    job.current.process.wait()
    try:job.run()
    except ValueError as error:assert '构建期间特效有修改' in str(error)
    else:raise AssertionError('stale batch published')
finally:job.close()
assert hashes(target)==before and not fx._BUILD_RUNNING
first_emitter[field['key']]=original_value+.119
job=fx.ExportBatch([first,second],target)
assert not job.poll();job.cancel()
assert hashes(target)==before and not fx._BUILD_RUNNING
first_emitter[field['key']]=original_value
# Same-folder empty directories are valid outputs created by the picker.
empty=output/'new-empty-directory';empty.mkdir(exist_ok=True)
bpy.context.scene.eiem_export_mod_name=empty.name
assert bpy.ops.eiem.export_package('EXEC_DEFAULT',directory=str(output))=={'FINISHED'}
assert len(modules['ini_reader'].read_package(empty)['effects'])==2
# Explicitly selecting a disabled attachment still selects that effect.
second.eff_effect.enabled=False
assert second in fx.export_roots([second])
# The same selection resolver distinguishes resource Meshes from character
# geometry, and includes enabled effects attached to selected character Mesh.
particle=emitter.eff_effect.particle_mesh
select(particle)
assert addon.selected_export_resources()[2]==[second]
select(owner,second)
meshes,rigs,roots=addon.selected_export_resources()
assert meshes==[owner] and roots==sorted([first,second],key=lambda root:root.name)
addon.native_export.export_native_package=real_native
# Restore the actual planner after the effect-only tripwire.
tree=ast.parse(Path(addon.__file__).read_text('utf-8'))
node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='plan_mesh_only_export')
exec(compile(ast.Module(body=[node],type_ignores=[]),addon.__file__,'exec'),addon.__dict__)
mixed=output/'character-and-effects';bpy.context.scene.eiem_export_mod_name=mixed.name
began=time.perf_counter()
assert bpy.ops.eiem.export_package('EXEC_DEFAULT',directory=str(output),lod_all=False,lod0=True)=={'FINISHED'}
mixed_seconds=time.perf_counter()-began
read=modules['ini_reader'].read_package(mixed)
assert read['parts'] and len(read['effects'])==2 and all(e['part'] for e in read['effects'])
assert (mixed/'compiled.bin').is_file()
# An effect-only export merges the selection into the existing character.
before_mixed=hashes(mixed);select(first)
assert bpy.ops.eiem.export_package('EXEC_DEFAULT',directory=str(output))=={'FINISHED'}
assert len(modules['ini_reader'].read_package(mixed)['effects'])==2
assert all(hashes(mixed)[name]==digest for name,digest in before_mixed.items() if name not in ('mod.ini','compiled.bin'))
# A mixed export cannot replace a dedicated effect package either.
select(owner,first);bpy.context.scene.eiem_export_mod_name=target.name
try:bpy.ops.eiem.export_package('EXEC_DEFAULT',directory=str(output))
except RuntimeError:pass
else:raise AssertionError('character replaced effect-only package')
assert hashes(target)==before
report=dict(passed=True,effectOnlySeconds=duration,mixedSeconds=mixed_seconds,
    batchEffects=2,childDeduplication=True,selectionAuthoritative=True,
    quickDestinationShared=True,atomicFailurePreserved=True,statePreserved=True,
    emptyDirectory=True,mixedNativeCompiled=True,characterEffectMerge=True,characterOverEffectRejected=True,
    staleBatchRejected=True,cancellationPreserved=True)
(output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print('PASS UNIFIED EXPORT '+json.dumps(report),flush=True)
package.unregister()

"""Update an existing character through the real effects-only export entry."""
import bpy
import importlib
import json
from pathlib import Path
import shutil
import sys
import time

addon_dir,scene,character,output=map(Path,sys.argv[sys.argv.index('--')+1:])
sys.path.insert(0,str(addon_dir.resolve().parent))
package=importlib.import_module(addon_dir.name);package.register()
addon=package.eiem_blender_addon;fx=addon.effect_editor
bpy.ops.wm.open_mainfile(filepath=str(scene),load_ui=False,use_scripts=False)
assert not output.exists(),'Use a fresh isolated output directory'
output.mkdir(parents=True)
target=output/character.name;shutil.copytree(character,target)
root=next(o for o in bpy.context.scene.objects if o.get('eff_effect_document'))
root.eff_effect.preview=False
fx.effect_cache=lambda:str(output/'cache')
modules,_=addon.native_export.pack_modules();reader=modules['ini_reader'].read_package
before=reader(target)
preserved={p.relative_to(target).as_posix():p.read_bytes() for p in target.rglob('*')
    if p.is_file() and p.name not in ('mod.ini','compiled.bin','effectattachment_'+root['eff_effect_id']+'.bin')}
(target/'state.ini').write_text('preserve-player-state=1',encoding='utf-8')
child=target/'group'/'unrelated';child.mkdir(parents=True)
(child/'mod.ini').write_text('[Mod]\nformat=2\nid=Unrelated\ntype=effect\n',encoding='utf-8')
def select(obj):
    for o in bpy.context.selected_objects:o.select_set(False)
    obj.hide_set(False);obj.select_set(True);bpy.context.view_layer.objects.active=obj
select(root);bpy.context.scene.eiem_export_mod_name=target.name
began=time.perf_counter()
assert bpy.ops.eiem.export_package('EXEC_DEFAULT',directory=str(output))=={'FINISHED'}
first_seconds=time.perf_counter()-began
read=reader(target)
expected_ids={e['id'] for e in before['effects']}|{root['eff_effect_id']}
assert len(read['parts'])==len(before['parts']) and {e['id'] for e in read['effects']}==expected_ids
effect=next(e for e in read['effects'] if e['id']==root['eff_effect_id'])
assert effect['part']=='Part'+addon.native_export.mesh_part_id(root.eff_effect.owner.name,0,1).removeprefix('Mesh')
first_resource=(target/('resources/effectattachment_'+root['eff_effect_id']+'.bin')).read_bytes()
# An unchanged character effect must validate the package but never start the
# protocol/compiler processes or publish another compiled generation.
from unittest.mock import patch
from importlib import import_module
offline=import_module(addon.__package__+'.eiem_offline_reload')
compiled_before=(target/'compiled.bin').read_bytes()
with patch.object(offline,'reload_context',side_effect=AssertionError('unchanged effect started compiler')):
    assert bpy.ops.eiem.export_update('EXEC_DEFAULT')=={'FINISHED'}
assert (target/'compiled.bin').read_bytes()==compiled_before
# Add another effect, then change just the first: selection must not delete it.
other=fx.import_source(bpy.context,fx.editor(root).state['source'])
other.eff_effect.owner=root.eff_effect.owner;other.eff_effect.bone=root.eff_effect.bone;other.eff_effect.preview=False
select(other)
assert bpy.ops.eiem.export_update('EXEC_DEFAULT')=={'FINISHED'}
other_file=target/('resources/effectattachment_'+other['eff_effect_id']+'.bin')
other_bytes=other_file.read_bytes()
root.location.x+=.031
select(root);began=time.perf_counter()
assert bpy.ops.eiem.export_update('EXEC_DEFAULT')=={'FINISHED'}
update_seconds=time.perf_counter()-began
assert (target/('resources/effectattachment_'+root['eff_effect_id']+'.bin')).read_bytes()!=first_resource
assert other_file.read_bytes()==other_bytes and {e['id'] for e in reader(target)['effects']}==expected_ids|{other['eff_effect_id']}
assert all((target/name).read_bytes()==data for name,data in preserved.items())
assert (target/'state.ini').read_text('utf-8')=='preserve-player-state=1'
assert (child/'mod.ini').read_text('utf-8').startswith('[Mod]')
snapshot={p.relative_to(target).as_posix():p.read_bytes() for p in target.rglob('*') if p.is_file()}
replace=addon.native_export._replace_published_file
failed=False
def fail_resource(source,destination):
    global failed
    if Path(destination)==target/'resources'/('effectattachment_'+root['eff_effect_id']+'.bin') and not failed:
        failed=True;raise OSError('injected effect publication failure')
    return replace(source,destination)
addon.native_export._replace_published_file=fail_resource
root.location.x+=.017
try:
    try:bpy.ops.eiem.export_update('EXEC_DEFAULT')
    except RuntimeError as error:assert 'injected effect publication failure' in str(error)
    else:raise AssertionError('publication failure did not propagate')
finally:addon.native_export._replace_published_file=replace
assert failed and snapshot=={p.relative_to(target).as_posix():p.read_bytes() for p in target.rglob('*') if p.is_file()}
report=dict(passed=True,firstSeconds=first_seconds,updateSeconds=update_seconds,
    characterPreserved=True,otherEffectsPreserved=True,partBound=True,stateAndNestedModsPreserved=True,
    midPublicationRollback=True)
(output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print('PASS EFFECT CHARACTER UPDATE '+json.dumps(report),flush=True)
package.unregister()

"""Export a child effect, then re-export its parent character without losing it."""
import bpy
import importlib
import json
from pathlib import Path
import shutil
import sys

addon_dir,scene,character,output=map(Path,sys.argv[sys.argv.index('--')+1:])
sys.path.insert(0,str(addon_dir.resolve().parent))
package=importlib.import_module(addon_dir.name);package.register()
addon=package.eiem_blender_addon
bpy.ops.wm.open_mainfile(filepath=str(scene),load_ui=False,use_scripts=False)
assert not output.exists(),'Use a fresh isolated output directory'
output.mkdir(parents=True)
parent=output/character.name;shutil.copytree(character,parent)
root=next(o for o in bpy.context.scene.objects if o.get('eff_effect_document'))
root.eff_effect.preview=False
addon.effect_editor.effect_cache=lambda:str(output/'cache')


def select(*objects):
    for obj in bpy.context.selected_objects:obj.select_set(False)
    for obj in objects:obj.hide_set(False);obj.select_set(True)
    bpy.context.view_layer.objects.active=objects[-1]


select(root);bpy.context.scene.eiem_export_mod_name='petals'
assert bpy.ops.eiem.export_package('EXEC_DEFAULT',directory=str(parent))=={'FINISHED'}
child=parent/'petals'
assert (child/'mod.ini').is_file()
before={p.relative_to(child).as_posix():p.read_bytes() for p in child.rglob('*') if p.is_file()}
modules,_=addon.native_export.pack_modules()
assert len(modules['ini_reader'].read_package(child)['effects'])==1
select(root.eff_effect.owner,root);bpy.context.scene.eiem_export_mod_name=parent.name
assert bpy.ops.eiem.export_package('EXEC_DEFAULT',directory=str(output),lod_all=False,lod0=True)=={'FINISHED'}
assert before=={p.relative_to(child).as_posix():p.read_bytes() for p in child.rglob('*') if p.is_file()}
read=modules['ini_reader'].read_package(parent)
assert read['parts'] and read['effects'] and (parent/'compiled.bin').is_file()
assert not any(name.startswith('petals/') for name in read['_files'])
report=dict(passed=True,nestedEffectExported=True,parentNativeReexported=True,
    childBytesPreserved=True,independentResourceScope=True)
(output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print('PASS NESTED BLENDER EXPORT '+json.dumps(report),flush=True)
package.unregister()

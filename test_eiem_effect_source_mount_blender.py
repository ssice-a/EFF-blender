"""Validate effect attachment identity against a real source Mesh/skeleton."""
import importlib,json,sys
from pathlib import Path
import bpy
addon_path,author,effect,build,output=sys.argv[sys.argv.index('--')+1:]
sys.path.insert(0,str(Path(addon_path).resolve().parent))
addon=importlib.import_module(Path(addon_path).name);addon.register()
mod=addon.eiem_blender_addon;editor=mod.effect_editor
mod.import_package(Path(author))
meshes=[o for o in bpy.data.objects if o.type=='MESH' and o.get('eiem_author_package')]
owner=next(o for o in meshes if 'body_01_lod0' in o.data.get('eiem_target_asset',''))
rig=next(m.object for m in owner.modifiers if m.type=='ARMATURE')
head=next((b for b in rig.data.bones if b.get('eiem_path','').endswith('/Bip001_Head')),None)
assert head,'source lacks expected complete head skeleton'
root=editor.import_source(bpy.context,Path(effect));root.eff_effect.owner=owner;root.eff_effect.bone=head.name;root.eff_effect.build_path=build
for key in ('F7','CTRL+J'):
    item=root.eff_effect.toggle_keys.add();item.key=key
row=editor.attachment(root)
assert row['part']=='' and row['bone'] and row['bones'] and row['rendererPath']
assert row['keys']==['F7','CTRL+J']
assert row['bones']==json.loads(owner['eiem_bone_paths_json']),'source skeleton paths disagree with regular author export'
print('MOUNT',row['targetAsset'],row['bone'],'palette',len(row['bones']),row['rendererPath'])
modules,_=mod.native_export.pack_modules();codec=modules['effect_native']
native=codec.read_native_build(Path(build))
assert native['rootName'] and native['bundles']
report=dict(attachment=row,nativeKey=native['key'],nativeBundles=len(native['bundles']))
Path(output).with_suffix('.attachment.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print('PASS real source -> complete skeleton mount -> typed Format 3 attachment input')
addon.unregister()

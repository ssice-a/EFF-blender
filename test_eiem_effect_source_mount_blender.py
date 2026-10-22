"""Validate standalone export against a real source Mesh/complete skeleton."""
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
modules,_=mod.native_export.pack_modules();codec=importlib.import_module(modules['ini_package'].__package__+'.runtime_controls')
codec.publish_effect_mod(Path(output),'Standalone effect source proof',[row])
result=modules['ini_reader'].read_package(Path(output));assert len(result['effects'])==1 and not result['parts']
bindings=[binding for binding in result['keyBindings'] if binding['variable']=='$fx_'+root['eff_effect_id']]
assert {binding['key'] for binding in bindings}=={'F7','CTRL+J'}
assert all(binding['type']=='cycle' and binding['values']==[0,1] for binding in bindings)
Path(output).with_suffix('.attachment.json').write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf-8')
print('PASS real source -> complete skeleton mount -> independent effect Mod -> strict reader')
addon.unregister()

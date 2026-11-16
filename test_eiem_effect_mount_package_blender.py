"""Real source -> standalone Mod -> editable Blender import, with animated Empty."""
import importlib
import json
import sys
from pathlib import Path
import bpy

addon_path,compiler,source,destination=map(Path,sys.argv[sys.argv.index('--')+1:])
sys.path.insert(0,str(addon_path.parent));addon=importlib.import_module(addon_path.name);addon.register()
runtime=addon.eiem_blender_addon;editor=runtime.effect_editor;animation=runtime.shape_animation
root=editor.import_source(bpy.context,str(source));root.eff_effect.preview=False
root.eff_effect.compiler=str(compiler)
root['eff_effect_attachment']=json.dumps(dict(targetPrefab='assets/game/weapon.prefab',
    rendererPath='Weapon/Renderer',targetPath='assets/game/weapon.mesh',targetAsset='Weapon',bones=[]))
pivot=bpy.data.objects.new('Author Orbit',None);bpy.context.scene.collection.objects.link(pivot)
pivot.rotation_mode='XYZ'
for frame,angle in ((1,0),(31,3.14159265),(61,6.2831853)):
    pivot.rotation_euler.z=angle;pivot.keyframe_insert('rotation_euler',frame=frame)
clip=animation.create_group([pivot]);clip.autoplay=True;clip.hotkey='NUMPAD5'
root.eff_effect.mount=pivot
editor.ExportBatch([root],destination).run()
modules,_=runtime.native_export.pack_modules();manifest=modules['author_format3'].read_format3_directory(destination)
assert manifest['modType']=='effect' and any(p.endswith('.ab') for p in manifest['_files'])
runtime.import_package(destination)
roots=[o for o in bpy.data.objects if o.get('eff_effect_document') and o!=root]
assert len(roots)==1 and roots[0].eff_effect.mount
restored=roots[0].eff_effect.mount
assert restored.animation_data and restored.eiem_bone_animations
assert restored.eiem_bone_animations[0].autoplay
print('PASS real EFFFX -> standalone Mod animated Empty -> editable author import:',destination)

"""Deleting an emitter Empty synchronizes native topology and clears its preview."""
import importlib
import json
import sys
from pathlib import Path

import bpy

addon_path,source=sys.argv[sys.argv.index('--')+1:]
sys.path.insert(0,str(Path(addon_path).resolve().parent))
addon=importlib.import_module(Path(addon_path).name);addon.register()
editor=addon.eiem_blender_addon.effect_editor
root=editor.import_source(bpy.context,source)
edit=editor.synchronize(root)
template=next(o for o in editor.objects(root) if any(
    edit.objects[i]['classId']==198 for i in json.loads(o.get('eff_effect_components','[]'))))
assert bpy.ops.eiem.effect_emitter_add(template=template['eff_effect_node'],name='删除验证发射器')=={'FINISHED'}
emitter=bpy.context.object;identity=emitter['eff_effect_node']
native_ids={identity,*json.loads(emitter['eff_effect_components'])}
edit=editor.synchronize(root)
particle_id=next(i for i in json.loads(emitter['eff_effect_components']) if edit.objects[i]['classId']==198)
curve_paths=editor.curve_editor.available(edit,particle_id)
assert curve_paths
editor.curve_editor.create_projection(bpy.context,emitter,particle_id,curve_paths[0])

preview=bpy.data.objects.new('删除验证预览',None)
editor.preview._collection(root).objects.link(preview);preview.parent=emitter
preview['eff_preview']=True;preview['eff_preview_key']=root['eff_effect_id']+identity+'shape'

bpy.data.objects.remove(emitter,do_unlink=True)
edit=editor.synchronize(root)
assert not native_ids.intersection(edit.objects)
assert not any(obj.get('eff_effect_node')==identity for obj in bpy.data.objects)
assert not any(obj.get('eff_preview') and obj.get('eff_preview_key','').startswith(root['eff_effect_id']+identity)
               for obj in bpy.data.objects)
assert identity not in edit.state.get('removed_emitters',[])
assert not editor.curve_editor.helpers(root)

# The properties-panel remove action must perform the same cleanup immediately.
bpy.context.view_layer.objects.active=root;root.select_set(True)
assert bpy.ops.eiem.effect_emitter_add(template=template['eff_effect_node'],name='面板删除验证')=={'FINISHED'}
panel_emitter=bpy.context.object;panel_identity=panel_emitter['eff_effect_node']
panel_native_ids={panel_identity,*json.loads(panel_emitter['eff_effect_components'])}
edit=editor.synchronize(root)
panel_particle_id=next(i for i in json.loads(panel_emitter['eff_effect_components']) if edit.objects[i]['classId']==198)
panel_curve_path=editor.curve_editor.available(edit,panel_particle_id)[0]
editor.curve_editor.create_projection(bpy.context,panel_emitter,panel_particle_id,panel_curve_path)
panel_preview=bpy.data.objects.new('面板删除验证预览',None)
editor.preview._collection(root).objects.link(panel_preview);panel_preview.parent=panel_emitter
panel_preview['eff_preview']=True;panel_preview['eff_preview_key']=root['eff_effect_id']+panel_identity+'shape'
assert bpy.ops.eiem.effect_emitter_remove(object_name=panel_emitter.name)=={'FINISHED'}
edit=editor.synchronize(root)
assert not panel_native_ids.intersection(edit.objects)
assert not any(helper.get('eff_curve_target')==panel_emitter for helper in editor.curve_editor.helpers(root))
assert not any(obj.get('eff_preview') and obj.get('eff_preview_key','').startswith(root['eff_effect_id']+panel_identity)
               for obj in bpy.data.objects)
print('PASS deleting an emitter Empty or using the panel removes native topology, preview and curve editor')
addon.unregister()

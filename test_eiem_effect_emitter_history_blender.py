"""Emitter snapshots undo/redo in Blender preserve the authored native state."""
import copy
import importlib
import json
from pathlib import Path
import sys

import bpy

addon_path, source, output = sys.argv[sys.argv.index('--')+1:]
sys.path.insert(0,str(Path(addon_path).resolve().parent))
addon=importlib.import_module(Path(addon_path).name);addon.register()
editor=addon.eiem_blender_addon.effect_editor
root=editor.import_source(bpy.context,source)
root_name=root.name
template=next(o for o in editor.objects(root) if any(editor.editor(root).objects[i]['classId']==198 for i in json.loads(o.get('eff_effect_components','[]'))))
template_node=template['eff_effect_node']
baseline=copy.deepcopy(editor.assets.export_document(root,editor.synchronize(root)))
bpy.context.preferences.edit.use_global_undo=True
bpy.ops.ed.undo_push(message='before adding emitter')
assert bpy.ops.eiem.effect_emitter_add(template=template_node,name='撤销验证发射器')=={'FINISHED'}
clone=bpy.context.object;clone_name=clone.name;clone_node=clone['eff_effect_node']
edit=editor.synchronize(root)
transform=next(i for i in json.loads(clone['eff_effect_components']) if edit.objects[i]['classId']==4)
assert clone.parent==template.parent
added=copy.deepcopy(editor.assets.export_document(root,edit))
editor.store(root,edit)
bpy.ops.ed.undo_push(message='after adding emitter')
assert bpy.ops.ed.undo()=={'FINISHED'}
root=bpy.data.objects[root_name]
assert bpy.data.objects.get(clone_name) is None
assert editor.assets.export_document(root,editor.synchronize(root))==baseline
assert bpy.ops.ed.redo()=={'FINISHED'}
root=bpy.data.objects[root_name];clone=bpy.data.objects[clone_name]
assert editor.assets.export_document(root,editor.synchronize(root))==added
bpy.context.view_layer.objects.active=clone
assert bpy.ops.eiem.effect_emitter_remove(object_name=clone_name)=={'FINISHED'}
bpy.ops.ed.undo_push(message='after removing emitter')
assert bpy.ops.ed.undo()=={'FINISHED'}
root=bpy.data.objects[root_name]
assert bpy.data.objects[clone_name]['eff_effect_node']==clone_node
assert editor.assets.export_document(root,editor.synchronize(root))==added
assert bpy.ops.ed.redo()=={'FINISHED'}
root=bpy.data.objects[root_name]
assert bpy.data.objects.get(clone_name) is None
assert editor.assets.export_document(root,editor.synchronize(root))==baseline

# Publish an addition without source removals for a separate native build.
bpy.context.view_layer.objects.active=root
assert bpy.ops.eiem.effect_emitter_add(template=template_node,name='撤销验证发射器')=={'FINISHED'}
editor.assets.write_document(root,editor.synchronize(root),Path(output)/'history-emitter.effedit.json')
# Deleting every emitter must still leave source templates available to add.
emitter_names=[obj.name for obj in editor.objects(root) if any(editor.editor(root).objects[i]['classId']==198 for i in json.loads(obj.get('eff_effect_components','[]')))]
for name in emitter_names:
 assert bpy.ops.eiem.effect_emitter_remove(object_name=name)=={'FINISHED'}
assert all(not any(editor.editor(root).objects[i]['classId']==198 for i in json.loads(obj.get('eff_effect_components','[]'))) for obj in editor.objects(root))
editor.assets.write_document(root,editor.synchronize(root),Path(output)/'empty-emitters.effedit.json')
assert template_node in {r[0] for r in editor.emitter_items(None,bpy.context)}
assert bpy.ops.eiem.effect_emitter_add(template=template_node,name='删除后重建')=={'FINISHED'}
assert bpy.context.object.name=='删除后重建'
editor.assets.write_document(root,editor.synchronize(root),Path(output)/'restored-emitter.effedit.json')
print('PASS add/remove undo/redo native state and preview snapshots')
addon.unregister()

"""Real author state -> selected emitter -> native Mod, plus playback invalidation."""
import copy
import importlib
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch
import bpy

addon_path, state_path, output, compiler = map(Path, sys.argv[sys.argv.index('--')+1:])
sys.path.insert(0,str(addon_path.resolve().parent))
addon=importlib.import_module(addon_path.name);addon.register()
fx=addon.eiem_blender_addon.effect_editor
state=json.loads(state_path.read_text('utf-8-sig'))
root=fx.import_workspace(bpy.context,state)
root.eff_effect.compiler=str(compiler.resolve())
root.eff_effect.usage='REPLACE_GAME_PREFAB'
root.eff_effect.replacement_prefab=state['catalog']['prefab']
root.eff_effect.preview=False
emitter=next(o for o in root.children_recursive if o.get('eff_effect_node') and
    any(fx.editor(root).objects[i]['classId']==198 for i in json.loads(o.get('eff_effect_components','[]'))))
for o in bpy.context.selected_objects:o.select_set(False)
emitter.select_set(True);bpy.context.view_layer.objects.active=emitter
selections=addon.eiem_blender_addon.selected_export_resources()[2]
assert len(selections)==1 and selections[0].emitters==(emitter['eff_effect_node'],)
baseline=copy.deepcopy(fx.synchronize(root).state)
output.mkdir(parents=True,exist_ok=True)
fx.effect_cache=lambda:str(output/'cache')
job=fx.ExportBatch(selections,output/'mod-single')
assert job.requests[0].payload.get('emitterScope')
job.run()
warm_started=time.perf_counter()
warm_job=fx.ExportBatch(selections,output/'mod-single')
warm_job.run()
assert all(request.reused for request in warm_job.requests),'unchanged selected export missed the native cache'
warm_seconds=time.perf_counter()-warm_started
print('Selected emitter unchanged warm export: %.3fs'%warm_seconds)
native=fx.effect_codec().read_native_build(job.requests[0].output)
edit=fx.synchronize(root)
scope=set(job.requests[0].payload['emitterScope'])
expected=sum(edit.objects[i]['classId']==198 for n in edit.state['catalog']['hierarchy']
             if n['identity'] in scope for i in n['components'])
assert 0<expected<len([o for o in edit.objects.values() if o['classId']==198])
assert native['particles']==expected,(native,expected)
assert edit.state==baseline,'selection changed author state'
(output/'selection-edits.json').write_text(fx.document.dumps(job.requests[0].portable_payload),'utf-8')
(output/'source.txt').write_text(edit.state['source'],'utf-8')
assert fx.export_selections([root,emitter])[0].emitters is None
assert fx.export_selections([emitter,root])[0].emitters is None

# Exercise the production frame handler: a pending author structure change
# must allow node rebuilding; the following unchanged frame reuses materials.
root.eff_effect.preview=True
session=fx.preview._session(root);session.accept(edit,'before')
fx.preview._pending_updates[root.as_pointer()]=(0,set())
seen=[]
with patch.object(fx.preview,'author_signature',return_value='after'), \
     patch.object(fx.preview,'_sync',return_value=edit), \
     patch.object(fx.preview,'update',side_effect=lambda *a:seen.append(fx.preview._reuse_materials)):
    fx.preview.frame(bpy.context.scene)
    fx.preview.frame(bpy.context.scene)
assert seen==[False,True],seen
assert root.as_pointer() not in fx.preview._pending_updates

# Reimport the actual published author resource, not the in-memory workspace.
# Its selected scope must remain editable and survive another native export.
root.eff_effect.preview=False
format_io=addon.eiem_blender_addon.format_io
author=next((output/'mod-single'/'effects'/'author').glob('*.effres'))
snapshot=format_io.read_format3_effect_author_resource(author)
restored,_,_=format_io.materialize_format3_effect_author_resource(
    author,snapshot['id'],snapshot['file'],output/'import-cache')
restored_root=fx.import_workspace(bpy.context,restored)
restored_root.eff_effect.compiler=str(compiler.resolve())
restored_root.eff_effect.usage='REPLACE_GAME_PREFAB'
restored_root.eff_effect.replacement_prefab=state['catalog']['prefab']
restored_root.eff_effect.preview=False
restored_edit=fx.synchronize(restored_root)
assert sum(restored_edit.objects[i]['classId']==198 for n in restored_edit.state['catalog']['hierarchy']
           for i in n['components'])==expected
roundtrip=fx.ExportBatch([restored_root],output/'mod-roundtrip')
roundtrip.run()
assert fx.effect_codec().read_native_build(roundtrip.requests[0].output)['particles']==expected

# A selected child may need an authored emitter parent only for placement.
# Compile that parent as a scaffold, without its own PS/material dependencies.
scope_api=importlib.import_module(addon.__name__+'.eiem_effect_scope')
topology=importlib.import_module(addon.__name__+'.eiem_effect_topology')
nested=fx.document.Editor(copy.deepcopy(restored_edit.state))
parent=topology.duplicate(nested,next(iter(scope)),'Placement parent')
child=topology.duplicate(nested,parent['identity'],'Selected child')
old_parent=next(n for n in nested.state['catalog']['hierarchy'] if n['transform']==child['parent'])
old_parent['children'].remove(child['transform'])
nested.value(old_parent['transform'])['m_Children'][:]=[
    p for p in nested.value(old_parent['transform'])['m_Children']
    if p['m_PathID']!=int(child['transform'].rsplit(':',1)[1])]
child['parent']=parent['transform'];parent['children'].append(child['transform'])
nested.value(child['transform'])['m_Father']['m_PathID']=int(parent['transform'].rsplit(':',1)[1])
nested.value(parent['transform'])['m_Children'].append(dict(m_FileID=0,m_PathID=int(child['transform'].rsplit(':',1)[1])))
requested=nested.export();requested['assets']=copy.deepcopy(nested.state.get('author_assets',[]))
requested=scope_api.payload(nested,requested,[child['identity']])
rebuilt_state=fx.document.load_source(nested.state['source'])
fx.document.apply_edit(rebuilt_state,requested)
fx.document.Editor(rebuilt_state).export()
nested_job=fx.effect_publish.Build(requested,compiler,output/'nested-cache','nested-selection',fx.effect_codec().read_native_build)
nested_job.run()
assert fx.effect_codec().read_native_build(nested_job.output)['particles']==1
(output/'nested-selection-edits.json').write_text(fx.document.dumps(nested_job.portable_payload),'utf-8')
addon.unregister()
print('PASS selected-emitter production export/reimport/re-export, unchanged author scene, root precedence and playback structure invalidation')

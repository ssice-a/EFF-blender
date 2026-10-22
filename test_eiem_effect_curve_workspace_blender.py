"""One emitter, one native Action: add/remove attributes without losing values."""
import copy
import importlib
import json
import math
import sys
from pathlib import Path

import bpy

addon_path,source,output=sys.argv[sys.argv.index('--')+1:]
output=Path(output);output.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(Path(addon_path).resolve().parent))
addon=importlib.import_module(Path(addon_path).name);addon.register()
editor=addon.eiem_blender_addon.effect_editor;graph=editor.curve_editor
root=editor.import_source(bpy.context,source);obj=bpy.data.objects['leaves_01']
identity=next(i for i in json.loads(obj['eff_effect_components']) if editor.editor(root).objects[i]['classId']==198)
baseline=copy.deepcopy(editor.assets.export_document(root,editor.synchronize(root)))
helper=graph.create_projection(bpy.context,obj,identity,None)
active=graph.active_paths(obj,editor.editor(root),identity)
assert len(active)>3 and len(graph.metadata(helper)['paths'])==len(active)
assert editor.assets.export_document(root,editor.synchronize(root))==baseline
assert graph.create_projection(bpy.context,obj,identity,['VelocityModule','x'],True)==helper
assert graph.create_projection(bpy.context,obj,identity,['RotationModule','curve'])==helper
assert len(graph.helpers(root))==1
assert len({r['rna_path'] for r in graph.metadata(helper)['rows']})==len(graph.metadata(helper)['rows'])

def values():return copy.deepcopy(editor.assets.export_document(root,editor.synchronize(root)))

def sample(path,branch='maxCurve'):
    data=editor.editor(root).value(identity,path)
    return [graph.curves.evaluate(data[branch]['m_Curve'],t/10)*data['scalar'] for t in range(11)]

# Removing a row or an entire native channel is display management, never erasure.
path=['VelocityModule','x'];before=values();graph.remove_attribute(helper,path)
assert values()==before and path not in graph.metadata(helper)['paths']
graph.add_attribute(helper,path);assert values()==before
rows=graph.metadata(helper)['rows'];r=next(r for r in rows if r['attribute']==path)
f=next(f for f in graph.fcurves(helper) if f.data_path==r['rna_path'])
graph.fcurves(helper).remove(f)
assert values()==before and path not in graph.metadata(helper)['paths']
graph.add_attribute(helper,path);assert values()==before

# Constant -> flat native curve preserves negative, zero and degree values.
for path,number in [(['InitialModule','startSpeed'],-2.5),(['VelocityModule','y'],0.0),(['InitialModule','startRotationX'],math.pi/4)]:
    edit=editor.synchronize(root);data=edit.value(identity,path);data['minMaxState']=0;data['scalar']=number
    editor.populate(obj,edit,json.loads(obj['eff_effect_components']));editor.store(root,edit)
    graph.add_attribute(helper,path)
    assert all(abs(v-number)<1e-7 for v in sample(path))
    assert editor.editor(root).value(identity,path)['minMaxState']==1
assert editor.editor(root).value(identity,['InitialModule','rotation3D'])

# Random constant endpoints become two flat curves without destroying their range.
path=['InitialModule','startSize'];edit=editor.synchronize(root);data=edit.value(identity,path)
data.update(minMaxState=3,minScalar=.3,scalar=.8)
editor.populate(obj,edit,json.loads(obj['eff_effect_components']));editor.store(root,edit)
graph.add_attribute(helper,path)
assert all(abs(v-.3)<1e-7 for v in sample(path,'minCurve'))
assert all(abs(v-.8)<1e-7 for v in sample(path,'maxCurve'))

# Adding a dormant module enables the module, preserves all other modules,
# and does not reset an existing native key's UI selection or handle modes.
record=graph.metadata(helper);first=record['rows'][0]
f=next(f for f in graph.fcurves(helper) if f.data_path==first['rna_path'])
f.keyframe_points[0].select_control_point=True;f.hide=True
path=['ForceModule','x'];unrelated=copy.deepcopy(editor.editor(root).value(identity,['InitialModule']))
graph.add_attribute(helper,path)
assert editor.editor(root).value(identity,['ForceModule','enabled'])
assert editor.editor(root).value(identity,['InitialModule'])==unrelated
assert f.keyframe_points[0].select_control_point and f.hide
graph.add_attribute(helper,['SizeModule','y'])
assert editor.editor(root).value(identity,['SizeModule','separateAxes'])

# The editing list survives closing, reopening and saving a blend.
expected_paths=copy.deepcopy(graph.metadata(helper)['paths']);before=values()
graph.close_projection(helper)
helper=graph.create_projection(bpy.context,obj,identity,None)
assert graph.metadata(helper)['paths']==expected_paths and values()==before
editor.assets.write_document(root,editor.synchronize(root),output/'workspace.effedit.json')
bpy.ops.wm.save_as_mainfile(filepath=str(output/'workspace.blend'))
bpy.ops.wm.open_mainfile(filepath=str(output/'workspace.blend'))
root=next(o for o in bpy.data.objects if o.get('eff_effect_document'));obj=bpy.data.objects['leaves_01'];helper=graph.helpers(root)[0]
assert graph.metadata(helper)['paths']==expected_paths and values()==before
for h in list(graph.helpers(root)):graph.close_projection(h)
addon.unregister()
print('PASS unified curve workspace: automatic collection, one helper, unique channels, remove/re-add and native channel deletion preserve source, zero/negative/degrees/random conversion, enable modules and axes, untouched selection/visibility, save/reopen')

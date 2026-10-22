"""Real Blender F-Curves: lossless native binding, export, history and lifecycle."""
import copy
import importlib
import json
import math
import statistics
import sys
import time
from pathlib import Path

import bpy

addon_path,source,output=sys.argv[sys.argv.index('--')+1:]
output=Path(output);output.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(Path(addon_path).resolve().parent))
addon=importlib.import_module(Path(addon_path).name);addon.register()
editor=addon.eiem_blender_addon.effect_editor;graph=editor.curve_editor
root=editor.import_source(bpy.context,source)
node=next(o for o in editor.objects(root) if o.name=='leaves_01')
identity=next(i for i in json.loads(node['eff_effect_components']) if editor.editor(root).objects[i]['classId']==198)
root_name,node_name=root.name,node.name
baseline=copy.deepcopy(editor.assets.export_document(root,editor.synchronize(root)))

# The emitter's existing animation and selection must not become the projection.
node['author_animation']=0.0;node.keyframe_insert(data_path='["author_animation"]',frame=3)
author_action=node.animation_data.action
author_action_name=author_action.name
selection=[o.name for o in bpy.context.selected_objects];active=bpy.context.object.name
base_counts=tuple(len(items) for items in (bpy.data.objects,bpy.data.actions,bpy.data.collections))
node[graph.PATHS]=json.dumps([['VelocityModule',k] for k in 'xyz'])
helper=graph.create_projection(bpy.context,node,identity,['VelocityModule','x'],True)
helper_name=helper.name
assert len(graph.fcurves(helper))==6
assert node.animation_data.action==author_action
assert helper.parent is None and helper not in editor.objects(root)
assert [o.name for o in bpy.context.selected_objects]==selection and bpy.context.object.name==active
assert editor.assets.export_document(root,editor.synchronize(root))==baseline

def sample_projection(helper):
    rows=graph.metadata(helper)['rows'];fs={f.data_path:f for f in graph.fcurves(helper)}
    for row in rows:
        f=fs[row['rna_path']]
        for i in range(41):
            expected=graph.curves.evaluate(row['data']['m_Curve'],i/40)*row['factor']
            assert abs(f.evaluate(i*2.5)-expected)<3e-5,(row['title'],i,f.evaluate(i*2.5),expected)

sample_projection(helper)
fs=list(graph.fcurves(helper));rows=graph.metadata(helper)['rows']
untouched=copy.deepcopy(rows[1]['data'])
f=fs[0]
# Insert an actual native key and edit independently weighted handles.
p=f.keyframe_points.insert(45,.35);p.handle_left_type='FREE';p.handle_right_type='FREE'
p.handle_left=(30,.1);p.handle_right=(75,.7);f.update()
editor.synchronize(root)
sample_projection(helper)
data=editor.editor(root).value(identity,rows[0]['path'])
assert len(data['m_Curve'])==3 and data['m_Curve'][1]['weightedMode']==3
assert editor.editor(root).value(identity,rows[1]['path'])==untouched
assert data['m_RotationOrder']==rows[0]['data']['m_RotationOrder']

# Linear interpolation changes adjacent native slopes, including the next key.
f.keyframe_points[0].interpolation='LINEAR';f.update()
editor.synchronize(root);sample_projection(helper)
f.keyframe_points[0].interpolation='BEZIER';f.update()
editor.synchronize(root);sample_projection(helper)
f.keyframe_points.remove(f.keyframe_points[1]);f.update()
editor.synchronize(root);sample_projection(helper)

# Source parameter changes refresh the view, including zero and negative factors.
scalar_key=editor.field_key(identity,['VelocityModule','x','scalar'])
for scalar in (0.0,-2.0,.5):
    node[scalar_key]=scalar;editor.synchronize(root);sample_projection(helper)
    assert graph.metadata(helper)['rows'][0]['zero']==(scalar==0)

# Invalid graph edits cannot be exported or overwrite the last valid native value.
valid=copy.deepcopy(editor.editor(root).value(identity,['VelocityModule','x']))
f=graph.fcurves(helper)[0];f.keyframe_points[0].interpolation='CONSTANT'
try:editor.synchronize(root);raise AssertionError('unsupported interpolation silently exported')
except ValueError as error:assert '贝塞尔' in str(error)
assert editor.editor(root).value(identity,['VelocityModule','x'])==valid
f.keyframe_points[0].interpolation='BEZIER';editor.synchronize(root)

# Zero-length weighted handles and large independent weights are represented.
record=graph.metadata(helper);rows=graph.channels(node,editor.editor(root),identity,['VelocityModule','x'],True)
keys=rows[0]['data']['m_Curve'];keys[0].update(weightedMode=2,outWeight=.9,outSlope=4)
keys[-1].update(weightedMode=1,inWeight=.9,inSlope=-2)
graph.apply_rows(node,editor.editor(root),identity,rows);graph.project(helper,rows,record)
sample_projection(helper)
editor.synchronize(root)

# Rotation joins the same emitter projection; storage remains radians.
rotation=graph.create_projection(bpy.context,node,identity,['RotationModule','curve'])
assert rotation==helper and len(graph.helpers(root))==1
rr=next(r for r in graph.metadata(helper)['rows'] if r['path']==['RotationModule','curve','minCurve'])
rp=next(f for f in graph.fcurves(rotation) if f.data_path==rr['rna_path']).keyframe_points[0]
left=list(rp.handle_left);right=list(rp.handle_right);dy=90-rp.co.y
rp.co.y=90;rp.handle_left=(left[0],left[1]+dy);rp.handle_right=(right[0],right[1]+dy)
editor.synchronize(root)
native=editor.editor(root).value(identity,['RotationModule','curve'])
assert abs(native['minCurve']['m_Curve'][0]['value']*native['scalar']-math.pi/2)<1e-6
sample_projection(rotation)

# Ordinary Blender undo/redo restores actual F-Curves and their author bindings.
editor.store(root,editor.synchronize(root));before=copy.deepcopy(editor.assets.export_document(root,editor.editor(root)))
bpy.context.preferences.edit.use_global_undo=True;bpy.ops.ed.undo_push(message='before native curve edit')
f=graph.fcurves(helper)[0];p=f.keyframe_points[0]
left=list(p.handle_left);right=list(p.handle_right);p.co.y+=.2
p.handle_left=(left[0],left[1]+.2);p.handle_right=(right[0],right[1]+.2)
editor.store(root,editor.synchronize(root));after=copy.deepcopy(editor.assets.export_document(root,editor.editor(root)))
bpy.ops.ed.undo_push(message='after native curve edit')
assert bpy.ops.ed.undo()=={'FINISHED'}
root=bpy.data.objects[root_name];node=bpy.data.objects[node_name]
assert editor.assets.export_document(root,editor.synchronize(root))==before
assert bpy.ops.ed.redo()=={'FINISHED'}
root=bpy.data.objects[root_name];node=bpy.data.objects[node_name];helper=bpy.data.objects[helper_name]
assert editor.assets.export_document(root,editor.synchronize(root))==after

# A rapid edit immediately followed by save/export must not wait for the UI timer.
f=graph.fcurves(helper)[0];p=f.keyframe_points[0];left=list(p.handle_left);right=list(p.handle_right)
p.co.y+=.1;p.handle_left=(left[0],left[1]+.1);p.handle_right=(right[0],right[1]+.1)
bpy.ops.wm.save_as_mainfile(filepath=str(output/'native-curves.blend'))
expected=copy.deepcopy(editor.assets.export_document(root,editor.synchronize(root)))
editor.assets.write_document(root,editor.synchronize(root),output/'curves.effedit.json')
bpy.ops.wm.open_mainfile(filepath=str(output/'native-curves.blend'))
root=bpy.data.objects[root_name];node=bpy.data.objects[node_name];helper=bpy.data.objects[helper_name]
assert editor.assets.export_document(root,editor.synchronize(root))==expected
sample_projection(helper)

# Time changes animate only helper values, never native author fields.
values=copy.deepcopy(editor.editor(root).value(identity))
for frame in (1,30,90):
    bpy.context.scene.frame_set(frame)
    assert editor.synchronize(root).value(identity)==values

# Repeated point drags use incremental author projection, preserving other edits.
editor.preview.live_update();samples=[];fields=node['eff_effect_fields']
for i in range(12):
    f=graph.fcurves(helper)[0];p=f.keyframe_points[0];left=list(p.handle_left);right=list(p.handle_right)
    p.co.y+=.001;p.handle_left=(left[0],left[1]+.001);p.handle_right=(right[0],right[1]+.001)
    started=time.perf_counter();graph.poll_updates();samples.append(time.perf_counter()-started)
    assert node['eff_effect_fields']==fields
assert not root.get('预览错误'),root.get('预览错误')
for h in list(graph.helpers(root)):graph.close_projection(h)
assert not graph.helpers(root)
assert tuple(len(items) for items in (bpy.data.objects,bpy.data.actions,bpy.data.collections))==base_counts
assert node.animation_data.action.name==author_action_name
print('PASS native Graph Editor binding: six F-Curves, independent weights, linear/add/delete, degrees/zero, source refresh, validation, undo/redo, immediate save/export, no author animation changes, cleanup')
print('NATIVE_CURVE_DRAG_MS median=%.2f max=%.2f'%(statistics.median(samples)*1000,max(samples)*1000))
addon.unregister()

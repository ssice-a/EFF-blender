"""An isolated real Graph Editor window; native G, undo, V, insert and delete."""
import copy
import importlib
import json
import sys
import traceback
from pathlib import Path

import bpy

addon_path,source,output=sys.argv[sys.argv.index('--')+1:]
output=Path(output);output.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(Path(addon_path).resolve().parent))
addon=importlib.import_module(Path(addon_path).name)
state={'step':-1}


def helper():return state['editor'].curve_editor.helpers(bpy.data.objects[state['root_name']])[0]


def override():
    window=state['graph_window'];area=next(a for a in window.screen.areas if a.type=='GRAPH_EDITOR')
    return bpy.context.temp_override(window=window,area=area,region=next(r for r in area.regions if r.type=='WINDOW'))


def select_first():
    fs=list(state['editor'].curve_editor.fcurves(helper()))
    row=next(r for r in state['editor'].curve_editor.metadata(helper())['rows'] if r['path']==['VelocityModule','x','minCurve'])
    target=next(f for f in fs if f.data_path==row['rna_path'])
    for f in fs:
        f.select=f==target
        for p in f.keyframe_points:p.select_control_point=False;p.select_left_handle=False;p.select_right_handle=False
    target.keyframe_points[0].select_control_point=True
    return target


def run():
    try:
        step=state['step']
        if step==-1:bpy.context.window.event_simulate(type='ESC',value='PRESS')
        elif step==0:
            addon.register();editor=addon.eiem_blender_addon.effect_editor;state['editor']=editor
            root=editor.import_source(bpy.context,source);state['root_name']=root.name
            node=bpy.data.objects['leaves_01'];state['node_name']=node.name
            state['identity']=next(i for i in json.loads(node['eff_effect_components']) if editor.editor(root).objects[i]['classId']==198)
            state['baseline']=copy.deepcopy(editor.assets.export_document(root,editor.synchronize(root)))
            state['original_window']=bpy.context.window
            state['selected']=[o.name for o in bpy.context.selected_objects];state['active']=bpy.context.object.name
            old=list(bpy.context.window_manager.windows)
            assert bpy.ops.eiem.effect_curve_edit(object_name=node.name,identity=state['identity'],path=json.dumps(['VelocityModule','x']),axes=True)=={'FINISHED'}
            state['graph_window']=next(w for w in bpy.context.window_manager.windows if w not in old)
        elif step==1:
            editor=state['editor'];root=bpy.data.objects[state['root_name']]
            assert not hasattr(editor.curve_editor,'_active') and not hasattr(editor.curve_editor,'thumbnail')
            assert editor.assets.export_document(root,editor.synchronize(root))==state['baseline']
            assert [o.name for o in bpy.context.selected_objects]==state['selected'] and bpy.context.object.name==state['active']
            with override():
                assert len(bpy.context.visible_fcurves)>6 and len(bpy.context.editable_fcurves)>6
                region=next(r for r in bpy.context.area.regions if r.type=='UI')
                if hasattr(region,'active_panel_category'):region.active_panel_category='EFF 曲线'
                f=select_first();p=f.keyframe_points[0];state['original_y']=p.co.y
                bpy.ops.ed.undo_push(message='before native graph editing')
                region=bpy.context.region;x,y=region.view2d.view_to_region(*p.co,clip=False)
                x+=region.x;y+=region.y;window=state['graph_window']
                bpy.ops.screen.screenshot(filepath=str(output/'native-graph-editor.png'))
                window.event_simulate(type='MOUSEMOVE',value='NOTHING',x=x,y=y)
                window.event_simulate(type='G',value='PRESS',x=x,y=y)
                window.event_simulate(type='Y',value='PRESS',x=x,y=y)
                window.event_simulate(type='MOUSEMOVE',value='NOTHING',x=x,y=y+40)
                window.event_simulate(type='LEFTMOUSE',value='PRESS',x=x,y=y+40)
                window.event_simulate(type='LEFTMOUSE',value='RELEASE',x=x,y=y+40)
        elif step==2:
            editor=state['editor'];root=bpy.data.objects[state['root_name']]
            f=select_first()
            assert abs(f.keyframe_points[0].co.y-state['original_y'])>.01
            state['edited']=copy.deepcopy(editor.assets.export_document(root,editor.synchronize(root)))
            assert state['edited']!=state['baseline']
            state['graph_window'].event_simulate(type='Z',value='PRESS',ctrl=True)
        elif step==3:
            editor=state['editor'];root=bpy.data.objects[state['root_name']]
            assert editor.assets.export_document(root,editor.synchronize(root))==state['baseline']
            state['graph_window'].event_simulate(type='Z',value='PRESS',ctrl=True,shift=True)
        elif step==4:
            editor=state['editor'];root=bpy.data.objects[state['root_name']]
            assert editor.assets.export_document(root,editor.synchronize(root))==state['edited']
            with override():
                count=len(bpy.context.window_manager.windows)
                assert bpy.ops.eiem.effect_curve_edit(object_name=state['node_name'])=={'FINISHED'}
                assert len(bpy.context.window_manager.windows)==count
                assert bpy.ops.eiem.effect_curve_add(attribute=json.dumps(['ForceModule','x']))=={'FINISHED'}
                assert bpy.ops.eiem.effect_curve_remove(path=json.dumps(['ForceModule','x']))=={'FINISHED'}
                assert bpy.ops.eiem.effect_curve_add(attribute=json.dumps(['InitialModule','startSpeed']))=={'FINISHED'}
                assert bpy.ops.eiem.effect_curve_constant(path=json.dumps(['InitialModule','startSpeed']),value=1.25)=={'FINISHED'}
                f=select_first()
                assert bpy.ops.graph.handle_type(type='AUTO_CLAMPED')=={'FINISHED'}
                assert f.keyframe_points[0].handle_right_type=='AUTO_CLAMPED'
                bpy.context.space_data.cursor_position_x=50;bpy.context.space_data.cursor_position_y=.15
                assert bpy.ops.graph.keyframe_insert(type='CURSOR_SEL')=={'FINISHED'}
                assert len(f.keyframe_points)==3
        elif step==5:
            editor=state['editor'];root=bpy.data.objects[state['root_name']]
            edit=editor.synchronize(root)
            row=next(r for r in editor.curve_editor.metadata(helper())['rows'] if r['path']==['VelocityModule','x','minCurve'])
            assert len(edit.value(state['identity'],row['path'])['m_Curve'])==3
            with override():
                f=select_first()
                for p in f.keyframe_points:p.select_control_point=False;p.select_left_handle=False;p.select_right_handle=False
                f.keyframe_points[1].select_control_point=True
                assert bpy.ops.graph.delete()=={'FINISHED'}
                assert len(f.keyframe_points)==2
        elif step==6:
            editor=state['editor'];root=bpy.data.objects[state['root_name']]
            with override():
                fs=list(editor.curve_editor.fcurves(helper()))
                rows=[r for r in editor.curve_editor.metadata(helper())['rows'] if r['attribute']==['VelocityModule','x']]
                selected={r['rna_path'] for r in rows};starts={}
                for f in fs:
                    f.select=f.data_path in selected
                    for p in f.keyframe_points:p.select_control_point=False;p.select_left_handle=False;p.select_right_handle=False
                    if f.select:f.keyframe_points[0].select_control_point=True;starts[f.data_path]=f.keyframe_points[0].co.y
                assert len(starts)==2
                assert bpy.ops.transform.translate(value=(0,.05,0))=={'FINISHED'}
                for f in fs:
                    if f.data_path in starts:assert abs(f.keyframe_points[0].co.y-starts[f.data_path]-.05)<1e-6
                assert bpy.ops.eiem.effect_curve_visibility()=={'FINISHED'}
                assert len(bpy.context.visible_fcurves)==2
            editor.synchronize(root)
            with override():bpy.ops.screen.screenshot(filepath=str(output/'native-graph-edited.png'))
            state['final']=copy.deepcopy(editor.assets.export_document(root,editor.synchronize(root)))
            with override():assert bpy.ops.eiem.effect_curve_add('INVOKE_DEFAULT')=={'FINISHED'}
        elif step==7:
            with override():bpy.ops.screen.screenshot(filepath=str(output/'add-attribute-search.png'))
            state['graph_window'].event_simulate(type='ESC',value='PRESS')
        elif step==8:
            with override():
                assert bpy.ops.eiem.effect_curve_visibility(all=True)=={'FINISHED'}
                assert len(bpy.context.visible_fcurves)>6
                assert bpy.ops.wm.window_close()=={'FINISHED'}
        elif step==9:
            editor=state['editor'];root=bpy.data.objects[state['root_name']]
            editor.curve_editor.cleanup_closed()
            assert not editor.curve_editor.helpers(root)
            assert editor.assets.export_document(root,editor.synchronize(root))==state['final']
            assert [o.name for o in bpy.context.selected_objects]==state['selected'] and bpy.context.object.name==state['active']
            addon.unregister()
            (output/'gui-result.json').write_text(json.dumps({'pass':True,'steps':10}),encoding='utf-8')
            print('PASS real native Graph Editor: shared emitter window, attribute add/remove/constant operators, multiple channels transform together, actual G Y mouse transform, native Ctrl+Z / redo, V auto handles, cursor insert/delete, window close cleanup, author selection preserved')
            bpy.ops.wm.quit_blender();return None
        state['step']+=1;return 1.0
    except Exception:
        error=traceback.format_exc();print(error)
        (output/'gui-result.json').write_text(json.dumps({'pass':False,'error':error}),encoding='utf-8')
        bpy.ops.wm.quit_blender();return None


bpy.app.timers.register(run,first_interval=1)

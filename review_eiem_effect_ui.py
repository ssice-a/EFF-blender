"""Render the actual Properties UI in an isolated factory Blender process."""
import importlib
import json
import sys
from pathlib import Path
import bpy

addon_path, source_root, output = sys.argv[sys.argv.index('--')+1:]
sys.path.insert(0,str(Path(addon_path).resolve().parent))
addon=importlib.import_module(Path(addon_path).name)
addon.register()
editor=addon.eiem_blender_addon.effect_editor
output=Path(output);output.mkdir(parents=True,exist_ok=True)
for obj in list(bpy.data.objects):bpy.data.objects.remove(obj,do_unlink=True)
roots=[]
for i,source in enumerate(sorted(Path(source_root).rglob('effect.json'))):
    root=editor.import_source(bpy.context,source.parent)
    root.location.x=i*24
    root.eff_effect.compiler=str(Path(addon_path).resolve().parents[1]/'tools/AnimeStudio/AnimeStudio.GUI/bin/Debug/net9.0-windows/AnimeStudio.GUI.exe')
    assert not editor.synchronize(root).export()['objects']
    roots.append(root)
scene=bpy.context.scene;scene.render.fps=30;scene.frame_set(151)
for root in roots:editor.preview.refresh(root,editor.synchronize(root))
assert all(not editor.synchronize(root).export()['objects'] for root in roots),'preview changed clean native values'
for o in bpy.context.selected_objects:o.select_set(False)
root=next(r for r in roots if 'pink' in r.name)
edit=editor.editor(root)
node=next(o for o in editor.objects(root) if any(edit.objects[i]['classId']==198 for i in json.loads(o.get('eff_effect_components','[]'))))
node.select_set(True);bpy.context.view_layer.objects.active=node
for area in bpy.context.screen.areas:
    if area.type=='PROPERTIES':area.spaces.active.context='DATA'
    if area.type=='VIEW_3D':
        area.spaces.active.shading.type='MATERIAL'
        area.spaces.active.region_3d.view_location=root.location
        area.spaces.active.region_3d.view_distance=22
bpy.ops.wm.save_as_mainfile(filepath=str(output/'effects-module-editor.blend'))
if bpy.app.background:
    print('PASS clean module review scene; source values unchanged')
    addon.unregister()
else:
    # Only this factory process changes its UI and quits; existing windows and
    # user projects are never opened, closed, saved, or used as test fixtures.
    area=next(a for a in bpy.context.screen.areas if a.type=='VIEW_3D')
    area.type='PROPERTIES'
    def prepare():
        area.spaces.active.context='DATA'
    bpy.app.timers.register(prepare,first_interval=1)
    def capture():
        bpy.ops.screen.screenshot(filepath=str(output/'effect-module-ui.png'))
        print('PASS actual UI screenshot',flush=True)
        bpy.ops.wm.quit_blender()
    bpy.app.timers.register(capture,first_interval=4)

"""Build a clean review scene and export real typed edits for reconstruction."""
import importlib,json,sys
from pathlib import Path
import bpy
from mathutils import Vector
addon_path,source_root,output=sys.argv[sys.argv.index('--')+1:]
sys.path.insert(0,str(Path(addon_path).resolve().parent))
addon=importlib.import_module(Path(addon_path).name);addon.register()
editor=addon.eiem_blender_addon.effect_editor
output=Path(output);output.mkdir(parents=True,exist_ok=True)
for obj in list(bpy.data.objects):bpy.data.objects.remove(obj,do_unlink=True)
scene=bpy.context.scene;scene.render.fps=30;scene.frame_end=300
scene.render.engine='CYCLES';scene.cycles.samples=16
scene.render.resolution_x=1100;scene.render.resolution_y=700;scene.render.resolution_percentage=100
scene.world.color=(.015,.015,.015)
scene.view_settings.view_transform='Standard'
roots=[]
for index,file in enumerate(sorted(Path(source_root).rglob('effect.json'))):
    root=editor.import_source(bpy.context,file.parent);roots.append(root)
    root.eff_effect.compiler=str(Path(addon_path).resolve().parents[1]/'tools/AnimeStudio/AnimeStudio.GUI/bin/Debug/net9.0-windows/AnimeStudio.GUI.exe')
    edit=editor.synchronize(root)
    assert not edit.export()['objects'],'clean source import changed native values'
    root.location.x=index*24
    slug=('lightpoints' if 'lightspot' in file.parent.name else 'pink-petals' if 'pink' in file.parent.name else 'pear-petals')
    root.eff_effect.build_path=str(output/'builds'/slug)
    # A separate copy exercises actual module addition, curve editing and burst growth.
    state=json.loads(editor.document.dumps(edit.state));changed=editor.document.Editor(state)
    identity=next(i for i,o in changed.objects.items() if o['classId']==198)
    changed.set_scalar(identity,['simulationSpeed'],1.1)
    changed.set_scalar(identity,['NoiseModule','enabled'],True)
    changed.array_change(identity,['EmissionModule','m_Bursts'],'ADD')
    editor.document.write_edits(state,output/(slug+'.effedit.json'))
    editor.document.write_edits(edit.state,output/(slug+'.clean.effedit.json'))
    for obj in editor.objects(root):
        if obj.get('eff_preview'):continue
        for field in json.loads(obj.get('eff_effect_fields','[]')):
            if field['kind']=='float':assert type(obj[field['key']]) is float
    print('REVIEW_SOURCE',slug,edit.state['source'])
scene.frame_set(151)
for root in roots:
    editor.preview.refresh(root,editor.synchronize(root))
    assert not root.get('预览错误'),root.get('预览错误')
    print('PREVIEW',root.name,sum(o.get('预览粒子数',0) for o in root.children_recursive),root.get('预览未还原模块',''))
camera=bpy.data.objects.new('Review Camera',bpy.data.cameras.new('Review Camera'));scene.collection.objects.link(camera);scene.camera=camera;camera.data.type='ORTHO';camera.data.ortho_scale=21
camera.location=(0,-21,8);camera.rotation_euler=(Vector((0,0,0))-camera.location).to_track_quat('-Z','Y').to_euler()
bpy.ops.object.light_add(type='AREA',location=(20,-5,15));bpy.context.object.data.energy=1800;bpy.context.object.data.shape='DISK';bpy.context.object.data.size=15
for root in roots:
    x=root.location.x;camera.location.x=x;camera.rotation_euler=(Vector((x,0,0))-camera.location).to_track_quat('-Z','Y').to_euler()
    scene.render.filepath=str(output/(('lightpoints' if 'lightspot' in root.name else 'pink-petals' if 'pink' in root.name else 'pear-petals')+'.png'))
    bpy.ops.render.render(write_still=True)
for obj in bpy.context.selected_objects:obj.select_set(False)
selected=next(r for r in roots if 'pink' in r.name);selected.select_set(True);bpy.context.view_layer.objects.active=selected
for screen in bpy.data.screens:
    for area in screen.areas:
        if area.type=='PROPERTIES':area.spaces.active.context='OBJECT'
        if area.type=='VIEW_3D':
            area.spaces.active.shading.type='MATERIAL'
            area.spaces.active.region_3d.view_location=selected.location
            area.spaces.active.region_3d.view_distance=23
bpy.ops.wm.save_as_mainfile(filepath=str(output/'effects-object-editor.blend'))
print('PASS clean review scene, rendered source assets, native edit manifests')
addon.unregister()

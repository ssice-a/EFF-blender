"""Factory scene only; verifies real Material Action slots and offline samples."""
import importlib,sys
from pathlib import Path
import bpy
sys.path.insert(0,str(Path(__file__).parent.parent))
addon=importlib.import_module('Blender.eiem_blender_addon');addon.register()
try:
    mesh=bpy.data.meshes.new('fixture');mesh.from_pydata([(0,0,0),(1,0,0),(0,1,0)],[],[(0,1,2)])
    obj=bpy.data.objects.new('fixture',mesh);bpy.context.collection.objects.link(obj);bpy.context.view_layer.objects.active=obj;obj.select_set(True)
    m=bpy.data.materials.new('fixture');obj.data.materials.append(m);m['eiem_source']='assets/test.mat';m['eiem_float._EmissionBrightness']='4'
    assert bpy.ops.eiem.material_animation(parameter='eiem_float._EmissionBrightness')=={'FINISHED'}
    clip=m.eiem_material_animations[0];clip.start=1;clip.end=31
    for frame,value in [(1,0),(16,1),(31,0)]:clip.value=value;clip.keyframe_insert('value',frame=frame)
    bpy.context.scene.render.fps=30
    rows=addon.material_animation.export_clips(m,'MaterialTest',addon)
    assert len(rows)==1 and rows[0]['duration']==1 and rows[0]['targets'][0]['samples'][0]==0
    assert abs(rows[0]['targets'][0]['samples'][30]-1)<1e-5
    clip.loop=False;clip.speed=2;rows=addon.material_animation.export_clips(m,'MaterialTest',addon);assert rows[0]['speed']==2 and not rows[0]['loop']
    print('PASS material animation: parameter picker, actual Material Action slot, keyframes, 60Hz samples and timing')
finally:addon.unregister()
assert not hasattr(bpy.types.Material,'eiem_material_animations')

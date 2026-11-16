"""Headless real Blender: shared Empty dependencies, unified actions, native compile, author roundtrip."""
import importlib
import json
import sys
from pathlib import Path
import bpy
from mathutils import Matrix

addon_path, compiler, report = map(Path,sys.argv[sys.argv.index('--')+1:])
sys.path.insert(0,str(addon_path.parent));addon=importlib.import_module(addon_path.name);addon.register()
runtime=addon.eiem_blender_addon
mounts=importlib.import_module(addon_path.name+'.eiem_effect_mounts')
animation=runtime.shape_animation
collection=bpy.context.scene.collection
bpy.context.scene.render.fps=30
def empty(name,parent=None):
    obj=bpy.data.objects.new(name,None);collection.objects.link(obj);obj.parent=parent;return obj
owner=bpy.data.objects.new('Weapon',bpy.data.meshes.new('Weapon'));collection.objects.link(owner)
pivot=empty('Orbit',owner);tip=empty('Tip',pivot)
for frame,x in ((1,0),(31,2),(61,0)):
    pivot.location.x=x;pivot.keyframe_insert('location',frame=frame)
clip=animation.create_group([pivot]);clip.autoplay=True;clip.hotkey='NUMPAD5';clip.sample_rate=2
roots=[empty('Effect A'),empty('Effect B')]
for root in roots:
    root.eff_effect.owner=owner;root.eff_effect.mount=tip;root.eff_effect.compiler=str(compiler)
assert mounts.dependencies(roots)=={pivot,tip}
rows=[{},{}];mounts.assemble(roots,rows,animation,Matrix.Identity(4))
assert rows[0]['authorHierarchy']==rows[1]['authorHierarchy']
assert len(rows[0]['authorHierarchy']['nodes'])==2
tracks=rows[0]['authorHierarchy']['animations'][0]['tracks']
position=next(t for t in tracks if t['kind']=='Position')['samples']
assert len(position)==5 and abs(position[2][0]-2)<1e-5,position
mounts.compile_clips(roots,rows)
assert rows[0]['_mount_native'][0]['data']==rows[1]['_mount_native'][0]['data']
assert len(rows[0]['_mount_native'][0]['data'])>100
cache={};restored=[empty('Restored A'),empty('Restored B')]
for root,row in zip(restored,rows):
    root.eff_effect.owner=owner;mounts.restore(root,row,cache,collection,Matrix.Identity(4))
assert restored[0].eff_effect.mount==restored[1].eff_effect.mount
assert len(cache)==1
graph2=mounts.graphs(restored,animation,Matrix.Identity(4))
out=graph2[restored[0].eff_effect.mount][0]
assert out['animations'][0]['tracks']==tracks,(out['animations'][0]['tracks'],tracks)
bad=empty('Source emitter');bad['eff_effect_node']='source:1';assert not mounts.eligible(bad)
Path(report).write_text(json.dumps(dict(status='PASS',shared=True,nodes=2,samples=len(position),
    nativeBytes=len(rows[0]['_mount_native'][0]['data']),editableRoundtrip=True)),encoding='utf-8')
print('PASS Empty mount dependencies, shared timeline, production native compiler and editable roundtrip')

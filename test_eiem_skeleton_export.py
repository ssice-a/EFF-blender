"""New bones through Blender edit, saved project, Mesh palette and Skeleton v2."""
import configparser
import importlib.util
import json
import sys
import types
import zlib
from pathlib import Path

import bpy
from mathutils import Matrix, Quaternion, Vector

addon_path, output = map(Path, sys.argv[sys.argv.index('--') + 1:])
package=types.ModuleType('eiem_skeleton_package')
package.__path__=[str(addon_path.parent)]
sys.modules[package.__name__]=package
spec=importlib.util.spec_from_file_location('eiem_skeleton_test',addon_path)
addon=importlib.util.module_from_spec(spec)
addon.__package__=package.__name__
sys.modules[spec.name] = addon
spec.loader.exec_module(addon); addon.register()

def flat(m): return [m[r][c] for c in range(4) for r in range(4)]
def matrix(record):
    p,q,s=record[2:]
    return Matrix.LocRotScale(Vector(p),Quaternion((q[3],q[0],q[1],q[2])),Vector(s))
def close(a,b): return max(abs(a[r][c]-b[r][c]) for r in range(4) for c in range(4))<1e-5

# Actual offline packages use an empty resource-root path. Source scale must
# survive; Blender bone displays cannot represent it directly.
nodes=[('',-1,(0,0,0),(0,0,0,1),(1,1,1)),
       ('Rig',0,(.3,1,.2),(0,0,.6,.8),(2,2,2)),
       ('Rig/Unused',1,(0,1,0),(0,0,0,1),(1,1,1))]
rig=addon.make_armature('SkeletonShared',{'coordinate':'unity-y-up-left-handed','nodes':nodes},bpy.context.scene.collection)
root_pose=flat(Matrix.Identity(4)); unused_pose=flat((matrix(nodes[1])@matrix(nodes[2])).inverted())
obj_names=[]
def mesh(name):
    data=bpy.data.meshes.new(name); data.from_pydata([(0,0,0),(1,0,0),(0,1,0)],[],[(0,1,2)])
    data['eiem_section']='Mesh'+name; data['eiem_asset']=name; data['eiem_source']='assets/test/'+name+'.asset'
    data['eiem_coordinate_space']='unity-y-up-left-handed'
    obj=bpy.data.objects.new(name,data); bpy.context.scene.collection.objects.link(obj)
    obj.modifiers.new('Skin','ARMATURE').object=rig; obj['eiem_render_section']='Render'+name
    obj['eiem_bone_paths_json']=json.dumps(['','Rig/Unused']); obj['eiem_bone_palette_json']=json.dumps([0,2])
    obj['eiem_bindposes_json']=json.dumps([root_pose,unused_pose]); obj['eiem_bone_hashes_json']=json.dumps([0,zlib.crc32(b'Rig/Unused')])
    for name in ('root','Unused','Extra','Tip'): obj.vertex_groups.new(name=name)
    obj.vertex_groups['Extra'].add([0,1],1,'REPLACE'); obj.vertex_groups['Tip'].add([2],1,'REPLACE')
    obj_names.append(obj.name); return obj

# Create real new bones, not copies of a source bone's identity properties.
bpy.context.view_layer.objects.active=rig; rig.select_set(True); bpy.ops.object.mode_set(mode='EDIT')
parent=rig.data.edit_bones['Rig']
extra=rig.data.edit_bones.new('Extra'); extra.head=(.4,.2,1.5); extra.tail=(.6,.5,1.8); extra.roll=.3; extra.parent=parent
tip=rig.data.edit_bones.new('Tip'); tip.head=extra.tail; tip.tail=(.7,.8,2.1); tip.roll=-.2; tip.parent=extra
bpy.ops.object.mode_set(mode='OBJECT')
first,second=mesh('First'),mesh('Second')
# The authored Rig keeps the source-Mesh donor catalog separately from the
# target object.  Extra/Tip are deliberately absent from First's local palette
# but are valid slots on a sibling native Mesh.
rig.data['eiem_source_bone_candidates_json']=json.dumps({
    'Rig/Extra': [['assets/test/NativeDonor.asset','NativeDonor',0]],
    'Rig/Extra/Tip': [['assets/test/NativeDonor.asset','NativeDonor',1]]})
records=addon.skeleton_author_nodes(rig)
assert [source for b,r,source in records]==[True,True,True,False,False]
assert [r[0] for b,r,s in records][-2:]==['Rig/Extra','Rig/Extra/Tip']
native_world=[]; basis=addon.unity_transform_matrix_to_blender_basis()
for bone,r,source in records:
    world=matrix(r) if r[1]<0 else native_world[r[1]]@matrix(r)
    native_world.append(world)
    if not source: assert close(basis@world@basis.inverted(),bone.matrix_local),bone.name

new_file=output/'test.mesh'; addon.write_mesh(new_file,first); result=addon.read_mesh(new_file)
assert result['bone_paths']==['Rig/Extra','Rig/Extra/Tip']
assert result['bone_index_paths']==['1','1/0'], result['bone_index_paths']
assert len(result['bindposes'])==len(result['bone_hashes'])==2
assert json.loads(first['eiem_bone_palette_json'])==[0,2]
assert json.loads(first['eiem_bindposes_json'])==[root_pose,unused_pose]
for vertex,path in enumerate(('Rig/Extra','Rig/Extra','Rig/Extra/Tip')):
    weights,indices=result['skin'][vertex]; assert weights==[1,0,0,0]
    assert result['bone_paths'][indices[0]]==path
    index=next(i for i,(bone,r,source) in enumerate(records) if r[0]==path)
    bind=Matrix([result['bindposes'][indices[0]][r::4] for r in range(4)])
    assert close(native_world[index]@bind,Matrix.Identity(4)) # bind frame is coherent

# The Format 3 package path requires a real source baseline. Test this
# authoring contract directly; native package/source transactions have their
# own current Format 3 integration fixtures.
skel_file=output/'test.skel'
addon.write_skeleton(skel_file,rig)
payload=addon.read_skeleton(skel_file)
assert payload['version']==2
assert payload['source_nodes']==[True,True,True,False,False]
assert len(payload['nodes'])==5
assert all(close(matrix(a),matrix(b)) for a,b in zip(payload['nodes'][:3],nodes))
original_skeleton=skel_file.read_bytes()
blend=output/'skeleton-author.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(blend))
rig_name,first_name=rig.name,first.name
bpy.ops.wm.open_mainfile(filepath=str(blend))
rig=bpy.data.objects[rig_name]
first=bpy.data.objects[first_name]
addon.write_skeleton(skel_file,rig)
assert skel_file.read_bytes()==original_skeleton
reloaded=output/'reloaded.mesh'
addon.write_mesh(reloaded,first)
reloaded_mesh=addon.read_mesh(reloaded)
for field in ('skin','bindposes','bone_paths','bone_index_paths','bone_sources'):
    assert reloaded_mesh[field]==result[field],field
# Source rest edits remain an error, rather than silently changing the bind.
bpy.context.view_layer.objects.active=rig
bpy.ops.object.mode_set(mode='EDIT')
rig.data.edit_bones['Rig'].head.x+=.1
bpy.ops.object.mode_set(mode='OBJECT')
try:
    addon.write_skeleton(skel_file,rig)
    raise AssertionError('source rest edit was silently ignored')
except ValueError as ex:
    assert '源骨骼绑定姿态已改变' in str(ex),str(ex)
assert skel_file.read_bytes()==original_skeleton
addon.unregister()
print('EFF_SKELETON_EXPORT_OK')

"""Exercise the real exporter with weights beyond the source mesh palette."""
import importlib.util
import json
import sys
import zlib
from pathlib import Path

import bpy
from mathutils import Matrix

addon_path, output = map(Path, sys.argv[sys.argv.index('--') + 1:])
spec = importlib.util.spec_from_file_location('eiem_skin_test', addon_path)
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)

def flat(matrix):
    return [matrix[r][c] for c in range(4) for r in range(4)]

nodes = [('Root', -1, (0, 0, 0), (0, 0, 0, 1), (1, 1, 1)),
         ('Root/Unused', 0, (0, 1, 0), (0, 0, 0, 1), (1, 1, 1)),
         ('Root/Pelvis', 0, (0, 2, 0), (0, 0, 0, 1), (1, 1, 1)),
         ('Root/Pelvis/Foot', 2, (0, -1, 0), (0, 0, 0, 1), (1, 1, 1)),
         ('Root/Accessory', 0, (1, 0, 0), (0, 0, 0, 1), (1, 1, 1))]
rig = addon.make_armature('SkeletonTest', {'coordinate':'unity-y-up-left-handed', 'nodes':nodes}, bpy.context.scene.collection)
by_path = {b['eiem_path']: i for i,b in enumerate(rig.data.bones)}
poses = {n[0]:flat(Matrix.Translation((0,-y,0))) for n,y in zip(nodes, (0,1,2,1,0))}

def mesh(name, paths):
    data=bpy.data.meshes.new(name)
    data.from_pydata([(0,0,0),(1,0,0),(0,1,0)],[],[(0,1,2)])
    data['eiem_section']='Mesh'+name; data['eiem_asset']=name
    data['eiem_source']='assets/test/%s.mesh' % name
    data['eiem_target_path']=data['eiem_source']
    data['eiem_target_asset']=name
    obj=bpy.data.objects.new(name,data); bpy.context.scene.collection.objects.link(obj)
    obj.modifiers.new('Skin','ARMATURE').object=rig
    obj['eiem_bone_palette_json']=json.dumps([by_path[p] for p in paths])
    obj['eiem_bone_paths_json']=json.dumps(paths)
    obj['eiem_bone_hashes_json']=json.dumps([zlib.crc32(p.encode()) for p in paths])
    obj['eiem_bindposes_json']=json.dumps([poses[p] for p in paths])
    return obj

obj=mesh('Body',['Root','Root/Unused'])
donor=mesh('OtherPart',['Root','Root/Pelvis','Root/Pelvis/Foot','Root/Accessory'])
for name in ('Root','Unused','Pelvis','Foot','Accessory'):
    obj.vertex_groups.new(name=name)
obj.vertex_groups['Pelvis'].add([0],1,'REPLACE')
obj.vertex_groups['Foot'].add([1],1,'REPLACE')
obj.vertex_groups['Accessory'].add([2],1,'REPLACE')
before=[[(g.group,g.weight) for g in v.groups] for v in obj.data.vertices]
file=output/'expanded.mesh'
addon.write_mesh(file,obj)
result=addon.read_mesh(file)
assert result['bind_count']==5, result['bind_count']
assert result['bone_paths'][:2]==['Root','Root/Unused']
assert dict(zip(result['bone_paths'], result['bone_index_paths'])) == {
    'Root': '', 'Root/Unused': '0', 'Root/Pelvis': '1',
    'Root/Pelvis/Foot': '1/0', 'Root/Accessory': '2',
}, result['bone_index_paths']
assert result['bindposes'][:2]==[poses['Root'],poses['Root/Unused']]
for vertex,name in enumerate(('Root/Pelvis','Root/Pelvis/Foot','Root/Accessory')):
    weights,indices=result['skin'][vertex]
    assert weights==[1,0,0,0], (vertex,weights)
    assert result['bone_paths'][indices[0]]==name
assert result['bindposes'][result['bone_paths'].index('Root/Pelvis')]==poses['Root/Pelvis']
assert before==[[(g.group,g.weight) for g in v.groups] for v in obj.data.vertices]
assert json.loads(obj['eiem_bone_palette_json'])==[by_path['Root'],by_path['Root/Unused']]

# One export may process several Meshes on the same rig. Reuse only the
# skeleton-wide data; per-object weights and source slots remain independent.
twin=obj.copy(); twin.data=obj.data.copy()
bpy.context.scene.collection.objects.link(twin)
cached_file=output/'cached.mesh'; expected=file.read_bytes()
nodes_fn=addon.skeleton_author_nodes
donors_fn=addon.shared_bone_source_candidates
calls={'nodes':0,'donors':0}
def counted_nodes(armature):
    calls['nodes']+=1
    return nodes_fn(armature)
def counted_donors(armature):
    calls['donors']+=1
    return donors_fn(armature)
addon.skeleton_author_nodes=counted_nodes
addon.shared_bone_source_candidates=counted_donors
try:
    cache={}
    addon.write_mesh(cached_file,obj,skin_cache=cache)
    assert cached_file.read_bytes()==expected
    addon.write_mesh(cached_file,twin,skin_cache=cache)
    assert cached_file.read_bytes()==expected
    assert calls=={'nodes':1,'donors':1},calls
    # Weight edits are never cached with the shared skeleton context.
    twin.vertex_groups['Foot'].add([0],.25,'REPLACE')
    addon.write_mesh(cached_file,twin,skin_cache=cache)
    changed=addon.read_mesh(cached_file)
    weights,indices=changed['skin'][0]
    assert abs(weights[0]-.8)<1e-6 and abs(weights[1]-.2)<1e-6,weights
    assert [changed['bone_paths'][i] for i in indices[:2]]==['Root/Pelvis','Root/Pelvis/Foot']
    # A distinct Armature must never borrow the first rig's validation.
    other_rig=rig.copy(); other_rig.data=rig.data.copy()
    bpy.context.scene.collection.objects.link(other_rig)
    twin.modifiers[0].object=other_rig
    addon.write_mesh(cached_file,twin,skin_cache=cache)
    assert calls=={'nodes':2,'donors':2},calls
    twin.modifiers[0].object=rig
    bpy.data.objects.remove(other_rig,do_unlink=True)
    # The next export starts a fresh context even for an unchanged rig.
    addon.write_mesh(cached_file,obj,skin_cache={})
    assert cached_file.read_bytes()==expected
    assert calls=={'nodes':3,'donors':3},calls
    bone=rig.data.bones['Pelvis']
    parent=bone.get('eiem_source_parent')
    bone['eiem_source_parent']='WrongParent'
    try:
        addon.write_mesh(cached_file,obj,skin_cache={})
        raise AssertionError('edited source bone accepted on the next export')
    except ValueError as error:
        assert '父级' in str(error),str(error)
    finally:
        if parent is None: del bone['eiem_source_parent']
        else: bone['eiem_source_parent']=parent
    assert cached_file.read_bytes()==expected
finally:
    addon.skeleton_author_nodes=nodes_fn
    addon.shared_bone_source_candidates=donors_fn
    bpy.data.objects.remove(twin,do_unlink=True)

obj['eiem_bone_sources_json'] = json.dumps([
    ['assets/a.mesh', 'MeshA', 3], ['assets/a.mesh', 'MeshA', 7]])
assert json.loads(obj['eiem_bone_sources_json'])[1][2] == 7
addon.write_mesh(file,obj)
with_sources = addon.read_mesh(file)
assert with_sources['skin'] == result['skin']
assert ('assets/a.mesh', 'MeshA', 3) in with_sources['bone_source_candidates'][0]
assert ('assets/a.mesh', 'MeshA', 7) in with_sources['bone_source_candidates'][1]
first=file.read_bytes(); addon.write_mesh(file,obj); assert file.read_bytes()==first

# Donor meshes may have another bind frame. Convert using common original
# slots, rather than recomputing every inverse bind from prefab transforms.
donor_basis=Matrix.Translation((3,4,5)) @ Matrix.Rotation(.3,4,'Z')
donor_paths=json.loads(donor['eiem_bone_paths_json'])
donor['eiem_bindposes_json']=json.dumps([
    flat(Matrix([poses[p][r::4] for r in range(4)]) @ donor_basis) for p in donor_paths])
addon.write_mesh(file,obj)
converted=addon.read_mesh(file)
for p in donor_paths:
    actual=converted['bindposes'][converted['bone_paths'].index(p)]
    assert max(abs(a-b) for a,b in zip(actual,poses[p])) < 1e-5, (p,actual)

# A saved source library survives donor object removal; full paths, not
# mutable armature indices, remain the authoring identity.
rig.data['eiem_source_bindings_json']=json.dumps(addon.shared_skin_bindings(rig))
bpy.data.objects.remove(donor,do_unlink=True)
obj['eiem_bone_palette_json']=json.dumps([999,998])
addon.write_mesh(file,obj)
assert addon.read_mesh(file)['bone_paths']==result['bone_paths']

# The current binary skin record holds four influences. Keep the strongest
# four deterministically, normalise, and never edit the author weights.
for name,weight in zip(('Root','Unused','Pelvis','Foot','Accessory'),(.1,.2,.3,.4,.5)):
    obj.vertex_groups[name].add([0],weight,'REPLACE')
weights_before=[(g.group,g.weight) for g in obj.data.vertices[0].groups]
addon.write_mesh(file,obj)
reduced=addon.read_mesh(file)
weights,indices=reduced['skin'][0]
assert [reduced['bone_paths'][i].split('/')[-1] for i in indices]==['Accessory','Foot','Pelvis','Unused']
assert abs(sum(weights)-1)<1e-6
assert max(abs(a-b) for a,b in zip(weights,[.5/1.4,.4/1.4,.3/1.4,.2/1.4]))<1e-6
assert weights_before==[(g.group,g.weight) for g in obj.data.vertices[0].groups]
first=file.read_bytes()

# A positive weight with no matching bone must fail, not turn into zero skin.
obj.vertex_groups.new(name='MissingBone').add([0],.2,'REPLACE')
try:
    addon.write_mesh(file,obj)
    raise AssertionError('unknown weighted group was silently discarded')
except ValueError as ex:
    assert 'MissingBone' in str(ex), str(ex)
assert file.read_bytes()==first
print('EFF_SKIN_EXPORT_OK')

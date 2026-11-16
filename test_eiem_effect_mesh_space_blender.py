"""Real Blender regression for custom particle Mesh pivots and parent scale."""
import importlib
from pathlib import Path
import sys
import bpy
from mathutils import Matrix, Vector

package = Path(sys.argv[sys.argv.index('--') + 1]).resolve()
sys.path.insert(0, str(package.parent))
addon = importlib.import_module(package.name)
addon.register()
assets = importlib.import_module(addon.__name__ + '.eiem_effect_assets')
codec = importlib.import_module(addon.__name__ + '.nativepack.mesh')

parent = bpy.data.objects.new('scaled rotated parent', None)
bpy.context.scene.collection.objects.link(parent)
parent.location = (12, -7, 3)
parent.scale = (.4, .7, .5)
parent.rotation_euler = (.1, .2, -.4)
data = bpy.data.meshes.new('off-origin particle shape')
data.from_pydata([(2,3,4),(4,3,4),(4,5,4),(2,5,4)], [], [(0,1,2),(0,2,3)])
obj = bpy.data.objects.new('custom particle Mesh', data)
bpy.context.scene.collection.objects.link(obj)
obj.parent = parent
obj.scale = (2, 2, 2)
obj.rotation_euler = (.3, -.2, .1)
obj.location = (5, 6, 7)
bpy.context.view_layer.update()
before = ([tuple(v.co) for v in data.vertices], obj.matrix_world.copy())
verts, faces, _, _ = assets.preview_mesh(obj)
pose = assets.mesh_placement_matrix(obj)
expected = [assets.BASIS.inverted() @ (pose.inverted() @ (obj.matrix_world @ v.co)) for v in data.vertices]
assert all(any((v - p).length < 1e-5 for p in expected) for v in verts)
origin = assets.mesh_shape_transform(obj, data) @ Vector((0, 0, 0))
assert origin.length < 1e-7, 'particle shape moved the object origin'
assert sum(verts, Vector()).length > 1, 'off-origin geometry was silently centred'
assert all(any((pose @ (assets.BASIS @ v) - obj.matrix_world @ vertex.co).length < 1e-5
                   for vertex in data.vertices) for v in verts), 'source appearance was not preserved'
snapshot = assets.mesh_snapshot(obj)
positions = list(zip(*[iter(snapshot['vertices'])] * 3))
assert all(any((Vector(p) - v).length < 1e-5 for v in verts) for p in positions), positions
assert before[0] == [tuple(v.co) for v in data.vertices] and before[1] == obj.matrix_world
fingerprint = assets.mesh_fingerprint(obj)
obj.location.x += 40
bpy.context.view_layer.update()
assert assets.mesh_fingerprint(obj) == fingerprint, 'scene translation changed particle shape'
parent.scale.x *= 2
bpy.context.view_layer.update()
assert assets.mesh_fingerprint(obj) != fingerprint, 'parent scale was omitted from export identity'
# Moving the origin is an explicit Blender edit, not an exporter side effect.
world_vertices = [obj.matrix_world @ vertex.co for vertex in data.vertices]
bpy.ops.object.select_all(action='DESELECT')
obj.select_set(True)
bpy.context.view_layer.objects.active = obj
bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY', center='BOUNDS')
bpy.context.view_layer.update()
assert all((old - obj.matrix_world @ vertex.co).length < 1e-4
           for old, vertex in zip(world_vertices, data.vertices)), 'origin edit changed visible geometry'
verts, _, _, _ = assets.preview_mesh(obj)
lo = Vector(tuple(min(v[i] for v in verts) for i in range(3)))
hi = Vector(tuple(max(v[i] for v in verts) for i in range(3)))
assert (lo + hi).length < 1e-4, 'explicit origin edit was not respected'
obj['eff_effect_mesh_source'] = 'preserved-native-asset'
assert assets.mesh_shape_transform(obj, data) == obj.matrix_basis.to_3x3().to_4x4(), 'native asset pivot changed'
print('PASS object origin, parent transform, native/preview parity, explicit origin edit, fingerprints')
addon.unregister()

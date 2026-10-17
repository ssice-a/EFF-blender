"""Exercise snapshot reuse across opening a .blend and subsequent mesh edits.

Run in factory-startup background Blender; saves only a generated temporary
scene, never an author project or a game Mod.
"""
import importlib
from pathlib import Path
import sys
import tempfile

import bpy


args = sys.argv[sys.argv.index('--') + 1:]
directory = Path(args[0]).resolve()
sys.path.insert(0, str(directory.parent))
package = importlib.import_module(directory.name)
package.register()
addon = package.eiem_blender_addon
exporter = addon.native_export

mesh = bpy.data.meshes.new('Snapshot Probe')
mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
obj = bpy.data.objects.new('Snapshot Probe', mesh)
bpy.context.scene.collection.objects.link(obj)
bpy.context.view_layer.update()
exporter._cached_mesh_snapshot(addon, obj)
assert exporter._NATIVE_SNAPSHOT_CACHE

with tempfile.TemporaryDirectory(prefix='eff-snapshot-cache-test-') as folder:
    path = str(Path(folder) / 'probe.blend')
    bpy.ops.wm.save_as_mainfile(filepath=path)
    # Opening an existing project must not disable edit tracking.
    bpy.ops.wm.open_mainfile(filepath=path)
    assert not exporter._NATIVE_SNAPSHOT_CACHE, 'opened project retained old snapshots'
    obj = bpy.data.objects['Snapshot Probe']
    first, _ = exporter._cached_mesh_snapshot(addon, obj)
    unchanged, hit = exporter._cached_mesh_snapshot(addon, obj)
    assert hit and unchanged == first, 'unchanged snapshot did not reuse cache'
    obj.data.vertices[0].co.x = 0.25
    obj.data.update()
    bpy.context.view_layer.update()
    edited, hit = exporter._cached_mesh_snapshot(addon, obj)
    fresh = addon.write_mesh(None, obj, return_snapshot=True)
    assert edited['vertices'] == fresh['vertices'] != first['vertices'], (
        'opening a project lost edit tracking: export reused old geometry')
    assert not hit, 'edited mesh reported a snapshot cache hit'
    assert addon._eiem_export_dirty_depsgraph_update in bpy.app.handlers.depsgraph_update_post
    assert not addon._EXPORT_IN_PROGRESS
    unchanged, hit = exporter._cached_mesh_snapshot(addon, obj)
    assert hit and unchanged == edited

    # Object transforms and pose evaluation do not enter local Mesh exports.
    # A sibling on the same rig must retain its snapshot when only obj changes.
    rig_data = bpy.data.armatures.new('Cache Rig')
    rig = bpy.data.objects.new('Cache Rig', rig_data)
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    bone = rig_data.edit_bones.new('Root')
    bone.head, bone.tail = (0, 0, 0), (0, 0, 1)
    bpy.ops.object.mode_set(mode='OBJECT')
    obj.modifiers.new('Cache Rig', 'ARMATURE').object = rig
    bpy.context.view_layer.update()
    token = addon._mesh_resource_token(obj)
    obj.location.x += 1
    rig.pose.bones['Root'].rotation_mode = 'XYZ'
    rig.pose.bones['Root'].rotation_euler.x = 0.2
    bpy.context.view_layer.update()
    assert addon._mesh_resource_token(obj) == token, 'pose/object evaluation invalidated local Mesh export'
    rig_data.bones['Root']['eiem_path'] = 'Root'
    bpy.context.view_layer.update()
    assert addon._mesh_resource_token(obj) != token, 'rest binding metadata edit reused stale skeleton'
    rig_data.bones['Root']['eiem_local_position'] = [1.0, 2.0, 3.0]
    token = addon._mesh_resource_token(obj)
    rig_data.bones['Root']['eiem_local_position'][0] = 2.0
    assert addon._mesh_resource_token(obj) != token, 'in-place bone metadata edit reused stale skeleton'
    token = addon._mesh_resource_token(obj)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode='EDIT')
    rig_data.edit_bones['Root'].tail.x = 0.3
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.context.view_layer.update()
    assert addon._mesh_resource_token(obj) != token, 'rest bone matrix edit reused stale skeleton'
    obj.modifiers.remove(obj.modifiers['Cache Rig'])
    bpy.data.objects.remove(rig, do_unlink=True)
    bpy.context.view_layer.update()
    edited, _ = exporter._cached_mesh_snapshot(addon, obj)

    # Repeated edits of one object must not evict its unchanged sibling.
    sibling = bpy.data.objects.new('Cache Sibling', obj.data.copy())
    bpy.context.scene.collection.objects.link(sibling)
    bpy.context.view_layer.update()
    limit = exporter._NATIVE_SNAPSHOT_CACHE_LIMIT
    exporter._NATIVE_SNAPSHOT_CACHE_LIMIT = 2
    try:
        sibling_snapshot, _ = exporter._cached_mesh_snapshot(addon, sibling)
        for _ in range(5):
            obj.data.vertices[0].co.x += 0.01
            obj.data.update()
            bpy.context.view_layer.update()
            edited, hit = exporter._cached_mesh_snapshot(addon, obj)
            assert not hit
            repeated, hit = exporter._cached_mesh_snapshot(addon, sibling)
            assert hit and repeated == sibling_snapshot, 'old edit history evicted an unchanged Mesh'
        assert len(exporter._NATIVE_SNAPSHOT_CACHE) == 2
    finally:
        exporter._NATIVE_SNAPSHOT_CACHE_LIMIT = limit
        bpy.data.objects.remove(sibling, do_unlink=True)

    # Bulk RNA fingerprints still invalidate the actual exported payload.
    obj.shape_key_add(name='Basis')
    shape = obj.shape_key_add(name='Probe')
    shape.data[1].co.y = 0.2
    shaped, _ = exporter._cached_mesh_snapshot(addon, obj)
    shape.data[1].co.y = 0.3
    reshaped, hit = exporter._cached_mesh_snapshot(addon, obj)
    assert not hit and reshaped['blend_vertices'] != shaped['blend_vertices']
    assert reshaped == addon.write_mesh(None, obj, return_snapshot=True)
    for name in ('First', 'Second'):
        obj.data.materials.append(bpy.data.materials.new(name))
    first_slot, _ = exporter._cached_mesh_snapshot(addon, obj)
    obj.data.polygons[0].material_index = 1
    edited, hit = exporter._cached_mesh_snapshot(addon, obj)
    assert not hit and edited['submeshes'] != first_slot['submeshes']
    assert edited == addon.write_mesh(None, obj, return_snapshot=True)

    # Missing tracking must recompute rather than claim a successful stale
    # export; restoring tracking must not resurrect those old entries.
    tracker = addon._eiem_export_dirty_depsgraph_update
    bpy.app.handlers.depsgraph_update_post.remove(tracker)
    try:
        obj.data.vertices[0].co.x = 0.5
        obj.data.update()
        bpy.context.view_layer.update()
        untracked, hit = exporter._cached_mesh_snapshot(addon, obj)
        assert not hit and untracked['vertices'] != edited['vertices']
        assert not exporter._NATIVE_SNAPSHOT_CACHE
    finally:
        bpy.app.handlers.depsgraph_update_post.append(tracker)
    restored, hit = exporter._cached_mesh_snapshot(addon, obj)
    assert not hit and restored == untracked

package.unregister()
assert addon._eiem_export_dirty_depsgraph_update not in bpy.app.handlers.depsgraph_update_post
assert addon._eiem_export_load_post not in bpy.app.handlers.load_post
print('EFF_SNAPSHOT_CACHE_OK: open project; reuse unchanged; export edited geometry')

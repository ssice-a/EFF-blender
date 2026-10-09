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

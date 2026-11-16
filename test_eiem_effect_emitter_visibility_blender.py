"""The Outliner visibility toggles the selected emitter's offline preview only.

Run with Blender background and three arguments after ``--``: addon path,
author scene path, and output report. Scene changes remain in memory only.
"""
import copy
import importlib
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch

import bpy

addon_path, project, output = map(Path, sys.argv[sys.argv.index('--') + 1:])
sys.path.insert(0, str(addon_path.resolve().parent))
addon = importlib.import_module(addon_path.name)
addon.register()
bpy.ops.wm.open_mainfile(filepath=str(project))
editor = addon.eiem_blender_addon.effect_editor
preview = editor.preview
scene = bpy.context.scene
root = next(obj for obj in scene.objects if obj.get('eff_effect_document') and
            obj.name == 'P_fxui_liino_relax_sp_01')
root.eff_effect.preview = True
root.eff_effect.preview_particles = True
root.eff_effect.show_shape = True
root.eff_effect.source_animation = False
edit = editor.synchronize(root)
node = next(obj for obj in editor.objects(root) if obj.name == 'wenli2')
identity = next(i for i in json.loads(node['eff_effect_components'])
                if edit.objects[i]['classId'] == 198)
scene.frame_set(73)
preview.refresh(root, edit)
bpy.context.view_layer.update()
baseline = copy.deepcopy(edit.export())

def related_previews():
    return [obj for obj in bpy.data.objects if obj.get('eff_preview') and
            obj.get('eff_preview_key', '').startswith(root['eff_effect_id'] + identity)]

def emitter_previews():
    return [obj for obj in related_previews() if obj.get('eff_shape_type') is not None or
            obj.get('eff_preview_geometry_nodes')]

objects = emitter_previews()
assert any(obj.get('eff_shape_type') is not None for obj in objects), 'fixture must include emitter bounds'
assert any(obj.get('eff_preview_geometry_nodes') for obj in objects), 'fixture must include particle preview'
visibility = {obj.name: obj.hide_viewport for obj in objects}
assert any(not hidden for hidden in visibility.values()), 'fixture must start with a visible preview'

original_frame = scene.frame_current
assert bpy.app.timers.is_registered(preview.visibility_update), 'visibility watcher is not registered'
with patch.object(preview, '_sync', side_effect=AssertionError('visibility synchronized author data')), \
     patch.object(preview, 'refresh', side_effect=AssertionError('visibility rebuilt the effect')):
    node.hide_set(True, view_layer=bpy.context.view_layer)
    bpy.context.view_layer.update()
    assert node.hide_get(view_layer=bpy.context.view_layer)
    preview.visibility_update()
    assert all(obj.hide_get() for obj in objects), 'hidden emitter preview remained visible'
    assert not root.hide_get(view_layer=bpy.context.view_layer), 'emitter hide toggled its whole effect root'
    assert edit.export() == baseline, 'visibility state was written into native author data'

    node.hide_set(False, view_layer=bpy.context.view_layer)
    bpy.context.view_layer.update()
    preview.visibility_update()
    assert all(not obj.hide_get() and obj.hide_viewport == visibility[obj.name] for obj in objects), 'unhidden emitter preview changed its native visibility'
    assert edit.export() == baseline, 'visibility roundtrip changed native author data'
assert scene.frame_current == original_frame, 'visibility required a timeline change'

# Hiding a root or parent branch must gate static meshes, trails and bounds as
# well. Their intrinsic visibility and hidden GN source templates survive it.
all_previews = [obj for obj in root.children_recursive if obj.get('eff_preview_key')]
native_visibility = {obj.as_pointer(): obj.hide_viewport for obj in all_previews}
templates = [obj for obj in root.children_recursive if obj.get('eff_preview') and not obj.get('eff_preview_key')]
siblings = [obj for obj in all_previews if obj not in objects]
assert all(not obj.hide_get() for obj in siblings), 'emitter toggle leaked to its siblings'
assert all(obj.hide_get() for obj in templates), 'GN source template became visible'
root.hide_set(True)
preview.visibility_update()
assert all(obj.hide_get() for obj in all_previews), 'root did not hide every preview kind'
# Timeline evaluation while hidden must not bake the eye into native flags.
scene.frame_set(original_frame + 1)
root.hide_set(False)
preview.visibility_update()
assert all(not obj.hide_get() for obj in all_previews), 'hidden playback prevented restoring previews'
scene.frame_set(original_frame)
assert all(obj.hide_viewport == native_visibility[obj.as_pointer()] for obj in all_previews)
node.hide_viewport = True
bpy.context.view_layer.update()
preview.visibility_update()
assert all(obj.hide_get() for obj in objects), 'monitor toggle did not gate the preview'
node.hide_viewport = False
bpy.context.view_layer.update()
preview.visibility_update()
assert all(not obj.hide_get() for obj in objects), 'monitor toggle did not restore the preview'

# After undo/load invalidates caches, visibility must work before any frame.
preview.clear_cache()
node.hide_set(True)
preview.visibility_update()
assert all(obj.hide_get() for obj in objects), 'cache reset lost the visibility watcher'
node.hide_set(False)
preview.visibility_update()
assert all(not obj.hide_get() for obj in objects)
timings=[]
with patch.object(preview, '_sync', side_effect=AssertionError('idle visibility synchronized author data')), \
     patch.object(preview, 'author_signature', side_effect=AssertionError('visibility scanned author fields')), \
     patch.object(preview, 'refresh', side_effect=AssertionError('idle visibility rebuilt the effect')):
    for _ in range(100):
        start=time.perf_counter();preview.visibility_update()
        timings.append((time.perf_counter()-start)*1000)
assert edit.export() == baseline, 'visibility or playback changed native author data'

Path(output).parent.mkdir(parents=True, exist_ok=True)
Path(output).write_text(json.dumps(dict(root=root.name, emitter=node.name,
    previewObjects=[obj.name for obj in objects], hideAndUnhide=True,
    authorDataUnchanged=True, idleMedianMs=sorted(timings)[len(timings)//2],
    hierarchyAndMonitor=True, hiddenPlayback=True, cacheReset=True), ensure_ascii=False, indent=2), 'utf-8')
addon.unregister()
assert not bpy.app.timers.is_registered(preview.visibility_update)
print('PASS: Outliner hide/unhide hides/restores only this emitter preview; native author data unchanged')

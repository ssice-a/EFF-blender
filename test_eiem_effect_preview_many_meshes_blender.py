"""Exercise continuous playback with more than 16 distinct author Meshes.

Run in factory-startup Blender with -- ADDON PROJECT OUTPUT. The project is
opened read-only; the test never saves it. Use a scene with at least 24 custom
particle Meshes under one effect root (for example the sword ring workbench).
"""
import collections
import importlib
import json
from pathlib import Path
import statistics
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
roots = [obj for obj in scene.objects if obj.get('eff_effect_document')]
for root in roots:
    root.eff_effect.preview = False
    root.eff_effect.show_shape = False

def resources(root):
    return {obj.eff_effect.particle_mesh for obj in root.children_recursive
            if not obj.get('eff_preview') and obj.eff_effect.particle_mesh}

root = max(roots, key=lambda obj: len(resources(obj)))
meshes = resources(root)
assert len(meshes) >= 24, 'fixture needs more than 16 distinct custom Meshes'
root.eff_effect.preview = True
baseline = editor.synchronize(root).export()
preview.clear_cache()
evaluations = collections.Counter()
rebuilds = collections.Counter()
real_mesh = editor.assets.preview_mesh
real_template = preview.particle_geometry._template

def evaluate(obj):
    evaluations[obj.name] += 1
    return real_mesh(obj)

def template(obj, geometry, material):
    previous = obj.get('eff_preview_template')
    previous = bpy.data.objects.get(previous) if isinstance(previous, str) else previous
    if not previous or preview.particle_geometry._templates.get(previous.as_pointer()) is not geometry:
        rebuilds[obj.name] += 1
    return real_template(obj, geometry, material)

timings = []
with patch.object(editor.assets, 'preview_mesh', side_effect=evaluate), \
     patch.object(preview.particle_geometry, '_template', side_effect=template):
    for frame in (14, 15, 16, 17):
        start = time.perf_counter()
        scene.frame_set(frame)
        timings.append((time.perf_counter() - start) * 1000)
        assert not root.get('预览错误'), root.get('预览错误')
    assert set(evaluations) == {obj.name for obj in meshes}, evaluations
    assert all(count == 1 for count in evaluations.values()), \
        f'static Meshes re-evaluated during playback: {evaluations}'
    assert all(count == 1 for count in rebuilds.values()), \
        f'unchanged GN templates rebuilt during playback: {rebuilds}'
    warm_evaluations = sum(evaluations.values())
    warm_rebuilds = sum(rebuilds.values())

    # Live edits must invalidate exactly the affected resource.
    target = sorted(meshes, key=lambda obj: obj.name)[0]
    before = evaluations.copy()
    target.data.vertices[0].co.x += .002
    target.data.update()
    bpy.context.view_layer.update()
    scene.frame_set(18)
    assert evaluations[target.name] == before[target.name] + 1, evaluations
    assert sum(evaluations.values()) == sum(before.values()) + 1, evaluations
    assert not root.get('预览错误'), root.get('预览错误')

    # Modifiers may depend on time without an Action. Retain per-frame
    # evaluation, but keep only their most recent geometry, not frame history.
    modifier = target.modifiers.new('Cache invalidation regression', 'SMOOTH')
    modifier.factor = 0
    bpy.context.view_layer.update()
    scene.frame_set(19)
    before = evaluations.copy()
    cache_size = len(preview._author_mesh_templates)
    for frame in (20, 21, 22):
        scene.frame_set(frame)
    assert evaluations[target.name] == before[target.name] + 3, evaluations
    assert sum(evaluations.values()) == sum(before.values()) + 3, evaluations
    assert len(preview._author_mesh_templates) == cache_size, 'cache retained old animation frames'
    assert editor.synchronize(root).export() == baseline, 'preview changed native author fields'

output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(dict(passed=True, distinctMeshes=len(meshes),
    frameMs=timings, warmMedianMs=statistics.median(timings[1:]),
    staticMeshEvaluations=warm_evaluations, templateRebuilds=warm_rebuilds,
    liveMeshEdit=True, dynamicMeshFrames=True, boundedFrameHistory=True,
    nativeUnchanged=True), indent=2), 'utf-8')
addon.unregister()
print('PASS many static Meshes reuse geometry, live edits invalidate, dynamic Meshes keep latest frame')

"""Verify atlas animation through the real Blender frame-change preview path."""
import copy
import importlib
import json
import sys
from pathlib import Path

import bpy

addon_path, source_root = sys.argv[sys.argv.index('--') + 1:]
sys.path.insert(0, str(Path(addon_path).resolve().parent))
addon = importlib.import_module(Path(addon_path).name)
addon.register()
editor = addon.eiem_blender_addon.effect_editor
preview = editor.preview
sim = preview.sim
source = next(Path(source_root).rglob('p_fxmap_common_map02_pearblossom_flower_01.efffx/effect.json')).parent
scene = bpy.context.scene
original_frame = scene.frame_current
root = editor.import_source(bpy.context, source)
edit = editor.editor(root)
node = next(o for o in editor.objects(root)
            if any(edit.objects[i]['classId'] == 198 for i in json.loads(o.get('eff_effect_components', '[]'))))
ids = json.loads(node['eff_effect_components'])
particle_id = next(i for i in ids if edit.objects[i]['classId'] == 198)
renderer_id = next(i for i in ids if edit.objects[i]['classId'] == 199)
data = edit.value(particle_id)
renderer = edit.value(renderer_id)

# Use a billboard quad and a deterministic sequence to exercise atlas UVs
# independently of the source renderer and its original playback mode.
renderer['m_RenderMode'] = 0
data['InitialModule']['startLifetime'] = dict(minMaxState=0, scalar=5.0, minScalar=5.0)
data['InitialModule']['maxNumParticles'] = 500
data['EmissionModule']['enabled'] = True
data['EmissionModule']['rateOverTime'] = dict(minMaxState=0, scalar=12.0, minScalar=12.0)
data['EmissionModule']['m_Bursts'] = []
data['UVModule'] = dict(enabled=True, mode=0, tilesX=2, tilesY=2, timeMode=2, fps=4.0,
                        startFrame=dict(minMaxState=0, scalar=.5, minScalar=.5),
                        frameOverTime=dict(minMaxState=0, scalar=0, minScalar=0),
                        cycles=1.0, animationType=0, rowIndex=0, rowMode=0)
editor.store(root, edit)
editor.refresh_fields(root)
seed = int(root['eff_effect_id'][:8], 16)

for frame in (31, 46, 61, 76):
    scene.frame_set(frame)
    edit = editor.synchronize(root)
    data = edit.value(particle_id)
    sampled = sim.particles(data, (frame - scene.frame_start) / scene.render.fps, seed=seed)
    preview_obj = next(o for o in root.children_recursive
                       if o.get('eff_preview_key') == root['eff_effect_id'] + particle_id)
    assert len(preview_obj.data.polygons) == len(sampled), 'billboard count diverged from particle simulation'
    assert preview_obj.data.materials, 'billboard preview lost its source material'
    image_nodes = [n for n in preview_obj.data.materials[0].node_tree.nodes
                   if n.type == 'TEX_IMAGE' and n.image]
    assert image_nodes, 'billboard preview material has no source image texture'
    uv_layer = preview_obj.data.uv_layers.active
    actual = [tuple(round(v, 6) for v in uv_layer.data[p.loop_start].uv)
              for p in preview_obj.data.polygons]
    expected = [tuple(round(v, 6) for v in sim.uv_transform(data['UVModule'], particle, (0, 0)))
                for particle in sampled]
    assert actual == expected, f'preview UVs diverged at frame {frame}'
    assert len(set(actual)) > 1, f'frame {frame} did not show multiple atlas cells'

scene.frame_set(original_frame)
collection = bpy.data.collections.get(root.get('eff_effect_collection'))
if collection:
    for obj in list(collection.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    scene.collection.children.unlink(collection)
    bpy.data.collections.remove(collection)
addon.unregister()
print('PASS Blender frame-change atlas preview: billboard geometry, authored FPS start frame, four timeline samples')

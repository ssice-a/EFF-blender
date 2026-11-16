"""Real liino template: inactive roots, replay caches and live author edits.

Run in a separate factory-startup Blender with -- ADDON PROJECT OUTPUT.
Never saves or modifies the user's template file.
"""
import copy
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
roots = [o for o in scene.objects if o.get('eff_effect_document')]
assert len(roots) == 4
for root in roots:
    root.eff_effect.preview = False
    root.eff_effect.show_shape = False
baselines = {root.name: copy.deepcopy(editor.synchronize(root).export()) for root in roots}

# Disabled roots must not synchronize thousands of fields or evaluate clips.
with patch.object(preview, '_sync', side_effect=AssertionError('disabled root synchronized')):
    for frame in (1, 30, 90):
        scene.frame_set(frame)
        assert all(not root.get('预览错误') for root in roots)
assert all(o.hide_viewport for root in roots for o in root.children_recursive if o.get('eff_preview'))

root = next(o for o in roots if o.name == 'P_fxui_liino_relax_sp_01')
root.eff_effect.preview = True
root.eff_effect.source_animation = True
preview._sessions.clear()
preview._author_mesh_templates.clear()
syncs, meshes, template_updates = [], [], []
real_sync, real_mesh = preview._sync, editor.assets.preview_mesh
real_template = preview.particle_geometry._template
real_signature = preview.author_signature
signature_checks = []
def sync(target):
    syncs.append(target.name)
    return real_sync(target)
def mesh(target):
    meshes.append(target.name)
    return real_mesh(target)

def template(target, geometry, material):
    previous = target.get('eff_preview_template')
    previous = bpy.data.objects.get(previous) if isinstance(previous, str) else previous
    if not previous or preview.particle_geometry._templates.get(previous.as_pointer()) is not geometry:
        template_updates.append(target.name)
    return real_template(target, geometry, material)

def signature(target):
    signature_checks.append(target.name)
    return real_signature(target)

timings = []
with patch.object(preview, '_sync', side_effect=sync), patch.object(editor.assets, 'preview_mesh', side_effect=mesh), \
     patch.object(preview.particle_geometry, '_template', side_effect=template), \
     patch.object(preview, 'author_signature', side_effect=signature):
    for frame in (73, 74, 75, 76, 77, 78):
        start = time.perf_counter()
        scene.frame_set(frame)
        timings.append((time.perf_counter()-start)*1000)
        assert not root.get('预览错误'), root.get('预览错误')
    assert syncs == [root.name], syncs
    assert len(signature_checks) < len(timings), 'stable playback recomputed the full author signature every frame'
    assert len(meshes) == len(set(meshes)), 'static particle template triangulated on every frame'
    assert len(template_updates) == len(set(template_updates)), 'unchanged particle templates rebuilt every frame'
    obj = bpy.data.objects['预览粒子 wenli2']
    assert obj['预览粒子数'] == 1 and obj.get('eff_preview_geometry_nodes')
    assert len(obj.data.vertices) == 1 and not obj.data.polygons, 'particle points expanded into mesh copies'
    assert any(m.type == 'NODES' and m.node_group and m.node_group.get('eff_particle_preview') for m in obj.modifiers)
    assert all(obj.data.attributes.get(name) for name in ('EFFTransform','EFFParticleColor','EFFUVScale','EFFUVOffset'))
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    instances = [item for item in depsgraph.object_instances if item.is_instance and item.parent and item.parent.original == obj]
    assert len(instances) == 1, 'particle Geometry Nodes did not produce one visible instance'
    mat = obj.data.materials[0]
    assert mat.get('eiem_shader') == 'HGRP/Effect/VFXBaseV2' and mat.surface_render_method == 'BLENDED'
    assert any(node.label == '_SampleTex1' for node in mat.node_tree.nodes if node.type == 'TEX_IMAGE')
    main_uv = next(node for node in mat.node_tree.nodes if node.get('eff_uv_scroll') == '_MainTex')
    uv_at_frame_78 = tuple(main_uv.inputs[1].default_value)
    assert mat.node_tree.nodes['EFF Alpha'].inputs[1].default_value > .9, 'visible portion of source clip is blank'
    assert all(editor.synchronize(r).export() == baselines[r.name] for r in roots), 'preview changed native source'

    edit = editor.editor(root)
    node = next(o for o in editor.objects(root) if o.name == 'wenli2')
    particle = next(i for i in json.loads(node['eff_effect_components']) if edit.objects[i]['classId'] == 198)
    key = editor.field_key(particle, ['InitialModule', 'startSpeed', 'scalar'])
    before = node[key]
    node[key] = float(before) + .25
    bpy.context.view_layer.update()
    assert root.as_pointer() in preview._pending_updates, 'author ID-property change did not reach the depsgraph invalidation handler'
    scene.frame_set(79)
    assert tuple(main_uv.inputs[1].default_value) != uv_at_frame_78, 'material UV speed did not advance with playback'
    assert edit.value(particle)['InitialModule']['startSpeed']['scalar'] == node[key], 'cache ignored a live author edit'
    assert len(syncs) == 2, syncs
    node[key] = before
    bpy.context.view_layer.update()
    edit=editor.synchronize(root)
    preview.refresh(root,edit)
    preview.prime_signature(root,edit)

    # A material edit must invalidate both synchronization and Shader reuse.
    author = editor.assets.material_object(root, next(i for i, spec in edit.objects.items()
        if spec.get('name') == 'M_fx_liino_relax_wenli_901' and spec['classId'] == 21))
    material = editor.material_bridge.material_for(author)
    old_alpha = material['eiem_float._TintColorAlpha']
    material['eiem_float._TintColorAlpha'] = '.4'
    bpy.context.view_layer.update()
    assert root.as_pointer() in preview._pending_updates, 'material author change did not reach the depsgraph invalidation handler'
    scene.frame_set(81)
    assert len(syncs) == 3, 'cache ignored live material controls'
    material['eiem_float._TintColorAlpha'] = old_alpha
    bpy.context.view_layer.update()
    edit=editor.synchronize(root)
    preview.refresh(root,edit)
    preview.prime_signature(root,edit)

# Rewind to the source fade-in: cached animation values must still change.
scene.frame_set(25)
assert mat.node_tree.nodes['EFF Alpha'].inputs[1].default_value == 0
scene.frame_set(97)
assert mat.node_tree.nodes['EFF Alpha'].inputs[1].default_value > .9
for target in roots:
    assert editor.synchronize(target).export() == baselines[target.name]
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(dict(passed=True, warmMedianMs=statistics.median(timings[1:]),
    frameMs=timings, staticMeshEvaluations=len(meshes), particleTemplateUpdates=len(template_updates), authorSyncs=len(syncs),
    authorSignatureChecks=len(signature_checks), nativeUnchanged=True, sourceFadeIn=True, disabledRootsSkipped=True), indent=2), 'utf-8')
addon.unregister()
print('PASS inactive roots, static mesh reuse, live fields/material invalidation, source fade-in and native preservation')

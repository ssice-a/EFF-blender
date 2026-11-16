"""Real author data: clipboard preserves edits; replacements retire resources.

Run in factory-startup Blender: -- ADDON AUTHOR_STATE_JSON MESH_SOURCE OUTPUT.
No user project, installed Mod, or source directory is modified.
"""
import base64
import copy
import importlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

import bpy

addon_path, fixture, mesh_source, output = map(Path, sys.argv[sys.argv.index('--') + 1:])
output.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(addon_path.resolve().parent))
addon = importlib.import_module(addon_path.name)
addon.register()
fx = addon.eiem_blender_addon.effect_editor

# Background Blender has no OS clipboard. Keep only that adapter in memory;
# execute the production operators against the actual Blender scene/context.
class Context:
    window_manager = SimpleNamespace(clipboard='')
    def __getattr__(self, key):return getattr(bpy.context, key)
context = Context()


def copy_emitter(identity):
    operator = SimpleNamespace(identity=identity, report=lambda kinds, message: print(message))
    return fx.EFF_OT_effect_emitter_copy.execute(operator, context)


def paste_emitter(root):
    operator = SimpleNamespace(object_name=root.name, report=lambda kinds, message: print(message))
    return fx.EFF_OT_effect_emitter_paste.execute(operator, context)


def select(obj):
    for item in bpy.context.selected_objects:item.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def renderer(edit, node):
    return next(i for i in node['components'] if edit.objects[i]['classId'] == 199)


def material(edit, render):
    return fx.preview._target(edit, render, ['m_Materials', 0])


state = json.loads(fixture.read_text('utf-8'))
source_root = fx.import_workspace(bpy.context, state)
source_edit = fx.synchronize(source_root)
mid = next(i for i, spec in source_edit.objects.items()
           if spec.get('gameSource') and spec['classId'] == 21 and
           source_edit.value(i) != state['original'][i])
source_node = next(n for n in state['catalog']['hierarchy']
                   if any(r.get('identity') == mid for i in n['components']
                          if source_edit.objects[i]['classId'] == 199
                          for r in source_edit.objects[i]['references']))
select(source_root)
assert copy_emitter(source_node['identity']) == {'FINISHED'}
clipboard = fx.block_clipboard.decode(context.window_manager.clipboard)
assert [r['classId'] for r in clipboard['resources']] == [21]

target_root = fx.import_source(bpy.context, state['source'])
select(target_root)
previous = target_root['eff_effect_document']
before = {name: {item.as_pointer() for item in getattr(bpy.data, name)}
          for name in ('objects', 'materials', 'meshes', 'images')}
with patch.object(fx.preview, '_material_asset', side_effect=ValueError('injected preview failure')):
    assert paste_emitter(target_root) == {'CANCELLED'}
assert target_root['eff_effect_document'] == previous
assert before == {name: {item.as_pointer() for item in getattr(bpy.data, name)} for name in before}, \
    'failed projection left partially pasted Blender data'
assert paste_emitter(target_root) == {'FINISHED'}
target_edit = fx.synchronize(target_root)
node = target_edit.state['catalog']['hierarchy'][-1]
rid = renderer(target_edit, node)
mid = material(target_edit, rid)
assert next(r['second'] for r in target_edit.value(mid)['m_SavedProperties']['m_Floats']
            if r['first'] == '_TintColorIntensity') == 50
assert fx.assets.material_object(target_root, mid), 'paste did not create the editable Material'
assert len(target_edit.state['author_assets']) == 1
(output / 'clipboard-material.effedit.json').write_text(
    fx.document.dumps(fx.assets.export_document(target_root, target_edit)), encoding='utf-8')

# Replace -> export -> restore the actual native author payload, three times.
# The replaced Material/Texture must never survive as an active resource/lease.
counts = []
for cycle in range(3):
    previous_mid = mid
    image = bpy.data.images.new('replacement ' + str(cycle), width=2, height=2, alpha=True)
    image.pixels[:] = [.2 + cycle * .1, .4, .8, .5] * 4
    fx.assets.material_object(target_root, mid).eff_effect_textures['_MainTex'].image = image
    payload = fx.assets.export_document(target_root, fx.synchronize(target_root))
    rows = payload['assets']
    kinds = sorted(target_edit.objects[row['template']]['classId'] for row in rows)
    assert kinds == [21, 28], kinds
    assert previous_mid not in {row['identity'] for row in rows}
    assert not any(row['owner'] == previous_mid for row in payload.get('gameReferences', []))
    counts.append(len(rows))
    restored = fx.document.load_source(state['source'])
    fx.document.apply_edit(restored, payload)
    target_root = fx.import_workspace(bpy.context, restored)
    target_edit = fx.synchronize(target_root)
    node = target_edit.state['catalog']['hierarchy'][-1]
    rid = renderer(target_edit, node)
    mid = material(target_edit, rid)

# A second cross-root paste includes the local pixels and recreates packed UI.
select(target_root)
assert copy_emitter(node['identity']) == {'FINISHED'}
clipboard = fx.block_clipboard.decode(context.window_manager.clipboard)
assert sorted(row['classId'] for row in clipboard['resources']) == [21, 28]
destination_root = fx.import_source(bpy.context, state['source'])
select(destination_root)
assert paste_emitter(destination_root) == {'FINISHED'}
destination = fx.synchronize(destination_root)
last = destination.state['catalog']['hierarchy'][-1]
mid = material(destination, renderer(destination, last))
slot = fx.assets.material_object(destination_root, mid).eff_effect_textures['_MainTex']
assert slot.image and slot.image.packed_file, 'authored pixels were not projected into the pasted Material'
source_texture = next(row for row in target_edit.state['author_assets']
                      if target_edit.objects[row['identity']]['classId'] == 28)
target_texture = next(row for row in destination.state['author_assets']
                      if destination.objects[row['identity']]['classId'] == 28)
reader = fx.assets.NativeAssets(target_edit, target_root['eff_effect_id'])
before = reader.codec.decode(reader.tree(source_texture['template'])[0].schema,
                             base64.b64decode(source_texture['payloadBase64']))
reader2 = fx.assets.NativeAssets(destination, destination_root['eff_effect_id'])
after = reader2.codec.decode(reader2.tree(target_texture['template'])[0].schema,
                             base64.b64decode(target_texture['payloadBase64']))
assert before['image data'].data == after['image data'].data
assert before['m_Width'].data == after['m_Width'].data == 2

# A real Mesh author snapshot follows the same clipboard resource path.
mesh_root = fx.import_source(bpy.context, str(mesh_source))
mesh_edit = fx.synchronize(mesh_root)
mesh_node = next(o for o in fx.objects(mesh_root) if o.eff_effect.particle_mesh)
mesh = mesh_node.eff_effect.particle_mesh
mesh.data.vertices[0].co.z += .03125
select(mesh_root)
assert copy_emitter(mesh_node['eff_effect_node']) == {'FINISHED'}
mesh_clipboard = fx.block_clipboard.decode(context.window_manager.clipboard)
assert any(row['classId'] == 43 for row in mesh_clipboard['resources'])
mesh_target = fx.import_source(bpy.context, str(mesh_source))
select(mesh_target)
assert paste_emitter(mesh_target) == {'FINISHED'}
mesh_target_edit = fx.synchronize(mesh_target)
last = mesh_target_edit.state['catalog']['hierarchy'][-1]
render = renderer(mesh_target_edit, last)
mesh_id = fx.preview._target(mesh_target_edit, render, ['m_Mesh'])
assert any(row['identity'] == mesh_id for row in mesh_target_edit.state['author_assets'])
mesh_obj = fx.assets.source_meshes(mesh_target)[mesh_id]
assert mesh_obj.data.polygons and mesh_obj.data.uv_layers

(output / 'resources-result.json').write_text(json.dumps(dict(
    materialBrightness=50, replacementAssetCounts=counts,
    imagePixelsPreserved=True, meshPreserved=True,
    failedPasteRolledBack=True,
    gameResourcePayloadsCopied=0), indent=2), encoding='utf-8')
print('PASS real Material50, image pixels, Mesh/UV, operator projection, and stable replacement resource closure')
addon.unregister()

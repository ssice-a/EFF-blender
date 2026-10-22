"""Real effect materials use the existing .mat RNA editor and native writer."""
import base64
import importlib
import json
from pathlib import Path
import sys

import bpy

addon_path, source, output = sys.argv[sys.argv.index('--') + 1:]
output = Path(output); output.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(addon_path).resolve().parent))
addon = importlib.import_module(Path(addon_path).name); addon.register()
editor = addon.eiem_blender_addon.effect_editor
common = addon.eiem_blender_addon.material_editor
bridge = editor.material_bridge
root = editor.import_source(bpy.context, source)
initial_edits = editor.document.dumps(editor.editor(root).export()['objects'])
edit = editor.synchronize(root)
assert editor.document.dumps(edit.export()['objects'])==initial_edits, 'opening the common editor modified native material data'
node = next(o for o in editor.objects(root) if o.name.startswith('leaves_01'))
renderer = next(i for i in json.loads(node['eff_effect_components']) if edit.objects[i]['classId'] == 199)
mid = editor.preview._target(edit, renderer, ['m_Materials', 0])
author = editor.assets.material_object(root, mid)
material = bridge.material_for(author)
assert material and material['eiem_shader'] == 'HGRP/Effect/VFXDitherAlpha'
assert common.features.profile(material) == 'vfx_dither'
assert common.features.owner(material, 'eiem_value4._MainTex2Color') == 'main2'
assert common.features.owner(material, 'eiem_float._NormalScale') == 'normal'

# Compare both entry points, including every owner and RNA control, against
# the ordinary editor. Drawing must be read-only, with no alternate widgets.
class Layout:
    def __init__(self): self.calls = []
    def row(self, **kwargs): return self
    def split(self, **kwargs): return self
    def box(self): return self
    def separator(self): pass
    def label(self, **kwargs): self.calls.append(('label', kwargs))
    def prop(self, owner, name, **kwargs):
        getattr(owner, name)
        self.calls.append(('prop', owner.as_pointer(), name, kwargs))
    def operator(self, operator, **kwargs):
        op = type('Operation', (), {})()
        self.calls.append(('operator', operator, kwargs, op.__dict__))
        return op

mesh = node.eff_effect.particle_mesh
context = type('Context', (), dict(material=material, object=mesh))()
common_layout = Layout(); common.draw(common_layout, material)
empty_layout = Layout(); bridge.draw(empty_layout, author)
assert common_layout.calls == empty_layout.calls
panel = type('Panel', (), dict(layout=Layout()))()
addon.eiem_blender_addon.EFF_PT_material_properties.draw(panel, context)
assert panel.layout.calls == common_layout.calls, 'material panel still branches into an effect editor'
assert any(c[0] == 'operator' and c[1] == 'eiem.material_texture_file' for c in common_layout.calls)
assert not any(c[0] == 'operator' and c[1] == 'eiem.effect_texture_open' for c in common_layout.calls)
before = dict(material.items()); common.draw(Layout(), material)
assert before == dict(material.items())

# Edit real RNA widgets used by the original EFF Material UI.
material.eiem_editor_values['eiem_float._TintColorIntensity'].scalar = 2.75
material.eiem_editor_values['eiem_value4._TintColor'].color = (3, .5, .25, .7)
material.eiem_editor_values['eiem_value4._MainTexUVSpeed'].uv_speed = (.125, -.25)
material.eiem_editor_values['eiem_render_queue'].integer = 3025
material.eiem_editor_values['eiem_texture_scale._MainTex'].vector2 = (1.25, .75)
material.eiem_editor_values['eiem_texture_offset._MainTex'].vector2 = (.2, -.1)
material.eiem_editor_features['mask'].enabled = False
material.eiem_editor_features['disturb'].enabled = True
edit = editor.synchronize(root)
data = edit.value(mid)
def saved(group, name):
    return next(r['second'] for r in data['m_SavedProperties'][group] if r['first'] == name)
assert saved('m_Floats', '_TintColorIntensity') == 2.75
assert saved('m_Colors', '_TintColor')['r'] == 3
assert saved('m_Colors', '_MainTexUVSpeed')['g'] == -.25
assert data['m_CustomRenderQueue'] == 3025
assert saved('m_TexEnvs', '_MainTex')['m_Scale']['x'] == 1.25
assert abs(saved('m_TexEnvs', '_MainTex')['m_Offset']['y'] + .1) < 1e-6
assert saved('m_Floats', '_UseMask') == 0 and '_USE_MASK' not in data['m_ValidKeywords']
assert saved('m_Floats', '_UseDisturb') == 1 and '_USE_DISTURB' in data['m_ValidKeywords']
snapshot = json.dumps(data, sort_keys=True)
for _ in range(4): assert json.dumps(editor.synchronize(root).value(mid), sort_keys=True) == snapshot

# Both selection contexts run the ordinary folder operator, retaining source
# references until a file is explicitly selected. Selecting a path itself
# must not import an author Image into Blender.
import struct
import zlib
def png(path, rgba):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 4, 2, 8, 6, 0, 0, 0))
                     + chunk(b'IDAT', zlib.compress((b'\x00' + bytes(rgba) * 4) * 2)) + chunk(b'IEND', b''))
color = output / 'common-color.png'; mask = output / 'common-mask.png'
png(color, (128, 64, 192, 160)); png(mask, (240, 120, 60, 80))
image_count = len(bpy.data.images)
bpy.context.view_layer.objects.active = mesh
assert bpy.ops.eiem.material_texture_file(slot='_MainTex', filepath=str(color), material_name=material.name) == {'FINISHED'}
bpy.context.view_layer.objects.active = author
assert bpy.ops.eiem.material_texture_file(slot='_MaskTex', filepath=str(mask), material_name=material.name) == {'FINISHED'}
assert len(bpy.data.images) == image_count, 'file selection eagerly imported texture Images'
edit = editor.synchronize(root)
exported = editor.assets.export_document(root, edit)
assert sorted(edit.objects[a['template']]['classId'] for a in exported['assets']) == [21, 28, 28]
native = editor.assets.NativeAssets(edit, root['eff_effect_id'])
mat_asset = next(a for a in exported['assets'] if edit.objects[a['template']]['classId'] == 21)
template, _ = native.tree(mid)
decoded = native.codec.decode(template.schema, base64.b64decode(mat_asset['payloadBase64']))
assert decoded['m_CustomRenderQueue'].data == 3025
assert '_USE_DISTURB' in [v.data for v in decoded['m_ValidKeywords'].items()]
assert '_USE_MASK' not in [v.data for v in decoded['m_ValidKeywords'].items()]
floats = {v['first'].data: v['second'].data for v in decoded['m_SavedProperties']['m_Floats'].items()}
assert floats['_TintColorIntensity'] == 2.75 and floats['_UseDisturb'] == 1
colors = {v['first'].data: v['second'] for v in decoded['m_SavedProperties']['m_Colors'].items()}
assert colors['_TintColor']['r'].data == 3
uv = next(v['second'] for v in decoded['m_SavedProperties']['m_TexEnvs'].items() if v['first'].data == '_MainTex')
assert uv['m_Scale']['x'].data == 1.25
# External image editing is the same path-based workflow as regular materials:
# exporting again reads the updated PNG rather than a stale Blender buffer.
pid = uv['m_Texture']['m_PathID'].data
def main_pixels(doc):
    asset = next(a for a in doc['assets'] if a['identity'].endswith(':' + str(pid)))
    template, _ = native.tree(asset['template'])
    return native.codec.decode(template.schema, base64.b64decode(asset['payloadBase64']))['image data'].data
assert main_pixels(exported)[:4] == bytes((128, 64, 192, 160))
file_signature = bridge.signature(author)
png(color, (32, 96, 224, 192))
assert bridge.signature(author) != file_signature, 'external texture changes do not refresh preview'
exported = editor.assets.export_document(root, editor.synchronize(root))
assert main_pixels(exported)[:4] == bytes((32, 96, 224, 192)), 'export reused stale file pixels'
editor.assets.write_document(root, edit, output / 'common-material.effedit.json')

# Native-only edits migrate into the common properties. Simulate a saved
# project from before these shared controls existed, including packed pixels.
legacy = bpy.data.images.new('Legacy edited mask', width=2, height=2, alpha=True)
legacy.pixels[:] = [.2, .4, .6, .8] * 4; legacy.pack()
author.eff_effect_textures['_MaskTex'].image = legacy
for key in list(material.keys()):
    if key.startswith('eiem_') or key.startswith('eff_material_'): del material[key]
float_index = next(i for i, r in enumerate(edit.value(mid)['m_SavedProperties']['m_Floats']) if r['first'] == '_TintColorIntensity')
legacy_key = editor.field_key(mid, ['m_SavedProperties', 'm_Floats', float_index, 'second'])
author[legacy_key] = 4.125
editor.upgrade_loaded(); edit = editor.synchronize(root)
assert material['eiem_float._TintColorIntensity'] == '4.125'
assert author.eff_effect_textures['_MaskTex'].image == legacy
assert bridge.material_for(author) == material

# Choosing a common path supersedes the old packed image; clearing that path
# restores the source and must not resurrect the old Image on the next sync.
assert bpy.ops.eiem.material_texture_file(slot='_MaskTex', filepath=str(mask), material_name=material.name) == {'FINISHED'}
editor.synchronize(root)
assert author.eff_effect_textures['_MaskTex'].image is None
material.eiem_editor_values['eiem_texture._MaskTex'].text = ''
editor.synchronize(root)
assert bridge.image(root, mid, '_MaskTex') is None
mask_index = next(i for i, r in enumerate(edit.value(mid)['m_SavedProperties']['m_TexEnvs']) if r['first'] == '_MaskTex')
assert editor.assets.image_for_slot(root, edit, mid, mask_index, editor.preview._resources(root, edit)) != legacy

expected = editor.assets.export_document(root, editor.synchronize(root))
names = material.name, author.name
bpy.ops.wm.save_as_mainfile(filepath=str(output / 'common-material.blend'))
bpy.ops.wm.open_mainfile(filepath=str(output / 'common-material.blend'))
root = next(o for o in bpy.data.objects if o.get('eff_effect_document'))
material, author = bpy.data.materials[names[0]], bpy.data.objects[names[1]]
assert material.eff_effect_author == author
assert editor.assets.export_document(root, editor.synchronize(root)) == expected
for _ in range(2): addon.unregister(); addon.register()
editor.upgrade_loaded()
assert editor.assets.export_document(root, editor.synchronize(root)) == expected
print('PASS same editor/RNA, typed/HDR/UV/queue controls, shader switches, common file picker, native payloads, legacy migration, restore source, save/reopen and registration')
addon.unregister()

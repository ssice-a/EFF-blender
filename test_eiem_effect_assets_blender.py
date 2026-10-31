"""Real effect source -> custom mesh/images -> native build -> saved author state."""
import base64
import importlib
import json
import math
from pathlib import Path
import sys

import bpy
from mathutils import Vector

addon_path, source, output = sys.argv[sys.argv.index('--') + 1:]
output = Path(output); output.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(addon_path).resolve().parent))
addon = importlib.import_module(Path(addon_path).name); addon.register()
editor = addon.eiem_blender_addon.effect_editor
assets = editor.assets
root = editor.import_source(bpy.context, source)
edit = editor.synchronize(root)
baseline = assets.export_document(root, edit)
controller_edits = {identity: value for identity, value in edit.emission_controllers()}
baseline_objects = {row['identity']: row for row in baseline['objects']}
assert set(baseline_objects) == set(controller_edits)
assert not baseline.get('assets')
for identity, value in controller_edits.items():
    original = edit.state['original'][identity]
    assert value.get('m_Enabled') == 0
    assert {key: item for key, item in value.items() if key != 'm_Enabled'} == {
        key: item for key, item in original.items() if key != 'm_Enabled'
    }
    assert baseline_objects[identity]['data'] == value
node = next(o for o in editor.objects(root) if o.name.startswith('leaves_01'))
renderer = next(i for i in json.loads(node['eff_effect_components']) if edit.objects[i]['classId'] == 199)
mid = editor.preview._target(edit, renderer, ['m_Materials', 0])
material = assets.material_object(root, mid)
assert material and material.eff_effect_textures.get('_MainTex')

# Pull out the source mesh, then alter actual geometry and UVs, with a modifier.
bpy.context.view_layer.objects.active = node
assert bpy.ops.eiem.effect_mesh_edit(object_name=node.name, renderer=renderer) == {'FINISHED'}
custom = node.eff_effect.particle_mesh
assert custom and custom.data.uv_layers.active and custom.data.materials
custom.data.vertices[0].co.z += .07
custom.rotation_euler = (.2, -.1, .3)
custom.scale = (1.2, .8, 1.1)
custom.location = (5, 0, 0)  # Template placement must not move emitted particles.
modifier = custom.modifiers.new('可编辑细分', 'SUBSURF'); modifier.levels = 1; modifier.render_levels = 1
for uv in custom.data.uv_layers.active.data: uv.uv.x *= .8
mesh_preview = assets.preview_mesh(custom)
snapshot = assets.mesh_snapshot(custom)
assert snapshot['vertex_count'] > 4 and snapshot['uvs'][0]
points = [Vector(snapshot['vertices'][i:i+3]) for i in range(0, len(snapshot['vertices']), 3)]
assert all(any((v-p).length < 1e-5 for p in points) for v in mesh_preview[0]), 'preview and native mesh coordinates differ'

# Asymmetric pixels and alpha expose orientation, color and mipmap mistakes.
image = bpy.data.images.new('作者花瓣颜色', width=4, height=2, alpha=True)
image.colorspace_settings.name = 'Non-Color'
pixels = [c for y in range(2) for x in range(4) for c in (x/3, y, .25, (x+y)/4)]
image.pixels.foreach_set(pixels); image.update(); image.pack()
slot = material.eff_effect_textures['_MainTex']; slot.image = image; slot.linear = True; slot.mipmaps = True
mask = material.eff_effect_textures['_MaskTex']; mask.image = image; mask.linear = True; mask.mipmaps = False

# Renderer material and texture selectors must be visible in the real layouts.
class Layout:
    def __init__(self): self.labels=[]; self.props=[]
    def row(self,**kwargs): return self
    def split(self,**kwargs): return self
    def box(self): return self
    def separator(self): pass
    def panel(self,*args,**kwargs): return self,self
    def prop(self,*args,**kwargs): self.props.append(kwargs.get('text',''))
    def label(self,**kwargs): self.labels.append(kwargs.get('text',''))
    def operator(self,*args,**kwargs): return type('Op',(),{})()
layout=Layout()
editor.effect_ui.Draw(editor,node,edit,renderer).renderer(layout)
assert any('使用材质' in s for s in layout.labels)
editor.effect_ui.Draw(editor,material,edit,mid).material(layout)
assert '原始参数' in layout.props and '特效主图' in layout.labels

scene=bpy.context.scene; scene.frame_set(90)
editor.preview.refresh(root,editor.synchronize(root))
assert not root.get('预览错误'), root.get('预览错误')
preview_material=editor.preview._material(root,edit,renderer,editor.preview._resources(root,edit))
assert any(n.type=='TEX_IMAGE' and n.image==image for n in preview_material.node_tree.nodes)

document=assets.export_document(root,editor.synchronize(root))
assert len(document['assets'])==4, [(a['template'],a['identity']) for a in document['assets']]
native=assets.NativeAssets(edit,root['eff_effect_id'])
types=[edit.objects[a['template']]['classId'] for a in document['assets']]
assert sorted(types)==[21,28,28,43]
texture=next(a for a in document['assets'] if edit.objects[a['template']]['classId']==28)
template,_=native.tree(texture['template'])
decoded=native.codec.decode(template.schema,base64.b64decode(texture['payloadBase64']))
assert decoded['m_MipCount'].data==3
raw=decoded['image data'].data
assert decoded['m_TextureFormat'].data in (25,27)
assert len(raw)==3*16
from PIL import Image
color_file=output/'color-space-check.png'
color_bytes=bytes((64,128,192,123, 240,32,96,255))
Image.frombytes('RGBA',(2,1),color_bytes).save(color_file)
loaded=bpy.data.images.load(str(color_file.resolve()),check_existing=True)
source_color_space=template['m_ColorSpace'].data
previous=slot.linear;slot.linear=not bool(source_color_space)
color_tree=assets.texture_tree(template,loaded,slot)
assert color_tree['m_TextureFormat'].data in (25,27)
assert len(color_tree['image data'].data)==16
assert color_tree['m_ColorSpace'].data==source_color_space,'export overrode the source Texture2D color space'
slot.linear=previous
assert all(not a['identity'] in edit.objects for a in document['assets'])
# Both source emitters share the imported Mesh and retain that shared binding.
other=next(o for o in editor.objects(root) if o.name.startswith('leaves_02'))
other_renderer=next(i for i in json.loads(other['eff_effect_components']) if edit.objects[i]['classId']==199)
other_edit=next(o for o in document['objects'] if o['identity']==other_renderer)
assert other.eff_effect.particle_mesh == custom
this_edit=next(o for o in document['objects'] if o['identity']==renderer)
assert other_edit['data']['m_Mesh']==this_edit['data']['m_Mesh']
assert other_edit['data']['m_Mesh']!=edit.value(other_renderer)['m_Mesh']
assert assets.export_document(root,editor.synchronize(root))==document,'asset export is not deterministic'
assets.write_document(root,editor.synchronize(root),output/'authored.effedit.json')

node_name,material_name=node.name,material.name
bpy.ops.wm.save_as_mainfile(filepath=str(output/'custom-petals.blend'))
bpy.ops.wm.open_mainfile(filepath=str(output/'custom-petals.blend'))
root=next(o for o in bpy.data.objects if o.get('eff_effect_document'))
node=bpy.data.objects[node_name]; material=bpy.data.objects[material_name]
assert node.eff_effect.particle_mesh and material.eff_effect_textures['_MainTex'].image
assert assets.export_document(root,editor.synchronize(root))==document,'save/reopen lost mesh/texture authoring'
for _ in range(3):
    addon.unregister(); addon.register()
root=next(o for o in bpy.data.objects if o.get('eff_effect_document'))
editor.upgrade_loaded()
assert assets.export_document(root,editor.synchronize(root))==document,'register/reload changed authoring'
print('PASS custom mesh/modifier/UV, material slots, asymmetric RGBA/mips, preview coordinates, shared clones, determinism and save/reopen')
addon.unregister()

"""Source assets are editable on import; joining geometry keeps native bindings."""
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
assets = editor.assets
root = editor.import_source(bpy.context, source)
edit = editor.synchronize(root)
emitters = [o for o in editor.objects(root) if o.eff_effect.particle_mesh]
assert len(emitters) == 2
particle = emitters[0].eff_effect.particle_mesh
assert emitters[1].eff_effect.particle_mesh == particle
assert particle.name.startswith('Plane001') and particle.parent == root
assert particle.hide_get() and particle.hide_render
assert len(assets.source_meshes(root)) == 1
mat = particle.data.materials[0]
author = mat.eff_effect_author
assert author and editor.root_of(author) == root
assert all(s.image is None for s in author.eff_effect_textures), 'import forced texture replacements'
baseline = assets.export_document(root, edit)
assert not any(edit.objects[r['identity']]['classId']!=114 for r in baseline['objects']) and not baseline.get('assets'), 'unchanged import changed the native assets'
names = set(bpy.data.objects.keys())
assets.expand_meshes(bpy.context, root, edit)
assert set(bpy.data.objects.keys()) == names, 'resource expansion duplicated assets'

# The actual Material property page edits the same native author values.
class Layout:
    def __init__(self): self.labels=[]; self.props=[]; self.operators=[]
    def row(self, **kwargs): return self
    def split(self, **kwargs): return self
    def box(self): return self
    def separator(self, **kwargs): pass
    def panel(self, *args, **kwargs): return self, self
    def prop(self, *args, **kwargs): self.props.append(kwargs.get('text', ''))
    def label(self, **kwargs):
        assert kwargs.get('icon') != 'ERROR', kwargs.get('text')
        self.labels.append(kwargs.get('text', ''))
    def operator(self, name, **kwargs):
        self.operators.append(name)
        return type('Op', (), {})()

context = type('Context', (), {'material': mat, 'object': particle})()
panel = addon.eiem_blender_addon.EFF_PT_material_properties
assert panel.poll(context)
assert not hasattr(bpy.types, 'EFF_PT_effect_material'), 'effect created a duplicate material panel'
layout = Layout()
panel.draw(type('Panel', (), {'layout': layout})(), context)
assert '原始参数' in layout.props and '特效主图' in layout.labels
assert 'eiem.material_texture_file' in layout.operators and 'eiem.effect_texture_open' not in layout.operators
assert 'eiem.import_material' not in layout.operators, 'effect binding showed the unrelated character .mat import'
ordinary = bpy.data.materials.new('普通材质入口测试')
ordinary_context = type('Context', (), {'material': ordinary, 'object': particle})()
ordinary_layout = Layout()
panel.draw(type('Panel', (), {'layout': ordinary_layout})(), ordinary_context)
assert 'eiem.import_material' in ordinary_layout.operators, 'normal material import entry changed'
assert '替换图片' not in ordinary_layout.props
mid = assets.material_identity(root, mat)
floats = edit.value(mid)['m_SavedProperties']['m_Floats']
field = editor.field_key(mid, ['m_SavedProperties', 'm_Floats', 0, 'second'])
author[field] = float(floats[0]['second']) + .125
editor.synchronize(root)
assert edit.value(mid)['m_SavedProperties']['m_Floats'][0]['second'] == author[field]

# Join an authored triangle into the source mesh with the source as active.
bpy.ops.object.select_all(action='DESELECT')
particle.hide_set(False); particle.select_set(True)
mesh = bpy.data.meshes.new('作者花瓣')
mesh.from_pydata([(0,0,0),(.1,0,0),(0,.2,.04)], [], [(0,1,2)])
mesh.uv_layers.new(name='UV0')
foreign_mat = bpy.data.materials.new('普通 Blender 材质')
mesh.materials.append(foreign_mat)
own = bpy.data.objects.new('自己的模型', mesh)
bpy.context.scene.collection.objects.link(own); own.select_set(True)
bpy.context.view_layer.objects.active = particle
original_vertices = len(particle.data.vertices)
assert bpy.ops.object.join() == {'FINISHED'}
assert len(particle.data.vertices) == original_vertices + 3
assert all(e.eff_effect.particle_mesh == particle for e in emitters)
try:
    assets.export_document(root, editor.synchronize(root))
    raise AssertionError('multiple used face materials exported silently')
except ValueError as error:
    assert '一个面材质' in str(error), str(error)

# Leave an unused Blender material in slot zero and use EFF material in slot 1.
particle.data.materials.clear()
particle.data.materials.append(foreign_mat); particle.data.materials.append(mat)
for polygon in particle.data.polygons: polygon.material_index = 1
for node in emitters:
    renderer = next(i for i in json.loads(node['eff_effect_components']) if edit.objects[i]['classId'] == 199)
    assert assets.renderer_material(root, edit, renderer) == mid
particle_id = next(i for i in json.loads(emitters[0]['eff_effect_components']) if edit.objects[i]['classId'] == 198)
shape_mesh = ['ShapeModule', 'm_Mesh']
if 'm_Mesh' in edit.value(particle_id)['ShapeModule']:
    edit.set_pointer(particle_id, shape_mesh, particle['eff_effect_mesh_source'])
    editor.store(root, edit)
result = assets.export_document(root, editor.synchronize(root))
mesh_assets = [a for a in result['assets'] if edit.objects[a['template']]['classId'] == 43]
assert len(mesh_assets) == 1, 'shared emitters duplicated their Mesh'
native = assets.NativeAssets(edit, root['eff_effect_id'])
template, _ = native.tree(mesh_assets[0]['template'])
decoded = native.codec.decode(template.schema, base64.b64decode(mesh_assets[0]['payloadBase64']))
assert decoded['m_VertexData']['m_VertexCount'].data >= original_vertices + 3
patches = {o['identity']: o['data'] for o in result['objects']}
renderers = [next(i for i in json.loads(o['eff_effect_components']) if edit.objects[i]['classId'] == 199) for o in emitters]
assert patches[renderers[0]]['m_Mesh'] == patches[renderers[1]]['m_Mesh']
if 'm_Mesh' in edit.value(particle_id)['ShapeModule']:
    assert patches[particle_id]['ShapeModule']['m_Mesh'] == patches[renderers[0]]['m_Mesh'], 'emitter Mesh reference lost its edited resource'
assets.write_document(root, editor.synchronize(root), output / 'joined.effedit.json')

# A normal Blender Shader must never be mistaken for an exportable game Shader.
particle.material_slots[1].material = foreign_mat
try:
    assets.export_document(root, editor.synchronize(root))
    raise AssertionError('ordinary Blender material exported silently')
except ValueError as error:
    assert 'EFF 材质' in str(error), str(error)
particle.material_slots[1].material = mat

root_name, particle_name, mat_name = root.name, particle.name, mat.name
bpy.ops.wm.save_as_mainfile(filepath=str(output / 'joined.blend'))
bpy.ops.wm.open_mainfile(filepath=str(output / 'joined.blend'))
root = bpy.data.objects[root_name]; particle = bpy.data.objects[particle_name]; mat = bpy.data.materials[mat_name]
assert mat.eff_effect_author and particle.material_slots[1].material == mat
assert assets.export_document(root, editor.synchronize(root)) == result, 'save/reopen lost resource edits'

# Old files have an explicit completion action, which preserves custom choices.
old = editor.import_source(bpy.context, source)
old['eff_effect_assets_version'] = 0
node = next(o for o in editor.objects(old) if o.eff_effect.particle_mesh)
node.eff_effect.particle_mesh = particle
bpy.context.view_layer.objects.active = old
assert bpy.ops.eiem.effect_assets_expand() == {'FINISHED'}
assert node.eff_effect.particle_mesh == particle
print('PASS automatic editable Mesh, shared bindings, unchanged native baseline, existing texture workflow, Material page, real join, face slots, native Mesh, validation, save/reopen and old-scene completion')
addon.unregister()

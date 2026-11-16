"""Render actual preview shaders: static/instanced UVs, alpha, scrolling and clip.

Run in factory-startup Blender with -- ADDON OUTPUT.
"""
import importlib
from pathlib import Path
import sys
from unittest.mock import patch

import bpy
from mathutils import Matrix

addon_path, output = (Path(p).resolve() for p in sys.argv[sys.argv.index('--') + 1:])
sys.path.insert(0, str(addon_path.resolve().parent))
addon = importlib.import_module(addon_path.name)
addon.register()
preview = addon.eiem_blender_addon.effect_editor.preview
geometry = preview.particle_geometry
for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)
root = bpy.data.objects.new('Material preview test', None)
bpy.context.scene.collection.objects.link(root)
root['eff_effect_id'] = 'preview-material-render'
image = bpy.data.images.new('Two color alpha source', width=2, height=1, alpha=True, float_buffer=True)
image.pixels[:] = [1, 0, 0, .2, 0, 0, 1, .8]
image.alpha_mode = 'STRAIGHT'
mask = bpy.data.images.new('Mask source', width=1, height=1, alpha=True, float_buffer=True)
mask.pixels[:] = [.25, .25, .25, 1]

class Edit:
    def __init__(self):
        self.state = {'values': {'material': {
            'm_ValidKeywords': [],
            'm_SavedProperties': {
                'm_Floats': [{'first': name, 'second': value} for name, value in
                (('_SurfaceType', 1), ('_DstBlend', 10), ('_UseMask', 0), ('_UseBlend', 0),
                 ('_MainUVSet', 0))],
                'm_Colors': [{'first': '_MainTexUVSpeed', 'second': dict(r=.5, g=0, b=0, a=0)}],
                'm_TexEnvs': [{'first': name, 'second': {
                    'm_Scale': dict(x=1, y=1), 'm_Offset': dict(x=0, y=0)}}
                    for name in ('_MainTex', '_MaskTex')]
            }}}}
        self.objects = {'material': {'name': 'Preview source'}}
    def value(self, identity):
        return self.state['values'][identity]
    def scalar(self, name, value):
        fields = self.value('material')['m_SavedProperties']['m_Floats']
        fields[:] = [row for row in fields if row['first'] != name]
        fields.append(dict(first=name, second=value))

edit = Edit()
quad = ([(-.5, -.5, 0), (.5, -.5, 0), (.5, .5, 0), (-.5, .5, 0)],
        [(0, 1, 2, 3)], [(0, 0), (1, 0), (1, 1), (0, 1)], False)

def material(instance):
    with patch.object(preview.assets, 'material_object', return_value=None), \
         patch.object(preview.assets, 'image_for_slot', side_effect=lambda *args: image if args[3] == 0 else mask):
        result = preview._material_asset(root, edit, 'material', {}, instance=instance,
                                         particle_attributes=instance == 'particle')
    for node in result.node_tree.nodes:
        if node.type == 'TEX_IMAGE':
            node.interpolation = 'Closest'
    return result

static_mat = material('static')
def link_signature(material):
    return tuple((link.from_node.as_pointer(), link.from_socket.identifier,
                  link.to_node.as_pointer(), link.to_socket.identifier)
                 for link in material.node_tree.links)

links_before = link_signature(static_mat)
positions = [tuple(round(value, 3) for value in node.location)
             for node in static_mat.node_tree.nodes]
assert static_mat.get('eff_preview_node_layout') == 'v1'
assert len(set(positions)) == len(positions), 'generated shader nodes still overlap'
assert len(set(positions)) > 1
preview._layout_material_nodes(static_mat.node_tree)
assert link_signature(static_mat) == links_before, 'node layout changed shader links'
assert [tuple(round(value, 3) for value in node.location)
        for node in static_mat.node_tree.nodes] == positions, 'node layout is not deterministic'
mesh = bpy.data.meshes.new('Static quad'); mesh.from_pydata(quad[0], [], quad[1])
uv = mesh.uv_layers.new(name='UVMap')
uv.data.foreach_set('uv', [v for loop in mesh.loops for v in quad[2][loop.vertex_index]])
color = mesh.color_attributes.new(name='EFFColor', type='FLOAT_COLOR', domain='POINT')
color.data.foreach_set('color', [1.] * 16)
obj = bpy.data.objects.new('Static preview', mesh)
bpy.context.scene.collection.objects.link(obj); obj.location.x = -1
mesh.materials.append(static_mat)
particle = bpy.data.objects.new('Particle preview', bpy.data.meshes.new('Point'))
bpy.context.scene.collection.objects.link(particle)
particle_mat = material('particle')
geometry.update(particle, quad, particle_mat, [Matrix.Translation((1, 0, 0))],
                [(1, 1, 1, .5)], [(.5, 1, 1)], [(.5, 0, 0)])

scene = bpy.context.scene
camera = bpy.data.objects.new('Preview camera', bpy.data.cameras.new('Preview camera'))
scene.collection.objects.link(camera); camera.location = (0, 0, 5)
camera.data.type = 'ORTHO'; camera.data.ortho_scale = 4; scene.camera = camera
scene.render.engine = 'CYCLES'; scene.cycles.device = 'CPU'; scene.cycles.samples = 1; scene.render.film_transparent = True
scene.render.resolution_x = 128; scene.render.resolution_y = 64; scene.render.resolution_percentage = 100
scene.view_settings.view_transform = 'Standard'; scene.render.image_settings.file_format = 'PNG'
scene.render.fps = 24; scene.frame_start = 1
output.mkdir(parents=True, exist_ok=True)

def render(name):
    scene.render.filepath = str(output / (name + '.png'))
    bpy.ops.render.render(write_still=True)
    pixels = bpy.data.images.load(scene.render.filepath, check_existing=False)
    values = list(pixels.pixels)
    def sample(x):
        return values[(32 * 128 + x) * 4:(32 * 128 + x + 1) * 4]
    return sample(24), sample(40), sample(96)

scene.frame_set(1)
red, blue, instance = render('static-and-particle')
assert red[0] > .8 and red[2] < .1, red
assert blue[2] > .8 and blue[0] < .1, blue
assert red[3] < blue[3] and abs(blue[3] / red[3] - 4) < .15, (red, blue)
assert instance[2] > .7 and instance[0] < .1, instance
unmasked_alpha = (red[3], blue[3])
assert not any(n.type == 'ATTRIBUTE' and n.attribute_name == geometry.UV_SCALE for n in static_mat.node_tree.nodes)

# PolarUV is not UV2. Until its game shader formula is recovered, the preview
# must keep using the mesh's active UV map so the source texture does not vanish.
edit.scalar('_MainUVSet', 2)
polar_mat = material('polar-fallback')
mesh.materials.clear(); mesh.materials.append(polar_mat)
polar_red, polar_blue, _ = render('polar-uv-fallback')
assert polar_red[0] > .8 and polar_red[2] < .1, polar_red
assert polar_blue[2] > .8 and polar_blue[0] < .1, polar_blue
polar_uv_node = next(n for n in polar_mat.node_tree.nodes
                     if n.type == 'UVMAP' and n.get('eff_uv_preview_mode') == 'PolarUV')
assert polar_uv_node.uv_map == '' and 'UV0 preview fallback' in polar_uv_node.label

# The UV mode can then return to the normal UV0 test path for the rest of the
# material checks; this isolates its effect from scrolling and alpha behavior.
edit.scalar('_MainUVSet', 0)
mesh.materials.clear(); mesh.materials.append(static_mat)

scene.frame_set(25)
preview._material_values(static_mat, edit, 'material')
red, blue, _ = render('scroll-one-second')
assert red[2] > .8 and blue[0] > .8, (red, blue)

scene.frame_set(1)
preview._material_values(static_mat, edit, 'material')
edit.scalar('_EnableAlphaTest', 1)
edit.scalar('_AlphaClipThreshold', .5)
static_mat = material('static')
red, blue, _ = render('alpha-clip')
assert red[3] < .02 and blue[3] > .4, (red, blue)
edit.scalar('_EnableAlphaTest', 0)
edit.scalar('_UseMask', 1)
edit.scalar('_UseMaskTexAsAlpha', 1)
# The playback frame must invalidate the cached material structure after an
# author edit. Exercise real node creation through that handler, then render it.
root['eff_effect_document'] = 'material-frame-test'
root.eff_effect.preview = True
preview._session(root).accept(edit, 'before-mask')
preview._pending_updates[root.as_pointer()] = (0, set())
rebuilt = []
def update_material(*args):
    rebuilt.append(material('static'))
with patch.object(preview, '_sync', return_value=edit) as synchronize, \
     patch.object(preview, 'author_signature', return_value='after-mask'), \
     patch.object(preview, 'update', side_effect=update_material):
    preview.frame(scene)
    static_mat = rebuilt[-1]
    mask_signature = static_mat['eff_source_signature']
    node_pointers = {node.as_pointer() for node in static_mat.node_tree.nodes}
    preview.frame(scene)
    assert synchronize.call_count == 1
    assert rebuilt[-1] == static_mat and static_mat['eff_source_signature'] == mask_signature
    assert {node.as_pointer() for node in static_mat.node_tree.nodes} == node_pointers
root.eff_effect.preview = False
del root['eff_effect_document']
red, blue, _ = render('mask-red-channel')
assert red[3] < unmasked_alpha[0] * .4 and blue[3] < unmasked_alpha[1] * .4, (red, blue, unmasked_alpha)

# Exercise the native packed rotation matrix and UV order numerically in a render.
# A half-turn changes U=.25 to .75; ST=(.5,1)+(.25,0) maps it to .625 (blue).
edit.scalar('_UseMask', 0)
fields = edit.value('material')['m_SavedProperties']
fields['m_Colors'].append(dict(first='_MainTexUVRotateMat', second=dict(r=-1, g=0, b=0, a=-1)))
fields['m_TexEnvs'][0]['second']['m_Scale']['x'] = .5
fields['m_TexEnvs'][0]['second']['m_Offset']['x'] = .25
static_mat = material('static')
red, blue, _ = render('rotate-before-st')
assert red[2] > .8 and blue[0] > .8, (red, blue)
addon.unregister()
print('PASS rendered static and GN UVs, source alpha, scroll, clip, mask and UV transform order')

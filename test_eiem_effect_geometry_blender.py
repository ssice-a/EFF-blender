"""Verify editable particle instances and shader-visible per-instance streams."""
import sys
from mathutils import Matrix
import bpy
from pathlib import Path

addon_path = sys.argv[sys.argv.index('--') + 1]
sys.path.insert(0, str(addon_path))
import eiem_effect_geometry as geometry

bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
source = ([( -.5, -.5, 0), (.5, -.5, 0), (.5, .5, 0), (-.5, .5, 0)],
          [(0, 1, 2, 3)], [(0, 0), (1, 0), (1, 1), (0, 1)], False)
material = bpy.data.materials.new('GN particle test')
points = bpy.data.meshes.new('Particle points')
obj = bpy.data.objects.new('Particle preview', points)
bpy.context.scene.collection.objects.link(obj)
transforms = [Matrix.Translation((-2, 0, 0)), Matrix.Translation((3, 1, 0))]
geometry.update(obj, source, material, transforms,
                [(1, 0, 0, .25), (0, 1, 0, .75)],
                [(.5, .5, 1), (.5, .5, 1)],
                [(.125, 0, 0), (.625, 0, 0)])
bpy.context.view_layer.update()
depsgraph = bpy.context.evaluated_depsgraph_get()
instances = [(row.object.name, row.parent.name if row.parent else None,
              row.parent.original.name if row.parent else None, row.matrix_world.copy())
             for row in depsgraph.object_instances if row.is_instance]
assert len(obj.data.vertices) == 2 and not obj.data.polygons
assert len(instances) == 2, instances
assert all(parent_original == obj.name for _, _, parent_original, _ in instances), instances
translations = sorted(tuple(round(v, 4) for v in matrix.translation[:2]) for _, _, _, matrix in instances)
assert translations == [(-2, 0), (3, 1)], translations
assert list(obj.data.attributes[geometry.COLOR].data[0].color) == [1, 0, 0, .25]
assert list(obj.data.attributes[geometry.UV_SCALE].data[1].vector) == [.5, .5, 1]
assert list(obj.data.attributes[geometry.UV_OFFSET].data[1].vector) == [.625, 0, 0]
tree = next(m.node_group for m in obj.modifiers if m.type == 'NODES')
assert tree.get('eff_particle_preview')
assert next(node for node in tree.nodes if node.type == 'OBJECT_INFO').inputs['Object'].default_value.name.startswith('粒子模板')

# Render the real point attributes through an instance-domain material.
material.use_nodes = True
material.surface_render_method = 'BLENDED'
nodes = material.node_tree.nodes; nodes.clear(); links = material.node_tree.links
attribute = nodes.new('ShaderNodeAttribute'); attribute.attribute_type = 'INSTANCER'
attribute.attribute_name = geometry.COLOR
uv_map = nodes.new('ShaderNodeUVMap'); uv_map.uv_map = 'UVMap'
uv_scale = nodes.new('ShaderNodeAttribute'); uv_scale.attribute_type = 'INSTANCER'; uv_scale.attribute_name = geometry.UV_SCALE
uv_offset = nodes.new('ShaderNodeAttribute'); uv_offset.attribute_type = 'INSTANCER'; uv_offset.attribute_name = geometry.UV_OFFSET
scaled_uv = nodes.new('ShaderNodeVectorMath'); scaled_uv.operation = 'MULTIPLY'
links.new(uv_map.outputs['UV'], scaled_uv.inputs[0]); links.new(uv_scale.outputs['Vector'], scaled_uv.inputs[1])
offset_uv = nodes.new('ShaderNodeVectorMath'); offset_uv.operation = 'ADD'
links.new(scaled_uv.outputs['Vector'], offset_uv.inputs[0]); links.new(uv_offset.outputs['Vector'], offset_uv.inputs[1])
image = bpy.data.images.new('Two color UV test', width=2, height=1, alpha=True)
image.pixels[:] = [1, 0, 0, 1, 0, 1, 0, 1]
texture = nodes.new('ShaderNodeTexImage'); texture.image = image; texture.interpolation = 'Closest'
links.new(offset_uv.outputs['Vector'], texture.inputs['Vector'])
emission = nodes.new('ShaderNodeEmission'); links.new(texture.outputs['Color'], emission.inputs['Color'])
transparent = nodes.new('ShaderNodeBsdfTransparent'); mix = nodes.new('ShaderNodeMixShader')
links.new(attribute.outputs['Alpha'], mix.inputs[0]); links.new(transparent.outputs[0], mix.inputs[1]); links.new(emission.outputs[0], mix.inputs[2])
output = nodes.new('ShaderNodeOutputMaterial'); links.new(mix.outputs[0], output.inputs['Surface'])
camera_data = bpy.data.cameras.new('Preview camera'); camera = bpy.data.objects.new('Preview camera', camera_data)
bpy.context.scene.collection.objects.link(camera); camera.location = (0, 0, 5); camera_data.type = 'ORTHO'; camera_data.ortho_scale = 8
scene = bpy.context.scene; scene.camera = camera; scene.render.engine = 'CYCLES'; scene.cycles.device = 'CPU'; scene.cycles.samples = 1
scene.render.resolution_x = 128; scene.render.resolution_y = 64; scene.render.resolution_percentage = 100
scene.render.film_transparent = True; scene.view_settings.view_transform = 'Standard'
scene.render.filepath = str(Path(bpy.app.tempdir) / 'eff_geometry_nodes.png')
bpy.ops.render.render(write_still=True)
image = bpy.data.images.load(scene.render.filepath, check_existing=False); pixels = list(image.pixels)
def rgba(x, y): return pixels[(y * image.size[0] + x) * 4:(y * image.size[0] + x + 1) * 4]
left = rgba(32, 32); right = rgba(112, 48)
assert left[0] > .8 and left[1] < .1 and abs(left[3] - .25) < .08, left
assert right[1] > .8 and right[0] < .1 and abs(right[3] - .75) < .08, right
print('PASS 2 editable GN instances, per-particle matrix/color/UV streams and rendered alpha/color')

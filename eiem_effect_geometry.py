"""Particle preview: one point per particle and editable GN instances.

The point attributes are sampled by the existing native-module simulation.
The node tree is a display adapter, never an alternate export format.
"""
import bpy

_templates = {}
TRANSFORM = 'EFFTransform'
COLOR = 'EFFParticleColor'
UV_SCALE = 'EFFUVScale'
UV_OFFSET = 'EFFUVOffset'
MARKER = 'EFFParticle'


def _attribute(mesh, name, kind, values, field='vector'):
    attr = mesh.attributes.get(name) or mesh.attributes.new(name, kind, 'POINT')
    attr.data.foreach_set(field, values)


def _template(obj, geometry, material):
    source = obj.get('eff_preview_template')
    source = bpy.data.objects.get(source) if isinstance(source, str) else source
    if not source:
        mesh = bpy.data.meshes.new('EFF particle template')
        source = bpy.data.objects.new('粒子模板 · ' + obj.name, mesh)
        obj.users_collection[0].objects.link(source)
        source.parent = obj.parent
        source['eff_preview'] = True
        obj['eff_preview_template'] = source.name
    # Hidden templates still supply Object Info geometry in EEVEE / Cycles.
    source.hide_viewport = False
    source.hide_render = True
    source.hide_set(True)
    source.hide_select = True
    mesh = source.data
    if _templates.get(source.as_pointer()) is not geometry:
        vertices, faces, uv, _ = geometry
        mesh.clear_geometry()
        mesh.from_pydata(vertices, [], faces)
        for name in ('UVMap', 'UV0'):
            layer = mesh.uv_layers.get(name) or mesh.uv_layers.new(name=name)
            layer.data.foreach_set('uv', [v for loop in mesh.loops for v in uv[loop.vertex_index]])
        tint = mesh.color_attributes.get('EFFColor') or mesh.color_attributes.new(name='EFFColor', type='FLOAT_COLOR', domain='POINT')
        tint.data.foreach_set('color', [1.] * (4 * len(vertices)))
        _templates[source.as_pointer()] = geometry
    if not mesh.materials:mesh.materials.append(material)
    elif mesh.materials[0] != material:mesh.materials[0] = material
    return source


def _nodes(obj, source):
    modifier = next((m for m in obj.modifiers if m.type == 'NODES' and m.node_group and m.node_group.get('eff_particle_preview')), None)
    if modifier and modifier.node_group:
        tree=modifier.node_group
        info=next((n for n in tree.nodes if n.type=='OBJECT_INFO'),None)
        if info:info.inputs['Object'].default_value=source
        return tree
    if not modifier:modifier = obj.modifiers.new('EFF · 粒子实例', 'NODES')
    tree = bpy.data.node_groups.new('EFF · 粒子实例 · ' + obj.name, 'GeometryNodeTree')
    tree['eff_particle_preview'] = True
    tree.description = '原生粒子模块采样 → 点属性 → 网格实例；节点修改只影响 Blender 预览'
    tree.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    tree.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    modifier.node_group = tree
    nodes, links = tree.nodes, tree.links
    inp = nodes.new('NodeGroupInput');inp.location = (-500, 100)
    src = nodes.new('GeometryNodeObjectInfo');src.label = '粒子模板（保留 UV / 材质）';src.location = (-280, -80)
    src.inputs['Object'].default_value=source
    inst = nodes.new('GeometryNodeInstanceOnPoints');inst.label = '每个存活粒子一个实例';inst.location = (0, 100)
    links.new(inp.outputs['Geometry'], inst.inputs['Points'])
    links.new(src.outputs['Geometry'], inst.inputs['Instance'])
    attr = nodes.new('GeometryNodeInputNamedAttribute');attr.data_type = 'FLOAT4X4'
    attr.inputs['Name'].default_value = TRANSFORM;attr.label = '完整变换（含非均匀缩放 / 剪切）';attr.location = (0, -180)
    transform = nodes.new('GeometryNodeSetInstanceTransform');transform.location = (240, 100)
    links.new(inst.outputs[0], transform.inputs['Instances'])
    links.new(attr.outputs['Attribute'], transform.inputs['Transform'])
    out = nodes.new('NodeGroupOutput');out.location = (480, 100)
    links.new(transform.outputs[0], out.inputs['Geometry'])
    return tree


def update(obj, geometry, material, transforms, colors, uv_scales, uv_offsets):
    source = _template(obj, geometry, material)
    _nodes(obj, source)
    mesh = obj.data
    count = len(transforms)
    if len(mesh.vertices) != count or mesh.polygons:
        mesh.clear_geometry()
        mesh.from_pydata([(0., 0., 0.)] * count, [], [])
    # FLOAT4X4 foreach_set stores each matrix in column-major buffer order.
    _attribute(mesh, TRANSFORM, 'FLOAT4X4', [v for m in transforms for col in m.transposed() for v in col], 'value')
    _attribute(mesh, COLOR, 'FLOAT_COLOR', [v for c in colors for v in c], 'color')
    _attribute(mesh, UV_SCALE, 'FLOAT_VECTOR', [v for s in uv_scales for v in s])
    _attribute(mesh, UV_OFFSET, 'FLOAT_VECTOR', [v for s in uv_offsets for v in s])
    _attribute(mesh, MARKER, 'FLOAT', [1.] * count, 'value')
    # Keep the assigned material visible in Properties on the point object.
    if not mesh.materials:mesh.materials.append(material)
    elif mesh.materials[0] != material:mesh.materials[0] = material
    mesh.update()
    obj.hide_select = False
    obj['eff_preview_geometry_nodes'] = True
    obj['预览粒子数'] = count


def clear_cache():
    _templates.clear()

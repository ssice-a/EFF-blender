"""Headless tangent export regressions; does not touch the user's Blender."""
import importlib.util
import math
import struct
import sys
import types
from pathlib import Path

import bpy
from mathutils import Vector

addon_path, output = map(Path, sys.argv[sys.argv.index('--') + 1:])
sys.path.insert(0, str(addon_path.parent))
package_name = 'EIEM_Blender'
for module_name in tuple(sys.modules):
    if module_name == package_name or module_name.startswith(package_name + '.'):
        del sys.modules[module_name]
package = types.ModuleType(package_name)
package.__path__ = [str(addon_path.parent)]
sys.modules[package_name] = package
spec = importlib.util.spec_from_file_location(
    package_name + '.eiem_blender_addon', addon_path)
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)


def bits(values):
    return struct.pack('<%df' % len(values), *values)


def make_object(name, uv=True):
    mesh = bpy.data.meshes.new(name)
    # Mirrored UV charts meet at vertex 0 with equal UV/normal but opposite
    # tangent direction/sign. Only the tangent seam forces this vertex split.
    mesh.from_pydata([(0,0,0),(1,0,0),(0,1,0),(-1,0,0),(0,-1,0), (9,9,9)],
                     [], [(0,1,2),(0,3,4)])
    mesh['eiem_coordinate_space'] = 'unity-y-up-left-handed'
    if uv:
        for layer_name in ('UV0', 'UV2'):
            layer = mesh.uv_layers.new(name=layer_name)
            for i, value in enumerate(((0,0),(1,0),(0,1),(0,0),(1,0),(0,-1))):
                layer.data[i].uv = value
        mesh.uv_layers.active_index = 1  # Generation must explicitly use UV0.
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def make_ngon_object(name):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(
        [(0, 0, 0), (1, 0, 0), (1.5, .8, 0), (.5, 1.4, 0), (-.3, .8, 0)],
        [], [(0, 1, 2, 3, 4)])
    mesh['eiem_coordinate_space'] = 'unity-y-up-left-handed'
    for layer_name in ('UV0', 'UV2'):
        layer = mesh.uv_layers.new(name=layer_name)
        for i, value in enumerate(((0,0), (1,0), (1,1), (.5,1), (0,1))):
            layer.data[i].uv = value
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def export(obj, name):
    mesh = obj.data
    before = (len(bpy.data.meshes),
              tuple((a.name, a.domain, a.data_type) for a in mesh.attributes),
              tuple(tuple(c.vector) for c in mesh.corner_normals),
              tuple(tuple(tuple(v.uv) for v in layer.data) for layer in mesh.uv_layers))
    file = output / (name + '.mesh')
    addon.write_mesh(file, obj)
    first = file.read_bytes()
    addon.write_mesh(file, obj)
    assert first == file.read_bytes(), 'Non-deterministic tangent generation'
    after = (len(bpy.data.meshes),
             tuple((a.name, a.domain, a.data_type) for a in mesh.attributes),
             tuple(tuple(c.vector) for c in mesh.corner_normals),
             tuple(tuple(tuple(v.uv) for v in layer.data) for layer in mesh.uv_layers))
    assert before == after, 'Export changed author data or leaked a temporary Mesh'
    return addon.read_mesh(file)


def corner_indices(payload):
    result = []
    for start in range(0, len(payload['indices']), 3):
        a,b,c = payload['indices'][start:start+3]
        result.extend((a,c,b))  # Back to Blender winding.
    return result


# Blender may re-encode an imported split normal by a tiny amount after an
# update/save. That is still the same author normal and must not trigger a full
# current-normal export. A visible direction edit must trigger it.
normal_probe = make_object('NormalEncodingProbe')
source_normals = [(0, 0, 1)] * len(normal_probe.data.vertices)
addon.set_point_attribute(normal_probe.data, 'EFF_SourceNormal', 'FLOAT_VECTOR',
                          source_normals, 'vector')
assert addon.normal_state_matches_source(normal_probe.data, source_normals)
for polygon in normal_probe.data.polygons:
    polygon.use_smooth = True
normal_probe.data.normals_split_custom_set([(0, .01, .99995)] * len(normal_probe.data.loops))
normal_probe.data.update()
assert addon.normal_state_matches_source(normal_probe.data, source_normals)
normal_probe.data.normals_split_custom_set([(0, .1, .994987)] * len(normal_probe.data.loops))
normal_probe.data.update()
assert not addon.normal_state_matches_source(normal_probe.data, source_normals)


# Imported meshes can retain zero-valued custom corner normals even though
# their point-domain source normal is valid. Blender's public setter repairs
# such values, so exercise the exporter's source-normal decision seam directly.
zero_corner_mesh = types.SimpleNamespace(
    vertices=[None],
    loops=[types.SimpleNamespace(vertex_index=0)],
    corner_normals=[types.SimpleNamespace(vector=(0, 0, 0))])
assert addon.normal_state_matches_source(zero_corner_mesh, [(0, 0, 1)]), (
    'Undefined custom normal displaced a valid source normal')
assert not addon.normal_state_matches_source(zero_corner_mesh, [(0, 0, 0)]), (
    'Zero-filled source normal was treated as a valid backup')
edited_corner_mesh = types.SimpleNamespace(
    vertices=[None],
    loops=[types.SimpleNamespace(vertex_index=0)],
    corner_normals=[types.SimpleNamespace(vector=(0, 1, 0))])
assert not addon.normal_state_matches_source(edited_corner_mesh, [(0, 0, 1)]), (
    'A real normal edit incorrectly restored the source normal')


# Missing tangents must produce actual UV0-derived data, not an empty array.
obj = make_object('NewGeometry')
result = export(obj, 'new')
assert len(result['tangents']) == result['vertex_count'] * 4, 'Missing generated tangents'
assert result['vertex_count'] == 7, 'Mirrored tangent-only seam was not split'
assert result['uvs'][1] == [] and len(result['uvs'][2]) == result['vertex_count'] * 2
indices = corner_indices(result)
assert indices[0] != indices[3]
for corner, index in enumerate(indices):
    normal = Vector(result['normals'][index*3:index*3+3])
    tangent = Vector(result['tangents'][index*4:index*4+3])
    sign = result['tangents'][index*4+3]
    # In source coordinates dP/du is -X on the first chart, +X on the second;
    # dP/dv is +Y on both. Independently validate the reflected tangent frame.
    assert (tangent - Vector((-1 if corner < 3 else 1, 0, 0))).length < 1e-6
    assert (sign * normal.cross(tangent) - Vector((0,1,0))).length < 1e-6
assert result['tangents'][5*4:6*4] == [0,0,0,0], 'Invented tangent on a loose vertex'

# MikkTSpace can return a zero frame at a corner of a very thin triangle,
# even though both geometry and UV0 have finite, nonzero derivatives. Keep
# that authored triangle/UV and derive only its missing frame in float64.
thin = bpy.data.meshes.new('ThinDerivativeTriangle')
thin.from_pydata([
    (.005176806356757879, .10240109264850616, .8831067085266113),
    (.005288796499371529, .09985694289207458, .8830226063728333),
    (.005176929756999016, .10240109264850616, .8831067085266113),
], [], [(0,1,2)])
thin_uv = thin.uv_layers.new(name='UV0')
for i, uv in enumerate(((.40936279296875,.027083778753876686),
                        (.40688133239746094,.027083754539489746),
                        (.40936279296875,.027083730325102806))):
    thin_uv.data[i].uv = uv
thin.polygons[0].use_smooth = True
thin.normals_split_custom_set([
    (.026430722326040268,.4349936544895172,.9000455141067505),
    (-.0037316977977752686,-.05335235595703125,.9985687732696533),
    (-.08269882202148438,.38142648339271545,.9206925630569458),
])
thin.update()
thin_obj=bpy.data.objects.new(thin.name,thin)
bpy.context.scene.collection.objects.link(thin_obj)
thin_result=export(thin_obj,'thin-derivative')
assert thin_result['vertex_count']==3 and len(thin_result['indices'])==3
for i in range(3):
    n=Vector(thin_result['normals'][i*3:i*3+3])
    t=Vector(thin_result['tangents'][i*4:i*4+3])
    assert all(math.isfinite(v) for v in t)
    assert abs(t.length-1)<1e-5 and abs(n.dot(t))<1e-5
# A genuinely collapsed UV has no derivative; do not invent an arbitrary
# basis just to pass validation when neither Blender nor UV0 supplies one.
for uv in thin.uv_layers['UV0'].data:uv.uv=(0,0)
thin.update()
missing_frames=[(0,0,0,1)]*3
unresolved=addon._recover_zero_uv_tangents(
    thin,missing_frames,[tuple(c.vector) for c in thin.corner_normals],range(3))
assert unresolved==[(0,0,0,1)]*3
try:
    addon._orthogonalize_missing_tangents(thin.name,unresolved,
                                         [tuple(c.vector) for c in thin.corner_normals])
    raise AssertionError('Collapsed UV0 acquired an invented tangent frame')
except ValueError as error:
    assert 'usable tangent' in str(error)

# N-gons are legal Blender authoring input. Tangent generation triangulates
# only its temporary working Mesh and leaves the source topology unchanged.
ngon = make_ngon_object('NgonGeometry')
before_ngon = (len(ngon.data.polygons), tuple(len(p.vertices) for p in ngon.data.polygons))
ngon_result = export(ngon, 'ngon')
assert len(ngon_result['tangents']) == ngon_result['vertex_count'] * 4
assert before_ngon == (len(ngon.data.polygons), tuple(len(p.vertices) for p in ngon.data.polygons))

# Import/re-export of generated data also remains byte-exact.
round_mesh = addon.make_mesh('GeneratedRoundTrip', result)
round_obj = bpy.data.objects.new('GeneratedRoundTrip', round_mesh)
bpy.context.scene.collection.objects.link(round_obj)
roundtrip = export(round_obj, 'roundtrip')
assert bits(result['tangents']) == bits(roundtrip['tangents'])
assert result['indices'] == roundtrip['indices']

# Native custom bases are retained, including sign and signed-zero bits,
# even if they differ from the frame MikkTSpace would choose for the UVs.
native = make_object('Native')
values = [(1, .125*i, -0.0) for i in range(6)]
signs = [1,-1,1,-1,1,-1]
addon.set_point_attribute(native.data, 'EFF_Tangent', 'FLOAT_VECTOR', values, 'vector')
addon.set_point_attribute(native.data, 'EFF_TangentSign', 'FLOAT', signs, 'value')
native_result = export(native, 'native')
assert native_result['vertex_count'] == 6
expected = [v for tangent,sign in zip(values, signs) for v in (*addon.blender_to_unity(tangent),sign)]
assert bits(native_result['tangents']) == bits(expected)

# Joined external geometry often inherits zero-filled point attributes.
# Preserve usable source points; generate only those without a valid frame.
mixed = make_object('Mixed')
mixed_values = [values[i] if i in (1,2) else (0,0,0) for i in range(6)]
mixed_signs = [signs[i] if i in (1,2) else 0 for i in range(6)]
addon.set_point_attribute(mixed.data, 'EFF_Tangent', 'FLOAT_VECTOR', mixed_values, 'vector')
addon.set_point_attribute(mixed.data, 'EFF_TangentSign', 'FLOAT', mixed_signs, 'value')
mixed_result = export(mixed, 'mixed')
for corner, index in enumerate(corner_indices(mixed_result)):
    source = mixed.data.loops[corner].vertex_index
    actual = mixed_result['tangents'][index*4:index*4+4]
    if source in (1,2):
        assert bits(actual) == bits((*addon.blender_to_unity(values[source]), signs[source]))
    else:
        new_index = indices[corner]
        assert bits(actual) == bits(result['tangents'][new_index*4:new_index*4+4])

# A zero authored corner normal can occur in imported meshes. When the point
# tangent is missing, keep the UV-derived tangent instead of projecting it
# through a zero-length normal.
all_missing_zero_normal = make_object('AllMissingZeroNormal')
all_missing_zero_corners = [tuple(c.vector) for c in all_missing_zero_normal.data.corner_normals]
all_missing_zero_corners[0] = (0, 0, 0)
_, all_missing_zero_tangents = addon.mesh_export_tangents(
    all_missing_zero_normal.data, None, all_missing_zero_corners, False)
all_missing_zero_tangent = Vector(all_missing_zero_tangents[0][:3])
assert abs(all_missing_zero_tangent.length - 1) < 1e-5

mixed_zero_normal = make_object('MixedZeroNormal')
addon.set_point_attribute(mixed_zero_normal.data, 'EFF_Tangent', 'FLOAT_VECTOR',
                          mixed_values, 'vector')
addon.set_point_attribute(mixed_zero_normal.data, 'EFF_TangentSign', 'FLOAT',
                          mixed_signs, 'value')
zero_normal_corners = [tuple(c.vector) for c in mixed_zero_normal.data.corner_normals]
zero_normal_corners[0] = (0, 0, 0)
_, zero_normal_tangents = addon.mesh_export_tangents(
    mixed_zero_normal.data, None, zero_normal_corners, False)
zero_normal_tangent = Vector(zero_normal_tangents[0][:3])
assert abs(zero_normal_tangent.length - 1) < 1e-5
assert all(math.isfinite(value) for value in zero_normal_tangents[0])

# A non-zero imported tangent can still be unusable after a join or a custom
# normal edit. It must be regenerated against the normal that is serialized.
parallel = make_object('ParallelTangent')
addon.set_point_attribute(parallel.data, 'EFF_Tangent', 'FLOAT_VECTOR',
                          [(0,0,1)] * 6, 'vector')
addon.set_point_attribute(parallel.data, 'EFF_TangentSign', 'FLOAT', [1] * 6, 'value')
parallel_result = export(parallel, 'parallel')
for index in corner_indices(parallel_result):
    normal = Vector(parallel_result['normals'][index*3:index*3+3])
    tangent = Vector(parallel_result['tangents'][index*4:index*4+3])
    assert abs(normal.dot(tangent)) < 1e-5 and abs(tangent.length-1) < 1e-5

# Generated data uses the final native Blender custom normals, not a backup.
custom = make_object('CustomNormals')
for polygon in custom.data.polygons:
    polygon.use_smooth = True
custom.data.normals_split_custom_set([(0,.6,.8)] * len(custom.data.loops))
custom.data.update()
custom_result = export(custom, 'custom-normal')
for index in corner_indices(custom_result):
    n = Vector(custom_result['normals'][index*3:index*3+3])
    t = Vector(custom_result['tangents'][index*4:index*4+3])
    assert abs(n.dot(t)) < 1e-5 and abs(t.length-1) < 1e-5

# UV-less meshes are legal (untextured); don't invent UV0 or an arbitrary basis.
no_uv = make_object('NoUV', uv=False)
assert export(no_uv, 'no-uv')['tangents'] == []

# Loose vertices can retain a stale source tangent after geometry edits. They
# are not rendered and must not make native frame packing reject the faces.
# The editable exchange writer still preserves them for a lossless roundtrip.
loose = make_object('LooseStaleTangent')
addon.set_point_attribute(loose.data, 'EFF_SourceNormal', 'FLOAT_VECTOR',
                          [(0, 0, 1)] * 6, 'vector')
loose.data['eiem_normal_baseline_crc'] = addon.normal_state_crc(loose.data)
addon.set_point_attribute(loose.data, 'EFF_Tangent', 'FLOAT_VECTOR',
                          [(1, 0, 0)] * 5 + [(1, 0, .0656)], 'vector')
addon.set_point_attribute(loose.data, 'EFF_TangentSign', 'FLOAT', [1] * 6, 'value')
before_loose = (len(loose.data.vertices), tuple(tuple(v.co) for v in loose.data.vertices))
native_snapshot = addon.write_mesh(None, loose, return_snapshot=True)
sys.path.insert(0, str(addon_path.parent.parent))
import numpy as np
from nativepack import mesh as native_codec
_, precision = native_codec.pack_frames(np.asarray(native_snapshot['normals']).reshape(-1, 3),
                                        np.asarray(native_snapshot['tangents']).reshape(-1, 4))
assert precision['tangentMaxError'] <= .015
assert native_snapshot['vertex_count'] == 5
assert sorted(set(native_snapshot['indices'])) == list(range(5))
assert before_loose == (len(loose.data.vertices), tuple(tuple(v.co) for v in loose.data.vertices))
editable = export(loose, 'loose-stale-tangent')
assert editable['vertex_count'] == 6
assert editable['tangents'][20:24] == [-1, 0, float(np.float32(.0656)), 1]

# Partially missing source data cannot be repaired without the derivative UV.
for layer in list(mixed.data.uv_layers):
    mixed.data.uv_layers.remove(layer)
try:
    addon.write_mesh(output / 'missing-uv.mesh', mixed)
    raise AssertionError('Invented a frame for missing data without UV0')
except ValueError as error:
    assert 'no UV0' in str(error)

# A malformed authored attribute is an error, not an excuse to discard it.
native.data.attributes.remove(native.data.attributes['EFF_TangentSign'])
try:
    addon.write_mesh(output / 'invalid.mesh', native)
    raise AssertionError('Incomplete source attributes were silently replaced')
except ValueError as error:
    assert 'both be present' in str(error)

print('EFF_TANGENT_EXPORT_OK')

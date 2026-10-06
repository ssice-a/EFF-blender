"""UV and tangent streams use semantic mapping instead of fixed attribute names."""
import importlib.util
import json
import struct
import sys
from pathlib import Path

import bpy


addon_path, output = map(Path, sys.argv[sys.argv.index("--") + 1:])
spec = importlib.util.spec_from_file_location("eiem_attribute_mapping_test", addon_path)
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)


def bits(values):
    return struct.pack("<%df" % len(values), *values)


def make_mesh(name, with_extra=True):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    mesh["eiem_coordinate_space"] = "unity-y-up-left-handed"
    mesh["eiem_uv_dimensions_json"] = "[2,0,4,0,0,0,0,0]"
    for channel in (0, 2):
        layer = mesh.uv_layers.new(name="UV%d" % channel)
        for loop in mesh.loops:
            layer.data[loop.index].uv = (loop.vertex_index * .25, channel * .125)
    if with_extra:
        addon.set_point_attribute(
            mesh, "AuthorUv2Extra", "FLOAT2",
            [(0.1, 0.2), (0.3, 0.4), (0.5, 0.6)], "vector")
        addon.set_point_attribute(
            mesh, "AuthorFrameT", "FLOAT_VECTOR",
            [(1, 0, 0)] * 3, "vector")
        addon.set_point_attribute(
            mesh, "AuthorFrameSign", "FLOAT", [1, 1, 1], "value")
        mesh[addon.AUTHOR_ATTRIBUTE_MAP] = json.dumps({
            "uv_zw": {"2": "AuthorUv2Extra"},
            "point": {
                "tangent": "AuthorFrameT",
                "tangent_sign": "AuthorFrameSign",
            },
        })
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


mapped = make_mesh("Mapped")
channels = addon.mesh_export_uv_channels(mapped.data)
assert channels[1] is None
assert channels[2][0] == 4
assert all(abs(actual - expected) < 1e-6
           for actual, expected in zip(channels[2][2][1], (0.3, 0.4)))
addon.write_mesh(output / "mapped.mesh", mapped)
payload = addon.read_mesh(output / "mapped.mesh")
assert bits(payload["uvs"][2]) == bits([
    0.0, 0.25, 0.1, 0.2,
    0.25, 0.25, 0.3, 0.4,
    0.5, 0.25, 0.5, 0.6,
])
assert bits(payload["tangents"]) == bits([
    -1.0, 0.0, 0.0, 1.0,
    -1.0, 0.0, 0.0, 1.0,
    -1.0, 0.0, 0.0, 1.0,
])

missing = make_mesh("Missing", with_extra=False)
channels = addon.mesh_export_uv_channels(missing.data)
assert channels[2][0] == 2 and channels[2][2] is None
addon.write_mesh(output / "missing-z-w.mesh", missing)
payload = addon.read_mesh(output / "missing-z-w.mesh")
assert len(payload["uvs"][2]) == 6

print("EFF_ATTRIBUTE_MAPPING_OK")

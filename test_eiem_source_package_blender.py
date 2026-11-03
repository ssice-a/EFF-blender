"""Headless Blender round trip for an AnimeStudio EFFSOURCE package."""
import importlib.util
from pathlib import Path
import struct
import sys
import tempfile
import zlib

import bpy


def png_chunk(kind, data):
    return (struct.pack(">I", len(data)) + kind + data +
            struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff))


def write_png(path):
    header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    pixel = b"\x00\xff\x80\x40\xff"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", header) +
                     png_chunk(b"IDAT", zlib.compress(pixel)) + png_chunk(b"IEND", b""))


def mesh_bytes(format_io):
    return format_io.encode_mesh({
        "version": 6,
        "coordinate": "unity-y-up-left-handed",
        "source": "assets/test/body.asset",
        "name": "Body",
        "vertex_count": 3,
        "vertices": [0, 0, 0, 1, 0, 0, 0, 1, 0],
        "normals": [],
        "tangents": [],
        "colors": [],
        "uvs": [[0, 0, 1, 0, 0, 1]] + [[] for _ in range(7)],
        "indices": [0, 1, 2],
        "submeshes": [(0, 0, 3, 0, 0, 3)],
        "skin": [],
        "bindposes": [],
        "bone_hashes": [],
        "bone_paths": [],
        "bone_index_paths": [],
        "bone_sources": [],
        "bone_source_candidates": [],
        "blend_vertices": [],
        "blend_frames": [],
        "blend_channels": [],
        "blend_weights": [],
        "additional": [],
    })


addon_dir = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "eiem_source_package_addon", addon_dir / "__init__.py",
    submodule_search_locations=[str(addon_dir)])
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()
format_io = addon.eiem_blender_addon.format_io

try:
    with tempfile.TemporaryDirectory(prefix="eiem-source-import-") as temporary:
        root = Path(temporary)
        for directory in ("source", "meshes", "materials", "textures"):
            (root / directory).mkdir()
        (root / "source" / "manifest.json").write_text(
            '{"format":"EFFSOURCE","version":1,"purpose":"author-source-baseline"}',
            encoding="utf-8")
        (root / "meshes" / "body.mesh").write_bytes(mesh_bytes(format_io))
        (root / "materials" / "body.mat").write_text(
            "format=EFFMAT\nversion=1\nsource=assets/test/body.mat\nname=BodyMaterial\n"
            "texture._BaseMap=TextureBody\n", encoding="utf-8")
        write_png(root / "textures" / "body.png")
        (root / "mod.ini").write_text(
            "[TextureBody]\npath=textures/body.png\nsource=assets/test/body.png\nlinear=true\n\n"
            "[MaterialBody]\npath=materials/body.mat\n\n"
            "[MeshBody]\npath=meshes/body.mesh\nsource=assets/test/body.asset\nasset=Body\n\n"
            "[PrefabTest]\npath=assets/test/body.prefab\nrender.0=RenderBody\n\n"
            "[RenderBody]\npath=assets/test/body.renderer\nasset=BodyRenderer\n"
            "mesh=MeshBody\nmaterial.0=MaterialBody\n", encoding="utf-8")

        imported = addon.eiem_blender_addon.import_package(
            root, clean=True, include_physics=False)
        assert imported == 1, imported
        mesh_objects = [obj for obj in bpy.data.objects
                        if obj.type == "MESH" and obj.get("eiem_author_package") == str(root)]
        assert len(mesh_objects) == 1, [obj.name for obj in mesh_objects]
        obj = mesh_objects[0]
        assert len(obj.data.vertices) == 3 and len(obj.data.polygons) == 1
        assert len(obj.data.uv_layers) == 1
        source_mesh = format_io.read_mesh(root / "meshes" / "body.mesh")
        for loop in obj.data.loops:
            start = loop.vertex_index * 2
            expected = source_mesh["uvs"][0][start:start + 2]
            actual = tuple(obj.data.uv_layers["UV0"].data[loop.index].uv)
            assert actual == tuple(expected), (loop.index, actual, expected)
        roundtrip = addon.eiem_blender_addon.write_mesh(None, obj, return_snapshot=True)
        expected_by_vertex = {
            tuple(source_mesh["vertices"][index * 3:index * 3 + 3]):
                tuple(source_mesh["uvs"][0][index * 2:index * 2 + 2])
            for index in range(source_mesh["vertex_count"])
        }
        actual_by_vertex = {
            tuple(roundtrip["vertices"][index * 3:index * 3 + 3]):
                tuple(roundtrip["uvs"][0][index * 2:index * 2 + 2])
            for index in range(roundtrip["vertex_count"])
        }
        assert actual_by_vertex == expected_by_vertex, (actual_by_vertex, expected_by_vertex)
        assert obj.data.materials and obj.data.materials[0]["eiem_source"] == "assets/test/body.mat"
        image = next(image for image in bpy.data.images if image.get("eiem_author_package") == str(root))
        assert image.colorspace_settings.name == "Non-Color"
        print("EFFSOURCE_BLENDER_IMPORT_OK", imported, obj.name, image.colorspace_settings.name)
finally:
    addon.unregister()

import hashlib
import struct
import tempfile
import unittest
from pathlib import Path

import eiem_format


class FormatTests(unittest.TestCase):
    @staticmethod
    def mesh_payload():
        return dict(
            version=6, coordinate="unity-y-up-left-handed", source="assets/body.mesh", name="Body",
            vertex_count=3, vertices=[0, 0, 0, 1, 0, 0, 0, 1, 0],
            normals=[0, 0, 1] * 3, tangents=[1, 0, 0, 1] * 3, colors=[],
            uvs=[[0, 0, 1, 0, 0, 1]] + [[] for _ in range(7)],
            indices=[0, 1, 2], submeshes=[(0, 0, 3, 0, 0, 3)], skin=[], bindposes=[],
            bone_hashes=[], bone_paths=[], bone_index_paths=[], bone_sources=[],
            bone_source_candidates=[], blend_vertices=[], blend_frames=[], blend_channels=[],
            blend_weights=[], additional=[])

    def test_mesh_snapshot_codec_preserves_editable_arrays(self):
        payload = self.mesh_payload()
        encoded = eiem_format.encode_mesh(payload)
        decoded = eiem_format.decode_mesh(encoded)
        self.assertEqual(decoded["vertices"], payload["vertices"])
        self.assertEqual(decoded["uvs"], payload["uvs"])
        self.assertEqual(decoded["indices"], payload["indices"])
        self.assertEqual(decoded["submeshes"], payload["submeshes"])
        self.assertEqual(decoded["source"], payload["source"])
        self.assertEqual(decoded["name"], payload["name"])

    def test_mesh_snapshot_codec_rejects_trailing_data(self):
        encoded = eiem_format.encode_mesh(self.mesh_payload())
        with self.assertRaisesRegex(ValueError, "trailing"):
            eiem_format.decode_mesh(encoded + b"x")

    def test_format3_mesh_resource_recovers_and_verifies_author_snapshot(self):
        author = eiem_format.encode_mesh(self.mesh_payload())
        native = b"native-test-bytes"

        def string(value):
            raw = value.encode("utf-8")
            return struct.pack("<Q", len(raw)) + raw

        metadata = b"".join((
            string("EFF_AUTHOR_RESOURCE_METADATA/3"), struct.pack("<Q", 1),
            string("PartBody"), string("native/meshes/PartBody.effres"),
            string("assets/body.mesh"), string("Body"), string("A" * 64),
            string("MaterialBody"), struct.pack("<QQQQQ", 3, 3, 0, 0, 0),
            struct.pack("<Q", 0), struct.pack("<Q", 0),
            struct.pack("<QQ", len(native), len(author)), string(hashlib.sha256(author).hexdigest().upper()),
        ))
        payload = native + author
        header = struct.pack("<16sIIQQ32s", b"EFF_AUTHOR_FILE3", 3, 1,
                             len(metadata), len(payload), hashlib.sha256(metadata).digest())
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "body.effres"
            path.write_bytes(header + metadata + payload)
            result = eiem_format.read_format3_mesh_resource(path)
            self.assertEqual(result["vertices"], self.mesh_payload()["vertices"])
            self.assertEqual(result["part"], "PartBody")
            corrupted = bytearray(path.read_bytes())
            corrupted[-1] ^= 1
            path.write_bytes(corrupted)
            with self.assertRaisesRegex(ValueError, "snapshot checksum"):
                eiem_format.read_format3_mesh_resource(path)

    def test_format3_material_resource_recovers_sparse_effmat_and_checks_payload(self):
        author = ("format=EFFMAT\nversion=1\noverrides=true\n"
                  "source=assets/base/body.mat\nname=Body\nfloat._Gloss=.4\n").encode()

        def string(value):
            raw = value.encode("utf-8")
            return struct.pack("<Q", len(raw)) + raw

        metadata = b"".join((
            string("EFF_AUTHOR_MATERIAL_EDITS/1"),
            string("MaterialBody"), string("native/materials/MaterialBody.effres"),
            string("assets/base/body.mat"), string("Body"),
            struct.pack("<Q", len(author)),
            string(hashlib.sha256(author).hexdigest().upper()),
        ))
        payload = author
        header = struct.pack("<16sIIQQ32s", b"EFF_AUTHOR_FILE3", 3, 2,
                             len(metadata), len(payload), hashlib.sha256(metadata).digest())
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "material.effres"
            path.write_bytes(header + metadata + payload)
            result = eiem_format.read_format3_material_resource(
                path, "MaterialBody", "native/materials/MaterialBody.effres")
            self.assertEqual(result["float._Gloss"], ".4")
            self.assertEqual(result["source"], "assets/base/body.mat")
            damaged = bytearray(path.read_bytes())
            damaged[-1] ^= 1
            path.write_bytes(damaged)
            with self.assertRaisesRegex(ValueError, "author snapshot checksum"):
                eiem_format.read_format3_material_resource(path)

    def test_format3_effmat_uses_manifest_texture_ids(self):
        source = ("format=EFFMAT\nversion=1\noverrides=true\n"
                  "source=assets/base/body.mat\nname=Body\n"
                  "texture._BaseMap=TextureTextureBody_D\n").encode()
        result = eiem_format.bind_effmat_texture_ids(source, [
            {"property": "_BaseMap", "texture": "TextureBody_D_123456789abc"},
        ])
        values = eiem_format.read_effmat_snapshot(result)
        self.assertEqual(values["texture._BaseMap"], "TextureBody_D_123456789abc")

    def test_format3_physics_snapshot_roundtrips_with_native_resource(self):
        document = b"EFFPHYS\0editable-document"
        skeleton = b"EFFSKEL\0editable-skeleton"
        author = eiem_format.encode_format3_physics_snapshot(document, skeleton)
        native = b"EFFRES2\0native-physics"

        def string(value):
            raw = value.encode("utf-8")
            return struct.pack("<Q", len(raw)) + raw

        relative = "native/physics/ResourcePhysicsA.effres"
        metadata = b"".join((
            string("EFF_AUTHOR_RESOURCE_METADATA/3"), struct.pack("<Q", 4),
            string("ResourcePhysicsA"), string(relative),
            string(hashlib.sha256(native).hexdigest().upper()),
            struct.pack("<QQQ", 5, len(native), 1),
            string("assets/character/body.mesh"), string("Body"),
            struct.pack("<Q", len(author)), string(hashlib.sha256(author).hexdigest().upper()),
        ))
        payload = native + author
        header = struct.pack("<16sIIQQ32s", b"EFF_AUTHOR_FILE3", 3, 4,
                             len(metadata), len(payload), hashlib.sha256(metadata).digest())
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "physics.effres"
            path.write_bytes(header + metadata + payload)
            result = eiem_format.read_format3_physics_resource(path, "ResourcePhysicsA", relative)
            self.assertEqual(result["document"], document)
            self.assertEqual(result["skeleton"], skeleton)
            damaged = bytearray(path.read_bytes())
            damaged[-1] ^= 1
            path.write_bytes(damaged)
            with self.assertRaisesRegex(ValueError, "Physics native/author checksum"):
                eiem_format.read_format3_physics_resource(path, "ResourcePhysicsA", relative)

    def test_format3_effect_author_resource_roundtrips_and_materializes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source"
            (source / "source").mkdir(parents=True)
            (source / "effect.json").write_text("{}", encoding="utf-8")
            (source / "source/manifest.json").write_text("{}", encoding="utf-8")
            (source / "source/data.bin").write_bytes(b"effect source")
            sidecar = root / "effect.effeffect"
            workspace = {"format": "EFF_EFFECT_WORKSPACE", "version": 1,
                         "catalog": {}, "schemas": {}, "values": {}}
            attachment = {"id": "EffectA", "label": "Test effect", "keys": ["CTRL+K"]}
            encoded_memory = eiem_format.encode_format3_effect_source(
                source, workspace, attachment)
            decoded_memory = eiem_format.decode_format3_effect_snapshot(encoded_memory)
            self.assertEqual(decoded_memory["attachment"], attachment)
            self.assertEqual(decoded_memory["workspace"], workspace)
            eiem_format.write_format3_effect_snapshot(
                sidecar, source, workspace, attachment)
            author = sidecar.read_bytes()
            resource_id = "ResourceEffectAuthorA"
            relative = "native/effect-author/a.effres"

            def string(value):
                raw = value.encode("utf-8")
                return struct.pack("<Q", len(raw)) + raw

            metadata = b"".join((
                string("EFF_AUTHOR_RESOURCE_METADATA/3"), struct.pack("<Q", 7),
                string(resource_id), string(relative), struct.pack("<Q", len(author)),
                string(hashlib.sha256(author).hexdigest().upper()),
            ))
            header = struct.pack("<16sIIQQ32s", b"EFF_AUTHOR_FILE3", 3, 7,
                                 len(metadata), len(author), hashlib.sha256(metadata).digest())
            resource = root / "effect.effres"
            resource.write_bytes(header + metadata + author)
            result = eiem_format.read_format3_effect_author_resource(resource, resource_id, relative)
            self.assertEqual(result["attachment"], attachment)
            self.assertEqual(result["workspace"]["format"], "EFF_EFFECT_WORKSPACE")
            restored, restored_attachment, source_path = eiem_format.materialize_format3_effect_author_resource(
                resource, resource_id, relative, root / "cache")
            self.assertEqual(restored_attachment, attachment)
            self.assertEqual(restored["source"], str(source_path))
            self.assertEqual((source_path / "source/data.bin").read_bytes(), b"effect source")

    def test_writer_reader_round_trip(self):
        writer = eiem_format.Writer()
        writer.i32(-7)
        writer.u32(0xFEEDBEEF)
        writer.f32(1.25)
        writer.string("裙子")
        writer.floats((1.0, 2.0, 3.0))

        reader = eiem_format.Reader(bytes(writer.data))
        self.assertEqual(reader.i32(), -7)
        self.assertEqual(reader.u32(), 0xFEEDBEEF)
        self.assertAlmostEqual(reader.f32(), 1.25)
        self.assertEqual(reader.string(), "裙子")
        self.assertEqual(reader.floats(), [1.0, 2.0, 3.0])
        self.assertEqual(reader.pos, len(reader.data))

    def test_safe_path_rejects_package_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(
                eiem_format.safe_path(root, "meshes/body.mesh"),
                (root / "meshes" / "body.mesh").resolve(),
            )
            with self.assertRaisesRegex(ValueError, "escapes package"):
                eiem_format.safe_path(root, "../outside.mesh")

    def test_parse_ini_ignores_runtime_control_flow(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "mod.ini").write_text(
                "[RenderBody]\n"
                "mesh = ResourceBody\n"
                "if $state == 1\n"
                "  draw = false\n"
                "else\n"
                "  draw = true\n"
                "endif\n"
                "[ResourceBody]\n"
                "path = meshes/body.mesh\n",
                encoding="utf-8",
            )

            parser = eiem_format.parse_ini(root)
            self.assertEqual(parser["RenderBody"]["mesh"], "ResourceBody")
            self.assertNotIn("draw", parser["RenderBody"])
            self.assertEqual(parser["ResourceBody"]["path"], "meshes/body.mesh")


if __name__ == "__main__":
    unittest.main()

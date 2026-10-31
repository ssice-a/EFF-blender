"""Format-3 publication validation tests."""
import hashlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
import sys
import importlib
import types
import zipfile

ROOT = Path(__file__).resolve().parents[2]
CORE_NAME = "_eff_canonical_nativepack"
if CORE_NAME not in sys.modules:
    core = types.ModuleType(CORE_NAME)
    core.__path__ = [str(ROOT / "tools" / "nativepack")]
    sys.modules[CORE_NAME] = core
author_format3 = importlib.import_module(CORE_NAME + ".author_format3")
HEADER, MAGIC = author_format3.HEADER, author_format3.MAGIC
read_format3_directory = author_format3.read_format3_directory


def _string(value):
    raw = value.encode("utf-8")
    return len(raw).to_bytes(8, "little") + raw


def _texture_metadata(width=4, height=4, mips=1, fmt=25, pixel_bytes=16):
    return b"".join((
        _string("EFF_AUTHOR_RESOURCE_METADATA/3"),
        (3).to_bytes(8, "little"),
        _string("TextureA"), _string("native/textures/a.effres"),
        _string("textures/a.dds"), _string("assets/base/a.tex"),
        _string("A" * 64), _string("CAB-a"), (1).to_bytes(8, "little"),
        (12).to_bytes(8, "little"), pixel_bytes.to_bytes(8, "little"),
        width.to_bytes(8, "little"), height.to_bytes(8, "little"),
        mips.to_bytes(8, "little"), fmt.to_bytes(8, "little"),
    ))


def _material_metadata(author):
    return b"".join((
        _string("EFF_AUTHOR_MATERIAL_EDITS/1"),
        _string("MaterialMetal"), _string("native/materials/metal.effres"),
        _string("assets/game/metal.mat"), _string("Metal"),
        len(author).to_bytes(8, "little"), _string(hashlib.sha256(author).hexdigest().upper()),
    ))


def _physics_metadata(native, author, targets):
    return b"".join((
        _string("EFF_AUTHOR_RESOURCE_METADATA/3"), (4).to_bytes(8, "little"),
        _string("ResourcePhysicsA"), _string("native/physics/a.effres"),
        _string(hashlib.sha256(native).hexdigest().upper()),
        (5).to_bytes(8, "little"), len(native).to_bytes(8, "little"),
        len(targets).to_bytes(8, "little"),
        *(_string(path) + _string(asset) for path, asset in targets),
        len(author).to_bytes(8, "little"), _string(hashlib.sha256(author).hexdigest().upper()),
    ))


def _string(value):
    raw = value.encode("utf-8")
    return len(raw).to_bytes(8, "little") + raw


def _effres(metadata, payload=b"P" * 64, kind=3):
    return HEADER.pack(MAGIC, 3, kind, len(metadata), len(payload),
                       hashlib.sha256(metadata).digest()) + metadata + payload


def _dds(width=4, height=4, mips=1, fmt=98, payload=b"P" * 16, alpha_mode=3):
    value = bytearray(148)
    value[:4] = b"DDS "
    struct.pack_into("<7I", value, 4, 124, 0x1007, height, width, 0, 0, mips)
    struct.pack_into("<3I", value, 76, 32, 4, int.from_bytes(b"DX10", "little"))
    struct.pack_into("<5I", value, 128, fmt, 3, 0, 1, alpha_mode)
    return bytes(value) + payload


class Format3PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "native" / "textures").mkdir(parents=True)
        (self.root / "textures").mkdir()
        (self.root / "mod.ini").write_text(
            "[Mod]\nformat=3\nid=TextureSmoke\n\n"
            "[TextureA]\npath=native/textures/a.effres\n"
            "dds=textures/a.dds\n", encoding="utf-8")
        (self.root / "native/textures/a.effres").write_bytes(
            _effres(_texture_metadata()))
        (self.root / "textures/a.dds").write_bytes(_dds())

    def test_template_table_uses_selected_source_snapshot_without_index(self):
        mod = self.root / "author-mod"
        source = self.root / "source.eff"
        (mod / "resources").mkdir(parents=True)
        (source / "source" / "snapshots").mkdir(parents=True)
        (mod / "mod.ini").write_text(
            "[Mod]\nformat=2\nid=TemplateSmoke\n\n"
            "[ReplaceMeshA]\ntarget.path=assets/game/mesh.mesh\n"
            "target.asset=MeshA\nparts=\n", encoding="utf-8")
        snapshot = b"serialized mesh template"
        snapshot_path = source / "source" / "snapshots" / "CAB-mesh.bin"
        snapshot_path.write_bytes(snapshot)
        manifest = {
            "format": "EFFSOURCE", "version": 1,
            "serializedFiles": [{"cab": "CAB-mesh", "snapshot": {
                "file": "source/snapshots/CAB-mesh.bin",
                "sha256": hashlib.sha256(snapshot).hexdigest().upper()}}],
            "resources": [{"type": "Mesh", "logicalPath": "assets/game/mesh.mesh",
                           "name": "MeshA", "native": {"cab": "CAB-mesh", "pathId": 42}}],
        }
        (source / "source" / "manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8")
        templates = author_format3.resolve_templates(mod, [source])
        memory_templates = author_format3.resolve_templates(
            None, [source], ini_bytes=(mod / "mod.ini").read_bytes())
        self.assertEqual(len(templates), 1)
        self.assertEqual(memory_templates, templates)
        self.assertEqual(templates[0]["cab"], "CAB-mesh")
        self.assertEqual(templates[0]["pathId"], 42)
        self.assertEqual(Path(templates[0]["file"]), snapshot_path.resolve())

    def tearDown(self):
        self.temp.cleanup()

    def test_accepts_standard_dds_and_self_describing_texture(self):
        result = read_format3_directory(self.root)
        self.assertEqual(result["format"], 3)
        self.assertEqual(result["modId"], "TextureSmoke")
        self.assertIn("textures/a.dds", result["_files"])

    def test_rejects_resource_metadata_checksum_mismatch(self):
        path = self.root / "native/textures/a.effres"
        value = bytearray(path.read_bytes())
        value[HEADER.size] ^= 1
        path.write_bytes(value)
        with self.assertRaisesRegex(ValueError, "checksum"):
            read_format3_directory(self.root)

    def test_rejects_wrong_texture_dimensions_and_mip_extent(self):
        (self.root / "textures/a.dds").write_bytes(_dds(width=8, height=8, payload=b"P" * 64))
        with self.assertRaisesRegex(ValueError, "dimensions/format/mips"):
            read_format3_directory(self.root)
        (self.root / "textures/a.dds").write_bytes(_dds(payload=b"P" * 15))
        with self.assertRaisesRegex(ValueError, "mip data extent"):
            read_format3_directory(self.root)

    def test_rejects_resource_kind_mismatch(self):
        (self.root / "native/textures/a.effres").write_bytes(
            _effres(_texture_metadata(), kind=2))
        with self.assertRaisesRegex(ValueError, "envelope differs"):
            read_format3_directory(self.root)

    def test_rejects_dds_path_escape(self):
        (self.root / "mod.ini").write_text(
            "[Mod]\nformat=3\nid=TextureSmoke\n\n"
            "[TextureA]\npath=native/textures/a.effres\n"
            "dds=../outside.dds\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "escapes Mod"):
            read_format3_directory(self.root)

    def test_validates_material_author_snapshot_and_texture_bindings(self):
        author = ("format=EFFMAT\nversion=1\noverrides=true\n"
                  "source=assets/game/metal.mat\nname=Metal\nfloat._Gloss=.4\n").encode()
        relative = "native/materials/metal.effres"
        (self.root / relative).parent.mkdir(parents=True)
        (self.root / relative).write_bytes(_effres(
            _material_metadata(author), author, kind=2))
        with (self.root / "mod.ini").open("a", encoding="utf-8") as stream:
            stream.write("\n[MaterialMetal]\npath=" + relative +
                         "\nsource.path=assets/game/metal.mat\nsource.asset=Metal\n")
        self.assertEqual(read_format3_directory(self.root)["format"], 3)

    def test_rejects_material_author_texture_binding_drift(self):
        author = ("format=EFFMAT\nversion=1\noverrides=true\n"
                  "source=assets/game/metal.mat\nname=Metal\ntexture._BaseMap=TextureMissing\n").encode()
        relative = "native/materials/metal.effres"
        (self.root / relative).parent.mkdir(parents=True)
        (self.root / relative).write_bytes(_effres(
            _material_metadata(author), author, kind=2))
        with (self.root / "mod.ini").open("a", encoding="utf-8") as stream:
            stream.write("\n[MaterialMetal]\npath=" + relative +
                         "\nsource.path=assets/game/metal.mat\nsource.asset=Metal\n"
                         "texture._BaseMap=TextureMissing\n")
        with self.assertRaisesRegex(ValueError, "undeclared Texture"):
            read_format3_directory(self.root)

    def test_physics_author_snapshot_is_bound_to_manifest_targets(self):
        relative = "native/physics/a.effres"
        path = self.root / relative
        path.parent.mkdir(parents=True)
        native = b"native-physics"
        document = b"EFFPHYS\0document"
        skeleton = b"EFFSKEL\0skeleton"
        author = b"EFFPHS3\0" + struct.pack("<IQQ", 1, len(document), len(skeleton)) + document + skeleton
        target = ("assets/game/body.mesh", "Body")
        lod_target = ("assets/game/character.fbx", "Body_LOD1")
        metadata = _physics_metadata(native, author, [target, lod_target])
        path.write_bytes(_effres(metadata, native + author, kind=4))
        controls = self.root / "native/controls.effres"
        controls.parent.mkdir(parents=True, exist_ok=True)
        controls.write_bytes(_effres(b"controls", b"", kind=6))
        with (self.root / "mod.ini").open("a", encoding="utf-8") as stream:
            stream.write(
                "\n[ResourcePhysicsA]\ntype=physics\npath=" + relative + "\ntargets=ReplaceBody\n\n"
                "[ResourceControls]\ntype=controls\npath=native/controls.effres\n\n"
                "[ReplaceBody]\ntarget.path=" + target[0] + "\ntarget.asset=" + target[1] + "\n"
                "lod.1.path=" + lod_target[0] + "\nlod.1.asset=" + lod_target[1] + "\n")
        self.assertEqual(read_format3_directory(self.root)["format"], 3)
        ini = author_format3.configparser.ConfigParser(interpolation=None, delimiters=("=",), strict=True)
        ini.optionxform = str
        ini.read(self.root / "mod.ini", encoding="utf-8")
        ini["ReplaceBody"]["lod.1.asset"] = "Other"
        with self.assertRaisesRegex(ValueError, "Mesh targets"):
            author_format3._validate_physics_resource(
                self.root, relative, "ResourcePhysicsA", ini, len(native + author))

    def test_effect_author_resource_is_linked_to_attachment(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as package:
            package.writestr("effect.json", "{}")
            package.writestr("source/manifest.json", "{}")
        state = json.dumps({"format": "EFF_EFFECT_AUTHOR3", "version": 1,
                            "workspace": {}, "attachment": {}}, separators=(",", ":")).encode()
        source_archive = archive.getvalue()
        author = struct.pack("<8sIQQ32s32s", b"EFFFXA3\0", 1,
                             len(source_archive), len(state), hashlib.sha256(source_archive).digest(),
                             hashlib.sha256(state).digest()) + source_archive + state
        relative = "native/effect-author/test.effres"
        resource_id = "ResourceEffectAuthor_Test"
        metadata = b"".join((
            _string("EFF_AUTHOR_RESOURCE_METADATA/3"), (7).to_bytes(8, "little"),
            _string(resource_id), _string(relative), len(author).to_bytes(8, "little"),
            _string(hashlib.sha256(author).hexdigest().upper()),
        ))
        (self.root / relative).parent.mkdir(parents=True, exist_ok=True)
        (self.root / relative).write_bytes(_effres(metadata, author, kind=7))
        controls = "native/controls.effres"
        controls_path = self.root / controls
        controls_path.parent.mkdir(parents=True, exist_ok=True)
        controls_path.write_bytes(_effres(b"controls", b"", kind=6))
        with (self.root / "mod.ini").open("a", encoding="utf-8") as stream:
            stream.write(
                "\n[ResourceEffectAuthor_Test]\ntype=effect-author\npath=" + relative +
                "\n[ResourceControls]\ntype=controls\npath=" + controls +
                "\n[EffectAttachmentTest]\npath=" + controls + "\nauthor=" + resource_id + "\n")
        self.assertEqual(read_format3_directory(self.root)["format"], 3)
        ini = author_format3.configparser.ConfigParser(interpolation=None, delimiters=("=",), strict=True)
        ini.optionxform = str
        ini.read(self.root / "mod.ini", encoding="utf-8")
        ini["EffectAttachmentTest"]["author"] = "MissingAuthor"
        with (self.root / "mod.ini").open("w", encoding="utf-8") as stream:
            ini.write(stream)
        with self.assertRaisesRegex(ValueError, "author resource is unresolved"):
            read_format3_directory(self.root)


if __name__ == "__main__":
    unittest.main()

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
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
CORE_NAME = "_eff_canonical_nativepack"
if CORE_NAME not in sys.modules:
    core = types.ModuleType(CORE_NAME)
    core.__path__ = [str(ROOT / "tools" / "nativepack")]
    sys.modules[CORE_NAME] = core
author_format3 = importlib.import_module(CORE_NAME + ".author_format3")
HEADER, MAGIC = author_format3.HEADER, author_format3.MAGIC
read_format3_directory = author_format3.read_format3_directory
bone_spec = importlib.util.spec_from_file_location(
    "eiem_author_bone_codec_test", Path(__file__).with_name("eiem_bone_animation.py"))
bone_codec = importlib.util.module_from_spec(bone_spec)
bone_spec.loader.exec_module(bone_codec)


def _effres(metadata, payload=b"P" * 64, kind=3):
    return author_format3.encode_resource_file(kind, metadata, payload)


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
        (self.root / "textures").mkdir()
        (self.root / "mod.ini").write_text(
            "[Mod]\nformat=3\nid=TextureSmoke\n\n"
            "[TextureA]\npath=textures/a.effres\n"
            "dds=textures/a.dds\n", encoding="utf-8")
        (self.root / "textures/a.effres").write_bytes(
            _effres(b"", b"native Texture2D scaffold"))
        (self.root / "textures/a.dds").write_bytes(_dds())

    def indexed_source(self, package, manifest, rows):
        by_target = {}
        by_type = {}
        for row in manifest["resources"]:
            identity = (row.get("type"), row.get("logicalPath"), row.get("name"))
            by_target.setdefault(identity, []).append(row)
            by_type.setdefault(row.get("type"), []).append(row)
        return types.SimpleNamespace(
            package=package.resolve(), manifest=manifest, rows=rows, files={},
            find_all=lambda kind, path, asset: by_target.get((kind, path, asset), ()),
            first_of_type=lambda kind: next(iter(by_type.get(kind, ())), None))

    def test_template_table_uses_selected_source_snapshot_without_index(self):
        source = self.root / "source.eff"
        (source / "source" / "snapshots").mkdir(parents=True)
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
        source_object = self.indexed_source(
            source, manifest, {row["cab"]: row for row in manifest["serializedFiles"]})
        rules = [dict(hidden=False, targets=[dict(
            sourcePath="assets/game/mesh.mesh", sourceAsset="MeshA")])]
        templates = author_format3.resolve_templates([source_object], rules)
        memory_templates = author_format3.resolve_templates([source_object], rules)
        self.assertEqual(len(templates), 1)
        self.assertEqual(memory_templates, templates)
        self.assertEqual(templates[0]["cab"], "CAB-mesh")
        self.assertEqual(templates[0]["pathId"], 42)
        self.assertEqual(Path(templates[0]["file"]), snapshot_path.resolve())

    def test_identical_template_snapshots_shared_across_prefabs_are_deduplicated(self):
        identity = ("Mesh", "assets/game/mesh.mesh", "MeshA")
        rules = [dict(hidden=False, targets=[dict(sourcePath=identity[1], sourceAsset=identity[2])])]

        def make_source(name, snapshot):
            source = self.root / name
            snapshot_path = source / "source" / "snapshots" / "CAB-shared.bytes"
            snapshot_path.parent.mkdir(parents=True)
            snapshot_path.write_bytes(snapshot)
            serialized = dict(cab="CAB-shared", snapshot=dict(
                file="source/snapshots/CAB-shared.bytes",
                sha256=hashlib.sha256(snapshot).hexdigest().upper()))
            manifest = dict(format="EFFSOURCE", version=1,
                serializedFiles=[serialized],
                resources=[dict(type=identity[0], logicalPath=identity[1], name=identity[2],
                                native=dict(cab="CAB-shared", pathId=42))])
            (source / "source" / "manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8")
            return self.indexed_source(source, manifest, {"CAB-shared": serialized})

        first = make_source("source-duplicate-a", b"identical serialized mesh")
        second = make_source("source-duplicate-b", b"identical serialized mesh")
        templates = author_format3.resolve_templates([second, first], rules)
        self.assertEqual(len(templates), 1)
        self.assertEqual(templates[0]["cab"], "CAB-shared")
        self.assertEqual(Path(templates[0]["file"]),
                         (self.root / "source-duplicate-a" / "source" / "snapshots" / "CAB-shared.bytes").resolve())

        conflicting = make_source("source-conflict", b"different serialized mesh")
        with self.assertRaisesRegex(ValueError, "ambiguous exact Mesh template"):
            author_format3.resolve_templates([first, conflicting], rules)

    def test_template_resolution_hashes_only_needed_unique_cabs(self):
        source = self.root / "source-many"
        snapshot_dir = source / "source" / "snapshots"
        snapshot_dir.mkdir(parents=True)
        mesh_bytes, texture_bytes = b"mesh cab", b"texture cab"
        mesh_path, texture_path = snapshot_dir / "mesh.bin", snapshot_dir / "texture.bin"
        mesh_path.write_bytes(mesh_bytes)
        texture_path.write_bytes(texture_bytes)
        serialized = [
            dict(cab="CAB-mesh", snapshot=dict(file="source/snapshots/mesh.bin",
                 sha256=hashlib.sha256(mesh_bytes).hexdigest().upper())),
            dict(cab="CAB-texture", snapshot=dict(file="source/snapshots/texture.bin",
                 sha256=hashlib.sha256(texture_bytes).hexdigest().upper())),
        ]
        manifest = dict(format="EFFSOURCE", version=1, serializedFiles=serialized,
            resources=[
                dict(type="Mesh", logicalPath="assets/game/a.mesh", name="MeshA",
                     native=dict(cab="CAB-mesh", pathId=1)),
                dict(type="Mesh", logicalPath="assets/game/b.mesh", name="MeshB",
                     native=dict(cab="CAB-mesh", pathId=2)),
                dict(type="Texture2D", logicalPath="assets/game/t.tex", name="Texture",
                     native=dict(cab="CAB-texture", pathId=3)),
            ])
        source_object = self.indexed_source(
            source, manifest, {row["cab"]: row for row in serialized})
        class NoResourceScan(list):
            def __iter__(self):
                raise AssertionError("template resolution scanned every EFFSOURCE resource")
        source_object.manifest["resources"] = NoResourceScan(manifest["resources"])
        rules = [dict(hidden=False, targets=[
            dict(sourcePath="assets/game/a.mesh", sourceAsset="MeshA"),
            dict(sourcePath="assets/game/b.mesh", sourceAsset="MeshB")])]
        digest = author_format3._digest
        with mock.patch.object(author_format3, "_digest", wraps=digest) as verify:
            templates = author_format3.resolve_templates([source_object], rules)
        self.assertEqual(len(templates), 2)
        self.assertEqual(verify.call_count, 1)
        self.assertEqual(verify.call_args.args[0], mesh_path.resolve())
        with mock.patch.object(author_format3, "_digest", wraps=digest) as verify:
            templates = author_format3.resolve_templates([source_object], rules,
                                                         include_texture=True)
        self.assertEqual(len(templates), 3)
        self.assertEqual(verify.call_count, 2)

    def tearDown(self):
        self.temp.cleanup()

    def test_accepts_standard_dds_and_self_describing_texture(self):
        result = read_format3_directory(self.root)
        self.assertEqual(result["format"], 3)
        self.assertEqual(result["modId"], "TextureSmoke")
        self.assertIn("textures/a.dds", result["_files"])
        self.assertEqual((self.root / "textures/a.effres").parent,
                         (self.root / "textures/a.dds").parent)

    def test_direct_writer_emits_a_complete_format3_controls_only_directory(self):
        output = self.root / "direct"
        output.mkdir()
        result = author_format3.write_format3_directory(
            output, "DirectSmoke", [], {}, [], [], [], [], [])
        self.assertEqual(result["format"], 3)
        validated = read_format3_directory(output)
        self.assertEqual(validated["modId"], "DirectSmoke")
        self.assertEqual(validated["_files"], {"mod.ini"})
        self.assertIn("controls=ini", (output / "mod.ini").read_text("utf-8"))

    def test_writer_preserves_imported_ini_controls_and_key_bindings(self):
        output = self.root / "preserved-controls"
        output.mkdir()
        author_format3.write_format3_directory(
            output, "PreservedControls", [], {}, [], [], [], [], [],
            state_defaults={"$mode": 1.0}, persistent_states={"$mode"},
            preserved_controls={"$mode": {
                "label": "Mode", "minimum": 0.0, "maximum": 2.0}},
            preserved_bindings=[dict(
                id="KeyCustomMode", variable="$mode", type="cycle", key="F7",
                speed=1.0, label="Mode", values=[0.0, 1.0, 2.0])],
            key_switch_enabled=False)
        parser = author_format3.configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        parser.read(output / "mod.ini", encoding="utf-8")
        restored = author_format3.read_controls_ini(output, parser)
        self.assertEqual(restored["defaults"]["$mode"], 1.0)
        self.assertEqual(restored["controls"]["$mode"]["maximum"], 2.0)
        self.assertEqual(restored["bindings"][0]["id"], "KeyCustomMode")
        self.assertEqual(restored["bindings"][0]["values"], [0.0, 1.0, 2.0])
        self.assertEqual(parser.get("Mod", "key_switch_enabled"), "0")

    def test_imported_visibility_remains_required_after_adding_a_new_switch(self):
        original = ("variable", 0.0, "$enabled", None, None)
        bindings = [{"variable": "$style", "visible": [1]}]
        expression = author_format3._part_visibility({
            "visibilityExpression": original, "visibilityBindings": bindings})

        def evaluate(node, values):
            op, value, variable, left, right = node
            if op == "number":
                return value
            if op == "variable":
                return values[variable]
            if op == "equal":
                return evaluate(left, values) == evaluate(right, values)
            if op == "and":
                return evaluate(left, values) and evaluate(right, values)
            self.fail("Unexpected expression operator: " + op)

        self.assertTrue(evaluate(expression, {"$enabled": 1, "$style": 1}))
        self.assertFalse(evaluate(expression, {"$enabled": 0, "$style": 1}))
        self.assertFalse(evaluate(expression, {"$enabled": 1, "$style": 0}))

    def test_resource_filenames_use_labels_and_case_insensitive_numeric_suffixes(self):
        rows = [('TextureA_0123456789ab', 'dao_Roll'),
                ('TextureB_abcdef012345', 'DAO_roll'),
                ('TextureC_deadbeef0123', 'dao_Roll_2'),
                ('TextureD_012345abcdef', '../刀:光*')]
        paths = author_format3.named_resource_paths(rows, 'textures', '.effres')
        self.assertEqual(paths, author_format3.named_resource_paths(reversed(rows), 'textures', '.effres'))
        self.assertEqual(len({p.casefold() for p in paths.values()}), 4)
        self.assertEqual(paths[rows[0][0]], 'textures/dao_Roll.effres')
        for relative in paths.values():
            self.assertEqual(Path(relative).parent.as_posix(), 'textures')
            self.assertNotRegex(relative, r'[a-fA-F0-9]{12,}')
            self.assertNotRegex(Path(relative).name, r'[:*\\/]')

    def test_texture_filename_uses_source_label_instead_of_internal_hash(self):
        output = self.root / 'readable'
        output.mkdir()
        texture = dict(id='TextureMat_0123456789ab', pixelLabel='mat_25.001_01',
                       width=4, height=4, mipCount=1, formatId=25, pixels=bytes(16),
                       colorSpace=1, filter=1, wrap=0, aniso=1, mipBias=0,
                       sourcePath='assets/game/texture.png', sourceAsset='Texture')
        manifest = author_format3.write_format3_directory(
            output, 'Readable', [], {}, [texture], [], [], [], [])
        row = manifest['_ini'][texture['id']]
        self.assertEqual(row['dds'], 'textures/mat_25.001_01.dds')
        self.assertNotIn('path', row)
        self.assertFalse(list((output / 'textures').glob('*.effres')))
        self.assertEqual((output / row['dds']).read_bytes()[148:], bytes(16))

    def test_meshes_share_and_select_distinct_author_skeletons(self):
        output = self.root / "multi-skeleton"
        output.mkdir()
        skeletons = [
            dict(id="weapon_a", label="Weapon A", data=bone_codec.encode_skeleton([
                ("WeaponA", -1, (0, 0, 0), (0, 0, 0, 1), (1, 1, 1)),
                ("WeaponA/FxAnchor", 0, (0, 0, 0), (0, 0, 0, 1), (1, 1, 1)),
            ])),
            dict(id="weapon_b", label="Weapon B", data=bone_codec.encode_skeleton([
                ("WeaponB", -1, (0, 0, 0), (0, 0, 0, 1), (1, 1, 1)),
            ])),
        ]
        clips = [
            dict(id="weapon_a_spin", skeleton="weapon_a", data=bone_codec.encode_animation({
                "id": "weapon_a_spin", "skeleton": "weapon_a", "duration": 1.0,
                "sample_rate": 30, "loop": True,
                "tracks": [{"path": "WeaponA/FxAnchor", "samples": [
                    (0, 0, 0, 0, 0, 0, 1, 1, 1, 1),
                    (0, 0, 0, 0, 0, 0.3826834, 0.9238795, 1, 1, 1),
                ]}],
            })),
            dict(id="weapon_b_pulse", skeleton="weapon_b", data=bone_codec.encode_animation({
                "id": "weapon_b_pulse", "skeleton": "weapon_b", "duration": 1.0,
                "sample_rate": 30, "loop": True,
                "tracks": [{"path": "WeaponB", "samples": [
                    (0, 0, 0, 0, 0, 0, 1, 1, 1, 1),
                    (0, 0.1, 0, 0, 0, 0, 1, 1, 1, 1),
                ]}],
            })),
        ]
        parts = [
            dict(partId=name, object=name, skeleton=skeleton_id,
                 visibilityExpression=("number", 1.0, "", None, None))
            for name, skeleton_id in (("Blade", "weapon_a"),
                                      ("Guard", "weapon_a"),
                                      ("Staff", "weapon_b"))
        ]
        author_format3.write_format3_directory(
            output, "MultiSkeleton", parts, {}, [], [], [], [], [],
            skeletons=skeletons, bone_animations=clips)
        (output / "meshes").mkdir()
        for name in ("Blade", "Guard", "Staff"):
            (output / "meshes" / (name + ".effres")).write_bytes(
                author_format3.encode_resource_file(1, b"mesh metadata", b"mesh"))

        manifest = (output / "mod.ini").read_text("utf-8")
        self.assertIn("skeleton=weapon_a", manifest)
        self.assertIn("skeleton=weapon_b", manifest)
        self.assertIn("[Animationweapon_a_spin]", manifest)
        self.assertIn("[Animationweapon_b_pulse]", manifest)
        self.assertNotIn("[BoneAnimation", manifest)
        self.assertEqual(read_format3_directory(output)["format"], 3)

    def test_unanimated_author_skeleton_can_be_referenced_by_mesh(self):
        output = self.root / "unanimated-skeleton"
        output.mkdir()
        skeleton = dict(id="weapon_rig", data=bone_codec.encode_skeleton([
            ("WeaponRoot", -1, (0, 0, 0), (0, 0, 0, 1), (1, 1, 1)),
            ("WeaponRoot/FxAnchor", 0, (0, 0, 0), (0, 0, 0, 1), (1, 1, 1)),
        ]))
        part = dict(partId="Blade", object="Blade", skeleton="weapon_rig",
            bonePaths=["WeaponRoot/FxAnchor"],
            visibilityExpression=("number", 1.0, "", None, None))
        author_format3.write_format3_directory(
            output, "UnanimatedSkeleton", [part], {}, [], [], [], [], [],
            skeletons=[skeleton])
        mesh_path = output / "meshes" / "Blade.effres"
        mesh_path.parent.mkdir(parents=True, exist_ok=True)
        mesh_path.write_bytes(author_format3.encode_resource_file(
            1, b"mesh metadata", b"mesh"))

        manifest = (output / "mod.ini").read_text("utf-8")
        self.assertIn("[Skeletonweapon_rig]", manifest)
        self.assertIn("skeleton=weapon_rig", manifest)
        self.assertNotIn("[ShapeAnimation", manifest)
        self.assertEqual(read_format3_directory(output)["format"], 3)

    def test_shape_and_bone_tracks_share_one_animation_clip_and_timeline(self):
        output = self.root / "mixed-animation"
        output.mkdir()
        skeleton = dict(id="weapon_rig", data=bone_codec.encode_skeleton([
            ("WeaponRoot", -1, (0, 0, 0), (0, 0, 0, 1), (1, 1, 1)),
        ]))
        samples = [(0, 0, 0, 0, 0, 0, 1, 1, 1, 1)] * 3
        bone_clip = dict(id="shared_swing", skeleton="weapon_rig", speed=1.0,
            sample_rate=2,
            key="F6", autoplay=False, data=bone_codec.encode_animation({
                "id": "shared_swing", "skeleton": "weapon_rig", "duration": 1.0,
                "sample_rate": 2, "loop": True,
                "tracks": [{"path": "WeaponRoot", "samples": samples}],
            }))
        part = dict(partId="Blade", object="Blade", skeleton="weapon_rig",
            visibilityBindings=[],
            visibilityExpression=("number", 1.0, "", None, None),
            shapeControls=[dict(enabled=True, identity="smile", shape="Smile",
                variable="$smile", label="Smile", default=0.0, minimum=0.0,
                maximum=1.0, hotkey_increase="", hotkey_decrease="",
                hotkey_speed=1.0)],
            shapeAnimations=[dict(id="shared_swing", label="Swing", key="F6",
                duration=1.0, speed=1.0, loop=True, autoplay=False, sample_rate=2,
                channels={"Smile": [0.0, 1.0, 0.0]})])
        author_format3.write_format3_directory(
            output, "MixedAnimation", [part], {}, [], [], [], [], [],
            skeletons=[skeleton], bone_animations=[bone_clip])
        manifest = (output / "mod.ini").read_text("utf-8")
        self.assertIn("[Animationshared_swing]", manifest)
        self.assertNotIn("[BoneAnimationshared_swing]", manifest)

        invalid = dict(bone_clip, data=bone_codec.encode_animation({
            "id": "shared_swing", "skeleton": "weapon_rig", "duration": 1.0,
            "sample_rate": 2, "loop": True,
            "tracks": [{"path": "WeaponRoot", "samples": samples[:2]}],
        }))
        with self.assertRaisesRegex(ValueError, "share one sample timeline"):
            author_format3._controls_resource(
                "MixedAnimation", [part], [], [], [], bone_animations=[invalid])

        invalid_rate = dict(bone_clip, data=bone_codec.encode_animation({
            "id": "shared_swing", "skeleton": "weapon_rig", "duration": 1.0,
            "sample_rate": 3, "loop": True,
            "tracks": [{"path": "WeaponRoot", "samples": samples}],
        }))
        with self.assertRaisesRegex(ValueError, "share one sample rate"):
            author_format3._controls_resource(
                "MixedAnimation", [part], [], [], [], bone_animations=[invalid_rate])

        second_skeleton = dict(id="shield_rig", data=bone_codec.encode_skeleton([
            ("ShieldRoot", -1, (0, 0, 0), (0, 0, 0, 1), (1, 1, 1)),
        ]))
        second_clip = dict(bone_clip, skeleton="shield_rig", data=bone_codec.encode_animation({
            "id": "shared_swing", "skeleton": "shield_rig", "duration": 1.0,
            "sample_rate": 2, "loop": True,
            "tracks": [{"path": "ShieldRoot", "samples": samples}],
        }))
        merged, _ = author_format3._controls_resource(
            "MixedAnimation", [part], [], [], [],
            bone_animations=[bone_clip, second_clip])
        self.assertTrue(merged)

    def test_part_can_inherit_a_runtime_renderer_material_slot(self):
        output = self.root / "inherited-material"
        output.mkdir()
        part = dict(partId="Body", object="Body", materialSlot=2,
                    vertices=3, indices=3, bonePaths=[], shapeNames=[],
                    authorData=b"editable mesh",
                    visibilityExpression=("number", 1.0, "", None, None))
        rules = [
            dict(hidden=False, parts=["Body"], targets=[dict(
                level=0, sourcePath="assets/game/body.mesh", sourceAsset="Body")]),
            dict(hidden=False, parts=["Body"], targets=[dict(
                level=0, sourcePath="assets/alternate/body.mesh", sourceAsset="Body")]),
        ]
        written = author_format3.write_format3_directory(
            output, "InheritedMaterial", [part], {}, [], rules, [], [], [])
        mesh = output / "meshes" / "Body.effres"
        mesh.parent.mkdir(parents=True, exist_ok=True)
        mesh.write_bytes(author_format3.encode_resource_file(1, b"mesh metadata", b"mesh"))
        manifest = (output / "mod.ini").read_text("utf-8")
        self.assertIn("material_slot=2", manifest)
        self.assertNotIn("material=", manifest)
        replace_sections = [name for name in written["_ini"] if name.startswith("Replace")]
        self.assertEqual(len(replace_sections), 2)
        self.assertEqual({written["_ini"][name]["target.path"] for name in replace_sections},
                         {"assets/game/body.mesh", "assets/alternate/body.mesh"})
        self.assertTrue(all(written["_ini"][name]["parts"] == "PartBody"
                            for name in replace_sections))
        read_format3_directory(output)

    def test_effect_attachment_uses_declared_format3_part_id(self):
        source_part = "PartS_actor_wulfa_cloth_01_lod0_1.___8c6702d24e8fe635"
        runtime_part = "Part" + source_part
        parts = [dict(partId=source_part, object="Cloth", visibilityExpression=(
            "number", 1.0, "", None, None))]
        effect = dict(id="Petals", label="Petals", part=source_part, bone="",
                      autoplay=True, position=[0.0, 0.0, 0.0],
                      rotation=[0.0, 0.0, 0.0, 1.0], scale=[1.0, 1.0, 1.0],
                      targetPrefab="assets/actor.prefab", rendererPath="Cloth",
                      targetPath="assets/cloth.mesh", targetAsset="Cloth", bones=[],
                      native=dict(key="A" * 64, prefab="assets/effect.prefab",
                                  rootName="Effect", root=0, particles=0, bundles=[]))

        encoded, _ = author_format3._controls_resource(
            "EffectSmoke", parts, [], [], [effect])
        header = HEADER.unpack_from(encoded)
        metadata = encoded[HEADER.size:HEADER.size + header[3]]
        expected = runtime_part.encode("utf-8")
        stale = source_part.encode("utf-8")
        self.assertIn(struct.pack("<Q", len(expected)) + expected, metadata)
        self.assertNotIn(struct.pack("<Q", len(stale)) + stale, metadata)

    def test_static_renderer_effect_accepts_an_empty_bone_palette(self):
        effect = dict(id="WeaponSpark", label="Weapon Spark", part="", bone="",
                      autoplay=True, position=[0.0, 0.0, 0.0],
                      rotation=[0.0, 0.0, 0.0, 1.0], scale=[1.0, 1.0, 1.0],
                      targetPrefab="assets/weapons/weapon.prefab",
                      rendererPath="",
                      targetPath="assets/weapons/weapon.mesh", targetAsset="Weapon",
                      bones=[], native=dict(key="A" * 64, prefab="assets/effect.prefab",
                          rootName="Effect", root=0, particles=0,
                          bundles=[dict(file="effects/0", hash="B" * 64,
                                        size=48, dependencies=[],
                                        data=b"UnityFS\x00static effect fixture")]))
        encoded, _ = author_format3._controls_resource("WeaponFx", [], [], [], [effect])
        header = HEADER.unpack_from(encoded)
        metadata = encoded[HEADER.size:HEADER.size + header[3]]
        self.assertIn(b"WeaponSpark", metadata)
        self.assertIn(b"assets/weapons/weapon.prefab", metadata)

    def test_effect_attachment_rejects_an_undeclared_part(self):
        effect = dict(part="MissingPart")
        with self.assertRaisesRegex(ValueError, "Effect attachment Part is not declared"):
            author_format3._effect_runtime_part(
                effect, [dict(partId="OtherPart")])

    def test_texture_effres_does_not_repeat_manifest_or_dds_metadata(self):
        encoded = (self.root / "textures/a.effres").read_bytes()
        self.assertEqual(HEADER.unpack_from(encoded)[3], 0)
        self.assertEqual(read_format3_directory(self.root)["format"], 3)

    def test_shared_texture_file_rejects_conflicting_dds_semantics(self):
        (self.root / "textures/b.dds").write_bytes(_dds())
        (self.root / "mod.ini").write_text(
            "[Mod]\nformat=3\nid=TextureSmoke\n\n[TextureA]\n"
            "path=textures/a.effres\ndds=textures/a.dds\n\n"
            "[TextureB]\npath=textures/a.effres\ndds=textures/b.dds\n",
            encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "conflicting DDS or sampling semantics"):
            read_format3_directory(self.root)

    def test_author_encoders_emit_final_format3_resources(self):
        material_author = (
            b"format=EFFMAT\nversion=1\noverrides=true\n"
            b"source=assets/game/metal.mat\nname=Metal\n")
        material = author_format3.encode_material_resource(
            "MaterialMetal", "materials/Metal.effres",
            "assets/game/metal.mat", "Metal", material_author)
        header = HEADER.unpack_from(material)
        self.assertEqual((header[1], header[2]), (3, 2))
        self.assertEqual(material[HEADER.size + header[3]:], material_author)

        native, author = b"native physics", b"editable physics"
        physics = author_format3.encode_physics_resource(
            "ResourcePhysicsA", "physics/ResourcePhysicsA.effres", native,
            author, [("assets/game/body.prefab", "Body")])
        header = HEADER.unpack_from(physics)
        metadata = author_format3._MetadataReader(
            physics[HEADER.size:HEADER.size + header[3]])
        self.assertEqual((metadata.string(), metadata.integer()),
                         (author_format3.METADATA_STRUCTURE, 4))
        self.assertEqual(physics[HEADER.size + header[3]:], native + author)

        effect = author_format3.encode_effect_resource(
            "ResourceEffectA", "effects/a.effres", b"UnityFS\0effect")
        self.assertEqual(HEADER.unpack_from(effect)[2], 5)
        effect_author = author_format3.encode_effect_author_resource(
            "ResourceEffectAuthorA", "effects/author/a.effres", b"editable effect")
        self.assertEqual(HEADER.unpack_from(effect_author)[2], 7)

    def test_direct_resource_encoder_rejects_legacy_texture_metadata(self):
        with self.assertRaisesRegex(ValueError, "Texture resource metadata"):
            author_format3.encode_resource_file(3, b"pixels metadata", b"pixels")
        with self.assertRaisesRegex(ValueError, "metadata is required"):
            author_format3.encode_resource_file(2, b"", b"material")

    def test_rejects_resource_metadata_checksum_mismatch(self):
        path = self.root / "textures/a.effres"
        path.write_bytes(_effres(b"metadata", b"native", kind=2))
        value = bytearray(path.read_bytes())
        value[HEADER.size] ^= 1
        path.write_bytes(value)
        with self.assertRaisesRegex(ValueError, "checksum"):
            author_format3._validate_effres(path, 2)

    def test_accepts_replaced_dds_layout_and_rejects_truncated_mip_data(self):
        scaffold_before = (self.root / "textures/a.effres").read_bytes()
        (self.root / "textures/a.dds").write_bytes(_dds(width=8, height=8, payload=b"P" * 64))
        self.assertEqual(read_format3_directory(self.root)["format"], 3)
        self.assertEqual((self.root / "textures/a.effres").read_bytes(), scaffold_before)
        (self.root / "textures/a.dds").write_bytes(_dds(payload=b"P" * 15))
        with self.assertRaisesRegex(ValueError, "mip data extent"):
            read_format3_directory(self.root)

    def test_accepts_photoshop_2d_depth_one_without_changing_scaffold(self):
        scaffold = (self.root / 'textures/a.effres').read_bytes()
        data = bytearray(_dds())
        struct.pack_into('<I', data, 24, 1)
        (self.root / 'textures/a.dds').write_bytes(data)
        self.assertEqual(read_format3_directory(self.root)['format'], 3)
        self.assertEqual((self.root / 'textures/a.effres').read_bytes(), scaffold)

    def test_rejects_volume_cube_and_array_dds(self):
        for offset, value in ((24, 2), (8, 0x801007), (112, 0x200000),
                              (112, 0x200), (132, 4), (140, 2)):
            with self.subTest(offset=offset, value=value):
                data = bytearray(_dds())
                struct.pack_into('<I', data, offset, value)
                (self.root / 'textures/a.dds').write_bytes(data)
                with self.assertRaises(ValueError):
                    read_format3_directory(self.root)

    def test_rejects_resource_kind_mismatch(self):
        (self.root / "textures/a.effres").write_bytes(
            HEADER.pack(MAGIC, 3, 2, 0, 64, hashlib.sha256(b"").digest()) + b"P" * 64)
        with self.assertRaisesRegex(ValueError, "envelope differs"):
            read_format3_directory(self.root)

    def test_rejects_dds_path_escape(self):
        (self.root / "mod.ini").write_text(
            "[Mod]\nformat=3\nid=TextureSmoke\n\n"
            "[TextureA]\npath=textures/a.effres\n"
            "dds=../outside.dds\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "escapes Mod"):
            read_format3_directory(self.root)

    def test_validates_material_author_snapshot_and_texture_bindings(self):
        author = ("format=EFFMAT\nversion=1\noverrides=true\n"
                  "source=assets/game/metal.mat\nname=Metal\nfloat._Gloss=.4\n").encode()
        relative = "materials/metal.effres"
        (self.root / relative).parent.mkdir(parents=True)
        (self.root / relative).write_bytes(author_format3.encode_material_resource(
            "MaterialMetal", "native/materials/metal.effres",
            "assets/game/metal.mat", "Metal", author))
        with (self.root / "mod.ini").open("a", encoding="utf-8") as stream:
            stream.write("\n[MaterialMetal]\npath=" + relative +
                         "\nsource.path=assets/game/metal.mat\nsource.asset=Metal\n")
        self.assertEqual(read_format3_directory(self.root)["format"], 3)

    def test_rejects_material_author_texture_binding_drift(self):
        author = ("format=EFFMAT\nversion=1\noverrides=true\n"
                  "source=assets/game/metal.mat\nname=Metal\n").encode()
        relative = "materials/metal.effres"
        (self.root / relative).parent.mkdir(parents=True)
        (self.root / relative).write_bytes(author_format3.encode_material_resource(
            "MaterialMetal", relative, "assets/game/metal.mat", "Metal", author))
        with (self.root / "mod.ini").open("a", encoding="utf-8") as stream:
            stream.write("\n[MaterialMetal]\npath=" + relative +
                         "\nsource.path=assets/game/metal.mat\nsource.asset=Metal\n"
                         "texture._BaseMap=TextureA\n")
        self.assertEqual(read_format3_directory(self.root)["format"], 3)
        ini = author_format3.configparser.ConfigParser(
            interpolation=None, delimiters=("=",), strict=True)
        ini.optionxform = str
        ini.read(self.root / "mod.ini", encoding="utf-8")
        ini["MaterialMetal"]["texture._BaseMap"] = "TextureMissing"
        with (self.root / "mod.ini").open("w", encoding="utf-8") as stream:
            ini.write(stream)
        with self.assertRaisesRegex(ValueError, "undeclared Texture"):
            read_format3_directory(self.root)

    def test_rejects_duplicate_ini_owned_material_bindings_in_snapshot(self):
        author = ("format=EFFMAT\nversion=1\noverrides=true\n"
                  "source=assets/game/metal.mat\nname=Metal\n"
                  "texture._BaseMap=TextureA\n").encode()
        relative = "materials/metal.effres"
        (self.root / relative).parent.mkdir(parents=True)
        (self.root / relative).write_bytes(author_format3.encode_material_resource(
            "MaterialMetal", relative, "assets/game/metal.mat", "Metal", author))
        with (self.root / "mod.ini").open("a", encoding="utf-8") as stream:
            stream.write("\n[MaterialMetal]\npath=" + relative +
                         "\nsource.path=assets/game/metal.mat\nsource.asset=Metal\n"
                         "texture._BaseMap=TextureA\n")
        with self.assertRaisesRegex(ValueError, "duplicated in author snapshot"):
            read_format3_directory(self.root)

    def test_legacy_resource_controls_keep_material_binding_mirror_compatible(self):
        author = ("format=EFFMAT\nversion=1\noverrides=true\n"
                  "source=assets/game/metal.mat\nname=Metal\n"
                  "texture._BaseMap=TextureA\n").encode()
        relative = "materials/metal.effres"
        (self.root / relative).parent.mkdir(parents=True)
        (self.root / relative).write_bytes(author_format3.encode_material_resource(
            "MaterialMetal", relative, "assets/game/metal.mat", "Metal", author))
        (self.root / "controls.effres").write_bytes(
            author_format3.encode_resource_file(6, b"legacy controls metadata", b""))
        ini = author_format3.configparser.ConfigParser(
            interpolation=None, delimiters=("=",), strict=True)
        ini.optionxform = str
        ini.read(self.root / "mod.ini", encoding="utf-8")
        ini["Mod"]["controls"] = "resource"
        ini["ResourceControls"] = {"type": "controls", "path": "controls.effres"}
        ini["MaterialMetal"] = {
            "path": relative, "source.path": "assets/game/metal.mat",
            "source.asset": "Metal", "texture._BaseMap": "TextureA"}
        with (self.root / "mod.ini").open("w", encoding="utf-8") as stream:
            ini.write(stream)
        self.assertIn("controls.effres", read_format3_directory(self.root)["_files"])

    def test_physics_author_snapshot_is_bound_to_manifest_targets(self):
        relative = "physics/a.effres"
        path = self.root / relative
        path.parent.mkdir(parents=True)
        native = b"native-physics"
        document = b"EFFPHYS\0document"
        skeleton = b"EFFSKEL\0skeleton"
        author = b"EFFPHS3\0" + struct.pack("<IQQ", 1, len(document), len(skeleton)) + document + skeleton
        target = ("assets/game/body.mesh", "Body")
        lod_target = ("assets/game/character.fbx", "Body_LOD1")
        path.write_bytes(author_format3.encode_physics_resource(
            "ResourcePhysicsA", "native/physics/a.effres", native,
            author, [target, lod_target]))
        controls = self.root / "controls.effres"
        controls.parent.mkdir(parents=True, exist_ok=True)
        controls.write_bytes(_effres(b"controls", b"", kind=6))
        with (self.root / "mod.ini").open("a", encoding="utf-8") as stream:
            stream.write(
                "\n[ResourcePhysicsA]\ntype=physics\npath=" + relative + "\ntargets=ReplaceBody\n\n"
                "[ResourceControls]\ntype=controls\npath=controls.effres\n\n"
                "[ReplaceBody]\ntarget.path=" + target[0] + "\ntarget.asset=" + target[1] + "\n"
                "lod.1.path=" + lod_target[0] + "\nlod.1.asset=" + lod_target[1] + "\n")
        self.assertEqual(read_format3_directory(self.root)["format"], 3)
        ini = author_format3.configparser.ConfigParser(interpolation=None, delimiters=("=",), strict=True)
        ini.optionxform = str
        ini.read(self.root / "mod.ini", encoding="utf-8")
        ini["ReplaceBody"]["lod.1.asset"] = "Other"
        # A single legacy rule unambiguously follows its current INI targets.
        author_format3._validate_physics_resource(
            self.root, relative, "ResourcePhysicsA", ini, len(native + author))
        for index, (source, level) in enumerate(((target, 0), (lod_target, 1))):
            prefix = 'bind.' + str(index) + '.'
            ini['ResourcePhysicsA'].update({prefix + 'rule': 'ReplaceBody',
                prefix + 'lod': str(level), prefix + 'source.path': source[0],
                prefix + 'source.asset': source[1]})
        author_format3._validate_physics_resource(
            self.root, relative, 'ResourcePhysicsA', ini, len(native + author))
        ini['ResourcePhysicsA']['bind.1.source.asset'] = 'ForgedSource'
        with self.assertRaisesRegex(ValueError, 'source binding'):
            author_format3._validate_physics_resource(
                self.root, relative, 'ResourcePhysicsA', ini, len(native + author))

    def test_writer_binds_physics_sources_to_independent_rules_and_lods(self):
        output = self.root / 'physics-retarget'
        output.mkdir()
        targets = [('assets/game/a.mesh', 'A'), ('assets/game/a_lod1.mesh', 'A_LOD1'),
                   ('assets/game/b.mesh', 'B')]
        rules = [dict(hidden=False, parts=['Blade'], targets=[dict(
            sourcePath=path, sourceAsset=asset, level=level)
            for level, (path, asset) in enumerate(targets[:2])]),
            dict(hidden=False, parts=['Blade'], targets=[dict(
                sourcePath=targets[2][0], sourceAsset=targets[2][1], level=0)])]
        part = dict(partId='Blade', object='Blade',
                    visibilityExpression=('number', 1.0, '', None, None))
        document, skeleton = b'EFFPHYS\0document', b'EFFSKEL\0skeleton'
        author = b'EFFPHS3\0' + struct.pack('<IQQ', 1, len(document), len(skeleton)) + document + skeleton
        native = b'native-physics'
        written = author_format3.write_format3_directory(
            output, 'PhysicsRetarget', [part], {}, [], rules, [], [], [],
            physics=[dict(id='ResourcePhysicsRibbon', author=author,
                          native=native, targets=targets)])
        ini = written['_ini']
        resource = ini['ResourcePhysicsRibbon']
        mapping = {(resource[f'bind.{i}.source.path'], resource[f'bind.{i}.source.asset']):
                   (resource[f'bind.{i}.rule'], resource[f'bind.{i}.lod']) for i in range(3)}
        self.assertEqual(mapping, {targets[0]: ('ReplaceA', '0'),
                                  targets[1]: ('ReplaceA', '1'), targets[2]: ('ReplaceB', '0')})
        path = output / resource['path']
        before = path.read_bytes()
        ini['ReplaceA']['target.path'] = 'assets/game/new_a.mesh'
        ini['ReplaceA']['target.asset'] = 'NewA'
        ini['ReplaceA']['lod.1.path'] = 'assets/game/new_a_lod1.mesh'
        ini['ReplaceA']['lod.1.asset'] = 'NewA_LOD1'
        ini['ReplaceB']['target.path'] = 'assets/game/new_b.mesh'
        ini['ReplaceB']['target.asset'] = 'NewB'
        author_format3._validate_physics_resource(output, resource['path'],
            'ResourcePhysicsRibbon', ini, len(native + author))
        self.assertEqual(path.read_bytes(), before)

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
        relative = "effects/author/test.effres"
        resource_id = "ResourceEffectAuthor_Test"
        (self.root / relative).parent.mkdir(parents=True, exist_ok=True)
        (self.root / relative).write_bytes(author_format3.encode_effect_author_resource(
            resource_id, "native/effect-author/test.effres", author))
        controls = "controls.effres"
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


class LogicalMeshRouteTests(unittest.TestCase):
    def test_dependency_only_routes_use_full_logical_identity(self):
        target = dict(level=0, sourcePath="assets/weapons/sword.fbx", sourceAsset="Sword")
        rules = [dict(targets=[target], hidden=False)]
        source = types.SimpleNamespace(manifest={"resources": [
            dict(type="Mesh", logicalPath=target["sourcePath"], name="Sword",
                 sourceBundle="Bundles/Windows/main/sword.ab"),
            dict(type="Mesh", logicalPath="assets/other.fbx", name="Sword",
                 sourceBundle="Bundles/Windows/main/other.ab")]})
        routes = author_format3.resolve_target_bundles([source], rules)
        self.assertEqual(routes, {("assets/weapons/sword.fbx", "Sword"):
                                  "bundles/windows/main/sword.ab"})
        conflicting = types.SimpleNamespace(manifest={"resources": [dict(
            source.manifest["resources"][0], sourceBundle="Bundles/Windows/main/other.ab")]})
        with self.assertRaisesRegex(ValueError, "conflicting logical Mesh Bundle routes"):
            author_format3.resolve_target_bundles([source, conflicting], rules)


if __name__ == "__main__":
    unittest.main()

"""Factory-startup regression for source and Format 3 single-material imports.

Run with Blender --background --factory-startup --python-exit-code 1 --python
this_file.py. Optional arguments after -- are real Material files to read back
into this disposable process, never into the user's open scene.
"""
import configparser
import importlib.util
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
import zlib

import bpy


TEST_DIR = Path(__file__).resolve().parent
ADDON_DIR = Path(os.environ.get("EFF_TEST_ADDON_DIR", TEST_DIR)).resolve()
spec = importlib.util.spec_from_file_location(
    "eiem_material_import_test", ADDON_DIR / "__init__.py",
    submodule_search_locations=[str(ADDON_DIR)])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
package.register()
addon = package.eiem_blender_addon
sys.path.insert(0, str(TEST_DIR.parent))
from nativepack.author_format3 import encode_material_resource


def write_png(path):
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data +
                struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff))
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n" +
        chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)) +
        chunk(b"IDAT", zlib.compress(b"\x00\xff\x80\x40\xff")) +
        chunk(b"IEND", b""))


def write_dds(path):
    header = struct.pack("<7I", 124, 0x100F, 2, 2, 8, 0, 0) + bytes(44)
    header += struct.pack("<8I", 32, 0x41, 0, 32,
                          0x000000FF, 0x0000FF00, 0x00FF0000, 0xFF000000)
    header += struct.pack("<5I", 0x1000, 0, 0, 0, 0)
    path.write_bytes(b"DDS " + header + bytes((255, 128, 64, 255)) * 4)


def make_package(root, kind):
    (root / "materials").mkdir(parents=True)
    (root / "textures").mkdir()
    source = "assets/test/body.mat"
    author = ("format=EFFMAT\nversion=1\nsource=" + source +
              "\nname=Body\nshader=TestShader\nfloat._Gloss=0.4\n"
              "texture._BaseMap=TextureBody\ntexture._DetailMap=TextureBody\n"
              "texture_scale._BaseMap=2,3\n")
    if kind == "source":
        (root / "source").mkdir()
        (root / "source/manifest.json").write_text(
            json.dumps(dict(format="EFFSOURCE", version=1)), encoding="utf-8")
        relative = "materials/body.mat"
        (root / relative).write_text(author, encoding="utf-8")
        write_png(root / "textures/body.png")
        ini = "[TextureBody]\npath=textures/body.png\n"
    else:
        relative = "materials/body.effres"
        author += "overrides=true\n"
        (root / relative).write_bytes(encode_material_resource(
            "MaterialBody", relative, source, "Body", author.encode("utf-8")))
        write_dds(root / "textures/body.dds")
        ini = ("[Mod]\nformat=3\n[TextureBody]\n"
               "path=textures/body.effres\ndds=textures/body.dds\n")
    ini += ("source=assets/test/body.png\nname=BodyTexture\nlinear=true\n"
            "[MaterialBody]\npath=" + relative + "\n")
    if kind == "format3":
        ini += "texture._BaseMap=TextureBody\ntexture._DetailMap=TextureBody\n"
    (root / "mod.ini").write_text(ini, encoding="utf-8")
    return root / relative


class MaterialImportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="eff-material-import-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.mesh = bpy.data.meshes.new("ImportTarget")
        self.obj = bpy.data.objects.new("ImportTarget", self.mesh)
        bpy.context.scene.collection.objects.link(self.obj)
        self.previous = bpy.data.materials.new("KeepExisting")
        self.previous["sentinel"] = "untouched"
        self.mesh.materials.append(self.previous)
        self.before = self.state()

    def state(self):
        return (len(bpy.data.materials), len(bpy.data.images),
                self.obj.active_material.as_pointer())

    def test_source_material_imports_only_its_referenced_images(self):
        path = make_package(self.root / "source", "source")
        with (path.parents[1] / "mod.ini").open("a", encoding="utf-8") as stream:
            stream.write("[TextureUnused]\npath=missing.png\n")
        material = addon.import_material_file(path, self.obj)
        self.assertEqual(self.obj.active_material, material)
        self.assertEqual(self.previous["sentinel"], "untouched")
        self.assertEqual(len(bpy.data.images), self.before[1] + 1)
        self.assertEqual(material["eiem_target_path"], "assets/test/body.mat")
        self.assertEqual(material["eiem_target_asset"], "Body")
        self.assertEqual(material["eiem_texture._BaseMap"], material["eiem_texture._DetailMap"])
        baseline = json.loads(material["eiem_baseline_json"])
        self.assertEqual(baseline["float._Gloss"], "0.4")
        self.assertNotIn("format", baseline)
        image = next(i for i in bpy.data.images
                     if Path(addon.image_absolute_path(i)) == path.parents[1] / "textures/body.png")
        self.assertEqual(image["eiem_source_section"], "TextureBody")
        self.assertEqual(image["eiem_author_package"], str(path.parents[1]))
        self.assertEqual(image["eiem_relative_path"], "textures/body.png")
        self.assertEqual(image.colorspace_settings.name, "Non-Color")
        by_section = {str(i.get("eiem_section")): i for i in bpy.data.images if i.get("eiem_section")}
        payload, _ = addon.material_override_payload(material, by_section, True)
        self.assertNotIn("float._Gloss=0.4", payload, "source defaults became Mod edits")
        material["eiem_float._Gloss"] = "0.6"
        payload, _ = addon.material_override_payload(material, by_section, True)
        self.assertIn("float._Gloss=0.6", payload)

    def test_mod_material_keeps_sparse_edits_and_dds_bindings(self):
        path = make_package(self.root / "mod", "format3")
        material = addon.import_material_file(path, self.obj)
        self.assertEqual(json.loads(material["eiem_baseline_json"]),
                         {"source": "assets/test/body.mat"})
        self.assertTrue(material["eiem_texture._BaseMap"].endswith("body.dds"))
        by_section = {str(i.get("eiem_section")): i for i in bpy.data.images if i.get("eiem_section")}
        payload, images = addon.material_override_payload(material, by_section, True)
        self.assertIn("float._Gloss=0.4", payload)
        self.assertEqual(len(images), 1)

    def test_mod_import_exposes_embedded_defaults_without_exporting_them(self):
        path = make_package(self.root / "mod", "format3")
        source = "assets/test/body.mat"
        record = dict(format="EFF_MATERIAL_IMPORT/1", source=source,
            value=dict(m_Name="Body", m_CustomRenderQueue=3000,
                m_ValidKeywords=["_MASK", "_NOISE"], m_InvalidKeywords=["_CLIP"],
                m_SavedProperties=dict(
                    m_Floats=[dict(first="_Gloss", second=0.2), dict(first="_UseMask", second=1.0)],
                    m_Colors=[dict(first="_Tint", second=dict(r=1.0, g=0.5, b=0.0, a=1.0)),
                              dict(first="_Accent", second=dict(r=1.0, g=0.0, b=0.0, a=1.0))],
                    m_TexEnvs=[dict(first="_Noise", second=dict(
                        m_Scale=dict(x=2.0, y=1.0), m_Offset=dict(x=0.0, y=0.0)))])),
            bindings=[dict(field="$.m_SavedProperties.m_TexEnvs[0].second.m_Texture",
                           type="Texture", path="packages/test/noise.png")])
        author = (f"format=EFFMAT\nversion=1\noverrides=true\nsource={source}\nname=Body\n"
                  "float._Gloss=0.4\nkeyword._MASK=1\n"
                  "color._Accent=0,1,0,1\n;EFF_MATERIAL_IMPORT=" + json.dumps(record) + "\n")
        path.write_bytes(encode_material_resource(
            "MaterialBody", "materials/body.effres", source, "Body", author.encode()))

        for route in ("material", "package"):
            with self.subTest(route=route):
                if route == "material":
                    material = addon.import_material_file(path, self.obj)
                else:
                    addon.import_package(path.parents[1])
                    material = next(m for m in bpy.data.materials if
                                    m.get("eiem_section") == "MaterialBody" and
                                    m.get("eiem_author_package") == str(path.parents[1]))
                self.assertEqual(material["eiem_float._Gloss"], "0.4")
                self.assertEqual(material["eiem_float._UseMask"], "1.0")
                self.assertEqual(material["eiem_value4._Tint"], "1.0,0.5,0.0,1.0")
                self.assertEqual(material["eiem_color._Accent"], "0,1,0,1")
                self.assertEqual(material["eiem_texture._Noise"], "packages/test/noise.png")
                self.assertEqual(material["eiem_texture_scale._Noise"], "2.0,1.0")
                self.assertEqual(material["eiem_keyword._MASK"], "1")
                self.assertEqual(material["eiem_keyword._CLIP"], "0")
                self.assertTrue(material["eiem_texture._BaseMap"].endswith("body.dds"))
                by_section = {i["eiem_section"]: i for i in bpy.data.images if i.get("eiem_section")}
                payload, _ = addon.material_override_payload(material, by_section, True)
                self.assertIn("float._Gloss=0.4", payload)
                self.assertNotIn("float._UseMask=1.0", payload)
                self.assertIn("keyword._MASK=1", payload)
                self.assertIn("color._Accent=0,1,0,1", payload)
                self.assertNotIn("keyword._NOISE=1", payload)
                self.assertNotIn("keyword._CLIP=0", payload)
                self.assertNotIn("texture._Noise", {line.partition("=")[0] for line in payload})
                self.assertNotIn("texture_scale._Noise=2.0,1.0", payload)
                material["eiem_float._UseMask"] = "0"
                payload, _ = addon.material_override_payload(material, by_section, True)
                self.assertIn("float._UseMask=0", payload)

    def dds_only_package(self, label, linear="true"):
        path = make_package(self.root / label, "format3")
        ini = path.parents[1] / "mod.ini"
        text = ini.read_text(encoding="utf-8")
        text = text.replace("path=textures/body.effres\n", "")
        text = text.replace("source=assets/test/body.png\nname=BodyTexture\n",
            "source.path=assets/declared/flow.png\nsource.asset=DeclaredFlow\n"
            "target.path=assets/legacy/unused.png\ntarget.asset=UnusedLegacyTarget\n")
        text = text.replace("linear=true\n", "" if linear is None else "linear=" + linear + "\n")
        ini.write_text(text, encoding="utf-8")
        return path

    def test_dds_only_source_and_sampling_survive_both_import_export_routes(self):
        for route in ("material", "package"):
            with self.subTest(route=route):
                path = self.dds_only_package(route)
                root = path.parents[1]
                if route == "material":
                    material = addon.import_material_file(path, self.obj)
                else:
                    self.assertEqual(addon.import_package(root), 0)
                    material = next(row for row in bpy.data.materials
                        if row.get("eiem_author_package") == str(root))
                image = next(row for row in bpy.data.images
                    if row.get("eiem_author_package") == str(root))
                self.assertEqual(image["eiem_target_path"], "assets/declared/flow.png")
                self.assertEqual(image["eiem_target_asset"], "DeclaredFlow")
                self.assertEqual(image["eiem_linear"], "true")
                native = dict(details=dict(textures=[dict(property=prop,
                    pointer=dict(isNull=False, identity="original"))
                    for prop in ("_BaseMap", "_DetailMap")]), native=dict(typeHash="12" * 16))
                original = dict(logicalPath="assets/original.png", name="Original",
                    details=dict(format="BC5", colorSpace=1))
                declared = dict(logicalPath="assets/declared/flow.png", name="DeclaredFlow",
                    details=dict(format="BC7", colorSpace=1))
                source = SimpleNamespace(resolve=lambda kind, *args:
                    native if kind == "Material" else declared,
                    resolve_identity=lambda identity: original)
                exporter = addon.native_export
                images = {str(row.get("eiem_section")): row for row in bpy.data.images
                          if row.get("eiem_section")}
                textures = {}
                record = exporter.collect_material(addon, source, material, images, textures,
                    self.root, None, include_native_fields=False)
                self.assertEqual(len(textures), 1, "two slots must share their authored Image")
                texture = next(iter(textures.values()))
                self.assertEqual((texture["sourcePath"], texture["sourceAsset"], texture["colorSpace"]),
                    ("assets/declared/flow.png", "DeclaredFlow", 0))
                output = self.root / (route + "-published")
                output.mkdir()
                exporter.pack_modules()[0]["author_format3"].write_format3_directory(
                    output, "RoundTrip", [], {record["id"]: record}, list(textures.values()), [], [], [], [])
                result = configparser.ConfigParser(interpolation=None)
                result.read(output / "mod.ini", encoding="utf-8")
                texture_section = next(result[s]["texture._BaseMap"] for s in result.sections()
                                       if result[s].get("texture._BaseMap"))
                values = result[texture_section]
                self.assertEqual(values["source.path"], "assets/declared/flow.png")
                self.assertEqual(values["source.asset"], "DeclaredFlow")
                self.assertEqual(values["linear"], "true")
                self.assertNotIn("path", values)
                self.assertEqual((output / values["dds"]).read_bytes(),
                                 (root / "textures/body.dds").read_bytes())
                self.assertFalse(list((output / "textures").glob("*.effres")))

    def test_explicit_srgb_is_not_overwritten_by_normal_slot_heuristic(self):
        material = addon.import_material_file(self.dds_only_package("normal", "false"), self.obj)
        material["eiem_texture._BumpMap"] = material["eiem_texture._BaseMap"]
        images = {str(row.get("eiem_section")): row for row in bpy.data.images
                  if row.get("eiem_section")}
        payload, _ = addon.material_override_payload(material, images, True)
        binding = next(row.split("=", 1)[1] for row in payload
                       if row.startswith("texture._BumpMap="))
        image = images[binding]
        self.assertEqual(image["eiem_author_package"], str(self.root / "normal"))
        self.assertEqual(image["eiem_linear"], "false")

    def test_omitted_linear_stays_inherited_after_both_import_routes(self):
        for route in ("material", "package"):
            with self.subTest(route=route):
                path = self.dds_only_package("inherited-" + route, None)
                if route == "material":
                    material = addon.import_material_file(path, self.obj)
                else:
                    addon.import_package(path.parents[1])
                    material = next(row for row in bpy.data.materials
                        if row.get("eiem_author_package") == str(path.parents[1]))
                image = next(row for row in bpy.data.images
                    if row.get("eiem_author_package") == str(path.parents[1]))
                material["eiem_texture._BumpMap"] = material["eiem_texture._BaseMap"]
                images = {str(row.get("eiem_section")): row for row in bpy.data.images
                          if row.get("eiem_section")}
                addon.material_override_payload(material, images, True)
                self.assertNotIn("eiem_linear", image)

    def test_equal_dds_pixels_do_not_merge_distinct_logical_templates(self):
        first_path = self.dds_only_package("first")
        second_path = self.dds_only_package("second")
        ini = second_path.parents[1] / "mod.ini"
        ini.write_text(ini.read_text(encoding="utf-8").replace(
            "assets/declared/flow.png", "assets/another/flow.png").replace(
            "DeclaredFlow", "AnotherFlow"), encoding="utf-8")
        first = addon.import_material_file(first_path, self.obj)
        second = addon.import_material_file(second_path, self.obj)
        images = {str(row.get("eiem_section")): row for row in bpy.data.images
                  if row.get("eiem_section")}
        bindings = []
        for material in (first, second):
            payload, _ = addon.material_override_payload(material, images, True)
            section = next(row.split("=", 1)[1] for row in payload
                           if row.startswith("texture._BaseMap="))
            bindings.append(section)
        self.assertNotEqual(bindings[0], bindings[1])
        self.assertEqual(images[bindings[0]]["eiem_target_path"], "assets/declared/flow.png")
        self.assertEqual(images[bindings[1]]["eiem_target_path"], "assets/another/flow.png")

    def test_new_dds_without_sampling_override_uses_original_slot_defaults(self):
        dds = self.root / "external.dds"
        write_dds(dds)
        material = bpy.data.materials.new("NewAuthorMaterial")
        material["eiem_source"] = "assets/test/body.mat"
        material["eiem_name"] = "Body"
        material["eiem_texture._BaseMap"] = str(dds)
        images, textures = {}, {}
        native = dict(details=dict(textures=[dict(property="_BaseMap", pointer=dict(
            isNull=False, identity="original"))]), native=dict(typeHash="12" * 16))
        original = dict(logicalPath="assets/original.png", name="Original",
            details=dict(format="BC5", colorSpace=0))
        source = SimpleNamespace(resolve=lambda *args: native,
                                 resolve_identity=lambda identity: original)
        result = addon.native_export.collect_material(addon, source, material, images,
            textures, self.root, None, include_native_fields=False)
        texture = textures[result["textureEdits"][0]["texture"]]
        self.assertEqual((texture["sourcePath"], texture["sourceAsset"], texture["colorSpace"]),
                         ("assets/original.png", "Original", 0))

    def test_source_and_mod_materials_never_overwrite_each_other(self):
        first = addon.import_material_file(make_package(self.root / "source", "source"), self.obj)
        saved = dict(first.items())
        second = addon.import_material_file(make_package(self.root / "mod", "format3"), self.obj)
        self.assertNotEqual(first, second)
        self.assertEqual(dict(first.items()), saved)
        self.assertNotEqual(first["eiem_texture._BaseMap"], second["eiem_texture._BaseMap"])

    def test_missing_source_image_fails_before_allocation(self):
        path = make_package(self.root / "source", "source")
        (path.parents[1] / "textures/body.png").unlink()
        with self.assertRaisesRegex(ValueError, "missing|不存在"):
            addon.import_material_file(path, self.obj)
        self.assertEqual(self.state(), self.before)

    def test_corrupt_source_image_rolls_back_allocations(self):
        path = make_package(self.root / "source", "source")
        (path.parents[1] / "textures/body.png").write_bytes(b"not an image")
        with self.assertRaises((ValueError, RuntimeError)):
            addon.import_material_file(path, self.obj)
        self.assertEqual(self.state(), self.before)

    def test_old_runtime_mod_still_requires_manager_migration(self):
        path = make_package(self.root / "mod", "format3")
        ini = path.parents[1] / "mod.ini"
        ini.write_text(ini.read_text(encoding="utf-8").replace("format=3", "format=2"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Mod Manager"):
            addon.import_material_file(path, self.obj)
        self.assertEqual(self.state(), self.before)

    def test_file_picker_accepts_both_material_inputs(self):
        prop = bpy.ops.eiem.import_material.get_rna_type().properties["filter_glob"]
        self.assertIn("*.mat", prop.default.split(";"))
        self.assertIn("*.effres", prop.default.split(";"))

    def test_source_import_operator_assigns_only_the_active_slot(self):
        path = make_package(self.root / "source", "source")
        other_slot = bpy.data.materials.new("OtherSlot")
        self.mesh.materials.append(other_slot)
        self.obj.active_material_index = 1
        bpy.context.view_layer.objects.active = self.obj
        result = bpy.ops.eiem.import_material(filepath=str(path))
        self.assertEqual(result, {"FINISHED"})
        self.assertEqual(self.mesh.materials[0], self.previous)
        self.assertNotEqual(self.obj.active_material, other_slot)
        self.assertEqual(self.previous["sentinel"], "untouched")


try:
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(MaterialImportTests))
    if not result.wasSuccessful():
        raise AssertionError("single-material import regression failed")
    if "--" in sys.argv:
        baseline = None
        for argument in sys.argv[sys.argv.index("--") + 1:]:
            path = Path(argument).resolve()
            mesh = bpy.data.meshes.new("RealMaterialProbe")
            obj = bpy.data.objects.new("RealMaterialProbe", mesh)
            bpy.context.scene.collection.objects.link(obj)
            material = addon.import_material_file(path, obj)
            print("REAL_MATERIAL_IMPORT_OK", str(path), material["eiem_source"],
                  sum(k.startswith("eiem_texture.") for k in material.keys()))
            root = next(parent for parent in path.parents if (parent / "mod.ini").is_file())
            if addon.format_io.package_kind(root) == "source":
                baseline = root
            elif baseline is not None:
                # Exercise the real Mod -> author values -> export record ->
                # Mod decoder seam, including DDS passthrough and source choice.
                exporter = addon.native_export
                modules, _ = exporter.pack_modules()
                primary = modules["author_source"].AuthorSource(baseline, None)
                source = exporter.material_source(primary, material, {})
                assert source is primary
                images = {str(i.get("eiem_section")): i for i in bpy.data.images if i.get("eiem_section")}
                textures, texture_inputs = {}, {}
                with tempfile.TemporaryDirectory(prefix="eff-real-material-roundtrip-") as temporary:
                    record = exporter.collect_material(
                        addon, source, material, images, textures, temporary,
                        modules["type_tree"].encode, include_native_fields=False,
                        texture_inputs=texture_inputs)
                    relative = "materials/roundtrip.effres"
                    output = Path(temporary) / "roundtrip.effres"
                    output.write_bytes(encode_material_resource(
                        record["id"], relative, record["sourcePath"],
                        record["sourceAsset"], record["authorData"]))
                    decoded = addon.format_io.read_format3_material_resource(
                        output, record["id"], relative)
                    ini = addon.parse_ini(root)
                    section = next(s for s in ini.sections() if ini[s].get("path") ==
                                   material["eiem_material_file"])
                    original = addon.format_io.read_package_material(root, section, ini[section], "format3")
                    defaults = json.loads(material["eiem_baseline_json"])
                    for key, value in material.items():
                        if key.startswith(("eiem_float.", "eiem_value4.", "eiem_keyword.", "eiem_int.")):
                            field = key[5:]
                            assert value == original.get(field, defaults.get(field)), key
                            assert decoded.get(field, defaults.get(field)) == value, key
                    assert textures and all(row.get("ddsSourcePath") for row in textures.values())
                    print("REAL_MOD_MATERIAL_ROUNDTRIP_OK", record["sourcePath"], len(textures))
    print("EFF_MATERIAL_IMPORT_OK")
finally:
    package.unregister()

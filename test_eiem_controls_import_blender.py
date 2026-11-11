"""Blender integration checks for INI-backed Format 3 author controls."""
import importlib.util
import configparser
import copy
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import unittest

import bpy


TEST_DIR = Path(__file__).resolve().parent
ADDON_DIR = Path(os.environ.get("EFF_TEST_ADDON_DIR", TEST_DIR)).resolve()
spec = importlib.util.spec_from_file_location(
    "eiem_controls_import_test", ADDON_DIR / "__init__.py",
    submodule_search_locations=[str(ADDON_DIR)])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
package.register()
sys.path.insert(0, str(TEST_DIR.parent))
from nativepack.state_rules import Condition
from nativepack import author_format3, control_program


class ControlsImportTests(unittest.TestCase):
    def setUp(self):
        self.meshes = {}
        self.rigs = []
        for section in ("MeshA", "MeshB"):
            mesh = bpy.data.meshes.new(section)
            mesh.from_pydata([(0.0, 0.0, 0.0)], [], [])
            obj = bpy.data.objects.new(section, mesh)
            bpy.context.scene.collection.objects.link(obj)
            obj.shape_key_add(name="Basis")
            key = obj.shape_key_add(name="Smile")
            key.data[0].co.x += 1.0
            self.meshes[section] = obj
        self.material = bpy.data.materials.new("MaterialGlow")
        self.material["eiem_section"] = "MaterialGlow"
        self.package_collection = bpy.data.collections.new("EFF Controls Import")
        self.package_collection["eiem_collection_role"] = "PACKAGE"
        self.package_collection["eiem_import_id"] = "controls-test"
        for obj in self.meshes.values():
            obj["eiem_import_id"] = "controls-test"
        bpy.context.scene.collection.children.link(self.package_collection)
        self.state = {
            "defaults": {"$shape_author_Smile": 0.25,
                         "$anim_speed_walk": 1.0,
                         "$mat_glow": 0.1},
            "persistent": {"$anim_speed_walk"},
            "controls": {
                "$shape_author_Smile": {
                    "label": "Smile", "minimum": 0.0, "maximum": 1.0},
                "$mat_glow": {"label": "Glow", "minimum": 0.1, "maximum": 0.8},
            },
            "bindings": [
                {"id": "KeyShapeIncrease", "variable": "$shape_author_Smile",
                 "type": "hold", "key": "F7", "speed": 0.5,
                 "label": "Increase", "values": [1.0]},
                {"id": "KeyShapeDecrease", "variable": "$shape_author_Smile",
                 "type": "hold", "key": "F8", "speed": 0.5,
                 "label": "Decrease", "values": [0.0]},
            ],
            "parts": {
                "PartA": {"mesh": "MeshA", "material": "MaterialGlow",
                          "slot": 0, "visible": "$shape_author_Smile > 0",
                          "shapes": {"Smile": "$shape_author_Smile"}},
                "PartB": {"mesh": "MeshB", "material": "MaterialGlow",
                          "slot": 0, "visible": "$shape_author_Smile > 0",
                          "shapes": {"Smile": "$shape_author_Smile"}},
            },
            "animations": {
                "walk": {
                    "id": "walk", "label": "Walk", "duration": 1.0,
                    "speed": 1.0, "speedVariable": "$anim_speed_walk",
                    "sampleRate": 2, "loop": True, "autoplay": False,
                    "key": "F6", "tracks": {
                        "$shape_author_Smile": [0.0, 0.5, 1.0],
                        "$mat_glow": [0.1, 0.4, 0.8],
                    }, "boneTracks": [],
                },
            },
            "material_tracks": {
                "$mat_glow": {"material": "MaterialGlow",
                               "property": "_Glow", "kind": "FLOAT", "component": 0},
            },
        }

    def tearDown(self):
        for rig in self.rigs:
            bpy.data.objects.remove(rig, do_unlink=True)
        for obj in self.meshes.values():
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.materials.remove(self.material, do_unlink=True)
        for child in list(self.package_collection.children):
            bpy.data.collections.remove(child)
        bpy.data.collections.remove(self.package_collection)

    def test_internal_program_and_bindings_survive_nested_mesh_rename_and_reexport(self):
        control_program.configure(TEST_DIR.parents[1] / 'bin/author-assets/eff_author_assets_format3.dll')
        source = '''[Mod]
format=3
id=ProgramRoundtrip
controls=ini
[MeshA]
path=meshes/a.effres
[MaterialGlow]
path=materials/a.effres
[PartStable]
mesh=MeshA
material=MaterialGlow
visible=1
[ParameterValue]
variable=$value
default=0.25
min=0
max=1
public=true
[BindingGlow]
target=material.float
material=MaterialGlow
property=_Glow
value=$value * 2
[BindingShape]
target=shape.weight
part=PartStable
channel=Smile
value=$value
[BindingVisible]
target=part.visible
part=PartStable
value=$value > 0
'''
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'mod.ini').write_text(source, encoding='utf-8')
            ini = configparser.ConfigParser(interpolation=None, delimiters=('=',))
            ini.optionxform = str
            ini.read_string(source)
            state = author_format3.read_controls_ini(root, ini)
            addon = package.eiem_blender_addon
            texts_before = set(bpy.data.texts)
            package.eiem_controls_import.restore_format3_controls(
                state, self.meshes, {}, {}, {'MaterialGlow': self.material},
                self.package_collection, bpy.context.scene,
                addon.unity_transform_matrix_to_blender_basis(), Condition)
            obj = self.meshes['MeshA']
            obj.name = 'Renamed flow mesh'
            lod = bpy.data.collections.new('LOD0')
            self.package_collection.children.link(lod)
            for collection in list(obj.users_collection):
                collection.objects.unlink(obj)
            lod.objects.link(obj)
            bpy.context.view_layer.objects.active = obj
            self.assertEqual(set(bpy.data.texts), texts_before)
            preserved = addon.native_export.preserved_controls_for_objects([obj])
            self.assertIn('default=0.25', preserved['program_source']['ParameterValue'])
            self.assertFalse(any(v.startswith('$binding_') for v in preserved['defaults']))
            part_id = addon.native_export.mesh_part_id(obj.name, 0, 1, obj['eiem_part_section'])
            self.assertEqual(author_format3.part_resource_id(part_id), 'PartStable')
            part = dict(partId=part_id, object=obj.name, material='MaterialGlow',
                shapeControls=addon.native_export.part_shape_controls(
                    addon.native_export.shape_controls(addon, obj, True), 'PartStable',
                    addon.native_export.control_program_bindings(preserved['program_source'])),
                visibilityBindings=[])
            self.assertFalse(part['shapeControls'])
            materials = {'MaterialGlow': dict(id='MaterialGlow', sourcePath='assets/example/a.mat',
                sourceAsset='A', authorData=b'format=EFFMAT\nversion=1\n', textureEdits=[])}
            target = dict(level=0, sourcePath='assets/example/a.mesh', sourceAsset='A')
            author_format3.write_format3_directory(root, 'ProgramRoundtrip', [part], materials,
                [], [dict(hidden=False, parts=[part_id], targets=[target])], [], [], [],
                state_defaults=preserved['defaults'], persistent_states=preserved['persistent'],
                preserved_controls=preserved['controls'],
                control_program_source=preserved['program_source'])
            again = configparser.ConfigParser(interpolation=None, delimiters=('=',))
            again.optionxform = str
            again.read(root / 'mod.ini', encoding='utf-8')
            restored = author_format3.read_controls_ini(root, again)
            self.assertEqual(restored['defaults']['$value'], .25)
            self.assertEqual(restored['parts']['PartStable']['shapes']['Smile'], '$binding_BindingShape')
            self.assertEqual(len(restored['material_tracks']), 1)
            self.assertEqual(len(restored['program_bindings']), 3)
            self.assertFalse(any(s.startswith('Control') and
                again[s].get('variable', '').startswith('$binding_') for s in again.sections()))

    def test_visual_shape_control_generates_ini_without_text_or_manual_variable(self):
        obj = self.meshes['MeshA']
        bpy.context.view_layer.objects.active = obj
        obj.active_shape_key_index = 1
        texts_before = set(bpy.data.texts)
        self.assertEqual(bpy.ops.eiem.shape_control(), {'FINISHED'})
        control = obj.data.eiem_shape_controls[0]
        control.label = 'Smile control'
        control.default = 0.5
        control.hotkey_increase = 'F7'
        control.hotkey_decrease = 'F8'
        self.assertEqual(control.variable, '')
        self.assertTrue(control.identity)
        self.assertNotIn('eiem_controls_ini_json', self.package_collection)
        addon = package.eiem_blender_addon
        self.assertIsNone(addon.native_export.preserved_controls_for_objects([obj]))
        self.assertFalse(hasattr(bpy.types, 'SCENE_PT_eiem_control_program'))
        self.assertFalse(hasattr(bpy.types.Scene, 'eiem_control_program_text'))
        part = dict(partId='MeshA', object=obj.name, material='', visibilityBindings=[],
                    shapeControls=addon.native_export.shape_controls(addon, obj, True))
        target = dict(level=0, sourcePath='assets/example/a.mesh', sourceAsset='A')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            author_format3.write_format3_directory(root, 'VisualControls', [part], {},
                [], [dict(hidden=False, parts=['MeshA'], targets=[target])], [], [], [])
            ini = configparser.ConfigParser(interpolation=None, delimiters=('=',))
            ini.optionxform = str
            ini.read(root / 'mod.ini', encoding='utf-8')
            state = author_format3.read_controls_ini(root, ini)
        variable = state['parts']['PartA']['shapes']['Smile']
        self.assertEqual(state['defaults'][variable], 0.5)
        self.assertEqual(state['controls'][variable]['label'], 'Smile control')
        self.assertEqual({row['key'] for row in state['bindings']}, {'F7', 'F8'})
        self.assertTrue(all(row['variable'] == variable for row in state['bindings']))
        self.assertEqual(set(bpy.data.texts), texts_before)

    def test_visual_edits_update_imported_parameter_without_text(self):
        control_program.configure(TEST_DIR.parents[1] / 'bin/author-assets/eff_author_assets_format3.dll')
        source = '''[Mod]
format=3
id=WidgetRoundtrip
controls=ini
[MeshA]
path=meshes/a.effres
[PartA]
mesh=MeshA
visible=1
shape.Smile=$value
[ParameterValue]
variable=$value
default=0.25
min=0
max=1
ui.min=0
ui.max=1
public=true
[UIValue]
type=slider
variable=$value
'''
        addon = package.eiem_blender_addon
        obj = self.meshes['MeshA']
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'mod.ini').write_text(source, encoding='utf-8')
            ini = configparser.ConfigParser(interpolation=None, delimiters=('=',))
            ini.optionxform = str
            ini.read_string(source)
            state = author_format3.read_controls_ini(root, ini)
            texts_before = set(bpy.data.texts)
            package.eiem_controls_import.restore_format3_controls(
                state, self.meshes, {}, {}, {}, self.package_collection, bpy.context.scene,
                addon.unity_transform_matrix_to_blender_basis(), Condition)
            control = obj.data.eiem_shape_controls[0]
            control.default = .75
            control.maximum = 2
            control.label = 'Edited Smile'
            control.hotkey_increase = 'F9'
            preserved = addon.native_export.preserved_controls_for_objects([obj])
            part = dict(partId='MeshA', object=obj.name, material='', visibilityBindings=[],
                        shapeControls=addon.native_export.shape_controls(addon, obj, True))
            target = dict(level=0, sourcePath='assets/example/a.mesh', sourceAsset='A')
            author_format3.write_format3_directory(root, 'WidgetRoundtrip', [part], {},
                [], [dict(hidden=False, parts=['MeshA'], targets=[target])], [], [], [],
                state_defaults=preserved['defaults'], persistent_states=preserved['persistent'],
                preserved_controls=preserved['controls'],
                control_program_source=preserved['program_source'])
            ini.clear()
            ini.read(root / 'mod.ini', encoding='utf-8')
            restored = author_format3.read_controls_ini(root, ini)
            self.assertEqual(restored['defaults']['$value'], .75)
            self.assertEqual(restored['controls']['$value']['label'], 'Edited Smile')
            self.assertEqual(restored['controls']['$value']['maximum'], 2)
            self.assertEqual(ini['ParameterValue']['ui.max'], '2')
            self.assertEqual(restored['parts']['PartA']['shapes']['Smile'], '$value')
            self.assertEqual(restored['bindings'][0]['key'], 'F9')
            self.assertEqual(set(bpy.data.texts), texts_before)

    def test_controls_restore_shared_shape_material_tracks_and_visibility(self):
        importer = package.eiem_controls_import
        importer.restore_format3_controls(
            self.state, self.meshes, {}, {}, {"MaterialGlow": self.material},
            self.package_collection, bpy.context.scene,
            package.eiem_blender_addon.unity_transform_matrix_to_blender_basis(),
            Condition)

        for obj in self.meshes.values():
            control = obj.data.eiem_shape_controls[0]
            self.assertEqual(control.variable, "$shape_author_Smile")
            self.assertEqual(control.default, 0.25)
            self.assertEqual(control.hotkey_increase, "F7")
            self.assertEqual(control.hotkey_decrease, "F8")
            clip = obj.data.eiem_shape_animations[0]
            self.assertEqual(clip.identity, "walk")
            self.assertIsNotNone(obj.data.shape_keys.animation_data.action)
            self.assertEqual(obj.data.shape_keys.animation_data.action_slot.handle,
                             clip.slot_handle)
            expression = __import__("json").loads(self.package_collection["eiem_controls_ini_json"])[
                "visibility"][obj.name]["0|MaterialGlow"]
            self.assertEqual(expression[0], "greater")

        material_clip = self.material.eiem_material_animations[0]
        self.assertEqual(material_clip.runtime_id, "walk")
        self.assertEqual(material_clip.property, "_Glow")
        self.assertAlmostEqual(material_clip.value, 0.1)
        self.assertIsNotNone(self.material.animation_data.action)
        self.assertEqual(self.material.animation_data.action_slot.handle,
                         next(slot.handle for slot in self.material.animation_data.action.slots
                              if slot.target_id_type == "MATERIAL"))

    def test_edit_and_export_preserves_ini_identities_and_shared_timeline(self):
        addon = package.eiem_blender_addon
        package.eiem_controls_import.restore_format3_controls(
            self.state, self.meshes, {}, {}, {"MaterialGlow": self.material},
            self.package_collection, bpy.context.scene,
            addon.unity_transform_matrix_to_blender_basis(), Condition)
        parts = []
        for name, obj in self.meshes.items():
            obj.data.eiem_shape_controls[0].default = 0.5
            obj.data.eiem_shape_controls[0].hotkey_increase = "F9"
            obj.data.eiem_shape_animations[0].speed = 2.0
            identity = obj.get("eiem_controls_mesh_id", name)
            parts.append(dict(partId=name, object=obj.name,
                shapeControls=addon.native_export.shape_controls(addon, obj, True),
                shapeAnimations=addon.shape_animation.export_clips(obj, addon),
                visibilityBindings=[], visibilityExpression=preserved_visibility(
                    self.package_collection, identity)))
        self.material.eiem_material_animations[0].speed = 2.0
        material_clips = package.eiem_material_animation.export_clips(
            self.material, "MaterialGlow", addon)
        preserved = addon.native_export.preserved_controls_for_objects(list(self.meshes.values()))
        state = author_format3._controls_resource(
            "roundtrip", parts, [], material_clips, [], return_state=True,
            state_defaults=preserved["defaults"], persistent_states=preserved["persistent"],
            preserved_controls=preserved["controls"], preserved_bindings=preserved["bindings"])
        self.assertEqual(state["defaults"]["$shape_author_Smile"], 0.5)
        self.assertEqual(state["defaults"]["$anim_speed_walk"], 2.0)
        self.assertEqual(set(state["animations"]), {"walk"})
        self.assertEqual(set(state["animations"]["walk"]["tracks"]),
                         {"$shape_author_Smile", "$mat_glow"})
        self.assertFalse(any(author_format3._chord_text(row[3], row[4]) == "F7"
                             for row in state["bindings"]))
        self.assertEqual(sum(row[1] == "$shape_author_Smile" and
                             author_format3._chord_text(row[3], row[4]) == "F9"
                             for row in state["bindings"]), 1)
        with tempfile.TemporaryDirectory() as temporary:
            ini = configparser.ConfigParser(interpolation=None)
            ini.optionxform = str
            for name, part in state["parts"].items():
                self.assertEqual(list(part["visible"]), preserved_visibility(
                    self.package_collection, "Mesh" + name.removeprefix("Part")))
                ini[name] = {"visible": self.state["parts"][name]["visible"],
                             **{"shape." + channel: variable
                                for channel, variable in part["shapes"].items()}}
            author_format3._write_controls_ini(temporary, ini, state, {})
            ini["MaterialGlow"] = {"path": "materials/MaterialGlow.effres"}
            restored = author_format3.read_controls_ini(temporary, ini)
        self.assertEqual(restored["animations"]["walk"]["speedVariable"], "$anim_speed_walk")
        self.assertTrue(Condition(restored["parts"]["PartA"]["visible"]).evaluate(
            {"$shape_author_Smile": 0.5}))
        for variable, values in self.state["animations"]["walk"]["tracks"].items():
            for a, b in zip(values, restored["animations"]["walk"]["tracks"][variable]):
                self.assertAlmostEqual(a, b, places=5)

    def test_bone_tracks_roundtrip_parent_and_child_transforms(self):
        addon = package.eiem_blender_addon
        payload = {"coordinate": "Unity", "nodes": [
            ("Root", -1, (0.0, 0.5, 0.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0)),
            ("Root/Tip", 0, (0.0, 1.0, 0.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0)),
        ]}
        rig = addon.make_armature("SkeletonTest", payload, self.package_collection)
        self.rigs.append(rig)
        clip = self.state["animations"]["walk"]
        for path in ("Root", "Root/Tip"):
            samples = []
            for i in range(3):
                angle = i * math.pi / 12
                samples.append([0.1 * i, (0.5 if path == "Root" else 1.0), 0,
                                0, 0, math.sin(angle), math.cos(angle), 1, 1, 1])
            clip["boneTracks"].append(dict(skeleton="Test", path=path, samples=samples))
        other = copy.deepcopy(clip)
        other.update(id="idle", label="Idle", tracks={}, speedVariable="$idle_speed", key="")
        for track in other["boneTracks"]:
            for sample in track["samples"]:
                sample[0] += 2.0
        self.state["animations"]["idle"] = other
        self.state["defaults"]["$idle_speed"] = 1.0
        self.state["persistent"].add("$idle_speed")
        package.eiem_controls_import.restore_format3_controls(
            self.state, self.meshes, {"SkeletonTest": rig}, {"SkeletonTest": payload},
            {"MaterialGlow": self.material}, self.package_collection, bpy.context.scene,
            addon.unity_transform_matrix_to_blender_basis(), Condition)
        action = rig.animation_data.action
        slot = rig.animation_data.action_slot
        for exported in addon.shape_animation.export_bone_clips(rig, addon, "Test"):
            decoded = addon.bone_animation.decode_animation(exported["data"])
            source = self.state["animations"][exported["id"]]
            self.assertEqual(exported["speedVariable"], source["speedVariable"])
            for original, restored in zip(source["boneTracks"], decoded["tracks"]):
                self.assertEqual(original["path"], restored["path"])
                for a, b in zip(original["samples"], restored["samples"]):
                    for x, y in zip(a, b):
                        self.assertAlmostEqual(x, y, places=4)
        self.assertEqual(rig.animation_data.action, action)
        self.assertEqual(rig.animation_data.action_slot, slot)

    def test_custom_bindings_with_distinct_speeds_are_not_collapsed_into_widget(self):
        self.state["bindings"][0]["speed"] = 0.25
        addon = package.eiem_blender_addon
        package.eiem_controls_import.restore_format3_controls(
            self.state, self.meshes, {}, {}, {"MaterialGlow": self.material},
            self.package_collection, bpy.context.scene,
            addon.unity_transform_matrix_to_blender_basis(), Condition)
        preserved = addon.native_export.preserved_controls_for_objects(list(self.meshes.values()))
        self.assertEqual(preserved["bindings"], self.state["bindings"])
        for obj in self.meshes.values():
            self.assertEqual(obj.data.eiem_shape_controls[0].hotkey_increase, "")
            self.assertEqual(obj.data.eiem_shape_controls[0].hotkey_decrease, "")


def preserved_visibility(collection, mesh):
    return json.loads(collection["eiem_controls_ini_json"])["visibility"][mesh]["0|MaterialGlow"]


if __name__ == "__main__":
    unittest.main(argv=[__file__], verbosity=2)

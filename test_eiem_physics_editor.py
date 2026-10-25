"""Real source data, parameter projections and transactional multi-group edits."""
import copy
import importlib.util
import json
import struct
import sys
from pathlib import Path

import bpy
from mathutils import Vector

addon_path, evidence, output = map(Path, sys.argv[sys.argv.index("--") + 1:])
package_name = "eiem_editor_test_package"
package_spec = importlib.util.spec_from_file_location(package_name, addon_path.with_name("__init__.py"),
    submodule_search_locations=[str(addon_path.resolve().parent)])
package = importlib.util.module_from_spec(package_spec)
sys.modules[package_name] = package
package_spec.loader.exec_module(package)
addon = sys.modules[package_name + ".eiem_blender_addon"]
addon.register()
author = addon.physics_authoring
native, editor = author.native, author.editor
rig, objects = native.import_source(evidence)
groups = [obj for obj in objects if obj.eiem_physics.kind == "NATIVE_GROUP"]
colliders = [obj for obj in objects if obj.eiem_physics.kind == "NATIVE_COLLIDER"]
first, second = groups[:2]
assert hasattr(bpy.types, "EFF_PT_physics_editor")
assert not hasattr(bpy.types, "EFF_PT_physics_object")
assert not hasattr(bpy.types, "EFF_PT_physics")
builtin_names = {preset.name for preset in bpy.context.scene.eiem_physics_editor_presets
                 if preset.source == "BUILTIN"}
assert "Chen 软飘带" in builtin_names
builtin = next(preset for preset in bpy.context.scene.eiem_physics_editor_presets
               if preset.name == "Chen 软飘带")
builtin_payload = json.loads(builtin.payload)
assert builtin_payload["scope"] == "PRESET"
assert not any("colliderCollisionConstraint" in item["path"] or
               "selfCollisionConstraint" in item["path"] for item in builtin_payload["fields"])

# Reading every available typed control does not rewrite serialized values.
before = native.current_record(first)
for tab, section, fields in editor.SECTIONS:
    for path, label, kind in fields:
        paths = [path + ".value", path + (".useCurve" if kind == "CURVE" else ".use")] if kind in {"CURVE", "LIMIT"} else [path]
        for scalar in paths:
            available = all(editor.has_parameter(first, path + "." + axis) for axis in "xyz") if kind == "VECTOR" else editor.has_parameter(first, scalar)
            if available:
                getattr(first.eiem_physics_editor_values, editor.property_name(scalar))
assert native.current_record(first) == before

# Ordinary forces do not rebuild the generated geometry. Boolean/enum controls
# write the original numeric field rather than saving an independent RNA value.
visuals = [(child.as_pointer(), child.data.as_pointer()) for child in first.children]
proxy = first.eiem_physics_editor_values
gravity = editor.read_number(first, "gravity")
proxy.p_gravity = gravity + .25
assert abs(editor.read_number(first, "gravity") - gravity - .25) < 1e-6
assert visuals == [(child.as_pointer(), child.data.as_pointer()) for child in first.children]
proxy.p_gravity = gravity
proxy.p_angleRestorationConstraint_useAngleRestoration = False
assert editor.read_number(first, "angleRestorationConstraint.useAngleRestoration") == 0
proxy.p_angleRestorationConstraint_useAngleRestoration = bool(before["fields"]["serializeData"]["angleRestorationConstraint"]["useAngleRestoration"])
proxy.p_colliderCollisionConstraint_mode = "2"
assert editor.read_number(first, "colliderCollisionConstraint.mode") == 2
proxy.p_colliderCollisionConstraint_mode = "1"
direction = Vector(proxy.p_gravityDirection)
proxy.p_gravityDirection = direction
assert (Vector(proxy.p_gravityDirection) - direction).length < 1e-6

# Clipboard parameters cannot include bone/reference/prebuild fields. Collision
# lists and game enable state survive both full and section-only operations.
payload = editor.capture_parameters(first)
assert set(payload) == {"scope", "fields", "curves"}
assert len(payload["curves"]) == 9
assert not any("m_PathID" in item["path"] or "serializeData2" in item["path"] for item in payload["fields"])
copied = {item["path"] for item in payload["fields"]}
for path in ("serializeData.clothType", "serializeData.normalAxis",
             "serializeData.normalAlignmentSetting.alignmentMode"):
    assert path in copied, "Full parameter copy omitted " + path
refs = [[ref.object for ref in group.eiem_physics.colliders] for group in (first, second)]
enabled = [group.eiem_native_physics.disabled for group in (first, second)]
original_second = editor.capture_parameters(second)
editor.paste_to_targets([first, second], payload)
assert editor.capture_parameters(first) == editor.capture_parameters(second)
assert refs == [[ref.object for ref in group.eiem_physics.colliders] for group in (first, second)]
assert enabled == [group.eiem_native_physics.disabled for group in (first, second)]

# A curve with a different key count replaces the array whole; there must be no
# stale target keys. Nonlinear Unity tangent/weight metadata is retained.
curve_path = "serializeData.damping"
varied = copy.deepcopy(payload)
keys = varied["curves"][curve_path]
middle = copy.deepcopy(keys[0])
middle.update(time=.4, value=.37, inSlope=.23, outSlope=.46, weightedMode=3, inWeight=.2, outWeight=.4)
keys.insert(1, middle)
editor.paste_to_targets([second], varied)
actual_keys = native.current_record(second)["fields"]["serializeData"]["damping"]["curve"]["m_Curve"]
assert len(actual_keys) == len(keys)
assert abs(actual_keys[1]["value"] - .37) < 1e-6 and actual_keys[1]["weightedMode"] == 3
editor.paste_to_targets([second], original_second)

# Checked groups receive only the changed scalar, not an entire parameter copy.
first.eiem_physics.editor_selected = second.eiem_physics.editor_selected = True
untouched = editor.read_number(second, "wind.influence")
proxy.p_gravity = 2.75
assert all(abs(editor.read_number(group, "gravity") - 2.75) < 1e-6 for group in (first, second))
assert editor.read_number(second, "wind.influence") == untouched
first.eiem_physics.editor_selected = second.eiem_physics.editor_selected = False
editor.paste_to_targets([first], payload)
editor.paste_to_targets([second], original_second)

# A missing target field aborts all targets before mutation.
bad = copy.deepcopy(payload)
bad["fields"].append({"path": "serializeData.wind.influence.missing", "floating": True, "value": 1.0})
prior = [editor.capture_parameters(group) for group in (first, second)]
try:
    editor.paste_to_targets([first, second], bad)
except ValueError:
    pass
else:
    raise AssertionError("invalid multi-target paste accepted")
assert prior == [editor.capture_parameters(group) for group in (first, second)]

# Roll back raw metadata and compact adapters if an unexpected apply fails.
backups = [editor.backup_parameters(group) for group in (first, second)]
original_apply = editor.apply_parameters
def fail_second(obj, payload):
    if obj == second:
        raise RuntimeError("injected apply failure")
    original_apply(obj, payload)
editor.apply_parameters = fail_second
try:
    editor.paste_to_targets([first, second], varied)
except RuntimeError:
    pass
else:
    raise AssertionError("injected failure did not abort")
finally:
    editor.apply_parameters = original_apply
assert backups == [editor.backup_parameters(group) for group in (first, second)]

# A source center is one value in Blender coordinates; fold the legacy offset.
collider = colliders[0]
collider.location = (.01, .02, .03)
expected_center = Vector(collider.eiem_physics_editor_values.collider_center) + Vector((.02, -.01, 0))
collider.eiem_physics_editor_values.collider_center = expected_center
assert collider.location.length < 1e-8
assert (Vector(collider.eiem_physics_editor_values.collider_center) - expected_center).length < 1e-6
native.export_source(output / "center.physics", [collider])
decoded = native.source.read(output / "center.physics")["components"][0]["fields"]["center"]
assert (addon.unity_transform_matrix_to_blender_basis() @ Vector([decoded[axis] for axis in "xyz"]) - expected_center).length < 1e-6

# Source data is authoritative for reset, including an entire edited curve.
context = bpy.context
context.scene.eiem_physics_editor_item = first
context.scene.eiem_physics_editor_preset_name = "源参数测试"
assert bpy.ops.eiem.physics_editor_action(action="SAVE_PRESET", target=first.name) == {"FINISHED"}
assert len(context.scene.eiem_physics_editor_presets) == len(builtin_names) + 1
preset = json.loads(context.scene.eiem_physics_editor_presets[0].payload)
assert set(preset) == {"scope", "fields", "curves", "chainType"}
assert preset["scope"] == "PRESET"
saved_gravity = editor.read_number(first, "gravity")
first.eiem_physics_editor_values.p_gravity = 8.5
assert bpy.ops.eiem.physics_editor_action(action="APPLY_PRESET", target=first.name) == {"FINISHED"}
assert abs(editor.read_number(first, "gravity") - saved_gravity) < 1e-6

# Portable presets round-trip through JSON, omit collision parameters, enforce
# simulation type, and leave each destination chain's collision set untouched.
preset_path = output / "chen-ribbon-test.json"
output.mkdir(parents=True, exist_ok=True)
document = editor.make_preset_document(first, "文件飘带预设")
assert editor.validate_preset_document(document)[0] == "文件飘带预设"
assert bpy.ops.eiem.physics_preset_export(filepath=str(preset_path), target=first.name) == {"FINISHED"}
disk_document = json.loads(preset_path.read_text(encoding="utf-8"))
assert disk_document["payload"] == document["payload"]
collision_before = editor.capture_parameters(second, "COLLISION")
colliders_before = [ref.object for ref in second.eiem_physics.colliders]
assert bpy.ops.eiem.physics_preset_import(filepath=str(preset_path)) == {"FINISHED"}
assert bpy.ops.eiem.physics_editor_action(action="APPLY_PRESET", target=second.name) == {"FINISHED"}
assert editor.capture_parameters(second, "PRESET") == document["payload"]
assert editor.capture_parameters(second, "COLLISION") == collision_before
assert [ref.object for ref in second.eiem_physics.colliders] == colliders_before
wrong_type = copy.deepcopy(document["payload"])
wrong_type["chainType"] += 10
prior = editor.backup_parameters(second)
try:
    editor.paste_to_targets([second], wrong_type)
except ValueError:
    pass
else:
    raise AssertionError("physics preset with a different simulation type was accepted")
assert editor.backup_parameters(second) == prior

assert bpy.ops.eiem.physics_editor_action(action="RESET", target=first.name, path="serializeData.damping") == {"FINISHED"}
restored = native.current_record(first)["fields"]["serializeData"]["damping"]
def float32_values(value):
    if isinstance(value, dict):
        return {key: float32_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [float32_values(item) for item in value]
    return struct.unpack("<f", struct.pack("<f", value))[0] if isinstance(value, float) else value
# The source JSON decimal and RNA's float32 must encode the same game value.
assert float32_values(restored) == float32_values(native.record(first)["fields"]["serializeData"]["damping"])

# A compact old author group can receive the source template and export all
# edited controls through the existing interchange adapter.
bones = list(rig.data.bones)
child = next(bone for bone in bones if bone.parent)
new = author.create_group(rig, [child.parent, child], "编辑器模板测试")
editor.paste_to_targets([new], payload)
assert native.is_parameter_group(new)
assert editor.read_number(new, "clothType") == editor.read_number(first, "clothType")
assert editor.read_number(new, "normalAxis") == editor.read_number(first, "normalAxis")

# The actual sidebar copy operator copies only parameters and curves. Even an
# earlier clipboard containing colliderRefs cannot change the target's set.
assert bpy.ops.eiem.physics_editor_action(action="COPY", target=first.name) == {"FINISHED"}
parameter_copy = json.loads(context.scene.eiem_physics_editor_clipboard)
assert set(parameter_copy) == {"scope", "fields", "curves"}
target_collider = next(item for item in colliders if item not in [ref.object for ref in first.eiem_physics.colliders])
new.eiem_physics.colliders.add().object = target_collider
own_colliders = [ref.object for ref in new.eiem_physics.colliders]
editor.paste_to_targets([new], parameter_copy)
assert [ref.object for ref in new.eiem_physics.colliders] == own_colliders
assert editor.capture_parameters(new) == editor.capture_parameters(first)
legacy = copy.deepcopy(parameter_copy)
legacy["colliderRefs"] = [{"kind": "NATIVE", "key": "missing-collider"}]
editor.paste_to_targets([new], legacy)
assert [ref.object for ref in new.eiem_physics.colliders] == own_colliders
prior = editor.backup_parameters(new)
unsupported = copy.deepcopy(parameter_copy)
next(item for item in unsupported["fields"] if item["path"] == "serializeData.clothType")["value"] = 10
try:
    editor.paste_to_targets([new], unsupported)
except ValueError:
    pass
else:
    raise AssertionError("BoneSpring silently converted to BoneCloth")
assert editor.backup_parameters(new) == prior
assert [ref.object for ref in new.eiem_physics.colliders] == own_colliders

# Deleting a new chain removes its owned previews/curve UI, while shared
# colliders and every skeleton bone remain intact. Native chains use disable.
disposable = author.create_group(rig, [child.parent, child], "删除测试")
editor.paste_to_targets([disposable], parameter_copy)
disposable.eiem_physics.colliders.add().object = target_collider
group_name = disposable.name
preview_names = [item.name for item in disposable.children if item.get("eiem_physics_visual")]
tree_name = disposable.eiem_native_physics.curve_mapping_tree.name
bone_names = [bone.name for bone in rig.data.bones]
shared_names = [ref.object.name for ref in disposable.eiem_physics.colliders]
assert preview_names
assert bpy.ops.eiem.physics_editor_action(action="DELETE_CHAIN", target=group_name) == {"FINISHED"}
assert group_name not in bpy.data.objects
assert all(name not in bpy.data.objects for name in preview_names)
assert tree_name not in bpy.data.node_groups
assert bone_names == [bone.name for bone in rig.data.bones]
assert all(name in bpy.data.objects for name in shared_names)
try:
    editor.delete_author_chain(first)
except ValueError:
    pass
else:
    raise AssertionError("source chain deleted without an exported disable operation")
context.scene.eiem_physics_group = new
new.eiem_physics_editor_values.p_gravity = 3.25
assert abs(new.eiem_physics.gravity - 3.25) < 1e-6
new.eiem_physics_editor_values.p_wind_influence = .6
record = author.author_document([new], "skeletons/test.skeleton")["groups"][0]
assert any(item["path"] == "serializeData.wind.influence" and abs(item["value"] - .6) < 1e-6 for item in record["nativeParameters"])
assert record["colliders"] == sorted(item.eiem_physics.identity for item in own_colliders)
assert any(item["path"] == "serializeData.normalAxis" for item in record["nativeParameters"])

# Saved controls are only projections: after a .blend reload their values still
# come from fields, while clipboard targets do not affect Mesh selections.
output.mkdir(parents=True, exist_ok=True)
filepath = output / "editor.blend"
source_name, collider_name = first.name, collider.name
preset_rows = {(item.name, item.source, item.payload)
               for item in bpy.context.scene.eiem_physics_editor_presets}
bpy.ops.wm.save_as_mainfile(filepath=str(filepath))
new_name = new.name
bpy.ops.wm.open_mainfile(filepath=str(filepath))
new = bpy.data.objects[new_name]
assert abs(new.eiem_physics_editor_values.p_gravity - 3.25) < 1e-6
assert {(item.name, item.source, item.payload) for item in bpy.context.scene.eiem_physics_editor_presets} == preset_rows

# Display filters never change serialized game enable or parameter values.
pinned = bpy.data.objects[collider_name]
serialized = native.current_record(pinned)
pinned.eiem_physics.editor_pinned = True
bpy.context.scene.eiem_physics_visibility = "GROUPS"
assert not pinned.hide_get()
pinned.eiem_physics.editor_hidden = True
assert pinned.hide_get()
pinned.eiem_physics.editor_hidden = False
assert not pinned.hide_get()
assert native.current_record(pinned) == serialized

# Draw all sidebar branches with a validating layout in background Blender.
from types import SimpleNamespace
class Layout:
    def __getattr__(self, name):
        def call(*args, **kwargs):
            if name == "prop":
                assert hasattr(args[0], args[1]), args[1]
            elif name == "operator":
                category, operator = args[0].split(".")
                assert getattr(getattr(bpy.ops, category), operator).get_rna_type()
                return SimpleNamespace()
            elif name == "panel":
                return self, self
            return self
        return call
    def __setattr__(self, name, value):
        pass
panel = SimpleNamespace(layout=Layout())
for item in (new, bpy.data.objects[source_name], bpy.data.objects[collider_name]):
    bpy.context.scene.eiem_physics_editor_item = item
    if item.eiem_physics.kind in editor.GROUPS:
        bpy.context.scene.eiem_physics_group = item
    for tab in ("STRUCTURE", "DYNAMICS", "COLLISION", "ADVANCED"):
        bpy.context.scene.eiem_physics_editor_tab = tab
        editor.EFF_PT_physics_editor.draw(panel, bpy.context)
bpy.context.scene.eiem_physics_editor_search = "wind.influence"
assert bpy.context.scene.eiem_physics_editor_tab == "DYNAMICS"
addon.unregister()
assert not hasattr(bpy.types, "EFF_PT_physics_editor")
print("EIEM_PHYSICS_EDITOR_OK", flush=True)

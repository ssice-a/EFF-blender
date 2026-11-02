"""Exercise Renderer and bone attachment export against synthetic source proof."""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

import bpy


addon_dir = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "eiem_renderer_mount_test", addon_dir / "__init__.py",
    submodule_search_locations=[str(addon_dir)])
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()

editor = addon.eiem_blender_addon.effect_editor
with tempfile.TemporaryDirectory(prefix="eiem-renderer-mount-") as temporary:
    package = Path(temporary)
    (package / "source").mkdir()
    mesh_path = "assets/weapons/weapon.mesh"
    mesh_asset = "Weapon"
    renderer_path = ""
    owner_mesh = bpy.data.meshes.new("Weapon")
    owner = bpy.data.objects.new("Weapon", owner_mesh)
    bpy.context.scene.collection.objects.link(owner)
    owner.data["eiem_target_path"] = mesh_path
    owner.data["eiem_target_asset"] = mesh_asset
    owner["eiem_author_package"] = str(package)
    owner["eiem_source_owner_path"] = renderer_path

    root = bpy.data.objects.new("WeaponSpark", None)
    bpy.context.scene.collection.objects.link(root)
    root["eff_effect_id"] = "weapon_spark"
    root.eff_effect.owner = owner

    proof = dict(prefab="assets/weapons/weapon.prefab",
                 resources=[dict(type="Mesh", logicalPath=mesh_path,
                                 name=mesh_asset, identity="mesh-id")],
                 rendererUses=[dict(mesh=dict(identity="mesh-id"),
                                    ownerPath=renderer_path, bones=[])],
                 hierarchy=[dict(transform="renderer-id", path=renderer_path)])
    manifest = package / "source" / "manifest.json"
    manifest.write_text(json.dumps(proof), encoding="utf-8")

    static = editor.attachment(root)
    assert static["bone"] == ""
    assert static["bones"] == []
    assert static["targetPrefab"] == proof["prefab"]
    assert static["rendererPath"] == renderer_path

    proof["resources"] = []
    manifest.write_text(json.dumps(proof), encoding="utf-8")
    try:
        editor.attachment(root)
    except ValueError as error:
        assert "Renderer" in str(error)
    else:
        raise AssertionError("static attachment accepted missing Renderer source proof")
    proof["resources"] = [dict(type="Mesh", logicalPath=mesh_path,
                                name=mesh_asset, identity="mesh-id")]
    manifest.write_text(json.dumps(proof), encoding="utf-8")

    owner.location = (2.0, 3.0, 4.0)
    root.location = (1.0, 0.0, 0.0)
    bpy.context.view_layer.update()
    expected_mount = owner.matrix_world @ root.matrix_local
    actual_mount = editor.preview.mount_matrix(root)
    assert max(abs(expected_mount[row][column] - actual_mount[row][column])
               for row in range(4) for column in range(4)) < 1e-6

    armature_data = bpy.data.armatures.new("WeaponRig")
    rig = bpy.data.objects.new("WeaponRig", armature_data)
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bone = armature_data.edit_bones.new("Hand")
    bone.tail.y = 0.1
    bpy.ops.object.mode_set(mode="OBJECT")
    rig.select_set(False)
    armature_data.bones["Hand"]["eiem_path"] = "Root/Hand"
    owner.modifiers.new("WeaponRig", "ARMATURE").object = rig
    root.eff_effect.bone = "Hand"

    proof["hierarchy"] = [dict(transform="root-id", path="Root"),
                          dict(transform="hand-id", path="Root/Hand")]
    proof["rendererUses"] = [dict(mesh=dict(identity="mesh-id"),
                                  ownerPath=renderer_path,
                                  skinningRoot=dict(identity="root-id"),
                                  bones=[dict(identity="hand-id")])]
    manifest.write_text(json.dumps(proof), encoding="utf-8")

    skinned = editor.attachment(root)
    assert skinned["bone"] == "Root/Hand"
    assert skinned["bones"] == ["Root/Hand"]

    proof["rendererUses"][0]["bones"] = []
    manifest.write_text(json.dumps(proof), encoding="utf-8")
    try:
        editor.attachment(root)
    except ValueError as error:
        assert "完整骨架调色板" in str(error)
    else:
        raise AssertionError("bone attachment accepted a missing source palette")

print("PASS static Renderer mount, exact source proof, bone mount, and missing-palette rejection")
addon.unregister()

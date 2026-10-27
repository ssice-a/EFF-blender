"""Check real Blender discovery, registration and development reload.

Run: blender --background --factory-startup --python-exit-code 1
             --python THIS_FILE -- ADDON_DIRECTORY
Does not import models, change scene data or save preferences.
"""

import sys
from pathlib import Path

import addon_utils
import bpy


args = sys.argv[sys.argv.index("--") + 1:]
if len(args) != 1:
    raise SystemExit("Expected an add-on package directory")
directory = Path(args[0]).resolve()
module_name = directory.name
entry = directory / "__init__.py"

# This is the same metadata reader used by Blender's add-on discovery.
metadata = addon_utils._fake_module(module_name, str(entry))
assert metadata is not None, "Package entry is not discoverable: missing literal bl_info"
sys.path.insert(0, str(directory.parent))


def callbacks(menu):
    return [callback for callback in getattr(menu.draw, "_draw_funcs", ())
            if callback.__module__ == module_name
            or callback.__module__.startswith(module_name + ".")]


scene_objects = tuple(bpy.data.objects)
previous_module = None
for cycle in range(3):
    module = addon_utils.enable(module_name, default_set=False)
    assert module is not None, "Add-on registration failed"
    assert module is not previous_module, "Reload kept a stale package module"
    assert Path(module.eiem_blender_addon.__file__).resolve() == directory / "eiem_blender_addon.py"
    assert Path(module.eiem_blender_addon.controls.__file__).resolve() == directory / "eiem_blender_controls.py"
    assert Path(module.eiem_blender_addon.format_io.__file__).resolve() == directory / "eiem_format.py"
    assert Path(module.eiem_blender_addon.lod.__file__).resolve() == directory / "eiem_lod.py"
    assert metadata.bl_info == module.bl_info == module.eiem_blender_addon.bl_info
    assert bpy.ops.eiem.import_package.get_rna_type() is not None
    assert bpy.ops.eiem.export_package.get_rna_type() is not None
    assert bpy.ops.eiem.export_package.get_rna_type().properties['package_directory'].is_skip_save
    assert bpy.ops.eiem.export_update.get_rna_type() is not None
    assert hasattr(bpy.types.Scene,'eiem_export_last_directory')
    resource_scope = bpy.ops.eiem.export_package.get_rna_type().properties['resource_scope']
    assert resource_scope.default == 'ALL'
    assert [item.identifier for item in resource_scope.enum_items] == ['ALL', 'MESH', 'MATERIALS', 'TEXTURES', 'PHYSICS']
    assert bpy.ops.eiem.export_mesh_only.get_rna_type() is not None
    assert bpy.ops.eiem.detect_game_directory.get_rna_type() is not None
    assert bpy.ops.eiem.choose_index_workspace.get_rna_type() is not None
    assert bpy.ops.eiem.choose_resource_root.get_rna_type() is not None
    assert bpy.ops.eiem.prepare_shared_tools.get_rna_type() is not None
    assert bpy.ops.eiem.install_update.get_rna_type() is not None
    assert 'update_channel' in module.eiem_blender_addon.EFF_AddonPreferences.bl_rna.properties
    assert 'game_directory' in module.eiem_blender_addon.EFF_AddonPreferences.bl_rna.properties
    assert bpy.ops.eiem.import_material.get_rna_type() is not None
    assert bpy.ops.eiem.import_animation.get_rna_type() is not None
    assert bpy.ops.eiem.import_physics.get_rna_type() is not None
    assert bpy.ops.eiem.export_physics.get_rna_type() is not None
    assert bpy.ops.eiem.physics_parameters.get_rna_type() is not None
    assert bpy.ops.eiem.physics_curve_key.get_rna_type() is not None
    assert bpy.ops.eiem.switch_create.get_rna_type() is not None
    assert bpy.ops.eiem.switch_key_record.get_rna_type() is not None
    assert bpy.ops.eiem.switch_create_confirm.get_rna_type() is not None
    assert bpy.ops.eiem.mesh_restore_source.get_rna_type() is not None
    assert bpy.ops.eiem.switch_state_drag.get_rna_type() is not None
    assert bpy.ops.eiem.switch_member_add.get_rna_type() is not None
    assert bpy.ops.eiem.switch_member_remove.get_rna_type() is not None
    assert bpy.ops.eiem.switch_member_remove_selected.get_rna_type() is not None
    assert bpy.ops.eiem.shape_key_record.get_rna_type() is not None
    assert hasattr(bpy.types, "EFF_UL_switch_states")
    assert hasattr(bpy.types, "VIEW3D_PT_eiem_updates")
    assert hasattr(bpy.types, "VIEW3D_PT_eiem_index_workspace")
    assert hasattr(bpy.types, "VIEW3D_PT_eiem_shared_tools")
    assert hasattr(bpy.types, "EFF_PT_physics_editor")
    assert hasattr(bpy.types, "EFF_PT_effect_object")
    assert hasattr(bpy.types, "MATERIAL_PT_eiem_properties")
    assert not hasattr(bpy.types, "EFF_PT_effect_material")
    assert bpy.ops.eiem.effect_import.get_rna_type() is not None
    assert bpy.ops.eiem.effect_export.get_rna_type() is not None
    assert bpy.ops.eiem.effect_build.get_rna_type() is not None
    assert bpy.ops.eiem.effect_curve_edit.get_rna_type() is not None
    assert bpy.ops.eiem.effect_key.get_rna_type() is not None
    assert 'toggle_keys' in module.eiem_blender_addon.effect_editor.EFF_PG_effect_object.bl_rna.properties
    assert hasattr(bpy.types.Object, "eff_effect")
    assert not hasattr(bpy.types.Scene, "eiem_effect_game_pid")
    assert not hasattr(bpy.types, "EFF_PT_physics_object")
    assert hasattr(bpy.types, "EFF_MT_physics_create")
    assert not hasattr(bpy.types, "EFF_MT_physics_copy")
    assert hasattr(bpy.types.Object, "eiem_physics")
    assert hasattr(bpy.types.Object, "eiem_native_physics")
    assert hasattr(bpy.types.Scene, "eiem_physics_visibility")
    assert hasattr(bpy.types.Scene, "eiem_physics_preview_style")
    assert hasattr(bpy.types.Scene, "eiem_physics_xray")
    handlers = [handler for handler in bpy.app.handlers.depsgraph_update_post
                if handler.__module__ == module_name + ".eiem_physics_authoring"]
    assert len(handlers) == 1
    export_tracker = module.eiem_blender_addon._eiem_export_dirty_depsgraph_update
    load_reset = module.eiem_blender_addon._eiem_export_load_post
    assert bpy.app.handlers.depsgraph_update_post.count(export_tracker) == 1
    assert bpy.app.handlers.load_post.count(load_reset) == 1
    assert hasattr(export_tracker, '_bpy_persistent')
    assert hasattr(load_reset, '_bpy_persistent')
    native_handlers = [handler for handler in bpy.app.handlers.depsgraph_update_post
                       if handler.__module__ == module_name + ".eiem_physics_native"]
    assert not native_handlers
    probe = bpy.data.objects.new("Physics Current Group Probe", None)
    bpy.context.scene.collection.objects.link(probe)
    probe.eiem_physics.kind = "GROUP"
    probe.select_set(True)
    bpy.context.view_layer.objects.active = probe
    # Background Blender does not dispatch the UI notifier queue. Exercise the
    # real message-bus callback directly; repeated register/unregister cycles
    # above still validate subscription cleanup.
    module.eiem_blender_addon.physics_authoring.active_object_updated()
    assert bpy.context.scene.eiem_physics_group == probe
    bpy.data.objects.remove(probe, do_unlink=True)
    assert hasattr(bpy.types.Scene, "eiem_switch_active")
    assert hasattr(bpy.types.Scene, "eiem_source_baseline")
    assert hasattr(bpy.types.Collection, "eiem_switch_state_index")
    assert len(callbacks(bpy.types.TOPBAR_MT_file_import)) == 2
    assert len(callbacks(bpy.types.TOPBAR_MT_file_export)) == 1
    assert tuple(bpy.data.objects) == scene_objects

    addon_utils.disable(module_name, default_set=False)
    assert export_tracker not in bpy.app.handlers.depsgraph_update_post
    assert load_reset not in bpy.app.handlers.load_post
    assert not callbacks(bpy.types.TOPBAR_MT_file_import)
    assert not callbacks(bpy.types.TOPBAR_MT_file_export)
    assert not hasattr(bpy.types.Scene, "eiem_switch_active")
    assert not hasattr(bpy.types.Object, "eff_effect")
    assert not hasattr(bpy.types, "EFF_PG_effect_key")
    assert not hasattr(bpy.types, "EFF_PT_effect_object")
    assert not hasattr(bpy.types.Scene, "eiem_source_baseline")
    assert not hasattr(bpy.types.Collection, "eiem_switch_state_index")
    assert not hasattr(bpy.types.Object, "eiem_physics")
    assert not hasattr(bpy.types.Scene, "eiem_physics_group")
    assert not hasattr(bpy.types.Scene, "eiem_physics_visibility")
    assert not hasattr(bpy.types.Scene, "eiem_physics_preview_style")
    assert not hasattr(bpy.types.Scene, "eiem_physics_xray")
    assert not hasattr(bpy.types, "OBJECT_PT_eiem_physics")
    assert not hasattr(bpy.types, "EFF_MT_physics_create")
    assert not hasattr(bpy.types, "EFF_MT_physics_copy")
    assert not [handler for handler in bpy.app.handlers.depsgraph_update_post
                if handler.__module__ == module_name + ".eiem_physics_authoring"]
    assert not [handler for handler in bpy.app.handlers.depsgraph_update_post
                if handler.__module__ == module_name + ".eiem_physics_native"]
    assert tuple(bpy.data.objects) == scene_objects

    # Blender Development disables, purges the package, then enables it again.
    previous_module = module
    for name in list(sys.modules):
        if name == module_name or name.startswith(module_name + "."):
            del sys.modules[name]

print("EFF_REGISTRATION_OK: discovery; import/export; 3 reload cycles; scene unchanged")

"""Quick-update destinations must never override a later normal export dialog.

Run with --background --factory-startup --python-exit-code 1 --python THIS_FILE
     -- ADDON_DIRECTORY OUTPUT_DIRECTORY
"""
import importlib
import json
from pathlib import Path
import sys

import bpy


addon_dir, output = map(Path, sys.argv[sys.argv.index('--') + 1:])
sys.path.insert(0, str(addon_dir.resolve().parent))
package = importlib.import_module(addon_dir.name)
package.register()
addon = package.eiem_blender_addon
output = output.resolve()
output.mkdir(parents=True, exist_ok=True)
destinations = []
dialogs = []


class BuildProbe:
    """Record the destination at the real publishing boundary."""
    def __init__(self, roots, destination):
        destinations.append(Path(destination).resolve())
        self.stats = dict(meshes=0, materials=0, textures=0, effects=1, exportSeconds=0)

    def run(self):
        return self.stats

    def poll(self):
        return True

    def close(self):
        pass


real_execute = addon.EFF_OT_export.execute
invoke_in_background = False


def accept_dialog(operator, context, event):
    # Replace only the external file picker; execute the actual export callback
    # with the RNA values that the normal dialog would submit.
    dialogs.append(operator.package_directory)
    return real_execute(operator, context)


def dispatch(operator, context):
    # Blender bypasses invoke in background mode. Reach the same callback on
    # the actual registered operator without requiring another GPU window.
    if bpy.app.background and invoke_in_background:
        return addon.EFF_OT_export.invoke(operator, context, None)
    return real_execute(operator, context)


original = (addon.selected_export_resources, addon.effect_editor.ExportBatch,
            addon.ExportHelper.invoke, addon.prefill_export_game_directory)
try:
    addon.selected_export_resources = lambda context=None: ([], [], [object()])
    addon.effect_editor.ExportBatch = BuildProbe
    addon.ExportHelper.invoke = accept_dialog
    addon.EFF_OT_export.execute = dispatch
    addon.prefill_export_game_directory = lambda *args: None
    internal = output / 'previous-quick-update'
    visible = output / 'chosen-parent'
    bpy.context.scene.eiem_export_mod_name = 'petals'
    assert bpy.ops.eiem.export_package('EXEC_DEFAULT', directory=str(visible),
        filepath=str(visible / 'ignored-filename'), package_directory=str(internal)) == {'FINISHED'}
    assert destinations[-1] == internal
    bpy.context.scene.eiem_export_mod_name = 'renamed-for-next-export'
    assert bpy.ops.eiem.export_update('EXEC_DEFAULT') == {'FINISHED'}
    assert destinations[-1] == internal, 'quick update stopped reusing its exact destination'
    # Reproduce already-remembered properties from an older add-on session.
    previous = bpy.context.window_manager.operator_properties_last('eiem.export_package')
    previous.package_directory = str(internal)
    bpy.context.scene.eiem_export_mod_name = 'petals'
    invoke_in_background = True
    # In a GUI Blender supplies remembered RNA values. Pass the legacy value
    # explicitly in background mode to exercise that exact callback state.
    inherited = dict(package_directory=previous.package_directory) if bpy.app.background else {}
    assert bpy.ops.eiem.export_package('INVOKE_DEFAULT', directory=str(visible),
        filepath=str(visible / 'ignored-filename'), **inherited) == {'FINISHED'}
    assert dialogs == [''], 'normal picker inherited the hidden quick-update destination'
    assert destinations[-1] == visible / 'petals', 'visible export choice was ignored'
    assert bpy.context.scene.eiem_export_last_directory == str(visible / 'petals')
    # Even an explicitly supplied override belongs only to EXEC_DEFAULT.
    assert bpy.ops.eiem.export_package('INVOKE_DEFAULT', directory=str(visible),
        package_directory=str(internal)) == {'FINISHED'}
    assert dialogs[-1] == '' and destinations[-1] == visible / 'petals'
    assert bpy.ops.eiem.export_package.get_rna_type().properties['package_directory'].is_skip_save
    report = dict(passed=True, normalDialogUsesVisibleDestination=True,
        legacyRememberedOverrideCleared=True, quickExactDestinationPreserved=True,
        overrideDoesNotPersist=True)
    (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('PASS EXPORT DESTINATION ' + json.dumps(report), flush=True)
finally:
    (addon.selected_export_resources, addon.effect_editor.ExportBatch,
     addon.ExportHelper.invoke, addon.prefill_export_game_directory) = original
    addon.EFF_OT_export.execute = real_execute
    package.unregister()
if not bpy.app.background:
    bpy.ops.wm.quit_blender()

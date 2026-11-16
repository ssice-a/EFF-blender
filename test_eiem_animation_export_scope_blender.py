"""Real native export: selected Mesh dependencies scope a shared Action.

Run in factory-startup Blender with ADDON_DIR BLEND EFFSOURCE NEW_MOD_DIR.
The input blend is read only; no game Mod is touched.
"""
import configparser
import importlib
from pathlib import Path
import sys

import bpy


addon_dir, blend, source, destination = map(
    lambda value: Path(value).resolve(), sys.argv[sys.argv.index('--') + 1:])
assert not destination.exists(), destination
sys.path.insert(0, str(addon_dir.parent))
package = importlib.import_module(addon_dir.name)
package.register()
bpy.ops.wm.open_mainfile(filepath=str(blend), load_ui=False, use_scripts=False)
addon = package.eiem_blender_addon
bpy.context.scene.render.fps = 30

reference = bpy.data.objects['MeshS_wpn_sword_0022_01_lod0_0.001']
template = bpy.data.materials['Material_漩涡3_UV旋转.001']
probe = reference.copy()
probe.data = reference.data.copy()
bpy.context.scene.collection.objects.link(probe)
probe.name = 'Scoped animation export probe'
probe.data.materials.clear()
action = bpy.data.actions.new('ScopedMaterialAction')


def animated_material(name):
    material = template.copy()
    material.name = name
    material.animation_data_clear()
    material.eiem_material_animations.clear()
    animation = material.animation_data_create()
    animation.action = action
    animation.action_slot = action.slots.new(id_type='MATERIAL', name=name)
    row = next(row for row in material.eiem_editor_values
               if row.key == 'eiem_float._MainTexUVRotate')
    for frame, angle in ((0, 0), (100, 360)):
        row.scalar = angle
        row.keyframe_insert('scalar', frame=frame)
    for curve in addon.material_animation.action_curves(material):
        for point in curve.keyframe_points:
            point.interpolation = 'LINEAR'
    return material


used = animated_material('ScopeUsed')
unused = animated_material('ScopeUnused')
broken = animated_material('ScopeBrokenUnused')
deleted = animated_material('ScopeDeleted')
identity = 'ScopedMaterialAction'
for material in (unused, broken, deleted):
    addon.material_animation.import_action(material, identity)
clip = unused.eiem_material_animations[0]
clip.autoplay, clip.speed, clip.sample_rate = True, 0.5, 30
# Neither an unused live channel, a stale Action slot, nor a removed material
# may become a required dependency of this Mesh export. Used is still pending
# registration, exercising group lookup without evaluating unrelated curves.
action.slots.remove(broken.animation_data.action_slot)
bpy.data.materials.remove(deleted, do_unlink=True)
probe.data.materials.append(used)
for polygon in probe.data.polygons:
    polygon.material_index = 0
assert not used.eiem_material_animations

result = addon.export_package(destination, [probe], lod_levels=[0],
                              effect_roots=[], source_baseline=str(source))
assert used.eiem_material_animations[0].identity == identity
assert used.eiem_material_animations[0].speed == 0.5
ini = configparser.ConfigParser(interpolation=None, strict=True)
ini.optionxform = str
ini.read(destination / 'mod.ini', encoding='utf-8-sig')
animations = [section for section in ini if section.startswith('Animation')]
assert animations == ['Animation' + identity], animations
animation = ini[animations[0]]
tracks = [ini[section] for section in ini if section.startswith('MaterialTrack')]
assert len(tracks) == 4, len(tracks)
assert len({track['material'] for track in tracks}) == 1
assert {track['material'] for track in tracks} == {
    ini[section]['material'] for section in ini if section.startswith('Part')}
assert {track['property'] for track in tracks} == {'_MainTexUVRotateMat'}
assert float(animation['speed']) == 0.5
assert len([key for key in animation if key.startswith('track.')]) == 4
modules, _ = addon.native_export.pack_modules()
modules['author_format3'].read_format3_directory(destination)
samples, _ = modules['author_format3'].decode_animation_samples(
    (destination / ini[animation['samples']]['path']).read_bytes())
assert len(samples) == 4 and {len(channel) for channel in samples} == {101}
by_variable = {track['variable']: track for track in tracks}
for index, channel in enumerate(samples):
    component = int(by_variable[animation['track.' + str(index)]]['component'])
    expected = (1, -1, 1) if component in (0, 3) else (0, 0, 0)
    assert all(abs(channel[position] - value) < 1e-5
               for position, value in zip((0, 50, 100), expected))

# The Action checkbox still controls whether selected resources export it.
clip.enabled = False
disabled = destination.with_name(destination.name + '-disabled')
assert not disabled.exists(), disabled
addon.export_package(disabled, [probe], lod_levels=[0], effect_roots=[],
                     source_baseline=str(source))
disabled_ini = configparser.ConfigParser(interpolation=None)
disabled_ini.read(disabled / 'mod.ini', encoding='utf-8-sig')
assert not any(section.startswith(('Animation', 'MaterialTrack'))
               for section in disabled_ini)
modules['author_format3'].read_format3_directory(disabled)
print('PASS native Mesh-scoped Action export: unused/deleted/stale-slot materials, '
      'pending track registration, unified identity, samples, export checkbox; '
      'writer=' + str(result.get('format3Writer')), flush=True)

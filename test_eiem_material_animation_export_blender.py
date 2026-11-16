"""Full Format 3 export for an existing material Action curve.

Run with Blender background arguments:
ADDON_DIR BLEND EFFSOURCE NEW_MOD_DIR
"""
import configparser
import importlib
from pathlib import Path
import sys

import bpy

addon_dir,blend,source,destination=map(Path,sys.argv[sys.argv.index('--')+1:])
addon_dir,blend,source,destination=map(lambda p:p.resolve(),(addon_dir,blend,source,destination))
if destination.exists():raise ValueError('Test Mod directory must not exist: '+str(destination))
if not (source/'source'/'manifest.json').is_file():raise ValueError('EFFSOURCE baseline is missing')
sys.path.insert(0,str(addon_dir.parent))
package=importlib.import_module(addon_dir.name);package.register()
try:
    bpy.ops.wm.open_mainfile(filepath=str(blend),load_ui=False,use_scripts=False)
    addon=package.eiem_blender_addon
    mesh=bpy.data.objects['MeshS_wpn_sword_0022_01_lod0_0.011']
    material=mesh.active_material
    if material is None:raise AssertionError('Target mesh has no active material')
    if len(material.eiem_material_animations):raise AssertionError('Input scene already contains registered material clips')
    row_index=next((index for index,row in enumerate(material.eiem_editor_values)
                    if row.key=='eiem_float._MainTexUVRotate'),None)
    if row_index is None:raise AssertionError('Target material is missing the UV angle editor property')
    row=material.eiem_editor_values[row_index]
    for frame,value in ((0,0.0),(100,360.0)):
        row.scalar=value
        row.keyframe_insert(data_path='scalar',frame=frame)
    armatures=[modifier.object for modifier in mesh.modifiers
               if modifier.type=='ARMATURE' and modifier.object]
    result=addon.export_package(destination,[mesh],armatures=armatures,
        source_baseline=str(source),include_switches=True)
    if len(material.eiem_material_animations)!=1:
        raise AssertionError('Export must register the existing material Action in the EFF animation group')
    clip=material.eiem_material_animations[-1]
    if (clip.identity,clip.source_path,clip.property,clip.source_mode)!=(
            'UVRotate_MainTex',f'eiem_editor_values[{row_index}].scalar',
            '_MainTexUVRotateMat','UV_ANGLE'):
        raise AssertionError('Existing UV rotation Action was mapped incorrectly')
    if (clip.start,clip.end)!=(0,100):
        raise AssertionError('EFF animation range must follow the material Action keyframes')
    ini=configparser.ConfigParser(interpolation=None,strict=True);ini.optionxform=str
    with (destination/'mod.ini').open(encoding='utf-8-sig') as stream:ini.read_file(stream)
    animation_id='Animation'+clip.identity
    if animation_id not in ini or 'MaterialAnimation'+clip.identity in ini:
        raise AssertionError('Material curves must use the unified Animation declaration')
    tracks=[dict(ini[section]) for section in ini.sections() if section.startswith('MaterialTrack')]
    tracks=[row for row in tracks if row.get('property')=='_MainTexUVRotateMat']
    if sorted(int(row['component']) for row in tracks)!=[0,1,2,3]:
        raise AssertionError('INI does not declare all four UV rotation matrix components')
    if len({row['variable'] for row in tracks})!=4:
        raise AssertionError('Each matrix component needs its own readable runtime variable')
    animation=ini[animation_id]
    if sorted(int(key.split('.',1)[1]) for key in animation if key.startswith('track.'))!=[0,1,2,3]:
        raise AssertionError('Unified Animation does not reference all four material tracks')
    samples_id=animation['samples']
    modules,_=addon.native_export.pack_modules()
    sample_channels,_=modules['author_format3'].decode_animation_samples(
        (destination/ini[samples_id]['path']).read_bytes())
    if len(sample_channels)!=4 or {len(channel) for channel in sample_channels}!={201}:
        raise AssertionError('Unified Animation sample resource does not contain four 201-sample tracks')
    material_tracks={row['variable']:int(row['component']) for row in tracks}
    by_component={material_tracks[animation['track.'+str(index)]]:sample_channels[index]
                  for index in range(4)}
    for component,expected in ((0,(1,-1,1)),(1,(0,0,0)),(2,(0,0,0)),(3,(1,-1,1))):
        actual=[by_component[component][index] for index in (0,100,200)]
        if any(abs(a-b)>1e-5 for a,b in zip(actual,expected)):
            raise AssertionError('UV rotation matrix samples differ: '+str((component,actual)))
    modules['author_format3'].read_format3_directory(destination)
    print('PASS direct material Action export:',clip.identity,
          'tracks=4','samples=201','writer=',result.get('format3Writer'),flush=True)
finally:pass

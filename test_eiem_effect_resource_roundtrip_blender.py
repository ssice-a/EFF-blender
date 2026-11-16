"""Current independent resources must win over an old effect author snapshot.

Run Blender with -- ADDON FORMAT3_PACKAGE. Only an isolated copy is modified.
"""
import configparser
import importlib
from pathlib import Path
import shutil
import sys
import tempfile
from unittest.mock import patch

import bpy

addon_path, package = map(Path, sys.argv[sys.argv.index('--') + 1:])
sys.path.insert(0, str(addon_path.resolve().parent))
addon = importlib.import_module(addon_path.name)
addon.register()
runtime = addon.eiem_blender_addon
assembly = importlib.import_module(addon.__name__ + '.eiem_effect_author_resources')
nativepack = importlib.import_module(addon.__name__ + '.nativepack.author_format3')
with tempfile.TemporaryDirectory(prefix='EFF-independent-effect-roundtrip-') as temporary:
    modified = Path(temporary) / 'mod'
    shutil.copytree(package, modified, ignore=shutil.ignore_patterns('test-cache', '.eff-backups'))
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read(modified / 'mod.ini', encoding='utf-8-sig')
    author_id = next(s for s in parser.sections() if parser[s].get('type') == 'effect-author')
    saved = runtime.format_io.read_format3_effect_author_resource(modified / parser[author_id]['path'])
    bindings = saved['edit']['resourceBindings']
    texture = next(r for r in bindings if r['type'] == 'Texture')
    pixels = bytes((17, 34, 51, 85))
    declaration = parser[texture['resource']]
    (modified / declaration['dds']).write_bytes(nativepack._dds_header(1, 1, 1, 4, False) + pixels)
    material = next(r for r in bindings if r['type'] == 'Material')
    path = modified / parser[material['resource']]['path']
    values = runtime.format_io.read_format3_material_resource(path)
    probe = 'float._AuthorRoundTripProbe'
    values[probe] = '2.5'
    data = ('\n'.join(k + '=' + v for k, v in values.items() if not k.startswith('_')) + '\n').encode('utf-8')
    path.write_bytes(nativepack.encode_material_resource(material['resource'], parser[material['resource']]['path'],
        values['source'], values['_source_asset'], data))
    runtime.import_package(modified, clean=True)
    root = next(o for o in bpy.data.objects if o.get('eff_effect_document'))
    standalone = [o for o in bpy.data.objects if o.type == 'MESH' and
                  o.get('eiem_author_package') == str(modified.resolve()) and o not in root.children_recursive]
    assert all(o.hide_render and o.hide_get() for o in standalone), \
        'effect-only resource Mesh was imported as a visible extra model'
    fx = runtime.effect_editor
    edit = fx.synchronize(root)
    reader = fx.assets.NativeAssets(edit, root['eff_effect_id']).reader
    tex, _ = reader.tree(texture['identity'])
    assert tex['m_Width'].data == tex['m_Height'].data == 1
    assert tex['image data'].data == pixels, 'published DDS was ignored in favour of stale snapshot pixels'
    assert any(row['first'] == '_AuthorRoundTripProbe' and row['second'] == 2.5
               for row in edit.value(material['identity'])['m_SavedProperties']['m_Floats'])
    scene_material = importlib.import_module(addon.__name__ + '.eiem_effect_scene_material')
    with patch.object(fx.document, 'load_source', side_effect=AssertionError('export reread material donor')), \
         patch.object(scene_material.Import, 'source', side_effect=AssertionError('export reimported EFF Material')):
        payload = fx.assets.export_document(root, edit)
        compiled, resources = assembly.externalize(edit, payload)
        again = fx.assets.export_document(root, fx.synchronize(root))
        assert payload == again, 'resource round trip is not stable'
    assert any(r.get('pixels') == pixels for r in resources['textures'])
    assert any(b'float._AuthorRoundTripProbe=2.5' in r['authorData'] for r in resources['materials'].values())
    assert compiled['authorReferences']
    # A newly declared shared DDS must be imported even though it was absent
    # from the preserved author's resourceBindings. Rename/delete the retired
    # slot declaration too: the current reachable graph is authoritative.
    records=saved['edit']['resourceBindings']
    source_material=next(r for r in records if r['type']=='Material' and parser[r['resource']].get('texture._MainTex'))
    old_texture=parser[source_material['resource']]['texture._MainTex']
    new_texture='TextureNewSharedDDS'
    parser[new_texture]=dict(parser[old_texture]);parser[new_texture]['dds']='textures/new-shared.dds'
    new_pixels=bytes((91,72,53,34))
    (modified/parser[new_texture]['dds']).write_bytes(nativepack._dds_header(1,1,1,4,False)+new_pixels)
    rebound=[]
    for record in records:
        if record['type']=='Material' and parser[record['resource']].get('texture._MainTex'):
            parser[record['resource']]['texture._MainTex']=new_texture;rebound.append(record)
    for section in list(parser):
        if section.startswith('Texture') and section!=new_texture and not any(
                value==section for owner in parser.sections() for key,value in parser[owner].items() if key.startswith('texture.')):
            del parser[section]
    with (modified/'mod.ini').open('w',encoding='utf-8') as stream:parser.write(stream,space_around_delimiters=False)
    nativepack.read_format3_directory(modified)
    workspace,_attachment,_=runtime.format_io.materialize_format3_effect_author_resource(
        modified/parser[author_id]['path'],author_id,parser[author_id]['path'],Path(temporary)/'cache')
    assembly.overlay_resources(workspace,modified,parser)
    editor=fx.document.Editor(workspace)
    output=editor.export();output['assets']=workspace['author_assets']
    _compiled,resources=assembly.externalize(editor,output)
    textures=[r for r in resources['textures'] if r.get('pixels')==new_pixels]
    assert len(textures)==1,'new shared DDS was lost or duplicated'
    texture_id=textures[0]['id']
    assert sum(any(t['texture']==texture_id for t in m['textureEdits']) for m in resources['materials'].values())==len(rebound)
    # Missing DDS and a wrong-kind declaration must fail explicitly; neither
    # may silently restore the source game's original texture.
    import copy
    baseline,_attachment,_=runtime.format_io.materialize_format3_effect_author_resource(
        modified/parser[author_id]['path'],author_id,parser[author_id]['path'],Path(temporary)/'cache2')
    valid=parser[new_texture]['dds'];parser[new_texture]['dds']='textures/missing.dds'
    try:assembly.overlay_resources(copy.deepcopy(baseline),modified,parser);raise AssertionError('missing DDS accepted')
    except (FileNotFoundError,ValueError):pass
    parser[new_texture]['dds']=valid
    for row in rebound:parser[row['resource']]['texture._MainTex']=row['resource']
    try:assembly.overlay_resources(copy.deepcopy(baseline),modified,parser);raise AssertionError('wrong resource kind accepted')
    except ValueError:pass
    print('PASS current INI: new shared DDS, rebindings, retired declarations, exact pixels, missing file/kind validation')
print('PASS external DDS/material overrides, readable ID import, stable re-export without source/index reads')
addon.unregister()

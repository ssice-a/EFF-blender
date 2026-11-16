"""Billboard author materials must survive without a mesh material slot.

Run with factory startup: -- ADDON SOURCE OUTPUT.
"""
import importlib
import json
from pathlib import Path
import struct
import sys
import zlib

import bpy

addon_path, source, output = map(Path, sys.argv[sys.argv.index('--') + 1:])
output.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(addon_path.resolve().parent))
addon = importlib.import_module(addon_path.name)
addon.register()
editor = addon.eiem_blender_addon.effect_editor
bridge = editor.material_bridge
root = editor.import_source(bpy.context, str(source))
edit = editor.editor(root)
node = next(o for o in editor.objects(root) if any(
    edit.objects[i]['classId'] == 199 and edit.value(i).get('m_RenderMode') == 0
    for i in json.loads(o.get('eff_effect_components', '[]'))))
renderer = next(i for i in json.loads(node['eff_effect_components'])
                if edit.objects[i]['classId'] == 199)
identity = editor.preview._target(edit, renderer, ['m_Materials', 0])
author = editor.assets.material_object(root, identity)
material = bridge.material_for(author)
assert material
assert not any(material in list(mesh.materials) for mesh in bpy.data.meshes), \
    'fixture must exercise a billboard material with no mesh slot'
assert not editor.assets.export_document(root, edit).get('assets'), \
    'material ownership must not turn unedited game resources into author assets'

def chunk(kind, data):
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))

texture = output / 'chosen-billboard.png'
texture.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 2, 2, 8, 6, 0, 0, 0))
                    + chunk(b'IDAT', zlib.compress((b'\x00' + bytes((64, 128, 255, 96)) * 2) * 2))
                    + chunk(b'IEND', b''))
material.eiem_editor_values['eiem_texture._MainTex'].text = str(texture)
material.eiem_editor_values['eiem_float._TintColorIntensity'].scalar = 2.5
editor.store(root, editor.synchronize(root))
names = root.name, author.name, material.name
project = output / 'billboard-material.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(project))
bpy.ops.wm.open_mainfile(filepath=str(project))
root, author = (bpy.data.objects[name] for name in names[:2])
material = bridge.material_for(author)
assert material is not None, 'billboard author material was lost after save/reopen'
assert material.name == names[2]
assert material['eiem_texture._MainTex'] == str(texture), 'chosen texture reverted after reopening'
assert material['eiem_float._TintColorIntensity'] == '2.5'
edit = editor.synchronize(root)
index = next(i for i, row in enumerate(edit.value(identity)['m_SavedProperties']['m_TexEnvs'])
             if row['first'] == '_MainTex')
assert Path(editor.assets.image_for_slot(root, edit, identity, index,
            editor.preview._resources(root, edit)).filepath) == texture

# Old files have resource objects and serialized native material data, but
# may already have lost every unassigned author Material datablock.
bpy.data.materials.remove(material, do_unlink=True)
editor.upgrade_loaded()
material = bridge.material_for(author)
assert material is not None, 'loading a legacy project did not restore its missing material editor'
assert material['eiem_float._TintColorIntensity'] == '2.5'
for _ in range(2):
    addon.unregister()
    addon.register()
    editor.upgrade_loaded()
assert bridge.material_for(author) == material
assert sum(m.eff_effect_author == author for m in bpy.data.materials) == 1
print('PASS billboard material ownership, chosen texture, native parameters, save/reopen and legacy recovery')
addon.unregister()

"""Real liino fixtures: import, zero-scale preview, clipboard and save/reopen.

Run in a separate factory-startup Blender with -- ADDON PREFAB_DIR OUTPUT.
The source .efffx files and the user's interactive scene are never modified.
"""
import copy
import importlib
import json
from pathlib import Path
import sys

import bpy

addon_path, source_root, output = map(Path, sys.argv[sys.argv.index('--') + 1:])
sys.path.insert(0, str(addon_path.resolve().parent))
addon = importlib.import_module(addon_path.name)
addon.register()
editor = addon.eiem_blender_addon.effect_editor
output.mkdir(parents=True, exist_ok=True)
names = ('p_fxui_liino_relax_sp_01', 'p_fxbat_liino_relax_react_01',
         'p_fxbat_liino_relax_react_02', 'p_fxbat_liino_relax_react_03')
expected_counts = (14, 6, 3, 6)
roots = []
baselines = {}
results = []
for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)

for name, expected_count in zip(names, expected_counts):
    root = editor.import_source(bpy.context, source_root / (name + '.efffx'), preserve_source=True)
    root.eff_effect.preview = False
    root.eff_effect.enabled = False
    # The delivered template must use the installed compiler, not the source
    # importer's optional repository Debug build path.
    compiler = Path('E:/AnimeStudio-net10-1ccfbc16bf7fe625e8295bac8074ac3b1b9a065b/AnimeStudio.GUI.exe')
    assert compiler.is_file(), compiler
    root.eff_effect.compiler = str(compiler)
    edit = editor.synchronize(root)
    native_baseline = copy.deepcopy(edit.export())
    assert not native_baseline['objects'], 'unchanged native source was edited by import'
    nodes = [node for node in edit.state['catalog']['hierarchy']
             if any(edit.objects[key]['classId'] == 198 for key in node['components'])]
    assert len(nodes) == expected_count
    assert not root.get('预览错误'), root.get('预览错误')
    copied = []
    for node in nodes:
        payload = editor.block_clipboard.copy_emitter(edit, node['identity'])
        portable = editor.block_clipboard.decode(editor.block_clipboard.encode(payload))
        assert portable == payload
        particle = next(key for key in node['components'] if edit.objects[key]['classId'] == 198)
        module = editor.block_clipboard.copy_module(edit, particle, 'EmissionModule')
        module_copy = editor.block_clipboard.decode(editor.block_clipboard.encode(module))
        assert module_copy == module
        copied.append(node['identity'])
    trial, added = editor.block_clipboard.paste_emitter(edit, edit.state['catalog']['root'],
                                                      editor.block_clipboard.copy_emitter(edit, copied[0]))
    assert added and trial is not edit
    assert len(trial.state['catalog']['hierarchy']) > len(edit.state['catalog']['hierarchy'])
    assert edit.export() == native_baseline, 'clipboard changed its source'
    for frame in (1, 30, 90):
        bpy.context.scene.frame_set(frame)
        editor.preview.refresh(root, editor.synchronize(root))
        assert not root.get('预览错误'), root.get('预览错误')
        assert editor.synchronize(root).export() == native_baseline, 'preview changed source values'
    baseline = editor.assets.export_document(root, editor.synchronize(root))
    assert not baseline['objects'] and not baseline.get('assets'), 'unmodified template regenerated resources'
    editor.assets.write_document(root, editor.synchronize(root), output / (name + '.effedit.json'))
    baselines[root.name] = baseline
    roots.append(root)
    results.append(dict(source=name, particles=expected_count, copiedEmitters=len(copied),
                        unchangedNative=True, addedEmitterCount=len(added)))
    print('PASS liino import/preview/native preservation/clipboard:', name, flush=True)

project = output / 'liino-idle-templates.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(project))
bpy.ops.wm.open_mainfile(filepath=str(project))
for root_name, baseline in baselines.items():
    root = bpy.data.objects[root_name]
    assert not root.eff_effect.preview and not root.eff_effect.enabled
    assert editor.assets.export_document(root, editor.synchronize(root)) == baseline
    assert not root.get('预览错误'), root.get('预览错误')
report = dict(passed=True, blender=bpy.app.version_string, sources=results,
              particles=sum(row['particles'] for row in results),
              saveReopenUnchanged=True, library=str(project))
(output / 'template-validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf-8')
print('PASS liino four-source library save/reopen and 29 complete emitter copies', flush=True)
addon.unregister()

"""Exercise module checkboxes and object navigation on actual effect sources."""
import copy
import importlib
import json
import sys
from pathlib import Path

import bpy

addon_path, source_root, output = sys.argv[sys.argv.index('--') + 1:]
sys.path.insert(0, str(Path(addon_path).resolve().parent))
addon = importlib.import_module(Path(addon_path).name)
addon.register()
editor = addon.eiem_blender_addon.effect_editor


class Layout:
    def __init__(self, records=None, enabled=True):
        self.records = records if records is not None else []
        self.enabled = enabled

    def row(self, **kwargs):
        return Layout(self.records, self.enabled)

    def split(self, **kwargs):
        return Layout(self.records, self.enabled)

    def column(self, **kwargs):
        return Layout(self.records, self.enabled)

    def box(self):
        return Layout(self.records, self.enabled)

    def separator(self, **kwargs):
        pass

    def template_color_ramp(self, *args, **kwargs):
        self.records.append(('color_ramp', args[0].name, self.enabled))

    def panel(self, key, **kwargs):
        # All panels expanded to exercise disabled bodies and their headers.
        return Layout(self.records, self.enabled), Layout(self.records, self.enabled)

    def label(self, **kwargs):
        self.records.append(('label', kwargs.get('text', ''), self.enabled))
        assert kwargs.get('icon') != 'ERROR', kwargs.get('text')

    def prop(self, obj, path, **kwargs):
        self.records.append(('prop', path, self.enabled))

    def prop_search(self, *args, **kwargs):
        pass

    def operator(self, name, **kwargs):
        op = type('RecordedOperator', (), {'idname': name})()
        self.records.append(('operator', op, self.enabled))
        return op


def draw(obj):
    bpy.context.view_layer.objects.active = obj
    layout = Layout()
    editor.EFF_PT_effect_object.draw(type('Panel', (), {'layout': layout})(), bpy.context)
    return layout.records


roots = []
for source in sorted(Path(source_root).rglob('effect.json')):
    if '.eff-backups' in str(source):
        continue
    root = editor.import_source(bpy.context, source.parent)
    roots.append(root)
    edit = editor.editor(root)
    initial_edits = copy.deepcopy(edit.export()['objects'])
    node = next(o for o in editor.objects(root) if any(
        edit.objects[i]['classId'] == 198 for i in json.loads(o.get('eff_effect_components', '[]'))))
    identity = next(i for i in json.loads(node['eff_effect_components']) if edit.objects[i]['classId'] == 198)

    # Reproduce the user action, including UI -> native data synchronization.
    path = ['UVModule', 'enabled']
    key = editor.field_key(identity, path)
    original = node[key]
    settings = copy.deepcopy(edit.value(identity, ['UVModule']))
    node[key] = True
    editor.synchronize(root)
    assert any(r[:2] == ('label', '贴图序列帧') for r in draw(node))
    node[key] = False
    editor.synchronize(root)
    disabled = draw(node)
    assert any(r[:2] == ('label', '贴图序列帧') for r in disabled), 'unchecking UVModule removed its header'
    assert ('prop', '["' + key + '"]', True) in disabled, 'disabled module lost its usable enable checkbox'
    assert edit.value(identity, ['UVModule']) == {**settings, 'enabled': False}, 'disabling erased module settings'
    picker = type('ModulePicker', (), {'object_name': node.name})()
    assert identity + '|UVModule' in [item[0] for item in editor.module_items(picker, bpy.context)]
    assert bpy.ops.eiem.effect_block(object_name=node.name, module=identity + '|UVModule') == {'FINISHED'}
    assert edit.value(identity, ['UVModule']) == {**settings, 'enabled': True}, 'enable picker reset module settings'
    node[key] = original
    editor.synchronize(root)

    # Every schema-backed module remains present in both states, in the same order.
    for module, (title, _) in editor.effect_ui.MODULES.items():
        data = edit.value(identity).get(module)
        if not isinstance(data, dict) or module == 'InitialModule':
            continue
        key = editor.field_key(identity, [module, 'enabled'])
        saved = node[key]
        before = copy.deepcopy(data)
        node[key] = True
        editor.synchronize(root)
        on = draw(node)
        node[key] = False
        editor.synchronize(root)
        off = draw(node)
        titles = set(t for t, _ in editor.effect_ui.MODULES.values())
        assert [r[1] for r in on if r[0] == 'label' and r[1] in titles] == [
            r[1] for r in off if r[0] == 'label' and r[1] in titles], module
        assert ('prop', '["' + key + '"]', True) in off, module
        assert edit.value(identity, [module]) == {**before, 'enabled': False}, module
        node[key] = saved
        editor.synchronize(root)

    assert editor.synchronize(root).export()['objects']==initial_edits, 'UI module toggles changed restored native data'

    # The navigator names actual imported nodes and materials, and can return
    # from the material to its emitter without touching native resource links.
    records = draw(root)
    navigation = [r[1] for r in records if r[0] == 'operator' and r[1].idname == 'eiem.effect_select']
    assert root.name in [op.object_name for op in navigation]
    assert node.name in [op.object_name for op in navigation]
    assert any(r[0] == 'label' and '发射器' in r[1] for r in records)
    assert bpy.ops.eiem.effect_select(object_name=node.name) == {'FINISHED'}
    assert bpy.context.object == node
    assert all(not o.select_get() for o in bpy.context.selected_objects if o != node)
    renderer = next(i for i in json.loads(node['eff_effect_components']) if edit.objects[i]['classId'] == 199)
    material_id = editor.preview._target(edit, renderer, ['m_Materials', 0])
    material = editor.assets.material_object(root, material_id)
    assert material and material.name in [op.object_name for op in navigation]
    assert bpy.ops.eiem.effect_resource(identity=material_id) == {'FINISHED'}
    assert bpy.context.object == material
    records = draw(material)
    assert any(r[0] == 'operator' and r[1].idname == 'eiem.effect_select' and r[1].object_name == node.name for r in records)
    assert bpy.ops.eiem.effect_select(object_name=node.name) == {'FINISHED'}
    assert bpy.ops.eiem.effect_select(object_name=root.name) == {'FINISHED'}
    assert bpy.context.object == root
    assert editor.synchronize(root).export()['objects']==initial_edits, 'navigation modified native data'

    # Retain disabled settings across an actual .blend save and reopen.
    node[editor.field_key(identity, ['UVModule', 'enabled'])] = False
    editor.synchronize(root)

output = Path(output)
output.parent.mkdir(parents=True, exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=str(output))
bpy.ops.wm.open_mainfile(filepath=str(output))
for root in (o for o in bpy.data.objects if o.get('eff_effect_document')):
    edit = editor.editor(root)
    node = next(o for o in editor.objects(root) if any(
        edit.objects[i]['classId'] == 198 for i in json.loads(o.get('eff_effect_components', '[]'))))
    identity = next(i for i in json.loads(node['eff_effect_components']) if edit.objects[i]['classId'] == 198)
    assert edit.value(identity, ['UVModule', 'enabled']) is False
    assert any(r[:2] == ('label', '贴图序列帧') for r in draw(node))
    node[editor.field_key(identity, ['UVModule', 'enabled'])] = True
    assert editor.synchronize(root).value(identity, ['UVModule', 'enabled']) is True

print('PASS stable module headers, reversible checkboxes, native settings, object/material navigation and save/reopen')
addon.unregister()

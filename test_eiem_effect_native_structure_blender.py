"""Verify source-native component/module/reference UI on real effect sources.

Run in an isolated Blender --background --factory-startup process:
  --python this_file -- ADDON_DIRECTORY OUTPUT_BLEND SOURCE_DIRECTORY [...]
"""
import copy
import importlib
import json
import sys
from pathlib import Path
from unittest.mock import patch

import bpy

addon_path, output, *sources = sys.argv[sys.argv.index('--') + 1:]
sys.path.insert(0, str(Path(addon_path).resolve().parent))
addon = importlib.import_module(Path(addon_path).name)
addon.register()
editor = addon.eiem_blender_addon.effect_editor


class Layout:
    def __init__(self, records=None, opened=None, parents=(), enabled=True):
        self.records = [] if records is None else records
        self.opened, self.parents, self.enabled = opened, parents, enabled

    def child(self, parents=None):
        return Layout(self.records, self.opened,
                      self.parents if parents is None else parents, self.enabled)

    def row(self, **kwargs): return self.child()
    def column(self, **kwargs): return self.child()
    def split(self, **kwargs): return self.child()
    def box(self): return self.child()
    def separator(self, **kwargs): pass
    def prop_search(self, *args, **kwargs): pass
    def template_color_ramp(self, *args, **kwargs): pass

    def panel(self, key, *, default_closed=False):
        self.records.append(('panel', key, self.parents, default_closed))
        body = self.child(self.parents + (key,)) if self.opened is None or key in self.opened else None
        return self.child(), body

    def label(self, *, text='', icon='NONE', **kwargs):
        assert icon != 'ERROR', text
        self.records.append(('label', text, self.parents, self.enabled))

    def prop(self, obj, path, *, text='', **kwargs):
        self.records.append(('prop', path, self.parents, self.enabled, text))

    def operator(self, idname, **kwargs):
        op = type('RecordedOperator', (), {'idname': idname})()
        self.records.append(('operator', op, self.parents, self.enabled))
        return op


def draw(obj, opened=None):
    bpy.context.view_layer.objects.active = obj
    layout = Layout(opened=opened)
    editor.EFF_PT_effect_object.draw(type('Panel', (), {'layout': layout})(), bpy.context)
    return layout.records


reports = []
for source in sources:
    root = editor.import_source(bpy.context, Path(source))
    edit = editor.editor(root)
    snapshot = copy.deepcopy(edit.state['values'])
    nodes = {obj.get('eff_effect_node'): obj for obj in editor.objects(root)
             if obj.get('eff_effect_node')}
    hierarchy = edit.state['catalog']['hierarchy']
    native_nodes = [nodes[node['identity']] for node in hierarchy]
    # Open only GameObject nodes, leaving native component bodies folded.
    opened = {'node_' + str(obj.as_pointer()) for obj in native_nodes if obj != root}
    records = draw(root, opened)
    component_panels = [row for row in records if row[0] == 'panel' and row[1].startswith('component_')]
    expected = [identity for node in hierarchy for identity in node['components']
                if edit.objects[identity]['classId'] != 1]
    assert [row[1] for row in component_panels] == [
        'component_' + editor.field_key(identity, ['_source_component']) for identity in expected], \
        'GameObject/component order differs from source hierarchy'
    assert all(row[3] is True for row in component_panels), 'components must initially be collapsed'
    assert not any(row[0] == 'prop' and 'fx_' in row[1] for row in records), \
        'collapsed components still draw their native parameters'

    particles = []
    readonly = set()
    for obj in native_nodes:
        ids = json.loads(obj.get('eff_effect_components', '[]'))
        for identity in ids:
            spec = edit.objects[identity]
            drawer = editor.effect_ui.Draw(editor, obj, edit, identity)
            # Every actual component can be expanded, including game scripts,
            # Transform, Animator and ordinary MeshRenderer/MeshFilter.
            expanded = Layout()
            with patch.object(Path, 'read_bytes', side_effect=AssertionError('source file read in expanded UI')), \
                 patch.object(Path, 'read_text', side_effect=AssertionError('source file read in expanded UI')), \
                 patch.object(editor, 'synchronize', side_effect=AssertionError('synchronize in expanded UI')):
                drawer.component(expanded, spec)
            if spec['classId'] == 198:
                particles.append(identity)
                layout = Layout(opened=set())
                drawer.component(layout, spec)
                expected_modules = [key for key, value in edit.value(identity).items()
                                    if key.endswith('Module') and isinstance(value, dict)]
                panels = [row for row in layout.records if row[0] == 'panel']
                assert [row[1] for row in panels] == [editor.field_key(identity, ['_system'])] + [
                    editor.field_key(identity, [module]) for module in expected_modules]
                assert all(row[3] is True for row in panels), 'modules must initially be collapsed'
                assert not any(row[0] == 'prop' and row[2] for row in layout.records), \
                    'collapsed modules draw body controls'
                assert not any(row[0] == 'label' and row[1] in ('运动', '交互与扩展', '外观随生命周期 / 速度变化')
                               for row in layout.records), 'synthetic module categories reappeared'
                # Expanding InitialModule must not mix ParticleSystem root fields into it.
                layout = Layout(opened={editor.field_key(identity, ['InitialModule'])})
                drawer.component(layout, spec)
                root_key = '["' + editor.field_key(identity, ['lengthInSec']) + '"]'
                assert not any(row[0] == 'prop' and row[1] == root_key for row in layout.records)
            elif spec['classId'] not in editor.document.EDITABLE:
                readonly.add(spec['type'])
                assert any(row[0] == 'label' and '只读' in row[1] for row in expanded.records), spec['type']
            if spec['classId'] in (199, 23):
                # Expanded renderers preserve actual PPtr placement and type names.
                layout = expanded
                material_rows = [row for row in layout.records if row[0] == 'label' and 'm_TexEnvs' in row[1]]
                assert material_rows, (spec['type'], 'no inline material texture fields')
                material_panel = editor.field_key(identity, ['m_Materials'])
                assert any(material_panel in row[2] for row in material_rows), \
                    'material textures were detached from m_Materials'
                for row in layout.records:
                    if row[0] == 'label' and 'm_Texture' in row[1] and '：' in row[1]:
                        assert material_panel in row[2], 'texture reference escaped its native Material PPtr'
                for index in range(len(edit.value(identity).get('m_Materials', []))):
                    mid = editor.preview._target(edit, identity, ['m_Materials', index])
                    if mid not in edit.state['values']:continue
                    for key in ('m_CustomRenderQueue', 'm_ShaderKeywords', 'm_ValidKeywords', 'm_InvalidKeywords'):
                        if key not in edit.value(mid):continue
                        field = editor.field_key(mid, [key])
                        assert any(row[0] in ('prop', 'panel') and field in row[1]
                                   and material_panel in row[2] for row in layout.records), \
                            'inline Material omitted native state ' + key

    # A missing TypeTree/data payload is shown at the original component slot,
    # rather than disappearing or preventing the remaining components drawing.
    identity = next(identity for identity in expected if edit.objects[identity]['classId'] != 4)
    saved_value = edit.state['values'].pop(identity)
    spec = edit.objects[identity]
    saved_reason = spec.get('reason')
    spec['reason'] = 'fixture: missing TypeTree'
    try:
        panel = 'component_' + editor.field_key(identity, ['_source_component'])
        records = draw(root, opened | {panel})
        message = ''.join(row[1] for row in records if row[0] == 'label')
        assert '来源未解析' in message and 'missing TypeTree' in message, 'unparsed component was hidden'
    finally:
        edit.state['values'][identity] = saved_value
        spec['reason'] = saved_reason

    # A redraw must not reread a source file, synchronize, create preview assets,
    # or mutate native values. Patch only during drawing, not source import.
    with patch.object(Path, 'read_bytes', side_effect=AssertionError('file read in UI draw')), \
         patch.object(Path, 'read_text', side_effect=AssertionError('file read in UI draw')), \
         patch.object(editor, 'synchronize', side_effect=AssertionError('synchronize in UI draw')), \
         patch.object(editor.assets, 'material_object', side_effect=AssertionError('material lookup while collapsed')):
        draw(root, opened)
    assert edit.state['values'] == snapshot, 'drawing the editor changed native author values'
    reports.append({'source': str(source), 'nodes': len(native_nodes), 'particleSystems': len(particles),
                    'componentPanels': len(expected), 'readonlyComponents': sorted(readonly)})

output = Path(output)
output.parent.mkdir(parents=True, exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=str(output))
print('PASS native effect structure UI ' + json.dumps(reports, ensure_ascii=False))
addon.unregister()

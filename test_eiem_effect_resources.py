import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import eiem_effect_document as document
import eiem_effect_resources as resources
import eiem_effect_clipboard as clipboard


class ResourceReachabilityTests(unittest.TestCase):
    def test_texture_transfer_selects_template_with_matching_immutable_flags(self):
        from nativepack import type_tree
        import eiem_effect_native as native
        schema = dict(nodes=[dict(level=0, type='Texture2D', name='Base', metaFlag=0),
                             dict(level=1, type='bool', name='m_IsAlphaChannelOptional', metaFlag=0),
                             dict(level=1, type='int', name='m_Width', metaFlag=0)])
        objects = [dict(identity=i, classId=28, schema='texture', type='Texture2D')
                   for i in ('alpha-optional', 'alpha-required')]
        edit = document.Editor(dict(format='EFF_EFFECT_WORKSPACE', version=1,
                                    catalog=dict(objects=objects), schemas={'texture': schema}))
        row = dict(key='image', classId=28, type='Texture2D',
                   schemaSha256=resources.schema_hash(edit, objects[0]['identity']),
                   value=dict(m_IsAlphaChannelOptional=False, m_Width=512))
        codec = type_tree.Schema.compile(schema['nodes'])
        def tree(identity):
            return type_tree.from_data(codec, dict(m_IsAlphaChannelOptional=identity == 'alpha-optional', m_Width=16)), b''
        with patch.object(native, 'Reader') as reader:
            reader.return_value.tree.side_effect = tree
            self.assertEqual(resources.templates(edit, [row])['image'], 'alpha-required')
            # New image construction uses destination defaults, then writes pixels.
            self.assertEqual(resources.templates(edit, [row], preserve_defaults=False)['image'], 'alpha-optional')
            edit.objects.pop('alpha-required')
            with self.assertRaisesRegex(ValueError, '结构模板'):
                resources.templates(edit, [row])

    def test_current_pointer_replaces_historical_reference_and_does_not_follow_scene(self):
        values = {'cab:1': {'mesh': dict(m_FileID=0, m_PathID=3),
                            'sibling': dict(m_FileID=0, m_PathID=2)},
                  'cab:2': {'mesh': dict(m_FileID=0, m_PathID=4)},
                  'cab:3': {}, 'cab:4': {}}
        objects = [dict(identity=i, classId=199 if i in ('cab:1', 'cab:2') else 43,
                        native=dict(cab='cab', pathId=int(i.split(':')[1])), references=[])
                   for i in values]
        objects[0]['references'] = [dict(field='$.mesh', identity='cab:4', cab='cab',
                                         fileId=0, pathId=4, gameLogical={'path': 'assets/old.asset'})]
        edit = document.Editor(dict(format='EFF_EFFECT_WORKSPACE', version=1,
                                    catalog=dict(objects=objects), values=values,
                                    original=copy.deepcopy(values), author_assets=[
                                        dict(identity='cab:3'), dict(identity='cab:4')]))
        self.assertEqual(resources.reachable(edit, {'cab:1'}), {'cab:1', 'cab:3'})
        reference = next(resources.references(edit, 'cab:1'))[-1]
        self.assertNotIn('gameLogical', reference)

    def test_exact_scope_does_not_restore_excluded_gameobject_components(self):
        objects = [dict(identity='cab:' + str(i), classId=kind,
                        native=dict(cab='cab', pathId=i), references=[])
                   for i, kind in ((1, 1), (2, 199), (3, 43))]
        values = {'cab:1': {'component': dict(m_FileID=0, m_PathID=2)},
                  'cab:2': {'mesh': dict(m_FileID=0, m_PathID=3)}, 'cab:3': {}}
        edit = document.Editor(dict(format='EFF_EFFECT_WORKSPACE', version=1,
                                    catalog=dict(objects=objects, hierarchy=[dict(identity='cab:1', components=['cab:2'])]),
                                    values=values, original=copy.deepcopy(values)))
        payload = dict(objects=[], gameReferences=[dict(owner='cab:2', path='assets/game.mat')])
        self.assertNotIn('gameReferences', resources.prune(edit, payload, {'cab:1'}))

    def test_real_changed_borrowed_material_survives_cross_root_clipboard(self):
        fixture = Path(__file__).resolve().parents[2] / 'diagnostics/effect-export-failure-20261115/author-state.json'
        if not fixture.is_file():
            self.skipTest('real exported author fixture is not installed')
        state = json.loads(fixture.read_text('utf-8'))
        if not Path(state['source']).is_dir():
            self.skipTest('real source template is not installed')
        source = document.Editor(state)
        mid = next(i for i, spec in source.objects.items()
                   if spec.get('gameSource') and spec['classId'] == 21 and source.value(i) != state['original'][i])
        node = next(n for n in state['catalog']['hierarchy']
                    if any(r.get('identity') == mid for i in n['components']
                           if source.objects[i]['classId'] == 199 for r in source.objects[i]['references']))
        payload = clipboard.decode(clipboard.encode(clipboard.copy_emitter(source, node['identity'])))
        self.assertEqual([r['classId'] for r in payload['resources']], [21])
        target = document.Editor(document.load_source(state['source']))
        original = copy.deepcopy(target.state)
        broken = copy.deepcopy(payload)
        broken['resources'][0]['schemaSha256'] = 'incompatible'
        broken['resources'][0]['schemaShapeSha256'] = 'incompatible'
        with self.assertRaisesRegex(ValueError, '结构模板'):
            clipboard.paste_emitter(target, target.state['catalog']['root'], broken)
        self.assertEqual(target.state, original)
        pasted, nodes = clipboard.paste_emitter(target, target.state['catalog']['root'], payload)
        renderer = next(i for i in nodes[0]['components'] if pasted.objects[i]['classId'] == 199)
        material = next(r['identity'] for r in pasted.objects[renderer]['references'] if r['field'] == '$.m_Materials[0]')
        brightness = next(r['second'] for r in pasted.value(material)['m_SavedProperties']['m_Floats']
                          if r['first'] == '_TintColorIntensity')
        self.assertEqual(brightness, 50)
        self.assertNotIn('gameSource', pasted.objects[material])
        self.assertEqual(len(pasted.state['author_assets']), 1)
        refs = [r for r in pasted.objects[material]['references'] if r.get('gameLogical')]
        self.assertEqual({r['gameLogical']['type'] for r in refs}, {'Shader', 'Texture2D'})
        self.assertTrue(all(r['gameLogical'].get('sourcePrefab') for r in refs))
        # A new array slot has no historical catalog row. It must still point
        # at the same copied author resource, without making another clone.
        pasted.value(renderer)['m_Materials'].append(copy.deepcopy(pasted.value(renderer)['m_Materials'][0]))
        shared = clipboard.copy_emitter(pasted, nodes[0]['identity'])
        self.assertEqual(len(shared['resources']), 1)
        row = next(r for r in shared['objects'] if r['classId'] == 199)
        material_refs = [r for r in row['references'] if r['path'][0] == 'm_Materials']
        self.assertEqual(len(material_refs), 2)
        self.assertEqual(material_refs[0]['target'], material_refs[1]['target'])
        again, copied_nodes = clipboard.paste_emitter(target, target.state['catalog']['root'], shared)
        copied_renderer = next(i for i in copied_nodes[0]['components'] if again.objects[i]['classId'] == 199)
        self.assertEqual(again.value(copied_renderer)['m_Materials'][0], again.value(copied_renderer)['m_Materials'][1])
        self.assertEqual(len(again.state['author_assets']), 1)
        self.assertEqual(target.state, original)


if __name__ == '__main__':
    unittest.main()

import copy
import json
from pathlib import Path
import tempfile
import unittest

import eiem_effect_document as effects


def fixture():
    def n(kind, name, level):
        return dict(type=kind, name=name, level=level, metaFlag=0, byteSize=-1, version=1)
    nodes = [n('ParticleSystem', 'Base', 0), n('PPtr<GameObject>', 'm_GameObject', 1),
             n('int', 'm_FileID', 2), n('SInt64', 'm_PathID', 2), n('float', 'simulationSpeed', 1),
             n('PPtr<Texture>', 'resource', 1), n('int', 'm_FileID', 2), n('SInt64', 'm_PathID', 2),
             n('string', 'label', 1), n('Array', 'Array', 2), n('int', 'size', 3), n('char', 'data', 3),
             n('EmissionModule', 'EmissionModule', 1), n('bool', 'enabled', 2), n('int', 'm_BurstCount', 2),
             n('vector', 'm_Bursts', 2), n('Array', 'Array', 3), n('int', 'size', 4),
             n('Burst', 'data', 4), n('float', 'time', 5), n('float', 'probability', 5)]
    value = dict(m_GameObject=dict(m_FileID=0, m_PathID=2**62+7), simulationSpeed=1.0,
                 resource=dict(m_FileID=0, m_PathID=0), label='test',
                 EmissionModule=dict(enabled=False, m_BurstCount=1, m_Bursts=[dict(time=0.0, probability=1.0)]))
    obj = dict(identity='cab:1', state='parsed', classId=198, type='ParticleSystem',
               schema='schemas/schema.json', native=dict(cab='cab', pathId=1), references=[])
    return dict(format='EFF_EFFECT_WORKSPACE', version=1, source='test', sourceManifestSha256='A'*64,
                sourceEffectSha256='B'*64, catalog=dict(objects=[obj]), values={'cab:1':value},
                original={'cab:1':copy.deepcopy(value)}, hashes={'cab:1':'C'*64},
                schemas={'schemas/schema.json':dict(nodes=nodes, managedTypes=[])})


class EffectTests(unittest.TestCase):
    @staticmethod
    def texture(identity, path, name, cab='cab', path_id=2):
        return dict(identity=identity, state='raw-backed', classId=28, type='Texture2D',
                    native=dict(cab=cab, pathId=path_id), logicalPaths=[dict(path=path, name=name)],
                    references=[])

    def test_logical_pointer_edit_resolves_exact_type_path_and_asset(self):
        state = fixture()
        state['catalog']['objects'].append(self.texture('tex:2', 'assets/effects/water.bundle', 'Tex_Water'))
        edit = effects.Editor(state)

        resolved = edit.set_pointer_by_logical(
            'cab:1', ['resource'], r'ASSETS\EFFECTS\WATER.BUNDLE', 'tex_water')

        self.assertEqual(resolved['identity'], 'tex:2')
        self.assertEqual(edit.value('cab:1', ['resource']), dict(m_FileID=0, m_PathID=2))
        exported = edit.export()['objects']
        self.assertEqual(exported[0]['data']['resource'], dict(m_FileID=0, m_PathID=2))

    def test_logical_pointer_edit_uses_the_existing_external_cab_reference(self):
        state = fixture()
        state['catalog']['objects'][0]['references'] = [dict(
            field='$.resource', fileId=1, pathId=22, cab='game-textures-cab',
            isNull=False, resolved=True, identity='game-textures-cab:22', type='Texture2D')]
        state['catalog']['objects'].append(self.texture(
            'game-textures-cab:22', 'assets/effects/shared.bundle', 'Tex_Shared',
            cab='game-textures-cab', path_id=22))
        edit = effects.Editor(state)

        edit.set_pointer_by_logical('cab:1', ['resource'],
                                    'assets/effects/shared.bundle', 'Tex_Shared')

        self.assertEqual(edit.value('cab:1', ['resource']), dict(m_FileID=1, m_PathID=22))
        self.assertEqual(edit.export()['objects'][0]['data']['resource'],
                         dict(m_FileID=1, m_PathID=22))

    def test_logical_pointer_resolution_rejects_ambiguous_missing_and_wrong_type(self):
        state = fixture()
        first = self.texture('tex:2', 'assets/effects/water.bundle', 'Tex_Water')
        second = self.texture('tex:3', 'assets/effects/water.bundle', 'Tex_Foam', path_id=3)
        state['catalog']['objects'].extend((first, second))
        edit = effects.Editor(state)

        with self.assertRaisesRegex(ValueError, '多个来源对象'):
            edit.set_pointer_by_logical('cab:1', ['resource'], 'assets/effects/water.bundle')
        with self.assertRaisesRegex(ValueError, '来源依赖图中未包含'):
            edit.set_pointer_by_logical('cab:1', ['resource'], 'assets/effects/missing.bundle', 'Tex_Missing')
        with self.assertRaisesRegex(ValueError, '目标类型不兼容'):
            effects.resolve_logical_asset(edit.objects.values(), 'Material',
                                          'assets/effects/water.bundle', 'Tex_Water')
        self.assertEqual(edit.value('cab:1', ['resource']), dict(m_FileID=0, m_PathID=0))

    def test_particle_counts_override_only_the_known_imported_auto_rule(self):
        state=fixture()
        for identity,kind in (('cab:2','VFXAutoEmissiveRate'),('cab:3','UnrelatedComponent')):
            state['catalog']['objects'].append(dict(identity=identity,classId=114,script={'class':kind}))
            state['values'][identity]=dict(m_Enabled=1,rateOverTime=4.0,scaleMode=0,axisBitMask=7)
            state['original'][identity]=copy.deepcopy(state['values'][identity]);state['hashes'][identity]='D'*64
        edit=effects.Editor(state);before=copy.deepcopy(state['values'])
        self.assertEqual(edit.use_particle_emission(['cab:1']),[])
        self.assertEqual(edit.use_particle_emission(),['cab:2'])
        self.assertEqual(edit.value('cab:2'),{**before['cab:2'],'m_Enabled':0})
        self.assertEqual(edit.value('cab:1'),before['cab:1'])
        self.assertEqual(edit.value('cab:3'),before['cab:3'])
        self.assertEqual(state['original'],before)
        self.assertEqual(edit.use_particle_emission(),[])
        self.assertEqual([r['identity'] for r in edit.export()['objects']],['cab:2'])
        edit.value('cab:2')['m_Enabled']=1
        self.assertEqual(edit.export()['objects'],[])

    def test_parameters_and_exact_integer_identity(self):
        state = fixture(); edit = effects.Editor(state)
        edit.set_scalar('cab:1', ['EmissionModule','enabled'], True)
        edit.set_scalar('cab:1', ['simulationSpeed'], 2.5)
        edit.set_scalar('cab:1', ['label'], '中文')
        data = json.loads(effects.dumps(edit.export()))
        self.assertEqual(data['objects'][0]['data']['m_GameObject']['m_PathID'], 2**62+7)
        self.assertEqual(data['objects'][0]['data']['label'], '中文')
        self.assertFalse(state['original']['cab:1']['EmissionModule']['enabled'])

    def test_list_growth_removal_independence_and_count(self):
        edit = effects.Editor(fixture()); path = ['EmissionModule','m_Bursts']
        edit.array_change('cab:1', path, 'COPY', 0)
        edit.set_scalar('cab:1', path+[1,'time'], 0.5)
        self.assertEqual(edit.value('cab:1', path)[0]['time'], 0.0)
        self.assertEqual(edit.value('cab:1', ['EmissionModule','m_BurstCount']), 2)
        edit.array_change('cab:1', path, 'REMOVE', 0)
        edit.array_change('cab:1', path, 'REMOVE', 0)
        edit.array_change('cab:1', path, 'ADD')
        self.assertEqual(edit.value('cab:1', path)[0]['time'], 0.0)
        self.assertEqual(edit.value('cab:1', ['EmissionModule','m_BurstCount']), 1)

    def test_invalid_and_ownership_edits_rejected(self):
        edit = effects.Editor(fixture())
        for path, value in [(['m_GameObject','m_PathID'], 0), (['simulationSpeed'], float('nan')),
                            (['simulationSpeed'], 1e40), (['EmissionModule','enabled'], 1),
                            (['EmissionModule','m_BurstCount'], 10)]:
            with self.assertRaises(ValueError): edit.set_scalar('cab:1', path, value)
        self.assertEqual(edit.export()['objects'], [])

    def test_path_escape_rejected(self):
        for path in ('../x', '/x', 'C:/x', 'a\\b', 'a//b'):
            with self.assertRaises(ValueError): effects.safe_path('.', path)

    def test_atomic_edit_export_and_workspace_persistence(self):
        edit = effects.Editor(fixture()); edit.set_scalar('cab:1', ['simulationSpeed'], 3.0)
        restored = effects.Editor(json.loads(effects.dumps(edit.state)))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'test.effedit.json'
            self.assertEqual(effects.write_edits(restored.state, path), 1)
            self.assertEqual(json.loads(path.read_text('utf-8'))['objects'][0]['data']['simulationSpeed'], 3.0)

    def test_edit_document_applies_only_to_matching_source_snapshot(self):
        source = fixture()
        edited = effects.Editor(source)
        edited.set_scalar('cab:1', ['simulationSpeed'], 3.0)
        payload = edited.export()
        restored = fixture()
        effects.apply_edit(restored, payload)
        self.assertEqual(restored['values']['cab:1']['simulationSpeed'], 3.0)
        damaged = dict(payload, sourceEffectSha256='0' * 64)
        with self.assertRaisesRegex(ValueError, 'effect'):
            effects.apply_edit(fixture(), damaged)

    def test_edit_document_refuses_unmaterialized_author_additions(self):
        payload = dict(effects.Editor(fixture()).export(), assets=[])
        payload['assets'] = [dict(identity='cab:99')]
        with self.assertRaisesRegex(ValueError, 'additions'):
            effects.apply_edit(fixture(), payload)


if __name__ == '__main__':
    unittest.main()

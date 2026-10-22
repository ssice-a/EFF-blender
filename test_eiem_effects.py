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
             n('string', 'label', 1), n('Array', 'Array', 2), n('int', 'size', 3), n('char', 'data', 3),
             n('EmissionModule', 'EmissionModule', 1), n('bool', 'enabled', 2), n('int', 'm_BurstCount', 2),
             n('vector', 'm_Bursts', 2), n('Array', 'Array', 3), n('int', 'size', 4),
             n('Burst', 'data', 4), n('float', 'time', 5), n('float', 'probability', 5)]
    value = dict(m_GameObject=dict(m_FileID=0, m_PathID=2**62+7), simulationSpeed=1.0, label='test',
                 EmissionModule=dict(enabled=False, m_BurstCount=1, m_Bursts=[dict(time=0.0, probability=1.0)]))
    obj = dict(identity='cab:1', state='parsed', classId=198, type='ParticleSystem',
               schema='schemas/schema.json', native=dict(cab='cab', pathId=1), references=[])
    return dict(format='EFF_EFFECT_WORKSPACE', version=1, source='test', sourceManifestSha256='A'*64,
                sourceEffectSha256='B'*64, catalog=dict(objects=[obj]), values={'cab:1':value},
                original={'cab:1':copy.deepcopy(value)}, hashes={'cab:1':'C'*64},
                schemas={'schemas/schema.json':dict(nodes=nodes, managedTypes=[])})


class EffectTests(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()

import copy
import unittest

from eiem_effect_clipboard import copy_emitter, copy_module, decode, encode, paste_emitter, paste_module
from eiem_effect_document import Editor


def state():
    nodes = [
        dict(type='ParticleSystem', name='ParticleSystem', level=0,
             metaFlag=0, byteSize=-1, version=1),
        dict(type='bool', name='enabled', level=1,
             metaFlag=0, byteSize=-1, version=1),
        dict(type='float', name='rate', level=1,
             metaFlag=0, byteSize=-1, version=1),
    ]
    first = 'cab:101'
    second = 'cab:202'
    obj = lambda identity: dict(identity=identity, state='parsed', classId=198,
                                type='ParticleSystem', schema='schema.json',
                                native=dict(cab='cab', pathId=int(identity.split(':')[1])),
                                references=[])
    values = {
        first: {'EmissionModule': {'enabled': True, 'rate': 7.0}},
        second: {'EmissionModule': {'enabled': False, 'rate': 1.0}},
    }
    return dict(format='EFF_EFFECT_WORKSPACE', version=1, source='test',
                sourceManifestSha256='A' * 64, sourceEffectSha256='B' * 64,
                catalog=dict(objects=[obj(first), obj(second)], hierarchy=[], root=first),
                values=values, original=copy.deepcopy(values),
                hashes={first: 'C' * 64, second: 'D' * 64},
                schemas={'schema.json': dict(nodes=nodes, managedTypes=[])})


class ClipboardTests(unittest.TestCase):
    def test_module_round_trip_does_not_carry_native_identity(self):
        edit = Editor(state())
        payload = copy_module(edit, 'cab:101', 'EmissionModule')
        restored = decode(encode(payload))
        pasted = paste_module(edit, 'cab:202', restored)
        self.assertEqual(pasted.value('cab:202', ['EmissionModule']),
                         {'enabled': True, 'rate': 7.0})
        self.assertNotIn('m_FileID', restored['value'])
        self.assertNotIn('sourceIdentity', restored['value'])

    def test_invalid_clipboard_is_rejected(self):
        with self.assertRaises(ValueError):
            decode('{"format":"EFF_EFFECT_BLOCK_CLIPBOARD","version":99}')

    def test_complete_visual_emitter_tree_round_trip(self):
        schema = {'nodes': [{'type': 'Object', 'name': 'value', 'level': 0,
                             'metaFlag': 0, 'byteSize': -1, 'version': 1}],
                  'managedTypes': []}
        def spec(identity, class_id, type_name, path_id, references=(), logical=()):
            return dict(identity=identity, state='parsed', classId=class_id,
                        type=type_name, schema='schema.json', name=type_name,
                        native=dict(cab='cab', pathId=path_id), references=list(references),
                        logicalPaths=list(logical))
        def ptr(path_id): return {'m_FileID': 0, 'm_PathID': path_id}
        def ref(field, type_name, identity=''):
            return dict(field=field, type=type_name, identity=identity, cab='cab', fileId=0, pathId=0)
        go1, tr1, ps1, rd1 = 'cab:1', 'cab:2', 'cab:3', 'cab:4'
        go2, tr2, ps2, rd2 = 'cab:5', 'cab:6', 'cab:7', 'cab:8'
        mesh, mat = 'cab:50', 'cab:51'
        objects = [
            spec(go1, 1, 'GameObject', 1, [ref('$.m_Component[0]', 'Component', tr1), ref('$.m_Component[1]', 'Component', ps1), ref('$.m_Component[2]', 'Component', rd1)]),
            spec(tr1, 4, 'Transform', 2, [ref('$.m_GameObject', 'GameObject', go1), ref('$.m_Father', 'Transform'), ref('$.m_Children[0]', 'Transform', tr2)]),
            spec(ps1, 198, 'ParticleSystem', 3, [ref('$.m_GameObject', 'GameObject', go1)]),
            spec(rd1, 199, 'ParticleSystemRenderer', 4, [ref('$.m_GameObject', 'GameObject', go1), ref('$.m_Mesh', 'Mesh', mesh), ref('$.m_Materials[0]', 'Material', mat)]),
            spec(go2, 1, 'GameObject', 5, [ref('$.m_Component[0]', 'Component', tr2), ref('$.m_Component[1]', 'Component', ps2), ref('$.m_Component[2]', 'Component', rd2)]),
            spec(tr2, 4, 'Transform', 6, [ref('$.m_GameObject', 'GameObject', go2), ref('$.m_Father', 'Transform', tr1)]),
            spec(ps2, 198, 'ParticleSystem', 7, [ref('$.m_GameObject', 'GameObject', go2)]),
            spec(rd2, 199, 'ParticleSystemRenderer', 8, [ref('$.m_GameObject', 'GameObject', go2), ref('$.m_Mesh', 'Mesh', mesh), ref('$.m_Materials[0]', 'Material', mat)]),
            spec(mesh, 43, 'Mesh', 50, logical=[dict(path='game/mesh', name='SharedMesh')]),
            spec(mat, 21, 'Material', 51, logical=[dict(path='game/material', name='SharedMaterial')]),
        ]
        values = {
            go1: {'m_Name': 'Emitter', 'm_Component': [ptr(2), ptr(3), ptr(4)]},
            tr1: {'m_GameObject': ptr(1), 'm_Father': ptr(0), 'm_Children': [ptr(6)]},
            ps1: {'m_GameObject': ptr(1), 'looping': True, 'EmissionModule': {'enabled': True, 'rate': 3.0}},
            rd1: {'m_GameObject': ptr(1), 'm_Mesh': ptr(50), 'm_Materials': [ptr(51)], 'm_Enabled': 1},
            go2: {'m_Name': 'ChildEmitter', 'm_Component': [ptr(6), ptr(7), ptr(8)]},
            tr2: {'m_GameObject': ptr(5), 'm_Father': ptr(2), 'm_Children': []},
            ps2: {'m_GameObject': ptr(5), 'looping': False, 'EmissionModule': {'enabled': True, 'rate': 6.0}},
            rd2: {'m_GameObject': ptr(5), 'm_Mesh': ptr(50), 'm_Materials': [ptr(51)], 'm_Enabled': 1},
        }
        state_data = dict(format='EFF_EFFECT_WORKSPACE', version=1, source='test',
                          sourceManifestSha256='A' * 64, sourceEffectSha256='B' * 64,
                          catalog=dict(objects=objects, hierarchy=[
                              dict(identity=go1, name='Emitter', transform=tr1, parent=None, children=[tr2], components=[tr1, ps1, rd1]),
                              dict(identity=go2, name='ChildEmitter', transform=tr2, parent=tr1, children=[], components=[tr2, ps2, rd2])], root=go1),
                          values=values, original=copy.deepcopy(values),
                          hashes={identity: 'C' * 64 for identity in values},
                          schemas={'schema.json': schema})
        edit = Editor(state_data)
        payload = copy_emitter(edit, go1)
        restored = decode(encode(payload))
        self.assertEqual(len(restored['nodes']), 2)
        self.assertTrue(all(set(row['value'].keys()) for row in restored['objects']))
        game_object = next(row for row in restored['objects'] if row['classId'] == 1)
        self.assertEqual(game_object['value']['m_Component'][0], {'m_FileID': 0, 'm_PathID': 0})
        pasted, nodes = paste_emitter(edit, go1, restored)
        self.assertEqual(len(nodes), 2)
        self.assertEqual(nodes[1]['parent'], nodes[0]['transform'])
        self.assertIn(int(nodes[0]['transform'].split(':')[1]),
                      [p['m_PathID'] for p in pasted.value(tr1, ['m_Children'])])
        renderer = next(identity for identity in nodes[0]['components'] if pasted.objects[identity]['classId'] == 199)
        self.assertEqual(pasted.value(renderer, ['m_Mesh']), ptr(50))
        child_transform = nodes[1]['transform']
        self.assertEqual(pasted.value(child_transform, ['m_Father']), ptr(int(nodes[0]['transform'].split(':')[1])))


if __name__ == '__main__':
    unittest.main()

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import eiem_effect_document as document
import eiem_effect_game_resources as game_resources
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
    def test_value_transfer_accepts_metadata_changes_but_rejects_field_changes(self):
        import eiem_effect_clipboard as clipboard
        edit = Editor(state())
        source = dict(schemaSha256=clipboard._schema_hash(edit, 'cab:101'),
                      schemaShapeSha256=clipboard._schema_shape_hash(edit, 'cab:101'))
        nodes = edit.state['schemas']['schema.json']['nodes']
        nodes[0].update(metaFlag=32768, version=9, byteSize=42)
        self.assertNotEqual(source['schemaSha256'], clipboard._schema_hash(edit, 'cab:101'))
        self.assertTrue(clipboard._schema_matches(edit, 'cab:101', source))
        nodes[1]['name'] = 'different_field'
        self.assertFalse(clipboard._schema_matches(edit, 'cab:101', source))
        nodes[1]['name'] = 'enabled'
        nodes[1]['type'] = 'float'
        self.assertFalse(clipboard._schema_matches(edit, 'cab:101', source))

    def test_native_emitter_json_preserves_byte_arrays_and_booleans(self):
        from nativepack import type_tree
        nodes = [
            dict(type='Renderer', name='Base', level=0, metaFlag=0),
            dict(type='bool', name='m_Enabled', level=1, metaFlag=0),
            dict(type='vector', name='m_VertexStreams', level=1, metaFlag=0),
            dict(type='Array', name='Array', level=2, metaFlag=0),
            dict(type='int', name='size', level=3, metaFlag=0),
            dict(type='UInt8', name='data', level=3, metaFlag=0),
        ]
        data = dict(m_Enabled=True, m_VertexStreams=dict(encoding='base64', bytes='AAH/'))
        schema = type_tree.Schema.compile(nodes)
        tree = type_tree.from_data(schema, document._native_input(data))
        restored = document._native_plain(tree, encode_bytes=True)
        self.assertEqual(restored, data)
        self.assertIs(restored['m_Enabled'], True)
        self.assertEqual(document._native_input(restored)['m_VertexStreams'], b'\x00\x01\xff')
        self.assertEqual(json.loads(document.dumps(restored)), data)

    def test_restored_game_material_lease_keeps_authored_texture_material(self):
        renderer, original, authored = 'cab:4', 'cab:51', 'cab:52'
        objects = [
            dict(identity=renderer, classId=199, native=dict(cab='cab',pathId=4), references=[]),
            dict(identity=original, classId=21, type='Material', name='Flow', native=dict(cab='cab',pathId=51),
                 references=[], logicalPaths=[dict(path='assets/game/flow.mat',name='Flow')]),
            dict(identity=authored, classId=21, type='Material', name='LocalFlow', native=dict(cab='cab',pathId=52),
                 template=original, references=[], logicalPaths=[])]
        data = dict(format='EFF_EFFECT_WORKSPACE',version=1,source='unused',
                    catalog=dict(objects=objects,hierarchy=[]),
                    values={renderer:dict(m_Materials=[dict(m_FileID=0,m_PathID=52)])},
                    author_assets=[dict(identity=authored,template=original)])
        row=dict(owner=renderer,field='$.m_Materials[0]',path='assets/game/flow.mat',asset='Flow',type='Material',previewSource='model.eff')
        edit=Editor(data)
        with patch.object(document,'load_source',side_effect=ValueError('ordinary model source')):
            game_resources.restore(edit,[row])
        self.assertEqual(edit.value(renderer,['m_Materials',0]),dict(m_FileID=0,m_PathID=52))
        self.assertEqual(edit.objects[renderer]['references'][0]['gameLogical']['path'],'assets/game/flow.mat')
        edit.value(renderer)['m_Materials'][0]['m_PathID']=999
        with patch.object(document,'load_source',side_effect=ValueError('ordinary model source')):
            game_resources.restore(edit,[row])
        self.assertEqual(edit.value(renderer,['m_Materials',0])['m_PathID'],51)

    def test_module_round_trip_does_not_carry_native_identity(self):
        edit = Editor(state())
        payload = copy_module(edit, 'cab:101', 'EmissionModule')
        restored = decode(encode(payload))
        pasted = paste_module(edit, 'cab:202', restored)
        self.assertEqual(pasted.value('cab:202', ['EmissionModule']),
                         {'enabled': True, 'rate': 7.0})
        self.assertNotIn('m_FileID', restored['value'])
        self.assertNotIn('sourceIdentity', encode(restored))

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

    def test_cross_root_emitter_copy_rebinds_game_assets_by_logical_path(self):
        schema = {'nodes': [{'type': 'Object', 'name': 'value', 'level': 0,
                             'metaFlag': 0, 'byteSize': -1, 'version': 1}],
                  'managedTypes': []}

        def spec(identity, class_id, type_name, cab, path_id, name='', references=(), logical=()):
            return dict(identity=identity, state='parsed', classId=class_id, type=type_name,
                        schema='schema.json', name=name or type_name,
                        native=dict(cab=cab, pathId=path_id), references=list(references),
                        logicalPaths=list(logical))

        def ptr(path_id, file_id=0):
            return {'m_FileID': file_id, 'm_PathID': path_id}

        def ref(field, type_name, identity='', cab='', file_id=0, path_id=0):
            return dict(field=field, type=type_name, identity=identity, cab=cab,
                        fileId=file_id, pathId=path_id)

        def effect_state(cab, source, include_resources):
            go, transform, particle, renderer = [cab + ':' + str(i) for i in range(1, 5)]
            mesh, material = 'gamecab:50', 'gamecab:51'
            external_file = 1 if include_resources else 0
            mesh_path = 'assets/gameplay/effects/meshes/fx_star'
            material_path = 'assets/gameplay/effects/materials/fx_star'
            objects = [
                spec(go, 1, 'GameObject', cab, 1, 'Emitter', [
                    ref('$.m_Component[0]', 'Component', transform, cab),
                    ref('$.m_Component[1]', 'Component', particle, cab),
                    ref('$.m_Component[2]', 'Component', renderer, cab)]),
                spec(transform, 4, 'Transform', cab, 2, references=[
                    ref('$.m_GameObject', 'GameObject', go, cab),
                    ref('$.m_Father', 'Transform'),
                    *([ref('$.m_Children[0]', 'Transform', cab + ':6', cab)] if include_resources else [])]),
                spec(particle, 198, 'ParticleSystem', cab, 3, references=[
                    ref('$.m_GameObject', 'GameObject', go, cab),
                    ref('$.ShapeModule.m_Mesh', 'Mesh', mesh, 'gamecab', external_file, 50)] if include_resources else [
                    ref('$.m_GameObject', 'GameObject', go, cab)]),
                spec(renderer, 199, 'ParticleSystemRenderer', cab, 4, references=[
                    ref('$.m_GameObject', 'GameObject', go, cab),
                    ref('$.m_Mesh', 'Mesh', mesh, 'gamecab', external_file, 50),
                    ref('$.m_Materials[0]', 'Material', material, 'gamecab', external_file, 51)]),
            ]
            values = {
                go: {'m_Name': 'Emitter', 'm_Component': [ptr(2), ptr(3), ptr(4)]},
                transform: {'m_GameObject': ptr(1), 'm_Father': ptr(0), 'm_Children': []},
                particle: {'m_GameObject': ptr(1), 'looping': True,
                           'ShapeModule': {'m_Mesh': ptr(50, external_file)},
                           'EmissionModule': {'enabled': True, 'rate': 3.0}},
                renderer: {'m_GameObject': ptr(1), 'm_Mesh': ptr(50, external_file),
                           'm_Materials': [ptr(51, external_file)], 'm_Enabled': 1},
            }
            if include_resources:
                logical_mesh = [dict(path=mesh_path, name='FxStarMesh')]
                logical_material = [dict(path=material_path, name='FxStarMaterial')]
                objects.extend([
                    spec(mesh, 43, 'Mesh', 'gamecab', 50, 'FxStarMesh',
                         logical=logical_mesh),
                    spec(material, 21, 'Material', 'gamecab', 51, 'FxStarMaterial',
                         logical=logical_material),
                ])
                values.update({mesh: {}, material: {}})
            state_data = dict(format='EFF_EFFECT_WORKSPACE', version=1, source=source,
                              sourceManifestSha256='A' * 64, sourceEffectSha256='B' * 64,
                              catalog=dict(objects=objects, hierarchy=[dict(
                                  identity=go, name='Emitter', transform=transform,
                                  parent=None, children=[], components=[transform, particle, renderer])],
                                  root=go),
                              values=values, original=copy.deepcopy(values),
                              hashes={identity: 'C' * 64 for identity in values},
                              schemas={'schema.json': schema})
            return state_data, (go, transform, particle, renderer), (mesh_path, material_path)

        source_state, source_ids, logical_paths = effect_state('sourcecab', 'game-source', True)
        game_state, _, _ = effect_state('gamecab', 'game-source', True)
        destination_base, destination_ids, _ = effect_state('destcab', 'destination', False)
        source_edit = Editor(source_state)
        payload = decode(encode(copy_emitter(source_edit, source_ids[0])))

        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary)
            (destination / 'source').mkdir()
            (destination / 'source' / 'manifest.json').write_text(json.dumps({
                'serializedFiles': [{'cab': 'destcab', 'externals': []}]
            }), encoding='utf-8')
            destination_base['source'] = str(destination)
            edit = Editor(destination_base)
            with patch.object(document, 'load_source', return_value=game_state):
                pasted, nodes = paste_emitter(edit, destination_ids[0], payload)

        renderer = next(identity for identity in nodes[0]['components']
                        if pasted.objects[identity]['classId'] == 199)
        self.assertEqual(pasted.value(renderer, ['m_Mesh']), ptr(50, 1))
        self.assertEqual(pasted.value(renderer, ['m_Materials', 0]), ptr(51, 1))
        refs = {row['field']: row for row in pasted.objects[renderer]['references']}
        self.assertEqual(refs['$.m_Mesh']['gameLogical']['path'], logical_paths[0])
        self.assertEqual(refs['$.m_Materials[0]']['gameLogical']['path'], logical_paths[1])

        # Blender stores the native document in the .blend. Reopening that
        # serialized state must still produce logical bindings for the builder.
        reopened = Editor(json.loads(document.dumps(pasted.state)))
        bindings = reopened.export()['gameReferences']
        self.assertEqual({row['path'] for row in bindings}, set(logical_paths))

    def test_cross_root_module_copy_sanitizes_and_rebinds_game_resource(self):
        schema = {'nodes': [{'type': 'Object', 'name': 'value', 'level': 0,
                             'metaFlag': 0, 'byteSize': -1, 'version': 1}],
                  'managedTypes': []}
        game_path = 'assets/gameplay/effects/meshes/fx_star'
        game_identity = 'gamecab:50'

        def ps_state(cab, source, has_mesh):
            identity = cab + ':1'
            pointer = {'m_FileID': 1, 'm_PathID': 50} if has_mesh else {'m_FileID': 0, 'm_PathID': 0}
            ps = dict(identity=identity, state='parsed', classId=198, type='ParticleSystem',
                      schema='schema.json', name='Particles', native=dict(cab=cab, pathId=1),
                      references=[dict(field='$.ShapeModule.m_Mesh', type='Mesh',
                                       identity=game_identity if has_mesh else '', cab='gamecab',
                                       fileId=1 if has_mesh else 0, pathId=50 if has_mesh else 0,
                                       gameLogical=dict(path=game_path, asset='FxStarMesh', type='Mesh',
                                                        previewSource='game-source') if has_mesh else None)])
            state_data = dict(format='EFF_EFFECT_WORKSPACE', version=1, source=source,
                              sourceManifestSha256='D' * 64, sourceEffectSha256='E' * 64,
                              catalog=dict(objects=[ps], hierarchy=[dict(identity=identity, name='Particles',
                                  transform=identity, parent=None, children=[], components=[identity])], root=identity),
                              values={identity: {'ShapeModule': {'m_Mesh': pointer}}},
                              original={identity: {'ShapeModule': {'m_Mesh': copy.deepcopy(pointer)}}},
                              hashes={identity: 'F' * 64}, schemas={'schema.json': schema})
            return state_data, identity

        source_state, source_ps = ps_state('sourcecab', 'game-source', True)
        destination_state, destination_ps = ps_state('destcab', 'destination', False)
        mesh = dict(identity=game_identity, state='parsed', classId=43, type='Mesh', schema='schema.json',
                    name='FxStarMesh', native=dict(cab='gamecab', pathId=50), references=[],
                    logicalPaths=[dict(path=game_path, name='FxStarMesh')])
        game_state = dict(format='EFF_EFFECT_WORKSPACE', version=1, source='game-source',
                          catalog=dict(objects=[mesh], hierarchy=[], root=game_identity),
                          values={game_identity: {}}, original={game_identity: {}},
                          hashes={game_identity: '0' * 64}, schemas={'schema.json': schema})
        payload = decode(encode(copy_module(Editor(source_state), source_ps, 'ShapeModule')))
        self.assertEqual(payload['value']['m_Mesh'], {'m_FileID': 0, 'm_PathID': 0})
        self.assertNotIn('sourceIdentity', encode(payload))

        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary)
            (destination / 'source').mkdir()
            (destination / 'source' / 'manifest.json').write_text(json.dumps({
                'serializedFiles': [{'cab': 'destcab', 'externals': []}]
            }), encoding='utf-8')
            destination_state['source'] = str(destination)
            with patch.object(document, 'load_source', return_value=game_state):
                pasted = paste_module(Editor(destination_state), destination_ps, payload)
            unavailable = copy.deepcopy(game_state)
            unavailable['catalog'].update(objects=[], hierarchy=[], root='')
            unavailable.update(values={}, original={}, hashes={})
            with patch.object(document, 'load_source', return_value=unavailable):
                logical_only = paste_module(Editor(destination_state), destination_ps, payload)

        self.assertEqual(pasted.value(destination_ps, ['ShapeModule', 'm_Mesh']),
                         {'m_FileID': 1, 'm_PathID': 50})
        self.assertEqual(pasted.export()['gameReferences'][0]['path'], game_path)
        self.assertEqual(logical_only.value(destination_ps, ['ShapeModule', 'm_Mesh']),
                         {'m_FileID': 0, 'm_PathID': 0})
        exported = logical_only.export()
        self.assertEqual(exported['gameReferences'][0]['path'], game_path)
        self.assertEqual(exported['objects'][0]['identity'], destination_ps)


if __name__ == '__main__':
    unittest.main()

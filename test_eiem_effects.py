import copy
import base64
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

import eiem_effect_document as effects
from nativepack import type_tree


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


def png_preview():
    def chunk(kind, data):
        body = kind + data
        return struct.pack('>I', len(data)) + body + struct.pack('>I', zlib.crc32(body))
    header = struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) +
            chunk(b'IDAT', zlib.compress(b'\x00\xff\x00\x00\xff\xff')) +
            chunk(b'IEND', b''))


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

    def authored_asset_fixture(self, directory):
        source = Path(directory)
        (source / 'source').mkdir(parents=True)
        (source / 'schemas').mkdir()
        nodes = [dict(type='Texture2D', name='Base', level=0, metaFlag=0),
                 dict(type='string', name='m_Name', level=1, metaFlag=0),
                 dict(type='int', name='width', level=1, metaFlag=0)]
        schema_file = source / 'schemas' / 'texture.json'
        schema_bytes = json.dumps(dict(nodes=nodes), separators=(',', ':')).encode()
        schema_file.write_bytes(schema_bytes)
        schema = type_tree.Schema.compile(nodes)
        original = type_tree.encode(type_tree.from_data(
            schema, dict(m_Name='source texture', width=1)))
        authored = type_tree.encode(type_tree.from_data(
            schema, dict(m_Name='authored texture', width=2)))
        native_file = source / 'source' / 'native.bin'
        native_file.write_bytes(original)
        state = fixture()
        state['source'] = str(source)
        state['schemas']['schemas/texture.json'] = dict(nodes=nodes, managedTypes=[])
        texture = dict(identity='cab:22', name='source texture', state='parsed',
                       classId=28, type='Texture2D', schema='schemas/texture.json',
                       native=dict(cab='cab', pathId=22, offset=0, size=len(original)),
                       logicalPaths=[], references=[])
        state['catalog']['objects'].append(texture)
        state['values']['cab:22'] = dict(m_Name='source texture', width=1)
        state['original']['cab:22'] = copy.deepcopy(state['values']['cab:22'])
        state['hashes']['cab:22'] = 'D' * 64
        manifest = dict(format='EFFSOURCE', version=1, serializedFiles=[dict(
            cab='cab', snapshot=dict(file='source/native.bin',
                                     sha256=hashlib.sha256(original).hexdigest().upper()),
            objects=[dict(pathId=22)])])
        (source / 'source' / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        receipt = dict(files={'schemas/texture.json': hashlib.sha256(schema_bytes).hexdigest().upper()})
        (source / 'source' / 'generated-files.json').write_text(json.dumps(receipt), encoding='utf-8')
        row = dict(identity='cab:99', template='cab:22',
                   sourceObjectSha256=hashlib.sha256(original).hexdigest().upper(),
                   payloadBase64=base64.b64encode(authored).decode('ascii'))
        return state, row, authored

    def test_edit_document_restores_authored_assets(self):
        with tempfile.TemporaryDirectory() as temp:
            state, asset, authored = self.authored_asset_fixture(temp)
            asset['previewPngBase64'] = base64.b64encode(png_preview()).decode('ascii')
            payload = dict(format='EFF_EFFECT_EDIT', version=1,
                           sourceManifestSha256=state['sourceManifestSha256'],
                           sourceEffectSha256=state['sourceEffectSha256'],
                           objects=[], assets=[asset])
            effects.apply_edit(state, payload)
            self.assertIn('cab:99', effects.Editor(state).objects)
            self.assertEqual(state['values']['cab:99'],
                             dict(m_Name='authored texture', width=2))
            self.assertEqual(state['author_assets'], [asset])
            self.assertNotIn('emitters', effects.Editor(state).export())
            from eiem_effect_native import Reader
            tree, raw = Reader(effects.Editor(state)).tree('cab:99')
            self.assertEqual(raw, authored)
            self.assertEqual(tree.plain(), dict(m_Name='authored texture', width=2))

    def test_invalid_author_texture_preview_is_rejected_without_partial_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            state, asset, _ = self.authored_asset_fixture(temp)
            asset['previewPngBase64'] = base64.b64encode(b'not a PNG').decode('ascii')
            payload = dict(format='EFF_EFFECT_EDIT', version=1,
                           sourceManifestSha256=state['sourceManifestSha256'],
                           sourceEffectSha256=state['sourceEffectSha256'],
                           objects=[], assets=[asset])
            before = copy.deepcopy(state)
            with self.assertRaisesRegex(ValueError, 'PNG'):
                effects.apply_edit(state, payload)
            self.assertEqual(state, before)

    def test_invalid_authored_asset_is_rejected_without_partial_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            state, asset, _ = self.authored_asset_fixture(temp)
            before = copy.deepcopy(state)
            asset['sourceObjectSha256'] = '0' * 64
            payload = dict(format='EFF_EFFECT_EDIT', version=1,
                           sourceManifestSha256=state['sourceManifestSha256'],
                           sourceEffectSha256=state['sourceEffectSha256'],
                           objects=[], assets=[asset])
            with self.assertRaisesRegex(ValueError, '摘要'):
                effects.apply_edit(state, payload)
            self.assertEqual(state, before)


if __name__ == '__main__':
    unittest.main()

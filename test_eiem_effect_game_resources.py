import copy
import json
from pathlib import Path
import tempfile
import unittest

import eiem_effect_document as document
import eiem_effect_game_resources as resources


class PortableGameResourceTests(unittest.TestCase):
    def test_new_game_resource_accepts_only_a_logical_source_prefab_without_local_donor(self):
        owner='local:1'
        values={owner:dict(texture=dict(m_FileID=0,m_PathID=0))}
        edit=document.Editor(dict(format='EFF_EFFECT_WORKSPACE',version=1,source='missing-author-source',
             catalog=dict(objects=[dict(identity=owner,classId=21,type='Material',references=[])],hierarchy=[]),
             values=values,original=copy.deepcopy(values)))
        resources.bind_logical(edit,owner,['texture'],'assets/game/star.png',expected='Texture',source_prefab='assets/game/donor.prefab')
        row=resources.bindings(edit)[0]
        self.assertEqual(row['sourcePrefab'],'assets/game/donor.prefab')
        self.assertNotIn('sourceRoot',row)
        self.assertNotIn('resourceIdentity',row)

    def test_imported_native_identity_is_portable_without_preview_or_index(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            (source / 'source').mkdir()
            manifest = dict(format='EFFSOURCE', prefab='assets/game/effect.prefab', root='cab:1',
                            vfsFingerprint='generation', hierarchy=[dict(identity='cab:1', name='Root')],
                            containerEntries=[dict(logicalPath='assets/game/effect.prefab', asset=dict(identity='cab:1'))])
            (source / 'source/manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
            target = dict(identity='CAB-'+'1'*32+':7', classId=28,
                          logicalPaths=[dict(path='assets/game/star.png', name='Star')])
            logical = dict(path='assets/game/star.png', asset='Star', type='Texture')
            binding = resources.portable_binding(logical, source, target)
            self.assertEqual(binding['resourceIdentity'], target['identity'])
            self.assertEqual(binding['resourceClassId'], 28)
            (source / 'source/manifest.json').unlink()
            self.assertEqual(resources.portable_binding(binding, source), binding)
            self.assertNotIn('resourceIdentity', resources.portable_binding(
                dict(logical, path='assets/game/other.png'), '', target))

    def test_source_proof_uses_exact_container_asset_and_survives_donor_removal(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            (source / 'source').mkdir()
            manifest = dict(format='EFFSOURCE', prefab='assets/game/effect.prefab', root='cab:1',
                            vfsFingerprint='generation', hierarchy=[dict(identity='cab:1', name='Root')],
                            containerEntries=[dict(logicalPath='assets/game/effect.prefab', asset=dict(identity='cab:1'))])
            path = source / 'source/manifest.json'
            path.write_text(json.dumps(manifest), encoding='utf-8')
            proof = resources.source_proof(source)
            self.assertEqual(proof['sourceRootIdentity'], 'cab:1')
            binding = dict(path='assets/game/star.png', asset='Star', type='Texture', **proof)
            path.unlink()
            self.assertEqual(resources.portable_binding(binding, source), binding)

    def test_preloaded_child_is_not_a_prefab_root_proof(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            (source / 'source').mkdir()
            manifest = dict(format='EFFSOURCE', prefab='assets/game/effect.prefab', root='cab:2',
                            vfsFingerprint='generation', hierarchy=[dict(identity='cab:2', name='Child')],
                            containerEntries=[dict(logicalPath='assets/game/effect.prefab', asset=dict(identity='cab:1'))])
            (source / 'source/manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'exact Prefab container'):
                resources.source_proof(source)

    def test_missing_preview_donor_keeps_logical_binding_and_all_source_proof_fields(self):
        owner = 'local:1'
        spec = dict(identity=owner, classId=21, type='Material', native=dict(cab='local', pathId=1), references=[])
        values = {owner: dict(m_Texture=dict(m_FileID=0, m_PathID=0))}
        edit = document.Editor(dict(format='EFF_EFFECT_WORKSPACE', version=1,
                                    source='no-donor-here', catalog=dict(objects=[spec], hierarchy=[]),
                                    values=values, original=copy.deepcopy(values)))
        row = dict(owner=owner, field='$.m_Texture', path='assets/game/star.png', asset='Star', type='Texture',
                   sourcePrefab='assets/game/effect.prefab', sourceRoot='Root', sourceRootIdentity='game:1',
                   sourceVfsFingerprint='generation')
        resources.restore(edit, [row])
        self.assertEqual(resources.bindings(edit), [row])


if __name__ == '__main__':
    unittest.main()

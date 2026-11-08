"""Exercise standalone effect-only Format 3 publication inputs."""
import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
PACKAGE_NAME = '_eiem_effect_export_tests'
package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(Path(__file__).parent)]
sys.modules[PACKAGE_NAME] = package


def load_module(name):
    path = Path(__file__).parent / (name + '.py')
    spec = importlib.util.spec_from_file_location(PACKAGE_NAME + '.' + name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


eiem_format = load_module('eiem_format')
eiem_effect_export = load_module('eiem_effect_export')
from tools.nativepack import author_format3


class StandaloneEffectExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        (self.source / 'source').mkdir(parents=True)
        (self.source / 'effect.json').write_text('{}', encoding='utf-8')
        (self.source / 'source/manifest.json').write_text('{}', encoding='utf-8')
        self.staging = self.root / 'staging'
        self.staging.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def effect(self, identity='WeaponFx'):
        attachment = dict(
            id=identity, label='Weapon FX', part='', bone='', autoplay=True,
            keys=[], position=[0, 0, 0], rotation=[0, 0, 0, 1], scale=[1, 1, 1],
            targetPrefab='assets/game/weapon.prefab', rendererPath='Weapon/Renderer',
            targetPath='assets/game/weapon.mesh', targetAsset='WeaponMesh', bones=[])
        workspace = dict(format='EFF_EFFECT_WORKSPACE', version=1,
                         catalog={}, schemas={}, values={})
        data = b'native effect bundle'
        native = dict(key='EffectBuild123', prefab=attachment['targetPrefab'],
                      rootName='WeaponFx', root=0, particles=1,
                      bundles=[dict(file='0.bundle', hash=hashlib.sha256(data).hexdigest().upper(),
                                    dependencies=[], data=data)])
        return dict(attachment, build='unused-build', _native=native,
                    _source=str(self.source),
                    _authorData=eiem_format.encode_format3_effect_source(
                        self.source, workspace, attachment))

    def test_writes_a_self_contained_effect_only_mod(self):
        manifest = eiem_effect_export.write_effect_directory(
            self.staging, 'WeaponEffects', [self.effect()], author_format3,
            lambda _path: None, eiem_format)

        self.assertEqual(manifest['modType'], 'effect')
        self.assertIn('controls.effres', manifest['_files'])
        self.assertIn('effects/EffectBuild123/0.effres', manifest['_files'])
        ini = (self.staging / 'mod.ini').read_text(encoding='utf-8')
        self.assertIn('type=effect', ini)
        self.assertIn('[EffectAttachmentWeaponFx]', ini)
        self.assertNotIn('[Replace', ini)
        self.assertEqual(author_format3.read_format3_directory(self.staging)['modType'], 'effect')

    def test_rejects_duplicate_effect_identity_before_writing(self):
        with self.assertRaisesRegex(ValueError, '重复'):
            eiem_effect_export.write_effect_directory(
                self.staging, 'WeaponEffects',
                [self.effect(), self.effect()], author_format3,
                lambda _path: None, eiem_format)
        self.assertFalse((self.staging / 'mod.ini').exists())

    def test_writes_game_prefab_replacement_by_logical_path(self):
        effect = self.effect()
        effect['mode'] = 'replace_game_prefab'
        effect['replacementPrefab'] = 'assets/game/effects/skill.prefab'
        effect['targetPrefab'] = effect['replacementPrefab']
        effect['rendererPath'] = ''
        effect['targetPath'] = ''
        effect['targetAsset'] = ''
        effect['bones'] = []
        snapshot_attachment = {key: value for key, value in effect.items()
                              if not key.startswith('_') and key != 'build'}
        effect['_authorData'] = eiem_format.encode_format3_effect_source(
            self.source, dict(format='EFF_EFFECT_WORKSPACE', version=1,
                              catalog={}, schemas={}, values={}), snapshot_attachment)
        manifest = eiem_effect_export.write_effect_directory(
            self.staging, 'EffectReplacement', [effect], author_format3,
            lambda _path: None, eiem_format)
        self.assertEqual(manifest['modType'], 'effect')
        self.assertEqual(author_format3.read_format3_directory(self.staging)['modType'], 'effect')
        controls = (self.staging / 'controls.effres').read_bytes()
        self.assertIn(b'assets/game/effects/skill.prefab', controls)
        self.assertNotIn(b'assets/game/weapon.mesh', controls)

    def test_rejects_unknown_effect_usage_mode(self):
        effect = self.effect()
        effect['mode'] = 'replace_one_callsite'
        with self.assertRaisesRegex(ValueError, '特效用途值无效'):
            eiem_effect_export.write_effect_directory(
                self.staging, 'InvalidEffect', [effect], author_format3,
                lambda _path: None, eiem_format)
        self.assertFalse((self.staging / 'mod.ini').exists())

    def test_rejects_snapshot_with_different_target_proof(self):
        effect = self.effect()
        effect['targetPath'] = 'assets/game/other.mesh'
        with self.assertRaisesRegex(ValueError, '来源证明不一致'):
            eiem_effect_export.write_effect_directory(
                self.staging, 'WeaponEffects', [effect], author_format3,
                lambda _path: None, eiem_format)
        self.assertFalse((self.staging / 'mod.ini').exists())

    def test_effect_author_snapshot_keeps_the_incremental_edit_document(self):
        edit = dict(format='EFF_EFFECT_EDIT', version=1, objects=[],
                    sourceManifestSha256='A' * 64, sourceEffectSha256='B' * 64,
                    assets=[dict(identity='source:99', template='source:22',
                                 sourceObjectSha256='A' * 64, payloadBase64='AQ==')],
                    emitters=dict(added=[], removed=[]))
        effect = self.effect()
        attachment = {key: value for key, value in effect.items()
                      if not key.startswith('_') and key != 'build'}
        workspace = dict(format='EFF_EFFECT_WORKSPACE', version=1, catalog={},
                         source=str(self.source), sourceManifestSha256='A' * 64,
                         sourceEffectSha256='B' * 64)
        encoded = eiem_format.encode_format3_effect_author(
            self.source, workspace, attachment, edit)
        decoded = eiem_format.decode_format3_effect_snapshot(encoded)
        self.assertEqual(decoded['edit'], edit)
        self.assertNotIn('source', decoded['workspace'])
        self.assertNotIn('source', decoded['edit'])
        self.assertEqual(decoded['workspace']['format'], 'EFF_EFFECT_WORKSPACE')

    def test_effect_author_snapshot_omits_private_runtime_attachment_fields(self):
        attachment = dict(
            id='EffectReplacement', mode='replace_game_prefab',
            replacementPrefab='assets/game/effects/skill.prefab',
            targetPrefab='assets/game/effects/skill.prefab',
            rendererPath='', targetPath='', targetAsset='',
            _source=str(self.source),
            _native={'bundles': [{'data': b'native bundle bytes'}]},
        )

        encoded = eiem_format.encode_format3_effect_source(
            self.source,
            dict(format='EFF_EFFECT_WORKSPACE', version=1, catalog={}),
            attachment,
        )
        decoded = eiem_format.decode_format3_effect_snapshot(encoded)

        self.assertEqual(
            decoded['attachment']['replacementPrefab'],
            'assets/game/effects/skill.prefab',
        )
        self.assertNotIn('_source', decoded['attachment'])
        self.assertNotIn('_native', decoded['attachment'])

    def test_effect_author_snapshot_rejects_a_mismatched_baseline(self):
        workspace = dict(format='EFF_EFFECT_WORKSPACE', version=1,
                         sourceManifestSha256='A' * 64,
                         sourceEffectSha256='B' * 64)
        edit = dict(format='EFF_EFFECT_EDIT', version=1,
                    sourceManifestSha256='C' * 64,
                    sourceEffectSha256='B' * 64)
        with self.assertRaisesRegex(ValueError, 'source digest differs'):
            eiem_format.encode_format3_effect_author(
                self.source, workspace, self.effect(), edit)


if __name__ == '__main__':
    unittest.main()

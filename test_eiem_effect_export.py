"""Exercise standalone effect-only Format 3 publication inputs."""
import hashlib
import copy
import io
import json
import struct
import zipfile
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

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
eiem_effect_publish = load_module('eiem_effect_publish')
eiem_effect_mount_targets = load_module('eiem_effect_mount_targets')
eiem_shared_resources = load_module('eiem_shared_resources')
from tools.nativepack import author_format3


class StandaloneEffectExportTests(unittest.TestCase):
    def test_deployed_compiler_selection_and_explicit_override(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {'LOCALAPPDATA':temporary}, clear=True):
            executable=Path(temporary)/'AnimeStudio.GUI.exe';executable.write_bytes(b'deployed')
            eiem_shared_resources.set_anime_studio(executable)
            self.assertEqual(eiem_effect_publish.compiler_path(),executable)
            other=Path(temporary)/'other.exe';other.write_bytes(b'explicit')
            self.assertEqual(eiem_effect_publish.compiler_path(str(other)),other)
            with self.assertRaises(ValueError):eiem_effect_publish.compiler_path(str(other.with_name('missing.exe')))
            eiem_shared_resources.set_resource_root(Path(temporary)/'resources')
            self.assertEqual(eiem_shared_resources.anime_studio(),executable)

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

    def effect(self, identity='WeaponFx', renderer='Weapon/Renderer'):
        attachment = dict(
            id=identity, label='Weapon FX', part='', bone='', autoplay=True,
            keys=[], position=[0, 0, 0], rotation=[0, 0, 0, 1], scale=[1, 1, 1],
            targetPrefab='assets/game/weapon.prefab', rendererPath=renderer,
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
        self.assertNotIn('controls.effres', manifest['_files'])
        self.assertIn('effects/Weapon_FX_0.effres', manifest['_files'])
        self.assertIn('effects/author/Weapon_FX.effres', manifest['_files'])
        ini = (self.staging / 'mod.ini').read_text(encoding='utf-8')
        self.assertIn('controls=ini', ini)
        self.assertIn('bundles=ResourceEffect_Weapon_FX_0', ini)
        self.assertIn('type=effect', ini)
        self.assertIn('[AttachEffectWeaponFx]', ini)
        self.assertNotIn('mode=', ini)
        self.assertNotRegex(ini, r'[A-Fa-f0-9]{16,}')
        self.assertNotIn('[Replace', ini)
        self.assertEqual(author_format3.read_format3_directory(self.staging)['modType'], 'effect')

    def test_rejects_duplicate_effect_identity_before_writing(self):
        with self.assertRaisesRegex(ValueError, '重复'):
            eiem_effect_export.write_effect_directory(
                self.staging, 'WeaponEffects',
                [self.effect(), self.effect()], author_format3,
                lambda _path: None, eiem_format)
        self.assertFalse((self.staging / 'mod.ini').exists())

    def test_game_dependency_keeps_logical_identity_and_source_prefab(self):
        effect = self.effect()
        effect['_native']['gameResources'] = [dict(
            path='assets/game/star.mat', type='Material', asset='Star',
            sourcePrefab='assets/game/star.prefab', sourceRoot='StarRoot')]
        eiem_effect_export.write_effect_directory(
            self.staging, 'WeaponEffects', [effect], author_format3,
            lambda _path: None, eiem_format)
        ini = (self.staging / 'mod.ini').read_text(encoding='utf-8')
        self.assertIn('game.0.path=assets/game/star.mat', ini)
        self.assertIn('game.0.source_prefab=assets/game/star.prefab', ini)
        self.assertIn('game.0.source_root=StarRoot', ini)
        parser = author_format3.configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        parser.read(self.staging / 'mod.ini', encoding='utf-8')
        state = author_format3.read_controls_ini(self.staging, parser)
        restored = state['effects']['WeaponFx']
        self.assertEqual(restored['game.0.source_prefab'], 'assets/game/star.prefab')
        self.assertEqual(restored['game.0.source_root'], 'StarRoot')

    def test_compiler_bin_update_invalidates_effect_build(self):
        compiler = self.root / 'AnimeStudio.GUI.exe'
        compiler.write_bytes(b'unchanged launcher')
        implementation = self.root / 'bin' / 'AnimeStudio.GUI.dll'
        implementation.parent.mkdir()
        implementation.write_bytes(b'old implementation')
        digest = eiem_effect_publish.digest
        payload = dict(source=str(self.source), sourceManifestSha256=digest(b'{}'),
                       sourceEffectSha256=digest(b'{}'))
        before = eiem_effect_publish.inputs_signature(payload, compiler)
        implementation.write_bytes(b'new implementation')
        self.assertNotEqual(before, eiem_effect_publish.inputs_signature(payload, compiler))

    def test_new_logical_resource_relocations_survive_ini_roundtrip(self):
        effect = self.effect()
        reference = dict(path='assets/game/unimported.png', type='Texture2D', asset='',
                         relocationCab='CAB-'+'1'*32, relocationId=1,
                         relocations=[dict(bundle=0, offset=0, kind=1)])
        effect['_native']['gameResources'] = [reference]
        eiem_effect_export.write_effect_directory(
            self.staging, 'WeaponEffects', [effect], author_format3,
            lambda _path: None, eiem_format)
        parser = author_format3.configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        parser.read(self.staging/'mod.ini', encoding='utf-8')
        restored = author_format3.read_controls_ini(self.staging, parser)['effects']['WeaponFx']
        text = (self.staging/'mod.ini').read_text('utf-8')
        for hidden in ('relocation_cab', 'relocation_id', 'patch.', reference['relocationCab']):
            self.assertNotIn(hidden, text)
        self.assertEqual(restored['game.0.path'], reference['path'])
        self.assertNotIn('game.0.asset', restored)
        self.assertEqual(restored['game.0.relocation_cab'], reference['relocationCab'])
        self.assertEqual(restored['game.0.patch.0.offset'], '0')

    def test_cold_export_uses_imported_binding_without_querying_game_index(self):
        compiler = self.root / 'AnimeStudio.GUI.exe'
        compiler.write_bytes(b'compiler')
        digest = eiem_effect_publish.digest
        payload = dict(source=str(self.source), sourceManifestSha256=digest(b'{}'),
                       sourceEffectSha256=digest(b'{}'),
                       gameReferences=[dict(owner='local:1', field='$.m_Texture', path='assets/game/star.png',
                           asset='Star', type='Texture', sourcePrefab='assets/game/star.prefab', sourceRoot='Root',
                           resourceIdentity='CAB-'+'1'*32+':7', resourceClassId=28)])
        with patch.object(eiem_effect_publish.subprocess, 'run', side_effect=AssertionError('export queried index')):
            request = eiem_effect_publish.Build(payload, compiler, self.root / 'cache', 'same-id', lambda _: None)
            self.assertEqual(request.payload, payload)
            self.assertEqual(request.portable_payload, payload)
            self.assertFalse(request.cached())
            self.assertEqual(request.inputs, eiem_effect_publish.inputs_signature(payload, compiler))

    def test_build_identity_tracks_imported_resource_binding(self):
        compiler = self.root / 'AnimeStudio.GUI.exe'
        compiler.write_bytes(b'compiler')
        digest = eiem_effect_publish.digest
        payload = dict(source=str(self.source), sourceManifestSha256=digest(b'{}'),
                       sourceEffectSha256=digest(b'{}'), gameReferences=[dict(
                           owner='local:1', field='$.m_Texture', path='assets/game/star.png',
                           asset='Star', type='Texture', sourcePrefab='assets/game/effect.prefab',
                           sourceRoot='Root', resourceIdentity='CAB-'+'1'*32+':7', resourceClassId=28)])
        with patch.object(eiem_effect_publish.subprocess, 'run', side_effect=AssertionError('export queried index')):
            first = eiem_effect_publish.Build(payload, compiler, self.root / 'cache', 'same-id', lambda _: None)
            self.assertEqual(first.portable_payload['gameReferences'][0]['sourcePrefab'], 'assets/game/effect.prefab')
            first.output.mkdir(parents=True)
            (first.output / 'runtime.bin').write_bytes(b'native')
            (first.output / 'eiem-request.json').write_text(eiem_effect_publish.dumps(dict(
                key=first.key, runtime=digest(b'native'))), encoding='utf-8')
            self.assertTrue(first.cached())
            payload['gameReferences'][0]['resourceIdentity'] = 'CAB-'+'2'*32+':8'
            second = eiem_effect_publish.Build(payload, compiler, self.root / 'cache', 'same-id', lambda _: None)
            self.assertFalse(second.cached())

    def test_lod_mounts_roundtrip_exact_consumers_and_keep_unrelated_meshes_out(self):
        resources=[];uses=[]
        for i in range(4):
            for name in ('Sword','SwordVfx'):
                identity=name+str(i)
                resources.append(dict(type='Mesh',identity=identity,name=name+'_lod'+str(i),
                                      logicalPath='assets/game/'+name+'.fbx'))
                uses.append(dict(ownerPath='Meshes/lod'+str(i)+'/'+name+'_lod'+str(i),
                                 mesh=dict(identity=identity),bones=[]))
        hierarchy=[dict(transform='T0',path='Meshes/lod0',localPosition=[0,0,0],localRotation=[0,0,0,1],localScale=[1,1,1]),
                   dict(transform='T1',path='Meshes/lod0/Sword_lod0',localPosition=[0,2,0],localRotation=[-.70710678,0,0,.70710678],localScale=[1,2,1])]
        proof=dict(resources=resources,rendererUses=uses,hierarchy=hierarchy)
        targets=eiem_effect_mount_targets.lod_targets(proof,uses[0])
        self.assertEqual([t['asset'] for t in targets],['Sword_lod1','Sword_lod2','Sword_lod3'])
        anchor=eiem_effect_mount_targets.lod_anchor(proof,uses[0],targets)
        self.assertEqual(anchor['path'],'Meshes')
        self.assertEqual(anchor['nodes'][1]['position'],[0,2,0])
        self.assertEqual(len(anchor['nodes']),2)
        effect=self.effect(renderer=uses[0]['ownerPath'])
        effect.update(lodTargets=targets,lodCoverage='all',lodAnchor=anchor)
        eiem_effect_export.write_effect_directory(self.staging,'WeaponEffects',[effect],author_format3,
            lambda _:None,eiem_format)
        ini=author_format3.configparser.ConfigParser(interpolation=None);ini.optionxform=str
        ini.read(self.staging/'mod.ini',encoding='utf-8')
        state=author_format3.read_controls_ini(self.staging,ini)
        self.assertEqual(state['effects']['WeaponFx']['lod.3.asset'],'Sword_lod3')
        restored=eiem_format.format3_effect_attachment('EffectAttachmentWeaponFx',state['effects']['WeaponFx'],{})
        self.assertEqual(restored['lodTargets'],targets)
        self.assertEqual(restored['lodCoverage'],'all')
        self.assertEqual(restored['lodAnchor'],anchor)
        ini['AttachEffectWeaponFx']['lod_anchor.1.rotation']='0,0,0,0'
        with self.assertRaisesRegex(ValueError,'LOD anchor'):
            author_format3.read_controls_ini(self.staging,ini)

    def test_lod_anchor_can_use_prefab_root_and_bone_mounts_keep_skeleton(self):
        selected=dict(ownerPath='lod0/Sword_lod0',bones=[dict(identity='Bone')])
        targets=[dict(renderer='lod1/Sword_lod1',path='assets/sword.fbx',asset='Sword_lod1',bones=[])]
        proof=dict(hierarchy=[dict(path='lod0',localPosition=[0,0,0],localRotation=[0,0,0,1],localScale=[1,1,1]),
                              dict(path=selected['ownerPath'],localPosition=[0,0,0],localRotation=[0,0,0,1],localScale=[1,1,1])])
        self.assertIsNone(eiem_effect_mount_targets.lod_anchor(proof,selected,targets,'Bone/Tip'))
        anchor=eiem_effect_mount_targets.lod_anchor(proof,selected,targets)
        self.assertEqual(anchor['path'],'')
        effect=self.effect(renderer=selected['ownerPath'])
        effect.update(lodCoverage='all',lodTargets=targets,lodAnchor=anchor)
        eiem_effect_export.write_effect_directory(self.staging,'RootMount',[effect],author_format3,lambda _:None,eiem_format)
        ini=author_format3.configparser.ConfigParser(interpolation=None);ini.optionxform=str
        ini.read(self.staging/'mod.ini',encoding='utf-8')
        self.assertEqual(ini['AttachEffectWeaponFx']['lod_anchor'],'.')
        restored=author_format3.read_controls_ini(self.staging,ini)['effects']['WeaponFx']
        self.assertEqual(eiem_format.format3_effect_attachment('EffectAttachmentWeaponFx',restored,{})['lodAnchor'],anchor)

    def test_shared_empty_hierarchy_and_native_only_playback_roundtrip(self):
        graph=dict(id='Orbit', nodes=[dict(path='Pivot',label='刀光挂点',position=[0,0,0],
            rotation=[0,0,0,1],scale=[1,1,1])])
        clip=dict(id='Spin',hierarchy='Orbit',label='旋转',key='NUMPAD5',duration=1.,speed=1.,
            loop=True,autoplay=True,sample_count=3,sample_rate=2,
            asset='assets/eff/animations/spin.anim',data=b'native archive fixture'.ljust(64,b'\0'))
        effects=[self.effect('First'),self.effect('Second')]
        for effect in effects:
            effect.update(authorHierarchy=graph,mountNode='Pivot',_mount_native=[clip])
        manifest=eiem_effect_export.write_effect_directory(self.staging,'MountEffects',effects,
            author_format3,lambda _:None,eiem_format)
        ini=author_format3.configparser.ConfigParser(interpolation=None);ini.optionxform=str
        ini.read(self.staging/'mod.ini',encoding='utf-8')
        state=author_format3.read_controls_ini(self.staging,ini)
        self.assertEqual(len(state['hierarchies']),1)
        self.assertEqual(len(state['animations']['Spin']['nativeOutputs']),1)
        self.assertEqual(state['animations']['Spin']['tracks'],{})
        self.assertEqual(state['effects']['Second']['node'],'Pivot')
        self.assertNotIn('samples',ini['AnimationSpin'])
        self.assertEqual(sum(ini[s].get('type')=='animation-clip' for s in ini),1)
        self.assertTrue(any(name.endswith('.ab') for name in manifest['_files']))
        ini['AttachEffectSecond']['node']='Missing'
        with self.assertRaisesRegex(ValueError,'hierarchy/node'):
            author_format3.read_controls_ini(self.staging,ini)

    def test_invalid_mount_hierarchy_cannot_be_exported(self):
        graph=dict(id='Orbit',nodes=[dict(path='../Bad',label='Bad',position=[0,0,0],
            rotation=[0,0,0,1],scale=[1,1,1])])
        effect=dict(self.effect(),authorHierarchy=graph,mountNode='../Bad')
        with self.assertRaisesRegex(ValueError,'hierarchy path'):
            eiem_effect_export.write_effect_directory(self.staging,'BadMount',[effect],
                author_format3,lambda _:None,eiem_format)

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
        controls = (self.staging / 'mod.ini').read_text(encoding='utf-8')
        self.assertIn('[ReplaceEffectWeaponFx]', controls)
        self.assertIn('target.path=assets/game/effects/skill.prefab', controls)
        self.assertNotIn('mode=', controls)
        self.assertNotIn('assets/game/weapon.mesh', controls)

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

    def test_unchanged_source_is_compressed_once_across_author_edits(self):
        workspace = dict(format='EFF_EFFECT_WORKSPACE', version=1, catalog={})
        archive_type = eiem_format.zipfile.ZipFile
        with patch.object(eiem_format.zipfile, 'ZipFile', wraps=archive_type) as archive:
            first = eiem_format.encode_format3_effect_source(self.source, workspace, dict(label='first'))
            second = eiem_format.encode_format3_effect_source(self.source, workspace, dict(label='second'))
            self.assertEqual(archive.call_count, 1, 'unchanged source was compressed again')
        before = eiem_format.decode_format3_effect_snapshot(first)
        after = eiem_format.decode_format3_effect_snapshot(second)
        self.assertEqual(before['_source_archive'], after['_source_archive'])
        self.assertEqual(after['attachment']['label'], 'second')

    def test_source_archive_cache_detects_same_stamp_content_changes_and_new_files(self):
        workspace = dict(format='EFF_EFFECT_WORKSPACE', version=1, catalog={})
        path = self.source / 'effect.json'
        first = eiem_format.encode_format3_effect_source(self.source, workspace, {})
        stamp = path.stat()
        path.write_text('[]', encoding='utf-8')
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        second = eiem_format.encode_format3_effect_source(self.source, workspace, {})
        self.assertNotEqual(eiem_format.decode_format3_effect_snapshot(first)['_source_archive'],
                            eiem_format.decode_format3_effect_snapshot(second)['_source_archive'])
        (self.source / 'new.png').write_bytes(b'new author resource')
        third = eiem_format.encode_format3_effect_source(self.source, workspace, {})
        self.assertNotEqual(eiem_format.decode_format3_effect_snapshot(second)['_source_archive'],
                            eiem_format.decode_format3_effect_snapshot(third)['_source_archive'])


class EffectSourceProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.source = Path(self.temp.name)
        self.cab = 'CAB-' + '1' * 32
        identity = self.cab.lower() + ':42'
        nodes = [dict(type=t, name=n, level=l, metaFlag=0) for t,n,l in (
            ('Shader','Base',0), ('string','m_Name',1),
            ('SerializedShader','m_ParsedForm',1), ('string','m_Name',2),
            ('TypelessData','programs',1))]
        name = b'HGRP/Effect/Test'
        raw = struct.pack('<i',0) + struct.pack('<i',len(name)) + name
        raw += bytes((-len(raw)) % 4)
        raw += struct.pack('<i',1024 * 1024) + b'p' * (1024 * 1024)
        self.snapshot = 'source/serialized/' + self.cab + '.bytes'
        files = {
            self.snapshot: raw,
            'schemas/shader.json': json.dumps(dict(nodes=nodes)).encode(),
        }
        catalog = dict(format='EFF_EFFECT_SOURCE',version=1,objects=[dict(
            identity=identity, classId=48, type='Shader',state='raw-backed',
            schema='schemas/shader.json',data=None,native=dict(cab=self.cab,pathId=42,offset=0,size=len(raw)),
            references=[],logicalPaths=[dict(path='packages/test.shader',name='Shader')])])
        self.manifest = dict(serializedFiles=[dict(cab=self.cab,sourceBundle='game.ab',
            objects=[dict(classId=48)],externals=[],snapshot=dict(file=self.snapshot,size=len(raw),sha256=hashlib.sha256(raw).hexdigest()))])
        files['effect.json'] = json.dumps(catalog).encode()
        files['source/manifest.json'] = json.dumps(self.manifest).encode()
        receipt = dict(format='EFF_GENERATED_FILES/1', files={n:hashlib.sha256(b).hexdigest().upper() for n,b in files.items()})
        files['source/generated-files.json'] = json.dumps(receipt).encode()
        for name,data in files.items():
            target = self.source/name
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(data)
        self.workspace = dict(format='EFF_EFFECT_WORKSPACE',version=1,catalog=catalog,schemas={},values={},original={})
        self.edit = dict(format='EFF_EFFECT_EDIT',version=1,objects=[])
        for field,name in (('sourceManifestSha256','source/manifest.json'),('sourceEffectSha256','effect.json')):
            self.workspace[field] = self.edit[field] = hashlib.sha256(files[name]).hexdigest().upper()

    def tearDown(self):
        self.temp.cleanup()

    def test_game_shader_keeps_reference_metadata_and_rebases_the_edit(self):
        before = copy.deepcopy(self.workspace)
        raw = eiem_format.encode_format3_effect_author(self.source,self.workspace,{},self.edit)
        result = eiem_format.decode_format3_effect_snapshot(raw)
        self.assertEqual(self.workspace,before,'source workspace was mutated')
        self.assertTrue((self.source/self.snapshot).is_file(),'source snapshot was removed')
        with zipfile.ZipFile(io.BytesIO(result['_source_archive'])) as archive:
            self.assertNotIn(self.snapshot,archive.namelist())
            manifest_bytes = archive.read('source/manifest.json')
            manifest = json.loads(manifest_bytes)
            self.assertEqual(manifest['serializedFiles'],[])
            receipt = json.loads(archive.read('source/generated-files.json'))['files']
            for name,digest in receipt.items():
                self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest().upper(),digest)
        spec = result['workspace']['catalog']['objects'][0]
        self.assertEqual(spec['shaderName'],'HGRP/Effect/Test')
        self.assertEqual(spec['state'],'game-reference')
        self.assertEqual(spec['native'],before['catalog']['objects'][0]['native'])
        self.assertEqual(result['edit']['sourceManifestSha256'],hashlib.sha256(manifest_bytes).hexdigest().upper())
        self.assertEqual(result['workspace']['sourceEffectSha256'],result['edit']['sourceEffectSha256'])

    def test_embedded_shader_programs_stay_in_the_author_source(self):
        self.manifest['runtimeEmbeddedBundles'] = ['game.ab']
        (self.source/'source/manifest.json').write_text(json.dumps(self.manifest),'utf-8')
        result = eiem_format.decode_format3_effect_snapshot(
            eiem_format.encode_format3_effect_source(self.source,self.workspace,{}))
        with zipfile.ZipFile(io.BytesIO(result['_source_archive'])) as archive:
            self.assertIn(self.snapshot,archive.namelist())
        self.assertEqual(result['workspace']['catalog']['objects'][0]['state'],'raw-backed')

    def test_mixed_native_files_cannot_be_discarded_with_a_game_shader(self):
        self.manifest['serializedFiles'][0]['objects'].append(dict(classId=21))
        (self.source/'source/manifest.json').write_text(json.dumps(self.manifest),'utf-8')
        result = eiem_format.decode_format3_effect_snapshot(
            eiem_format.encode_format3_effect_source(self.source,self.workspace,{}))
        with zipfile.ZipFile(io.BytesIO(result['_source_archive'])) as archive:
            self.assertIn(self.snapshot,archive.namelist())

    def test_shader_without_a_game_logical_path_keeps_its_native_programs(self):
        self.workspace['catalog']['objects'][0]['logicalPaths'] = []
        result = eiem_format.decode_format3_effect_snapshot(
            eiem_format.encode_format3_effect_source(self.source,self.workspace,{}))
        with zipfile.ZipFile(io.BytesIO(result['_source_archive'])) as archive:
            self.assertIn(self.snapshot,archive.namelist())

    def test_projection_rejects_corrupt_shader_source_even_when_programs_are_omitted(self):
        path = self.source/self.snapshot
        corrupt = bytearray(path.read_bytes());corrupt[-1] ^= 1;path.write_bytes(corrupt)
        with self.assertRaisesRegex(ValueError,'原生底稿摘要不匹配'):
            eiem_format.encode_format3_effect_author(self.source,self.workspace,{},self.edit)


if __name__ == '__main__':
    unittest.main()

"""Independent author compilation uses only installed add-on tools."""
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
import unittest
import hashlib
import struct
import os
import subprocess

spec=importlib.util.spec_from_file_location('eiem_offline_reload',Path(__file__).with_name('eiem_offline_reload.py'))
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def block_fixture(generation=7, payload=b'payload', index_override=None, index_generation=None, external=False):
    def string(value):
        data=value.encode('utf-8')
        return struct.pack('<Q',len(data))+data
    digest=hashlib.sha256(payload).hexdigest().upper()
    index=(string('EFF_MOD_BLOCK_INDEX/external-blobs/le64' if external else 'EFF_MOD_BLOCK_INDEX/le64')+
           struct.pack('<QQ',generation if index_generation is None else index_generation,1)+
           string('resource')+struct.pack('<QQ',0 if external else 192,len(payload))+
           string(hashlib.sha256(payload).hexdigest().upper()))
    if external:
        index+=string('compiled.blocks/'+digest+'.bin')
    if index_override is not None:index=index_override
    slot=struct.pack('<QQQ',generation,192 if external else 192+len(payload),len(index))+hashlib.sha256(index).digest()
    slot+=hashlib.sha256(slot).digest()
    slots=(bytes(88)+slot) if generation%2 else (slot+bytes(88))
    return (b'EFF_MOD_BLOCKS/2' if external else b'EFF_MOD_BLOCKS/1')+slots+(b'' if external else payload)+index

class OfflineExportTests(unittest.TestCase):
    def make_game(self, root):
        (root/'Endfield_Data/StreamingAssets/VFS').mkdir(parents=True)
        (root/'Endfield.exe').touch();(root/'GameAssembly.dll').touch()
        return root.resolve()

    def test_arbitrary_export_uses_explicit_game_and_rejects_stale_selection(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            root=Path(folder);selected=self.make_game(root/'selected');other=self.make_game(root/'other')
            with patch.object(module,'discover_game_directories',return_value=[other]):
                self.assertEqual(module.resolve_game_directory(root/'release/Mod',str(selected)),selected)
                with self.assertRaisesRegex(ValueError,'游戏目录无效'):
                    module.resolve_game_directory(root/'release/Mod',str(root/'missing'))
            with patch.dict(os.environ,{'EFF_RELOAD_GAME':str(other)}):
                self.assertEqual(module.resolve_game_directory(root/'release/Mod',str(selected)),selected)

    def test_game_picker_accepts_executable_data_and_launcher_games_folder(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{},clear=True):
            root=Path(folder);game=self.make_game(root/'launcher/games/Endfield Game')
            for selected in (game,game/'Endfield.exe',game/'Endfield_Data',root/'launcher',root/'launcher/games'):
                self.assertEqual(module.resolve_game_directory(root/'distribution',str(selected)),game)
            second=self.make_game(root/'launcher/games/other-install')
            with self.assertRaisesRegex(ValueError,'多份'):
                module.resolve_game_directory(root/'distribution',str(root/'launcher/games'))

    def test_destination_inference_precedes_auto_discovery_and_legacy_env_still_works(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{},clear=True):
            root=Path(folder);game=self.make_game(root/'game');other=self.make_game(root/'other')
            with patch.object(module,'discover_game_directories',return_value=[other]) as find:
                self.assertEqual(module.resolve_game_directory(game/'plugin/mods/Mod'),game)
                find.assert_not_called()
                with patch.dict(os.environ,{'EFF_RELOAD_GAME':str(other)}):
                    self.assertEqual(module.resolve_game_directory(root/'release'),other)

    def test_auto_detection_requires_one_valid_installation(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{},clear=True):
            root=Path(folder);a=self.make_game(root/'game-a');b=self.make_game(root/'game-b')
            for candidates in ([],[a,b]):
                with patch.object(module,'discover_game_directories',return_value=candidates):
                    with self.assertRaisesRegex(ValueError,'游戏目录'):
                        module.resolve_game_directory(root/'release')
            with patch.object(module,'discover_game_directories',return_value=[a]):
                self.assertEqual(module.resolve_game_directory(root/'release'),a)

    def test_discovery_uses_local_hints_and_deduplicates_valid_games(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);game=self.make_game(root/'game');settings=root/'local/EFF/Repair/settings.json'
            settings.parent.mkdir(parents=True);settings.write_text(json.dumps({'game':str(game)}))
            with patch.dict(os.environ,{'LOCALAPPDATA':str(root/'local')},clear=True),patch.object(module,'_registry_game_hints',return_value=[game,root/'missing']),patch.object(module,'_launcher_game_hints',return_value=[]):
                self.assertEqual(module.discover_game_directories(),[game])
                settings.write_text('{broken json')
                self.assertEqual(module.discover_game_directories(),[game])

    def test_reload_context_accepts_separate_game_without_changing_export_destination(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{},clear=True):
            root=Path(folder);game=self.make_game(root/'game');destination=root/'release/MyMod'
            compiler=root/'compiler.exe'
            with patch.object(module,'_select_compiler',return_value=(compiler,self.protocol())):
                self.assertEqual(module.reload_context(destination,game_directory=str(game)),
                                 (compiler,destination.resolve()/'compiled.bin',destination.resolve(),game))
            self.assertFalse(destination.exists())

    def test_reextracted_source_uses_its_current_index_before_old_parent_workspace(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'assets/character.eff';(source/'source').mkdir(parents=True)
            old=root/'index/endfield_assets.eidx';old.parent.mkdir();old.write_bytes(b'old index')
            current=root/'current/index.eidx';current.parent.mkdir();current.write_bytes(b'current index')
            (source/'source/manifest.json').write_text(json.dumps(dict(sourceIndex=str(current))))
            with patch.dict(os.environ,{},clear=True):
                self.assertEqual(module.prepare_static_sources(root/'stage',source,None),current.resolve())
                current.unlink()
                self.assertEqual(module.prepare_static_sources(root/'stage',source,None),old.resolve())

    def test_moved_project_uses_shared_index_for_selected_game_without_rewriting_manifest(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{},clear=True):
            root=Path(folder);source=root/'project/character.eff';(source/'source').mkdir(parents=True)
            manifest=source/'source/manifest.json';original=json.dumps(dict(sourceIndex=str(root/'missing/index.eidx')))
            manifest.write_text(original)
            game=self.make_game(root/'game');other=self.make_game(root/'other-game')
            shared=root/'resources';shared.mkdir();workspace=root/'existing-workspace';index=workspace/'index/endfield_assets.eidx';index.parent.mkdir(parents=True);index.write_bytes(b'index')
            wrong=root/'wrong-workspace';wrong_index=wrong/'index/endfield_assets.eidx';wrong_index.parent.mkdir(parents=True);wrong_index.touch()
            registry=shared/'registry.json';registry.write_text(json.dumps(dict(format='EFF_SHARED_INDEXES/1',entries=[
                dict(game=str(other),workspace=str(wrong)),dict(game=str(game),workspace=str(workspace))])))
            with patch.dict(os.environ,{'EFF_RESOURCE_ROOT':str(shared)}):
                with patch.object(module.subprocess,'run') as run:
                    self.assertEqual(module.prepare_static_sources(root/'stage',source,(None,None,None,game)),index.resolve())
                    run.assert_not_called()
                self.assertEqual(module.shared_source_indexes(other),[wrong_index.resolve()])
                self.assertEqual(manifest.read_text(),original)
                registry.write_text('{broken')
                with self.assertRaisesRegex(ValueError,'未找到资源索引'):
                    module.prepare_static_sources(root/'stage',source,(None,None,None,game))

    def test_explicit_index_configuration_is_respected_and_does_not_silently_fall_back(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{},clear=True):
            root=Path(folder);source=root/'character.eff';source.mkdir();index=root/'index/endfield_assets.eidx';index.parent.mkdir();index.touch()
            with patch.dict(os.environ,{'EFF_SOURCE_INDEX':str(root/'missing.eidx')}):
                with self.assertRaisesRegex(ValueError,'EFF_SOURCE_INDEX'):
                    module.prepare_static_sources(root/'stage',source,None)
            explicit=root/'explicit.eidx';explicit.touch()
            with patch.dict(os.environ,{'EFF_SOURCE_INDEX':str(explicit)}):
                self.assertEqual(module.prepare_static_sources(root/'stage',source,None),explicit.resolve())

    def test_shared_index_default_location_and_malformed_entries(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{},clear=True):
            root=Path(folder);game=root/'game';workspace=root/'workspace';index=workspace/'index/endfield_assets.eidx';index.parent.mkdir(parents=True);index.touch()
            registry=root/'EFF/Resources/registry.json';registry.parent.mkdir(parents=True)
            registry.write_text(json.dumps(dict(format='EFF_SHARED_INDEXES/1',entries=[None,{},dict(game=[],workspace='x'),
                dict(game=str(game),workspace='invalid\0path'),
                dict(game=str(game),workspace=str(workspace)),dict(game=str(game),workspace=str(workspace))])))
            with patch.dict(os.environ,{'LOCALAPPDATA':str(root)}):
                self.assertEqual(module.shared_source_indexes(game),[index.resolve()])

    def test_shape_animation_requires_shared_channel_playback_capability(self):
        capability='shared-channel-groups/replace-conflicting-playback'
        for consumer_capability in (None,capability):
            with self.subTest(consumer=consumer_capability),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);stage=root/'stage';stage.mkdir();(stage/'mod.ini').write_text('[ShapeAnimationgroup]\npath=resources/a.bin\n')
                declaration=self.protocol();declaration['structures'].update(shapeAnimation='sampled-shape-weight-clips/le32/1',shapeAnimationPlayback=capability)
                consumer=json.loads(json.dumps(declaration));consumer['structures']['shapeAnimationPlayback']=consumer_capability
                def invoke(command,**kwargs):
                    if command[-1]=='--protocol':return SimpleNamespace(returncode=0,stdout=json.dumps(declaration),stderr='')
                    (stage/'compiled.bin').write_bytes(b'new output');return SimpleNamespace(returncode=0,stdout='COMPILED-MOD-PUBLISHED',stderr='')
                with patch.object(module.subprocess,'run',side_effect=invoke),patch.object(module,'_game_protocol',return_value=consumer):
                    if consumer_capability:
                        self.assertTrue(module.compile_reload(stage,(root/'compiler',None,None,root))['offlineReloadPrepared'])
                    else:
                        with self.assertRaisesRegex(ValueError,'共享形态频道'):module.compile_reload(stage,(root/'compiler',None,None,root))
                        self.assertFalse((stage/'compiled.bin').exists())
    def protocol(self):
        return dict(format=module.EXPECTED_PROTOCOL, compiledMod=module.COMPILED_MOD,
                    staticSourceInputs=module.STATIC_SOURCE_INPUTS,
                    sourcePreparation='in-process/index-vfs+author-declarations',
                    structures=dict(module.STRUCTURES),compiler='arbitrary-label',abi=999,
                    compiledExportCarryover=module.EXPORT_CARRYOVER)

    def test_compile_only_requested_staged_mod(self):
        with tempfile.TemporaryDirectory() as folder:
            game=Path(folder);stage=game/'stage';stage.mkdir();other=game/'other';other.mkdir()
            sentinel=other/'compiled.bin';sentinel.write_bytes(b'other Mod')
            calls=[]
            def invoke(command,**kwargs):
                calls.append(command)
                if command[-1]=='--protocol':return SimpleNamespace(returncode=0,stdout=json.dumps(self.protocol()),stderr='')
                self.assertEqual(command,[str(game/'local-compiler.exe'),'--export-mod',str(stage.resolve()),str(game)])
                (stage/'compiled.bin').write_bytes(b'independent result')
                return SimpleNamespace(returncode=0,stdout='COMPILED-MOD-PUBLISHED',stderr='')
            with patch.object(module.subprocess,'run',side_effect=invoke):
                self.assertTrue(module.compile_reload(stage,(game/'local-compiler.exe',stage/'compiled.bin',stage,game))['offlineReloadPrepared'])
            self.assertEqual(sentinel.read_bytes(),b'other Mod');self.assertEqual(len(calls),2)
            self.assertFalse((game/'plugin/resource-reload-cache').exists())

    def test_updated_game_refreshes_index_once_then_retries_same_author_stage(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);stage=root/'stage';stage.mkdir();prior=root/'published';prior.mkdir()
            (prior/'compiled.bin').write_bytes(b'previous Mod')
            compiler=root/'addon/nativepack/eff_resource_pack.exe'
            helper=compiler.with_name('eff_source_index.exe');helper.parent.mkdir(parents=True);helper.touch()
            index=root/'workspace/index/endfield_assets.eidx';index.parent.mkdir(parents=True);index.write_bytes(b'old index')
            exports=[];updates=[]
            def invoke(command, **kwargs):
                if command == [str(compiler),'--protocol']:
                    return SimpleNamespace(returncode=0,stdout=json.dumps(self.protocol()),stderr='')
                if command == [str(helper),'--protocol']:
                    return SimpleNamespace(returncode=0,stdout=json.dumps(dict(format='EFF_SOURCE_INDEX_UPDATER',version=1,update='bundle-content/incremental',authorPackages='unchanged')),stderr='')
                if command[0] == str(helper):
                    self.assertEqual(command,[str(helper),'--update',str(root),str(index.resolve())])
                    updates.append(command);index.write_bytes(b'current index')
                    return SimpleNamespace(returncode=0,stdout='SOURCE-INDEX-PREPARED '+json.dumps(dict(index=str(index),parsed=2,reused=273606,removed=0)),stderr='')
                exports.append(command)
                if len(exports)==1:
                    return SimpleNamespace(returncode=3,stdout='',stderr='OFFLINE-SOURCE-INDEX-STALE\nold catalog')
                self.assertEqual(command,exports[0])
                self.assertEqual(index.read_bytes(),b'current index')
                (stage/'compiled.bin').write_bytes(b'new Mod')
                return SimpleNamespace(returncode=0,stdout='COMPILED-MOD-PUBLISHED',stderr='')
            with patch.object(module.subprocess,'run',side_effect=invoke):
                result=module.compile_reload(stage,(compiler,None,None,root),previous=prior,source_index=index)
            self.assertEqual(len(exports),2);self.assertEqual(len(updates),1)
            self.assertEqual(result['offlineSourceIndexRefresh']['parsed'],2)
            self.assertEqual((prior/'compiled.bin').read_bytes(),b'previous Mod')

    def test_other_errors_do_not_refresh_index_and_failed_retry_is_bounded(self):
        for code, message, expected in ((1,'OFFLINE-SOURCE-INDEX-STALE',0),
                (3,'some other error',0),(3,'OFFLINE-SOURCE-INDEX-STALE',1)):
            with self.subTest(code=code,message=message), tempfile.TemporaryDirectory() as folder:
                root=Path(folder);stage=root/'stage';stage.mkdir();prior=root/'prior';prior.mkdir()
                (prior/'compiled.bin').write_bytes(b'previous Mod')
                calls=[]
                def invoke(command,**kwargs):
                    if command[-1]=='--protocol':
                        return SimpleNamespace(returncode=0,stdout=json.dumps(self.protocol()),stderr='')
                    calls.append(command)
                    return SimpleNamespace(returncode=code,stdout='',stderr=message)
                with patch.object(module.subprocess,'run',side_effect=invoke),patch.object(module,'refresh_source_index',return_value={}) as refresh:
                    with self.assertRaisesRegex(ValueError,'构建失败'):
                        module.compile_reload(stage,(root/'compiler',None,None,root),previous=prior,source_index=root/'index')
                self.assertEqual(refresh.call_count,expected)
                self.assertEqual(len(calls),expected+1)
                self.assertEqual((prior/'compiled.bin').read_bytes(),b'previous Mod')

    def test_failed_index_update_stops_before_retry_and_preserves_published_mod(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);stage=root/'stage';stage.mkdir();prior=root/'prior';prior.mkdir()
            (prior/'compiled.bin').write_bytes(b'previous Mod')
            replies=[SimpleNamespace(returncode=0,stdout=json.dumps(self.protocol()),stderr=''),
                     SimpleNamespace(returncode=3,stdout='',stderr='OFFLINE-SOURCE-INDEX-STALE')]
            with patch.object(module.subprocess,'run',side_effect=replies) as run,patch.object(module,'refresh_source_index',side_effect=ValueError('索引刷新失败')):
                with self.assertRaisesRegex(ValueError,'索引刷新失败'):
                    module.compile_reload(stage,(root/'compiler',None,None,root),previous=prior,source_index=root/'index')
            self.assertEqual(run.call_count,2)
            self.assertEqual((prior/'compiled.bin').read_bytes(),b'previous Mod')

    def test_index_helper_requires_compatible_protocol_and_current_index_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);compiler=root/'eff_resource_pack.exe';helper=root/'eff_source_index.exe'
            index=root/'workspace/index/endfield_assets.eidx'
            with self.assertRaisesRegex(ValueError,'缺少'):
                module.refresh_source_index(compiler,root,index)
            helper.touch()
            declaration=dict(format='EFF_SOURCE_INDEX_UPDATER',version=1,update='bundle-content/incremental',authorPackages='unchanged')
            for value in ([],dict(declaration,authorPackages='refresh')):
                with patch.object(module.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(value),stderr='')) as run:
                    with self.assertRaisesRegex(ValueError,'协议'):
                        module.refresh_source_index(compiler,root,index)
                    self.assertEqual(run.call_count,1)
            for receipt in ({'index':str(root/'other'), 'parsed':2,'reused':4,'removed':0},
                            {'index':str(index), 'parsed':-1,'reused':4,'removed':0}):
                replies=[SimpleNamespace(returncode=0,stdout=json.dumps(declaration),stderr=''),
                         SimpleNamespace(returncode=0,stdout='SOURCE-INDEX-PREPARED '+json.dumps(receipt),stderr='')]
                with patch.object(module.subprocess,'run',side_effect=replies):
                    with self.assertRaisesRegex(ValueError,'完整凭据'):
                        module.refresh_source_index(compiler,root,index)

    def test_previous_snapshot_is_read_only_and_requires_declared_support(self):
        for support in (None, 'unknown-layout', 'validated-previous-mod'):
            with self.subTest(support=support), tempfile.TemporaryDirectory() as folder:
                game=Path(folder);stage=game/'stage';stage.mkdir();previous=game/'published';previous.mkdir()
                prior=previous/'compiled.bin';prior.write_bytes(b'previous result')
                declaration=self.protocol();declaration['compiledCacheInputs']=support
                def invoke(command, **kwargs):
                    if command[-1]=='--protocol':
                        return SimpleNamespace(returncode=0,stdout=json.dumps(declaration),stderr='')
                    expected=[str(game/'compiler'),'--export-mod',str(stage.resolve()),str(game)]
                    expected+=['--previous-mod',str(previous.resolve())]
                    self.assertEqual(command,expected)
                    (stage/'compiled.bin').write_bytes(b'new result')
                    return SimpleNamespace(returncode=0,stdout='COMPILED-MOD-PUBLISHED',stderr='')
                with patch.object(module.subprocess,'run',side_effect=invoke):
                    module.compile_reload(stage,(game/'compiler',None,None,game),previous=previous)
                self.assertEqual(prior.read_bytes(),b'previous result')

    def test_old_cache_capability_does_not_excuse_lost_discoveries(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);stage=root/'stage';stage.mkdir();prior=root/'prior';prior.mkdir()
            (prior/'compiled.bin').write_bytes(b'published additions')
            declaration=self.protocol();declaration.pop('compiledExportCarryover')
            declaration['compiledCacheInputs']='validated-previous-mod'
            with patch.object(module.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(declaration),stderr='')) as run:
                with self.assertRaisesRegex(ValueError,'继承'):
                    module.compile_reload(stage,(root/'compiler',None,None,root),previous=prior)
                self.assertEqual(run.call_count,1)
            self.assertEqual((prior/'compiled.bin').read_bytes(),b'published additions')

    def test_concurrent_addition_is_recompiled_before_publish(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);published=root/'published';published.mkdir();stage=root/'stage';stage.mkdir()
            path=published/'compiled.bin';path.write_bytes(b'PFB-A')
            first={'offlinePreviousRevision':module.published_revision(published)}
            path.write_bytes(b'PFB-A+PFB-B')
            events=[]
            def compile_current(*args,**kwargs):
                events.append('compile')
                (stage/'compiled.bin').write_bytes(path.read_bytes()+b'+new Mesh')
                return {'offlinePreviousRevision':module.published_revision(published)}
            def publish():
                events.append('publish')
                self.assertEqual((stage/'compiled.bin').read_bytes(),b'PFB-A+PFB-B+new Mesh')
                return 'committed'
            with patch.object(module,'compile_reload',side_effect=compile_current):
                result,receipt=module.publish_reload(stage,published,publish,None,None,first)
            self.assertEqual(result,'committed');self.assertEqual(events,['compile','publish'])

    def test_continuous_concurrent_changes_keep_old_publication(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'compiled.bin';path.write_bytes(b'latest')
            with patch.object(module,'compile_reload',return_value={'offlinePreviousRevision':('stale',)}) as compile_current:
                with self.assertRaisesRegex(ValueError,'未发布'):
                    module.publish_reload(root,root,lambda:self.fail('stale state was published'),None,None,{'offlinePreviousRevision':None})
                self.assertEqual(compile_current.call_count,2)
            self.assertEqual(path.read_bytes(),b'latest')

    def test_block_revision_reads_committed_index_and_ignores_tail(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'compiled.bin'
            path.write_bytes(block_fixture())
            before=module.published_revision(root)
            self.assertEqual(before[2:4],('blocks',7))
            with path.open('ab') as stream:stream.write(b'uncommitted data')
            self.assertEqual(module.published_revision(root),before)
            corrupted=bytearray(path.read_bytes());corrupted[104+56]^=1;path.write_bytes(corrupted)
            with self.assertRaisesRegex(ValueError,'代际'):module.published_revision(root)

    def test_block_revision_rejects_checksummed_invalid_directory(self):
        for options in (dict(index_override=b'not an index'),dict(index_generation=8)):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as folder:
                root=Path(folder);(root/'compiled.bin').write_bytes(block_fixture(**options))
                with self.assertRaisesRegex(ValueError,'代际'):module.published_revision(root)

    def test_block_compiler_rejects_old_dll_before_first_export(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);stage=root/'stage';stage.mkdir()
            declaration=self.protocol();declaration['compiledModStore']=module.COMPILED_STORE
            consumer=self.protocol()
            with patch.object(module,'_game_protocol',return_value=consumer),patch.object(module.subprocess,'run',
                    return_value=SimpleNamespace(returncode=0,stdout=json.dumps(declaration),stderr='')) as run:
                with self.assertRaisesRegex(ValueError,'分块 Mod'):
                    module.compile_reload(stage,(root/'compiler',None,None,root))
                self.assertEqual(run.call_count,1)
            self.assertFalse((stage/'compiled.bin').exists())

    def test_block_compile_receipt_does_not_hash_source_payload(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);stage=root/'stage';stage.mkdir()
            declaration=self.protocol();declaration['compiledModStore']='EFF_MOD_BLOCKS/1'
            artifact=block_fixture(payload=b'x'*(4*1024*1024))
            def invoke(command,**kwargs):
                if command[-1]=='--protocol':
                    return SimpleNamespace(returncode=0,stdout=json.dumps(declaration),stderr='')
                (stage/'compiled.bin').write_bytes(artifact)
                return SimpleNamespace(returncode=0,stdout='COMPILED-MOD-PUBLISHED',stderr='')
            hashes=[];sha=hashlib.sha256
            def checked_hash(data=b''):
                hashes.append(len(data))
                self.assertLess(len(data),1024*1024,'compiled payload was hashed again')
                return sha(data)
            with patch.object(module.subprocess,'run',side_effect=invoke),patch.object(module.hashlib,'sha256',side_effect=checked_hash):
                result=module.compile_reload(stage,(root/'compiler',None,None,root))
            self.assertTrue(hashes)
            self.assertEqual(result['offlineReloadKey'],module.published_revision(stage)[-1].upper())

    def test_external_block_compile_checks_blob_presence_without_hashing_payload(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);stage=root/'stage';stage.mkdir()
            declaration=self.protocol();declaration['compiledModStore']=module.COMPILED_STORE
            payload=b'x'*(4*1024*1024);digest=hashlib.sha256(payload).hexdigest().upper()
            artifact=block_fixture(payload=payload,external=True)
            blob=stage/'compiled.blocks'/(digest+'.bin')
            def invoke(command,**kwargs):
                if command[-1]=='--protocol':
                    return SimpleNamespace(returncode=0,stdout=json.dumps(declaration),stderr='')
                (stage/'compiled.bin').write_bytes(artifact)
                blob.parent.mkdir(exist_ok=True);blob.write_bytes(payload)
                return SimpleNamespace(returncode=0,stdout='COMPILED-MOD-PUBLISHED',stderr='')
            with patch.object(module.subprocess,'run',side_effect=invoke),patch.object(Path,'read_bytes',side_effect=AssertionError('full file read')):
                result=module.compile_reload(stage,(root/'compiler',None,None,root))
            self.assertEqual(result['offlineReloadKey'],module.published_revision(stage)[-1].upper())
            blob.unlink()
            with self.assertRaisesRegex(ValueError,'分块文件缺失'):
                module.compiled_snapshot(stage,validate_files=True)

    def test_block_compiler_must_produce_its_declared_container(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);stage=root/'stage';stage.mkdir()
            declaration=self.protocol();declaration['compiledModStore']='EFF_MOD_BLOCKS/1'
            def invoke(command,**kwargs):
                if command[-1]=='--protocol':
                    return SimpleNamespace(returncode=0,stdout=json.dumps(declaration),stderr='')
                (stage/'compiled.bin').write_bytes(b'old packet output')
                return SimpleNamespace(returncode=0,stdout='COMPILED-MOD-PUBLISHED',stderr='')
            with patch.object(module.subprocess,'run',side_effect=invoke):
                with self.assertRaisesRegex(ValueError,'声明的分块'):
                    module.compile_reload(stage,(root/'compiler',None,None,root))

    @unittest.skipUnless(os.name=='nt' and os.environ.get('EIEM_CARRYOVER_SMOKE'), 'optional real C++ writer fixture')
    def test_cpp_writer_shares_python_publication_mutex(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);tool=os.environ['EIEM_CARRYOVER_SMOKE'];path=root/'compiled.bin'
            subprocess.run([tool,'--commit-fixture',str(path)],check=True,capture_output=True)
            before=module.published_revision(root)
            with module.publication_lock(root):
                process=subprocess.Popen([tool,'--commit-fixture',str(path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                with self.assertRaises(subprocess.TimeoutExpired):process.communicate(timeout=0.15)
                self.assertEqual(module.published_revision(root),before)
            output,error=process.communicate(timeout=5)
            self.assertEqual(process.returncode,0,error);self.assertIn(b'COMMITTED 2',output)
            self.assertEqual(module.published_revision(root)[3],2)

    @unittest.skipUnless(os.name=='nt' and os.environ.get('EIEM_CARRYOVER_SMOKE'), 'optional real C++ author-reader fixture')
    def test_cpp_author_reader_returns_under_python_publication_mutex(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);tool=os.environ['EIEM_CARRYOVER_SMOKE'];path=root/'compiled.bin'
            subprocess.run([tool,'--commit-fixture',str(path)],check=True,capture_output=True)
            before=module.published_revision(root)
            with module.publication_lock(root):
                process=subprocess.Popen([tool,'--author-read-fixture',str(path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                try:
                    output,error=process.communicate(timeout=5)
                finally:
                    if process.poll() is None:
                        process.kill();process.communicate()
                self.assertEqual(process.returncode,0,error)
                self.assertIn(b'AUTHOR-READER-RETURNED',output)
                self.assertEqual(module.published_revision(root),before)

    def test_game_and_workspace_exports_use_addon_compiler_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);addon=root/'addon';tool=addon/'nativepack/eff_resource_pack.exe';tool.parent.mkdir(parents=True);tool.touch()
            game=self.make_game(root/'game');author=game/'plugin/mods/A';author.mkdir(parents=True)
            foreign=game/'plugin/tools/eff_resource_pack.exe';foreign.parent.mkdir();foreign.touch()
            reply=SimpleNamespace(returncode=0,stdout=json.dumps(self.protocol()),stderr='')
            with patch.object(module,'__file__',str(addon/'eiem_offline_reload.py')),patch.object(module.subprocess,'run',return_value=reply) as run,patch.dict(module.os.environ,{},clear=True):
                context=module.reload_context(author);self.assertEqual(context[0],tool);self.assertEqual(context[2],author.resolve())
                with patch.dict(module.os.environ,{'EFF_RELOAD_GAME':str(game)}):self.assertEqual(module.reload_context(root/'export')[0],tool)
                tool.unlink()
                with self.assertRaisesRegex(ValueError,'编译器'):module.reload_context(author)
                self.assertTrue(all(call.args[0][0]==str(tool) for call in run.call_args_list))

    def test_old_dll_rejected_before_publication(self):
        with tempfile.TemporaryDirectory() as folder:
            game=Path(folder);declaration=game/'plugin/package-protocol.json';declaration.parent.mkdir()
            declaration.write_text(json.dumps(dict(self.protocol(),compiledMod='another-byte-layout')))
            stage=game/'stage';stage.mkdir();artifact=stage/'compiled.bin';artifact.write_bytes(b'previous')
            with patch.object(module.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(self.protocol()),stderr='')) as run:
                with self.assertRaisesRegex(ValueError,'独立 Mod'):module.compile_reload(stage,(game/'compiler',None,None,game))
                self.assertEqual(run.call_count,1)
            self.assertEqual(artifact.read_bytes(),b'previous')

    def test_failed_or_incomplete_compile_keeps_previous_mod(self):
        with tempfile.TemporaryDirectory() as folder:
            game=Path(folder);stage=game/'stage';stage.mkdir();published=game/'published';published.mkdir();old=published/'compiled.bin';old.write_bytes(b'previous')
            protocol=SimpleNamespace(returncode=0,stdout=json.dumps(self.protocol()),stderr='')
            for result in (SimpleNamespace(returncode=1,stdout='',stderr='invalid target'),SimpleNamespace(returncode=0,stdout='',stderr='')):
                with patch.object(module.subprocess,'run',side_effect=[protocol,result]):
                    with self.assertRaises(ValueError):module.compile_reload(stage,(game/'compiler',None,None,game))
                self.assertEqual(old.read_bytes(),b'previous')

    def test_source_preparation_seeds_descriptor_without_starting_a_second_tool(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);addon=root/'addon'
            game=root/'game';stage=root/'stage';stage.mkdir();prior=root/'prior';prior.mkdir();(prior/'source-inputs.bin').write_bytes(b'prior')
            source=root/'workspace/character.eff';source.mkdir(parents=True);index=root/'workspace/index/endfield_assets.eidx';index.parent.mkdir();index.touch()
            with patch.object(module,'__file__',str(addon/'eiem_offline_reload.py')),patch.object(module.subprocess,'run') as run,patch.dict(module.os.environ,{},clear=True):
                selected_index=module.prepare_static_sources(stage,source,(addon/'compiler',None,None,game),previous=prior)
                run.assert_not_called()
                self.assertEqual(selected_index,index.resolve())
            self.assertEqual((prior/'source-inputs.bin').read_bytes(),b'prior')
            self.assertEqual((stage/'source-inputs.bin').read_bytes(),b'prior')

    def test_source_discovery_and_compile_share_one_process(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);stage=root/'stage';stage.mkdir();compiler=root/'compiler';index=root/'index.eidx'
            calls=[]
            def invoke(command,**kwargs):
                calls.append(command)
                if command[-1]=='--protocol':return SimpleNamespace(returncode=0,stdout=json.dumps(self.protocol()),stderr='')
                self.assertEqual(command,[str(compiler),'--export-mod',str(stage.resolve()),str(root),'--source-index',str(index.resolve())])
                (stage/'compiled.bin').write_bytes(b'one complete candidate')
                return SimpleNamespace(returncode=0,stdout='COMPILED-MOD-COST source-index 125\nCOMPILED-MOD-COST total 250\nCOMPILED-MOD-PUBLISHED',stderr='')
            with patch.object(module.subprocess,'run',side_effect=invoke):
                result=module.compile_reload(stage,(compiler,None,None,root),source_index=index)
            self.assertEqual(result['offlineCompilerCostsMs']['source-index'],125)
            self.assertEqual(len([call for call in calls if '--export-mod' in call]),1)

    def test_combined_discovery_requires_an_actual_compiler_capability(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);stage=root/'stage';stage.mkdir();(stage/'compiled.bin').write_bytes(b'previous')
            declaration=self.protocol();declaration.pop('sourcePreparation')
            with patch.object(module.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(declaration),stderr='')) as run:
                with self.assertRaisesRegex(ValueError,'进程内来源查询'):
                    module.compile_reload(stage,(root/'compiler',None,None,root),source_index=root/'index.eidx')
                self.assertEqual(run.call_count,1)
            self.assertEqual((stage/'compiled.bin').read_bytes(),b'previous')

    def test_failed_protocol_query_never_guesses_from_executable_markers(self):
        with tempfile.TemporaryDirectory() as folder:
            compiler = Path(folder) / 'eff_resource_pack.exe'
            compiler.write_bytes(b'EFF_NATIVE_PACK_V35_1 EFF_NATIVE_PACK_V36_ASSEMBLY_1')
            cache=Path(folder)/'cache';cache.mkdir();before=b'previous complete publication'
            (cache/'current.tsv').write_bytes(before)
            for response in (SimpleNamespace(returncode=1,stdout='',stderr='query failed'),
                             module.subprocess.TimeoutExpired(str(compiler),5),
                             OSError('cannot execute compiler')):
                with self.subTest(response=response), patch.object(module.subprocess,'run',side_effect=
                        response if isinstance(response,Exception) else [response]) as run:
                    with self.assertRaisesRegex(ValueError,'协议'):
                        module.compile_reload(Path(folder)/'author',(compiler,Path(folder)/'baseline',cache,Path(folder)))
                    self.assertEqual(run.call_count,1)
                    self.assertEqual(run.call_args.args[0],[str(compiler),'--protocol'])
                self.assertEqual((cache/'current.tsv').read_bytes(),before)

    def test_compatibility_uses_structures_and_ignores_release_labels(self):
        with tempfile.TemporaryDirectory() as folder:
            game = Path(folder)
            declaration = game / 'plugin/package-protocol.json'
            declaration.parent.mkdir()
            self.assertIsNone(module._game_protocol(game))
            protocol = dict(format=module.EXPECTED_PROTOCOL, version=1, abi=36,
                            compiler=module.COMPILERS[36], authorFormat=2, resourceVersion=2)
            declaration.write_text(json.dumps(protocol))
            self.assertEqual(module._game_protocol(game)['abi'], 36)
            compiler = game / 'eff_resource_pack.exe'
            with patch.object(module.subprocess, 'run', return_value=SimpleNamespace(
                    returncode=0, stdout=json.dumps(protocol), stderr='')):
                self.assertEqual(module._compiler_protocol(compiler)['abi'], 36)
            protocol.update(version=999, abi=999, compiler='future-build', authorFormat=999,
                            resourceVersion=999, structures=dict(module.STRUCTURES))
            declaration.write_text(json.dumps(protocol))
            expected = module._game_protocol(game)
            with patch.object(module.subprocess, 'run', return_value=SimpleNamespace(
                    returncode=0, stdout=json.dumps(protocol), stderr='')):
                self.assertEqual(module._compiler_protocol(compiler, expected)['abi'], 999)
            protocol['structures']['manifest'] = 'different-field-order'
            declaration.write_text(json.dumps(protocol))
            with self.assertRaisesRegex(ValueError, '结构'):
                module._game_protocol(game)

    def test_rule_encoding_requires_a_matching_reader(self):
        rules = 'compressed-rules/xpress-huffman/le64'
        old = dict(format=module.EXPECTED_PROTOCOL, structures=dict(module.STRUCTURES,
                   manifestEncoding=module.MANIFEST_ENCODING, manifestInputs=[module.MANIFEST_ENCODING]))
        current = dict(old, structures=dict(old['structures'], manifestEncoding=rules,
                       manifestInputs=[rules, module.MANIFEST_ENCODING]))
        with tempfile.TemporaryDirectory() as folder:
            compiler = Path(folder) / 'eff_resource_pack.exe'
            with patch.object(module.subprocess, 'run', return_value=SimpleNamespace(
                    returncode=0, stdout=json.dumps(current), stderr='')):
                with self.assertRaisesRegex(ValueError, '清单字段布局'):
                    module._compiler_protocol(compiler, old)
                self.assertEqual(module._compiler_protocol(compiler, current)['structures']['manifestEncoding'], rules)

    def test_runtime_index_requires_matching_reader(self):
        index = module.COMPILED_MOD
        produced = dict(compiledRuntimeIndex=index)
        with self.assertRaisesRegex(ValueError, '运行索引'):
            module._require_manifest_reader(produced, {})
        with self.assertRaisesRegex(ValueError, '运行索引'):
            module._require_manifest_reader(produced, dict(compiledRuntimeIndex='different-layout'))
        module._require_manifest_reader(produced, dict(compiledRuntimeIndex=index))


if __name__=='__main__':unittest.main()

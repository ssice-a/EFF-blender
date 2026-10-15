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

class OfflineExportTests(unittest.TestCase):
    def test_reextracted_source_uses_its_current_index_before_old_parent_workspace(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'assets/character.eff';(source/'source').mkdir(parents=True)
            old=root/'index/endfield_assets.eidx';old.parent.mkdir();old.write_bytes(b'old index')
            current=root/'current/index.eidx';current.parent.mkdir();current.write_bytes(b'current index')
            (source/'source/manifest.json').write_text(json.dumps(dict(sourceIndex=str(current))))
            with patch.dict(os.environ,{},clear=True):
                self.assertEqual(module.prepare_static_sources(root/'stage',source,None),current.resolve())
                current.unlink()
                with self.assertRaisesRegex(ValueError,'来源索引已不存在'):
                    module.prepare_static_sources(root/'stage',source,None)

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
            root=Path(folder);path=root/'compiled.bin';index=b'validated index'
            slot=struct.pack('<QQQ',7,192,len(index))+hashlib.sha256(index).digest()
            slot+=hashlib.sha256(slot).digest()
            path.write_bytes(b'EFF_MOD_BLOCKS/1'+bytes(88)+slot+index)
            before=module.published_revision(root)
            self.assertEqual(before[2:4],('blocks',7))
            with path.open('ab') as stream:stream.write(b'uncommitted data')
            self.assertEqual(module.published_revision(root),before)
            corrupted=bytearray(path.read_bytes());corrupted[104+56]^=1;path.write_bytes(corrupted)
            with self.assertRaisesRegex(ValueError,'代际'):module.published_revision(root)

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

    def test_game_and_workspace_exports_use_addon_compiler_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);addon=root/'addon';tool=addon/'nativepack/eff_resource_pack.exe';tool.parent.mkdir(parents=True);tool.touch()
            game=root/'game';(game/'Endfield_Data').mkdir(parents=True);author=game/'plugin/mods/A';author.mkdir(parents=True)
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

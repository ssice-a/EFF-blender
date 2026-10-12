"""Independent author compilation uses only installed add-on tools."""
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
import unittest

spec=importlib.util.spec_from_file_location('eiem_offline_reload',Path(__file__).with_name('eiem_offline_reload.py'))
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class OfflineExportTests(unittest.TestCase):
    def protocol(self):
        return dict(format=module.EXPECTED_PROTOCOL, compiledMod=module.COMPILED_MOD,
                    staticSourceInputs=module.STATIC_SOURCE_INPUTS,
                    sourcePreparation='in-process/index-vfs+author-declarations',
                    structures=dict(module.STRUCTURES),compiler='arbitrary-label',abi=999)

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
                    if support=='validated-previous-mod':expected+=['--previous-mod',str(previous.resolve())]
                    self.assertEqual(command,expected)
                    (stage/'compiled.bin').write_bytes(b'new result')
                    return SimpleNamespace(returncode=0,stdout='COMPILED-MOD-PUBLISHED',stderr='')
                with patch.object(module.subprocess,'run',side_effect=invoke):
                    module.compile_reload(stage,(game/'compiler',None,None,game),previous=previous)
                self.assertEqual(prior.read_bytes(),b'previous result')

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

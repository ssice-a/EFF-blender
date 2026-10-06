"""The author output and native candidate publication must share one compiler."""
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
    def test_global_cache_compiles_whole_character_set(self):
        with tempfile.TemporaryDirectory() as folder:
            game=Path(folder);mods=game/'plugin/mods';author=mods/'CharacterA';author.mkdir(parents=True)
            (author/'mod.ini').write_text('[Mod]\nformat=2\nid=A\n')
            other=mods/'CharacterB';other.mkdir();(other/'mod.ini').write_text('[Mod]\nformat=2\nid=B\n')
            cache=game/'plugin/resource-reload-cache';cache.mkdir();(cache/'current.tsv').write_text('EFF_RESOURCE_RELOAD_CURRENT\t1\n'+'A'*64+'\t'+'B'*64+'\n')
            compiler=game/'compiler.exe';baseline=game/'baseline'
            protocol=dict(format=module.EXPECTED_PROTOCOL,version=1,abi=35,compiler=module.COMPILERS[35],authorFormat=2,resourceVersion=2,modSetVersion=1,arguments=['author','baseline','cache'])
            with patch.object(module.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(protocol),stderr='')) as run:
                module.compile_reload(author,(compiler,baseline,cache,game))
                self.assertEqual(run.call_args.args[0],[str(compiler),'--mods',str(mods.resolve()),str(baseline),str(cache)])
            del protocol['modSetVersion'];before=(cache/'current.tsv').read_bytes()
            with patch.object(module.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(protocol),stderr='')) as run:
                with self.assertRaisesRegex(ValueError,'多角色'):module.compile_reload(author,(compiler,baseline,cache,game))
                self.assertEqual(run.call_count,1)
            self.assertEqual((cache/'current.tsv').read_bytes(),before)

    def test_installed_legacy_compiler_uses_set_cache_and_its_declared_path_layout(self):
        with tempfile.TemporaryDirectory() as folder:
            game=Path(folder);mods=game/'plugin/mods';author=mods/'LZY';author.mkdir(parents=True)
            (author/'mod.ini').write_text('[Mod]\nformat=2\nid=LZY\n')
            cache=game/'plugin/resource-reload-cache';cache.mkdir()
            before=('EFF_RESOURCE_RELOAD_CURRENT\t1\n'+'A'*64+'\t'+'B'*64+'\n').encode()
            (cache/'current.tsv').write_bytes(before)
            owner=b'EFF_OFFLINE_CACHE_OWNER\t2\n'+b'C'*64+b'\n'
            (cache/'owner.tsv').write_bytes(owner)
            compiler=game/'compiler.exe';baseline=game/'baseline'
            protocol=dict(format=module.EXPECTED_PROTOCOL,version=1,abi=35,
                          compiler=module.COMPILERS[35],authorFormat=2,resourceVersion=2,modSetVersion=1)
            expected=[str(compiler),'--mods',str(mods.resolve()),str(baseline),str(cache),str(game/'GameAssembly.dll')]
            def run(command,**kwargs):
                if command[-1]=='--protocol':
                    return SimpleNamespace(returncode=0,stdout=json.dumps(protocol),stderr='')
                if command!=expected:
                    return SimpleNamespace(returncode=1,stdout='',stderr='legacy CLI or set-cache owner mismatch')
                return SimpleNamespace(returncode=0,stdout='OFFLINE-REUSED',stderr='')
            with patch.object(module.subprocess,'run',side_effect=run):
                self.assertTrue(module.compile_reload(author,(compiler,baseline,cache,game))['offlineReloadPrepared'])
            self.assertEqual((cache/'owner.tsv').read_bytes(),owner)
            self.assertEqual((cache/'current.tsv').read_bytes(),before)

    def test_game_and_workspace_exports_use_same_compiler(self):
        with tempfile.TemporaryDirectory() as folder:
            game=Path(folder)/'game';baseline=game/'plugin/resource-reload-baseline';baseline.mkdir(parents=True)
            (baseline/'baseline.tsv').write_text('baseline')
            compiler=game/'plugin/tools/eff_resource_pack.exe';compiler.parent.mkdir()
            compiler.write_bytes(b'EFF_NATIVE_PACK_V35_1')
            author=game/'plugin/mods/ModA';author.mkdir(parents=True)
            protocol = dict(format='EFF_OFFLINE_PACKAGE_PROTOCOL', version=1, abi=35,
                            compiler='EFF_NATIVE_PACK_V35_1', authorFormat=2, resourceVersion=2,
                            arguments=['author','baseline','cache'])
            queried = SimpleNamespace(returncode=0,stdout=json.dumps(protocol),stderr='')
            with patch.dict(module.os.environ,{'EFF_RELOAD_GAME':str(game)}), patch.object(module.subprocess,'run',return_value=queried):
                self.assertEqual(module.reload_context(author)[2],game/'plugin/resource-reload-cache')
                exported=Path(folder)/'ModA';self.assertEqual(module.reload_context(exported)[2],Path(folder)/'ModA.reload')
                self.assertEqual(module.reload_context(exported)[3],game)
            self.assertFalse((game/'GameAssembly.dll').exists())
            cache=Path(folder)/'cache';cache.mkdir();key='A'*64;digest='B'*64
            (cache/'current.tsv').write_text('EFF_RESOURCE_RELOAD_CURRENT\t1\n'+key+'\t'+digest+'\n')
            with patch.object(module.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(protocol),stderr='')) as run:
                result=module.compile_reload(author,(compiler,baseline,cache,game))
                self.assertTrue(result['offlineReloadPrepared']);self.assertEqual(result['offlineReloadKey'],key)
                self.assertEqual(run.call_args.args[0],[str(compiler),str(author.resolve()),str(baseline),str(cache)])
            before=(cache/'current.tsv').read_bytes()
            with patch.object(module.subprocess,'run',side_effect=[queried,SimpleNamespace(returncode=1,stdout='',stderr='bad skeleton')]):
                with self.assertRaisesRegex(ValueError,'bad skeleton'):module.compile_reload(author,(compiler,baseline,cache,game))
            self.assertEqual((cache/'current.tsv').read_bytes(),before)

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

    def test_manifest_byte_layout_is_checked_independently_of_abi(self):
        legacy = dict(format=module.EXPECTED_PROTOCOL, version=1, abi=35,
                      compiler=module.COMPILERS[35], authorFormat=2, resourceVersion=2)
        current = dict(legacy, structures=dict(module.STRUCTURES,
                      manifestEncoding=module.MANIFEST_ENCODING,
                      manifestInputs=[module.MANIFEST_ENCODING, *module.LEGACY_ENCODINGS.values()]))
        with tempfile.TemporaryDirectory() as folder:
            compiler = Path(folder) / 'eff_resource_pack.exe'
            # Equal release labels do not make different headers readable.
            with patch.object(module.subprocess, 'run', return_value=SimpleNamespace(
                    returncode=0, stdout=json.dumps(current), stderr='')):
                with self.assertRaisesRegex(ValueError, '清单字段布局'):
                    module._compiler_protocol(compiler, legacy)
            # The new reader accepts both known layouts regardless of labels.
            for marker in module.COMPILERS.values():
                old = dict(legacy, abi=999, compiler=marker, authorFormat=999,
                           resourceVersion=999, version=999)
                with patch.object(module.subprocess, 'run', return_value=SimpleNamespace(
                        returncode=0, stdout=json.dumps(old), stderr='')):
                    module._compiler_protocol(compiler, current)
            future = dict(current, abi=999, version=999, compiler='arbitrary-build')
            with patch.object(module.subprocess, 'run', return_value=SimpleNamespace(
                    returncode=0, stdout=json.dumps(future), stderr='')):
                module._compiler_protocol(compiler, current)

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

    def test_failed_layout_check_does_not_publish_a_candidate(self):
        with tempfile.TemporaryDirectory() as folder:
            game = Path(folder)
            declaration = game / 'plugin/package-protocol.json'
            declaration.parent.mkdir()
            legacy = dict(format=module.EXPECTED_PROTOCOL, version=1, abi=35,
                          compiler=module.COMPILERS[35], authorFormat=2, resourceVersion=2)
            declaration.write_text(json.dumps(legacy))
            cache = game / 'cache'; cache.mkdir()
            before = b'previous complete publication'
            (cache / 'current.tsv').write_bytes(before)
            current = dict(legacy, structures=dict(module.STRUCTURES))
            with patch.object(module.subprocess, 'run', return_value=SimpleNamespace(
                    returncode=0, stdout=json.dumps(current), stderr='')) as run:
                with self.assertRaisesRegex(ValueError, '清单字段布局'):
                    module.compile_reload(game / 'author', (game / 'compiler.exe',
                        game / 'baseline', cache, game))
                self.assertEqual(run.call_count, 1)
            self.assertEqual((cache / 'current.tsv').read_bytes(), before)

    def test_first_static_export_compiles_without_preexisting_baseline(self):
        with tempfile.TemporaryDirectory() as folder:
            game=Path(folder);author=game/'plugin/mods/NewRole';author.mkdir(parents=True)
            compiler=game/'plugin/tools/eff_resource_pack.exe';compiler.parent.mkdir();compiler.write_bytes(b'fixture')
            protocol=dict(format=module.EXPECTED_PROTOCOL,compiler=module.COMPILERS[35],
                          staticSourceInputs=module.STATIC_SOURCE_INPUTS,modSetVersion=1,
                          arguments=module.COMPILER_ARGUMENTS,structures=dict(module.STRUCTURES,
                              modSet=module.MOD_SET_STRUCTURE,manifestEncoding=module.MANIFEST_ENCODING,manifestInputs=[module.MANIFEST_ENCODING]))
            reply=SimpleNamespace(returncode=0,stdout=json.dumps(protocol),stderr='')
            cache=game/'plugin/resource-reload-cache';cache.mkdir()
            (cache/'current.tsv').write_text('EFF_RESOURCE_RELOAD_CURRENT\t1\n'+'A'*64+'\t'+'B'*64+'\n')
            with patch.object(module.subprocess,'run',return_value=reply) as run:
                context=module.reload_context(author)
                result=module.compile_reload(author,context)
                self.assertTrue(result['offlineReloadPrepared'])
                self.assertEqual(run.call_args.args[0][1],'--mods')
            self.assertFalse((game/'plugin/resource-reload-baseline/baseline.tsv').exists())

    def test_missing_static_target_rejects_without_reporting_old_current(self):
        with tempfile.TemporaryDirectory() as folder:
            game=Path(folder);author=game/'plugin/mods/NewRole';author.mkdir(parents=True)
            baseline=game/'plugin/resource-reload-baseline';baseline.mkdir();(baseline/'baseline.tsv').write_text('fixture')
            cache=game/'plugin/resource-reload-cache';cache.mkdir();before=b'previous complete candidate';(cache/'current.tsv').write_bytes(before)
            protocol=dict(format=module.EXPECTED_PROTOCOL,compiler=module.COMPILERS[35],modSetVersion=1,
                          arguments=module.COMPILER_ARGUMENTS,staticSourceInputs=module.STATIC_SOURCE_INPUTS)
            with patch.object(module.subprocess,'run',side_effect=[
                    SimpleNamespace(returncode=0,stdout=json.dumps(protocol),stderr=''),
                    SimpleNamespace(returncode=1,stdout='',stderr='static source manifest does not cover current INI targets')]):
                with self.assertRaisesRegex(ValueError,'static source manifest'):
                    module.compile_reload(author,(game/'compiler.exe',baseline,cache,game))
            self.assertEqual((cache/'current.tsv').read_bytes(),before)

    def test_static_preparation_uses_staged_mod_and_existing_author_index(self):
        with tempfile.TemporaryDirectory() as folder:
            game=Path(folder)/'game';stage=Path(folder)/'staged';stage.mkdir()
            source=Path(folder)/'workspace/exports/character.eff';source.mkdir(parents=True)
            index=Path(folder)/'workspace/index/endfield_assets.eidx';index.parent.mkdir();index.touch()
            tool=game/'plugin/tools/static-sources/EndfieldVfsProbe.exe';tool.parent.mkdir(parents=True);tool.touch()
            protocol=dict(format=module.EXPECTED_PROTOCOL,structures=dict(module.STRUCTURES),staticSourceInputs=module.STATIC_SOURCE_INPUTS)
            def invoke(command, **kwargs):
                if command[-1]=='--protocol':
                    return SimpleNamespace(returncode=0,stdout=json.dumps(protocol),stderr='')
                self.assertEqual(command[1:5],['--prepare-static-sources',str(game),str(index),str(stage)])
                (stage/'source-inputs.bin').write_bytes(b'generated')
                return SimpleNamespace(returncode=0,stdout='STATIC-SOURCES-PREPARED',stderr='')
            with patch.object(module.subprocess,'run',side_effect=invoke),patch.dict(module.os.environ,{},clear=True):
                module.prepare_static_sources(stage,source,(game/'compiler.exe',game/'baseline',game/'cache',game))
            self.assertEqual((stage/'source-inputs.bin').read_bytes(),b'generated')

    def test_full_export_seeds_descriptor_but_still_checks_new_declarations(self):
        with tempfile.TemporaryDirectory() as folder:
            game=Path(folder);previous=game/'published';stage=game/'stage';source=game/'source'
            for path in (previous,stage,source):path.mkdir()
            descriptor=previous/'source-inputs.bin';descriptor.write_bytes(b'previous-validated-descriptor')
            tool=game/'plugin/tools/static-sources/EndfieldVfsProbe.exe';tool.parent.mkdir(parents=True);tool.touch()
            protocol=dict(format=module.EXPECTED_PROTOCOL,structures=dict(module.STRUCTURES),staticSourceInputs=module.STATIC_SOURCE_INPUTS)
            requests=[]
            def invoke(command,**kwargs):
                if command[-1]=='--protocol':return SimpleNamespace(returncode=0,stdout=json.dumps(protocol),stderr='')
                requests.append(command)
                self.assertEqual((stage/'source-inputs.bin').read_bytes(),b'previous-validated-descriptor')
                # Changed declarations are assessed by the source tool, not by
                # the exporter. Replacing the seed must not change publication.
                (stage/'source-inputs.bin').write_bytes(b'new-target-descriptor')
                return SimpleNamespace(returncode=0,stdout='STATIC-SOURCES-PREPARED',stderr='')
            with patch.object(module.subprocess,'run',side_effect=invoke),patch.dict(module.os.environ,{},clear=True):
                module.prepare_static_sources(stage,source,(game/'compiler.exe',game/'baseline',game/'cache',game),previous=previous)
            self.assertEqual(len(requests),1)
            self.assertEqual(descriptor.read_bytes(),b'previous-validated-descriptor')
            self.assertEqual((stage/'source-inputs.bin').read_bytes(),b'new-target-descriptor')

if __name__=='__main__':unittest.main()

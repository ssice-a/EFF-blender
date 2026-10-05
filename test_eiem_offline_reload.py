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
    def test_game_and_workspace_exports_use_same_compiler(self):
        with tempfile.TemporaryDirectory() as folder:
            game=Path(folder)/'game';baseline=game/'plugin/resource-reload-baseline';baseline.mkdir(parents=True)
            (baseline/'baseline.tsv').write_text('baseline');(game/'GameAssembly.dll').write_bytes(b'game')
            compiler=game/'plugin/tools/eff_resource_pack.exe';compiler.parent.mkdir();compiler.write_bytes(b'exe')
            author=game/'plugin/mods/LZY';author.mkdir(parents=True)
            with patch.dict(module.os.environ,{'EFF_RELOAD_GAME':str(game)}):
                self.assertEqual(module.reload_context(author)[2],game/'plugin/resource-reload-cache')
                exported=Path(folder)/'LZY';self.assertEqual(module.reload_context(exported)[2],Path(folder)/'LZY.reload')
            cache=Path(folder)/'cache';cache.mkdir();key='A'*64;digest='B'*64
            (cache/'current.tsv').write_text('EFF_RESOURCE_RELOAD_CURRENT\t1\n'+key+'\t'+digest+'\n')
            with patch.object(module.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='',stderr='')) as run:
                result=module.compile_reload(author,(compiler,baseline,cache,game/'GameAssembly.dll'))
                self.assertTrue(result['offlineReloadPrepared']);self.assertEqual(result['offlineReloadKey'],key)
                self.assertEqual(run.call_args.args[0],[str(compiler),str(author.resolve()),str(baseline),str(cache),str(game/'GameAssembly.dll')])
            before=(cache/'current.tsv').read_bytes()
            with patch.object(module.subprocess,'run',return_value=SimpleNamespace(returncode=1,stdout='',stderr='bad skeleton')):
                with self.assertRaisesRegex(ValueError,'bad skeleton'):module.compile_reload(author,(compiler,baseline,cache,game/'GameAssembly.dll'))
            self.assertEqual((cache/'current.tsv').read_bytes(),before)

if __name__=='__main__':unittest.main()

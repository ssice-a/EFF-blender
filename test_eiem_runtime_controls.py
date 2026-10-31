import copy
import os
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from nativepack.ini_package import export_directory,update_directory,ini_document
from nativepack.ini_reader import read_package
from nativepack.runtime_controls import material_decode,effect_encode,effect_decode,publish_effect_mod,apply,native_build,native_archive
from nativepack.resource_file import decode_resource
spec=importlib.util.spec_from_file_location('runtime_fixture',Path(__file__).parents[1]/'nativepack/test_directory_package.py')
fixture_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture_module);fixture=fixture_module.fixture

def effect_builds():
    configured=os.environ.get('EFF_EFFECT_TEST_BUILD')
    if configured:
        path=Path(configured)/'runtime.bin';native_build(path);return [path]
    candidates=(Path(__file__).parents[2]/'bin/effects-runtime-validation-20261019').glob('*/runtime.bin')
    result=[]
    for path in candidates:
        try:native_build(path)
        except ValueError:continue
        result.append(path)
    return result

class RuntimeControlsTests(unittest.TestCase):
    def test_effect_toggle_keys_share_one_binary_state(self):
        row=dict(id='petals',label='Petals',autoplay=True,keys=['F7','CTRL+J'])
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);root.mkdir(exist_ok=True)
            (root/'mod.ini').write_text('[Mod]\nformat=2\nid=Effects\n\n[Constants]\n',encoding='utf-8')
            with patch('nativepack.runtime_controls.effect_encode',return_value=b'effect fixture'):
                apply(root,ini_document(root),effects=[row])
            ini=ini_document(root);variable='$fx_petals'
            bindings=[section for section in ini.sections() if section.startswith('KeyRuntime')]
            self.assertEqual(len(bindings),2)
            self.assertEqual({ini[name]['key'] for name in bindings},{'F7','CTRL+J'})
            self.assertTrue(all(ini[name]['type']=='cycle' and ini[name][variable]=='0,1' for name in bindings))
            self.assertEqual(ini['Constants']['persist '+variable],'1')

    def test_merge_selected_effects_preserves_other_effects_and_controls(self):
        candidates=effect_builds()
        if not candidates:self.skipTest('No native effect fixture')
        row=dict(id='first',label='First',part='PartBody',bone='Bip001/Head',build=str(candidates[0].parent),key='F7')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);data=self.data();data['effects']=[row,dict(row,id='second',key='F8')]
            export_directory(root,**data)
            second=root/'resources/effectattachment_second.bin';before=second.read_bytes()
            ini=ini_document(root);other_sections={s:dict(ini[s]) for s in ini.sections() if not s.endswith(('first','fx_first')) and s!='Constants'}
            apply(root,ini,effects=[dict(row,bone='Bip001/Body')],merge_effects=True)
            package=read_package(root)
            self.assertEqual(len(package['effects']),2);self.assertEqual(second.read_bytes(),before)
            self.assertEqual(next(e['bone'] for e in package['effects'] if e['id']=='first'),'Bip001/Body')
            self.assertTrue(all(dict(ini[s])==v for s,v in other_sections.items()))
            self.assertEqual(len(package['materialAnimations']),1)
    def test_standalone_publish_roundtrip_and_state_retention(self):
        candidates=effect_builds()
        if not candidates:self.skipTest('No native effect fixture')
        row=dict(id='petals',label='Petals',part='',bone='Bip001/Head',build=str(candidates[0].parent),keys=['F7','CTRL+J'],targetPrefab='assets/actor.prefab',rendererPath='Mesh/body',targetPath='assets/body.mesh',targetAsset='Body',bones=['Bip001/Head'])
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'Petals';publish_effect_mod(target,'Petals',[row]);package=read_package(target)
            self.assertFalse(package['parts']);self.assertFalse(package['rules']);self.assertEqual(package['effects'][0]['targetAsset'],'Body')
            bindings=[binding for binding in package['keyBindings'] if binding['variable']=='$fx_petals']
            self.assertEqual({binding['key'] for binding in bindings},{'F7','CTRL+J'})
            self.assertTrue(all(binding['type']=='cycle' and binding['values']==[0,1] for binding in bindings))
            (target/'state.ini').write_text('test=preserve',encoding='utf-8');publish_effect_mod(target,'Petals',[row]);self.assertEqual((target/'state.ini').read_text('utf-8'),'test=preserve')
            nested=target/'group'/'Child';nested.mkdir(parents=True);export_directory(nested,**fixture())
            nested_files={p.relative_to(nested).as_posix():p.read_bytes() for p in nested.rglob('*') if p.is_file()}
            publish_effect_mod(target,'Petals',[row])
            self.assertEqual({p.relative_to(nested).as_posix():p.read_bytes() for p in nested.rglob('*') if p.is_file()},nested_files)
            self.assertEqual(len(read_package(target)['effects']),1)
            (target/'mod.ini').write_text('[Mod]\nformat=2\nid=Character\n',encoding='utf-8')
            with self.assertRaises(ValueError):publish_effect_mod(target,'Petals',[row])
    def data(self):
        data=fixture();mid=data['materials'][0]['id']
        data['material_clips']=[dict(id='matflow',label='Emission flow',key='F6',duration=1,speed=1,loop=True,autoplay=True,
            targets=[dict(variable='$matflow_0',material=mid,property='_SpecialDissolveScheduleOffset',kind='FLOAT',component=0,samples=[0,.5,1])])]
        return data
    def test_material_roundtrip_partial_and_removal(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);data=self.data();package=export_directory(root,**data)
            self.assertEqual(len(package['materialAnimations']),1);clip=package['materialAnimations'][0]
            self.assertEqual(clip['tracks']['$matflow_0'],(0,.5,1))
            file=root/'resources/materialanimation_matflow.bin';before=file.read_bytes()
            update_directory(root,parts=data['parts']);self.assertEqual(file.read_bytes(),before)
            update_directory(root,materials=data['materials'],textures=data['textures'],material_clips=[])
            self.assertFalse(file.exists());self.assertFalse(read_package(root)['materialAnimations'])

    def test_duplicate_channels_and_missing_material_rejected(self):
        for mutation in ('duplicate','missing'):
            with tempfile.TemporaryDirectory() as folder:
                data=self.data();targets=data['material_clips'][0]['targets']
                if mutation=='duplicate':targets.append(dict(targets[0],variable='$other'))
                else:targets[0]['material']='MaterialMissing'
                with self.assertRaises(ValueError):export_directory(Path(folder),**data)
    def test_real_effect_embedded_and_corruption_rejected(self):
        candidates=effect_builds()
        if not candidates:self.skipTest('No native effect fixture')
        row=dict(id='smoke',label='Smoke',part='PartBody',bone='Bip001/Head',build=str(candidates[0].parent),autoplay=True,key='F7')
        data=effect_encode('Test',row);decoded=effect_decode(decode_resource(data,10));self.assertEqual(decoded['bone'],row['bone'])
        damaged=bytearray(data);damaged[-1]^=1
        with self.assertRaises(ValueError):effect_decode(decode_resource(damaged,10))

if __name__=='__main__':unittest.main()

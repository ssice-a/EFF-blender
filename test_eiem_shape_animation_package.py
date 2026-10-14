import importlib.util
import copy
from pathlib import Path
import tempfile
import unittest
from nativepack.ini_package import export_directory,update_directory,ini_document,save_ini
from nativepack.ini_reader import read_package
from nativepack.shape_animation import encode,decode,KIND
from nativepack.resource_file import decode_resource

spec=importlib.util.spec_from_file_location('shape_fixture',Path(__file__).parents[1]/'nativepack/test_directory_package.py')
fixture_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture_module)
def fixture():
    data=fixture_module.fixture();part=data['parts'][0]
    shape=part['shapeControls'][0]['shape']
    part['shapeAnimations']=[dict(id='wing',label='翅膀运动',key='F6',duration=1,speed=1,loop=True,autoplay=False,channels={shape:[0,.5,1]})]
    return data
class AnimationTests(unittest.TestCase):
    def test_multiple_groups_can_reference_the_same_mesh_and_channel(self):
        data=fixture();alternative=copy.deepcopy(data['parts'][0]['shapeAnimations'][0]);alternative.update(id='fold',label='收拢',key='F7')
        next(iter(alternative['channels'].values()))[:]=[1,.5,0]
        data['parts'][0]['shapeAnimations'].append(alternative)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);package=export_directory(root,**data)
            clips=package['shapeAnimations'];self.assertEqual(len(clips),2)
            self.assertEqual(set(clips[0]['tracks']),set(clips[1]['tracks']))
            self.assertNotEqual(clips[0]['speedVariable'],clips[1]['speedVariable'])
    def test_multi_mesh_group_has_one_clock_and_speed_and_requires_complete_update(self):
        data=fixture();second=copy.deepcopy(data['parts'][0]);second.update(partId='second-part',object='MeshSecond')
        next(iter(second['shapeAnimations'][0]['channels'].values()))[:]=[1,.5,0]
        data['parts'].append(second);data['rules'][0]['parts'].append(second['partId'])
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);package=export_directory(root,**data)
            self.assertEqual(len(package['shapeAnimations']),1)
            clip=package['shapeAnimations'][0];self.assertEqual(len(clip['tracks']),2)
            self.assertEqual(sorted(t[0] for t in clip['tracks'].values()),[0,1])
            path=root/'resources/shape_animation_wing.bin';before=path.read_bytes()
            with self.assertRaisesRegex(ValueError,'all parts'):update_directory(root,parts=[data['parts'][0]])
            self.assertEqual(path.read_bytes(),before)
            for part in data['parts']:part['shapeAnimations'][0]['speed']=2
            update_directory(root,parts=data['parts']);updated=read_package(root)['shapeAnimations']
            self.assertEqual(len(updated),1);self.assertEqual(updated[0]['speed'],2);self.assertEqual(len(updated[0]['tracks']),2)
            self.assertEqual(len(list(root.glob('resources/shape_animation_*.bin'))),1)
        second['shapeAnimations'][0]['speed']=3
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError,'conflicting settings'):export_directory(Path(folder),**data)
    def test_roundtrip_partial_preserves_geometry_and_texture_updates_preserve_clip(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);data=fixture();package=export_directory(root,**data)
            self.assertEqual(len(package['shapeAnimations']),1)
            path=root/'resources/shape_animation_wing.bin';old=path.read_bytes()
            geometry={p:p.read_bytes() for p in root.glob('meshes/*')}
            update_directory(root,textures=data['textures'],materials=data['materials'],texture_only=True)
            self.assertEqual(old,path.read_bytes())
            data['parts'][0]['shapeAnimations'][0]['speed']=2
            update_directory(root,parts=data['parts'])
            updated=read_package(root)['shapeAnimations'][0]
            self.assertEqual(updated['speed'],2)
            self.assertEqual(geometry,{p:p.read_bytes() for p in root.glob('meshes/*')})
            data['parts'][0]['shapeAnimations']=[];update_directory(root,parts=data['parts'])
            self.assertFalse(path.exists());self.assertEqual(read_package(root)['shapeAnimations'],[])
    def test_invalid_domain_and_unknown_channel_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            data=fixture();next(iter(data['parts'][0]['shapeAnimations'][0]['channels'].values()))[1]=99
            with self.assertRaisesRegex(ValueError,'range'):export_directory(Path(folder),**data)
    def test_codec_truncation(self):
        clip=dict(id='a',label='test',speedVariable='$speed',duration=1,speed=1,loop=True,autoplay=False)
        result=decode(decode_resource(encode('Test',clip,[('$shape',[0,.2,1])]),KIND))
        self.assertAlmostEqual(result['tracks']['$shape'][1],.2)
        bad=decode_resource(encode('Test',clip,[('$shape',[0,.2,1])]),KIND);bad['blocks']['@shape-animation']=bad['blocks']['@shape-animation'][:-1]
        with self.assertRaises(ValueError):decode(bad)
    def test_mesh_update_removes_clip_when_controls_are_disabled(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);data=fixture();export_directory(root,**data)
            part=data['parts'][0];part['shapeAnimations']=[];part['shapeControls'][0]['enabled']=False
            update_directory(root,parts=[part])
            self.assertEqual(read_package(root)['shapeAnimations'],[])
            self.assertFalse((root/'resources/shape_animation_wing.bin').exists())
    def test_animation_requires_its_own_declaration_and_speed_variable(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);data=fixture();export_directory(root,**data)
            ini=ini_document(root);ini['ResourceHidden']=dict(ini['ShapeAnimationwing']);ini.remove_section('ShapeAnimationwing');save_ini(root,ini)
            with self.assertRaisesRegex(ValueError,'ShapeAnimation declaration'):read_package(root)
        for alias_shape in (True,False):
            with self.subTest(alias_shape=alias_shape), tempfile.TemporaryDirectory() as folder:
                root=Path(folder);data=fixture();data['parts'][0]['shapeControls'][0]['default']=1
                package=export_directory(root,**data);clip=package['shapeAnimations'][0];tracks=list(clip['tracks'].items())
                if alias_shape:
                    clip['speedVariable']=tracks[0][0]
                else:
                    clip['id']='other'
                    ini=ini_document(root);ini['ShapeAnimationother']={'path':'resources/shape_animation_other.bin'};save_ini(root,ini)
                (root/('resources/shape_animation_'+clip['id']+'.bin')).write_bytes(encode('Test',clip,tracks))
                with self.assertRaisesRegex(ValueError,'speed variable aliases'):read_package(root)
if __name__=='__main__':unittest.main()

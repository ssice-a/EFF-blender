import copy
import struct
import unittest
import numpy as np
import eiem_effect_timeline as timeline
import eiem_effect_vat as vat
import eiem_effect_trails as trails
from test_eiem_effect_simulation import particle_data, constant


class SourcePreviewTests(unittest.TestCase):
    def test_sparse_stream_coefficients_and_stepped_segments(self):
        raw=b''.join(struct.pack('<fi',t,len(keys))+b''.join(struct.pack('<i4f',i,*c) for i,c in keys)
            for t,keys in [(-3.4e38,[(0,(0,0,0,7))]),(0,[(0,(2,3,4,5)),(1,(0,0,0,8))]),
                           (1,[(0,(0,0,0,14))]),(float('inf'),[])])
        curves=timeline.streamed_curves(list(struct.unpack('<%dI'%(len(raw)//4),raw)),2)
        self.assertEqual(timeline.sample_stream(curves[0],.5),8)
        self.assertEqual(timeline.sample_stream(curves[0],2),14)
        self.assertEqual(timeline.sample_stream(curves[1],100),8)
        self.assertEqual(timeline.sample_stream(curves[0],-1),7)
        with self.assertRaises(ValueError):timeline.streamed_curves([0,500],2)

    def test_material_hashes_and_texture_st_resolution(self):
        data=dict(m_SavedProperties=dict(m_Floats=[dict(first='_displayFrame',second=1)],
            m_Colors=[],m_TexEnvs=[dict(first='_DissolveTex',second={})]))
        self.assertEqual(timeline.material_property(data,2284710970),('_displayFrame','m_Floats',0))
        self.assertEqual(timeline.material_property(data,597292450),('_DissolveTex_ST','m_TexEnvs',0))
        self.assertEqual((597292450>>28)&3,2)
        self.assertEqual(timeline.crc('center/vertex_animation_textures1_mesh/MESH'),862993540)

    def test_float_sampling_preserves_negative_and_hdr_values(self):
        pixels=np.array([[[2,-3,4,1],[5,6,7,1]],[[8,9,10,1],[11,12,13,1]]],dtype=np.float16)
        sampled=vat.sample_texture(pixels,[(.25,.25),(1.25,.25),(-.25,.25)],{})
        np.testing.assert_array_equal(sampled,pixels[0,[0,0,1]])
        center=vat.sample_texture(pixels,[(.5,.5)],dict(m_FilterMode=1))
        np.testing.assert_array_equal(center,pixels.astype(np.float32).mean(axis=(0,1))[None,:])
        clamped=vat.sample_texture(pixels,[(2,2)],dict(m_WrapU=1,m_WrapV=1))
        np.testing.assert_array_equal(clamped,pixels[1,1][None,:])

    def test_softbody_is_offset_plus_base_and_integer_frame_wrap(self):
        positions=np.zeros((4,2,4),dtype=np.float16)
        positions[:,:,3]=1;positions[3,:,0]=2;positions[1,:,0]=5
        rotations=np.zeros_like(positions);rotations[:,:,3]=1
        mesh=dict(vertices=np.array([[10,20,30],[40,50,60]],dtype=np.float32),
                  normals=None,uvs={1:np.array([[.25,.875],[.75,0]])})
        floats=dict(_frameCount=2,_TextureFormat=1,_displayFrame=1,_boundMaxX=1,_boundMinZ=0)
        source=copy.deepcopy(mesh)
        a,n,frame=vat.softbody(mesh,floats,(positions,dict(settings={})),(rotations,dict(settings={})))
        np.testing.assert_array_equal(a,[[12,20,30],[0,0,0]])
        np.testing.assert_allclose(n,[[0,1,0],[0,1,0]])
        floats['_displayFrame']=2.9
        b,_,frame=vat.softbody(mesh,floats,(positions,dict(settings={})),None)
        self.assertEqual(frame,2);np.testing.assert_array_equal(b[0],[15,20,30])
        floats['_displayFrame']=3
        c,_,_=vat.softbody(mesh,floats,(positions,dict(settings={})),None)
        np.testing.assert_array_equal(c,a)
        np.testing.assert_array_equal(mesh['vertices'],source['vertices'])

    def test_seekable_trails_dead_retention_and_ribbon_groups(self):
        data=particle_data(8)
        data['InitialModule']['startLifetime']=constant(.6)
        data['TrailModule']=dict(enabled=True,mode=0,ratio=1,lifetime=constant(1),dieWithParticles=True)
        original=copy.deepcopy(data)
        first,_=trails.paths(data,1.2,5)
        trails.paths(data,1.8,5)
        again,_=trails.paths(data,1.2,5)
        self.assertTrue(first);self.assertEqual(first,again);self.assertEqual(data,original)
        live={p['id'] for p in trails.sim.particles(data,1.2,5)}
        self.assertTrue(all(path[0][1]['id'] in live for path in first))
        data['TrailModule']['dieWithParticles']=False
        retained,_=trails.paths(data,1.2,5)
        self.assertTrue(any(path[0][1]['id'] not in live for path in retained))
        data['TrailModule'].update(mode=1,ribbonCount=2)
        ribbons,_=trails.paths(data,1.2,5)
        self.assertEqual(len(ribbons),2)
        data['TrailModule']['splitSubEmitterRibbons']=True
        self.assertEqual(trails.paths(data,1.2,5)[0],[])


if __name__=='__main__':unittest.main()

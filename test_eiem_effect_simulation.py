import copy
import math
import random
import unittest
import eiem_effect_simulation as sim

def constant(value):return dict(minMaxState=0,scalar=value)
def particle_data(rate=10):
    return dict(lengthInSec=2,looping=True,InitialModule=dict(startLifetime=constant(3),startSpeed=constant(2),maxNumParticles=100),EmissionModule=dict(enabled=True,rateOverTime=constant(rate)))
def gradient(alpha0,alpha1):
    return dict(key0=dict(r=1,g=0,b=0,a=alpha0),key1=dict(r=0,g=0,b=1,a=alpha1),ctime0=0,ctime1=65535,atime0=0,atime1=65535,m_NumColorKeys=2,m_NumAlphaKeys=2,m_Mode=0)

class SimulationTests(unittest.TestCase):
    def test_curved_emission_cache_invalidates_and_seek_is_stable(self):
        data=particle_data();rate=dict(minMaxState=1,scalar=20,maxCurve=dict(m_Curve=[dict(time=0,value=.2,outSlope=.8),dict(time=1,value=1,inSlope=.8)]))
        data['EmissionModule']['rateOverTime']=rate
        sim.emission_birth.cache_clear()
        a=sim.particles(data,6);b=sim.particles(data,6.2);before={p['id']:p['born'] for p in a}
        for p in b:
            if p['id'] in before:self.assertEqual(p['born'],before[p['id']])
        misses=sim.emission_birth.cache_info().misses
        data['InitialModule']['startSize']=constant(2)
        self.assertEqual([p['born'] for p in sim.particles(data,6)],[p['born'] for p in a])
        self.assertEqual(sim.emission_birth.cache_info().misses,misses)
        rate['scalar']=30
        self.assertNotEqual(len(sim.particles(data,6)),len(a))
        self.assertGreater(sim.emission_birth.cache_info().misses,misses)
    def test_integral_splits_at_uneven_keys_and_preserves_tails(self):
        keys=[dict(time=.2,value=2,outSlope=-3),dict(time=.23,value=1,inSlope=1,outSlope=2),dict(time=.8,value=4,inSlope=0)]
        data=dict(minMaxState=1,scalar=3,maxCurve=dict(m_Curve=keys))
        for end in (.05,.21,.6,1.3):
            n=20000;step=end/n
            reference=sum(sim.scalar(data,(i+.5)*step) for i in range(n))*step
            self.assertAlmostEqual(sim.integrate(data,end),reference,places=5)
        lower=dict(m_Curve=[dict(time=0,value=.2),dict(time=1,value=.7)])
        dual=dict(data,minMaxState=2,minCurve=lower)
        self.assertAlmostEqual(sim.integrate(dual,.6,0),3*sim.curve_integral(lower['m_Curve'],.6))
    def test_count_speed_and_zero_limit(self):
        data=particle_data();p=sim.particles(data,1)
        self.assertEqual(len(p),10)
        for row in p:self.assertAlmostEqual(row['position'][2],2*row['age'])
        data['InitialModule']['maxNumParticles']=0;self.assertEqual(sim.particles(data,1),[])
    def test_seek_identity_never_depends_on_lifetime_window(self):
        data=particle_data(7.3);a=sim.particles(data,10);b=sim.particles(data,10.2)
        first={p['id']:p for p in a}
        for p in b:
            if p['id'] in first:self.assertAlmostEqual(p['born'],first[p['id']]['born'],places=8)
        self.assertEqual(a,sim.particles(data,10))
    def test_gradient_modes_and_alpha_fade(self):
        a,b=gradient(0,1),gradient(.2,.8)
        g=dict(minMaxState=3,minGradient=a,maxGradient=b)
        self.assertAlmostEqual(sim.color(g,0,.5)[3],.1)
        self.assertAlmostEqual(sim.color(g,1,.5)[3],.9)
        g=dict(minMaxState=2,minColor=dict(r=0,g=0,b=0,a=.2),maxColor=dict(r=1,g=1,b=1,a=.8))
        self.assertAlmostEqual(sim.color(g,.9,.25)[3],.35)
    def test_native_radius_rotation_and_shape_rejection(self):
        sphere=dict(enabled=True,type=1,radius=dict(value=2),m_Position=dict(x=1))
        for i in range(30):
            p,_=sim.shape_point(sphere,random.Random(i));self.assertAlmostEqual(sum((v-(1 if j==0 else 0))**2 for j,v in enumerate(p)),4)
        p,d=sim.shape_point(dict(enabled=True,type=5,m_Rotation=dict(y=90)),random.Random(3))
        self.assertAlmostEqual(d[0],1);self.assertAlmostEqual(d[2],0,places=6)
        with self.assertRaises(ValueError):sim.shape_point(dict(enabled=True,type=6),random.Random(1))
    def test_curve_integral_uv_and_burst(self):
        linear=dict(minMaxState=1,scalar=1,maxCurve=dict(m_Curve=[dict(time=0,value=0,outSlope=1),dict(time=1,value=1,inSlope=1)]))
        self.assertAlmostEqual(sim.integrate(linear,1),.5)
        uv=sim.uv_transform(dict(enabled=True,tilesX=2,tilesY=2,frameOverTime=constant(.5)),dict(phase=.5,random=.3,age=1),(.5,.5))
        self.assertEqual(uv,(.25,.25))
        data=particle_data(0);data['looping']=False;data['EmissionModule']['m_Bursts']=[dict(time=.5,cycleCount=2,repeatInterval=.25,probability=1,countCurve=constant(3))]
        self.assertEqual(len(sim.particles(data,1)),6)

    def test_uv_fps_starts_at_authored_frame_and_wraps(self):
        module=dict(enabled=True,mode=0,tilesX=2,tilesY=2,timeMode=2,fps=4,
                    startFrame=constant(.5),frameOverTime=constant(0),cycles=1)
        particle=dict(age=0,phase=0,random=.5)
        self.assertEqual(sim.uv_transform(module,particle,(0,0)),(0,0))
        particle['age']=.25
        self.assertEqual(sim.uv_transform(module,particle,(0,0)),(.5,0))
        particle['age']=.5
        self.assertEqual(sim.uv_transform(module,particle,(0,0)),(0,.5))
        module.update(animationType=1,rowIndex=1)
        particle['age']=0
        self.assertEqual(sim.uv_transform(module,particle,(0,0)),(.5,0))

if __name__=='__main__':unittest.main()

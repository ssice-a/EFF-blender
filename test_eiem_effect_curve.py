"""Native curve projection: independent reference math and lossless editing."""
import copy
import math
import random
import unittest

import eiem_effect_curve as curves
import eiem_effect_simulation as sim


class CurveTests(unittest.TestCase):
    def test_weighted_curve_matches_parametric_bezier(self):
        # The reference samples known Bezier parameters, rather than solving x.
        rng=random.Random(8)
        for _ in range(24):
            a=curves.new_key(.1,rng.uniform(-2,2));b=curves.new_key(.9,rng.uniform(-2,2))
            a.update(outSlope=rng.uniform(-6,6),outWeight=rng.random(),weightedMode=2)
            b.update(inSlope=rng.uniform(-6,6),inWeight=rng.random(),weightedMode=1)
            x=(.1,.1+.8*a['outWeight'],.9-.8*b['inWeight'],.9)
            y=(a['value'],a['value']+.8*a['outWeight']*a['outSlope'],
               b['value']-.8*b['inWeight']*b['inSlope'],b['value'])
            for i in range(101):
                u=i/100
                self.assertAlmostEqual(sim.curve([a,b],curves.bezier(*x,u)),curves.bezier(*y,u),places=7)

    def test_integral_matches_independent_midpoint_quadrature(self):
        keys=[curves.new_key(.13,2),curves.new_key(.47,-1),curves.new_key(.92,3)]
        keys[0].update(outSlope=-7,outWeight=.72,weightedMode=2)
        keys[1].update(inSlope=3,inWeight=.14,outSlope=8,outWeight=.19,weightedMode=3)
        keys[2].update(inSlope=-2,inWeight=.61,weightedMode=1)
        for end in (.05,.25,.67,1.2):
            n=20000;step=end/n
            expected=sum(sim.curve(keys,(i+.5)*step) for i in range(n))*step
            self.assertAlmostEqual(sim.curve_integral(keys,end),expected,places=7)
        self.assertEqual(curves.integral(keys,0),0)

    def model(self,factor=3):
        keys=[curves.new_key(0,0),curves.new_key(.5,1),curves.new_key(1,0)]
        for k in keys:k['nativeExtra']=12
        return curves.Model([dict(path=['VelocityModule','x','maxCurve'],title='X',
            data=dict(m_Curve=keys,m_PreInfinity=2,m_PostInfinity=2,m_RotationOrder=4),factor=factor,axis=0)])

    def test_open_is_lossless_and_cancel_buffer_is_independent(self):
        m=self.model();before=copy.deepcopy(m.channels)
        m.bounds();curves.handle(m.keys,1,'in')
        self.assertEqual(m.channels,before)
        m.move(1,.6,1.5)
        self.assertEqual(m.original,before)
        self.assertEqual(m.keys[1]['value'],.5)
        self.assertEqual(m.keys[1]['nativeExtra'],12)

    def test_independent_weighted_handles_and_negative_multiplier(self):
        m=self.model(-3);before=copy.deepcopy(m.keys[1])
        m.move_handle(1,'out',.9,-6)
        self.assertEqual(m.keys[1]['inSlope'],before['inSlope'])
        self.assertEqual(m.keys[1]['inWeight'],before['inWeight'])
        self.assertEqual(m.keys[1]['weightedMode'],2)
        t,v=curves.handle(m.keys,1,'out')
        self.assertAlmostEqual(t,.9);self.assertAlmostEqual(v,2)
        m.move_handle(1,'in',.3,0)
        self.assertEqual(m.keys[1]['weightedMode'],3)
        t,v=curves.handle(m.keys,1,'in')
        self.assertAlmostEqual(t,.3);self.assertAlmostEqual(v,0)

    def test_add_delete_presets_and_local_undo_redo(self):
        m=self.model();before=copy.deepcopy(m.channels)
        m.checkpoint();m.add(.75,6)
        self.assertEqual(m.keys[2]['value'],2)
        self.assertEqual(m.keys[2]['nativeExtra'],12)
        self.assertTrue(m.undo());self.assertEqual(m.channels,before)
        self.assertTrue(m.undo(True));self.assertEqual(len(m.keys),4)
        m.checkpoint();m.delete();self.assertEqual(m.channels,before)
        m.preset('DOWN');self.assertAlmostEqual(sim.curve(m.keys,.3),.7)
        self.assertEqual(m.channels[0]['data']['m_RotationOrder'],4)
        self.assertEqual(m.keys[0]['nativeExtra'],12)

    def test_invalid_values_do_not_mutate(self):
        m=self.model();before=copy.deepcopy(m.channels)
        for invalid in (float('nan'),float('inf')):
            for fn in (lambda:m.move(1,invalid,1),lambda:m.add(invalid,1),lambda:m.move_handle(1,'in',invalid,1)):
                with self.assertRaises(ValueError):fn()
        with self.assertRaises(ValueError):m.add(.5,1)
        self.assertEqual(m.channels,before)
        m.move(1,-100,2);self.assertGreater(m.keys[1]['time'],0)
        m.move(1,100,2);self.assertLess(m.keys[1]['time'],1)


if __name__=='__main__':unittest.main()

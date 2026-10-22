"""Motion contracts used by the Blender adapter, independent of bpy."""
import math
import random
import unittest
import eiem_effect_simulation as sim
from test_eiem_effect_simulation import constant, particle_data


class PreviewParityTests(unittest.TestCase):
    def test_speed_modifier_affects_displacement_over_the_whole_lifetime(self):
        data = particle_data(1)
        data['VelocityModule'] = dict(enabled=True, x=constant(2), speedModifier=constant(.25))
        particle = sim.particles(data, 2)[0]  # born at 1, age 1
        self.assertAlmostEqual(particle['position'][0], .5)
        self.assertAlmostEqual(particle['position'][2], .5)
        data['VelocityModule']['speedModifier'] = constant(0)
        self.assertEqual(sim.particles(data, 2)[0]['position'], [0, 0, 0])

    def test_curved_speed_modifier_is_integrated_not_applied_at_current_time(self):
        data = particle_data(1)
        data['InitialModule']['startLifetime'] = constant(2)
        data['VelocityModule'] = dict(enabled=True, speedModifier=dict(minMaxState=1, scalar=1,
            maxCurve=dict(m_Curve=[dict(time=0,value=1,outSlope=-1),dict(time=1,value=0,inSlope=-1)])))
        self.assertAlmostEqual(sim.particles(data, 2)[0]['position'][2], 1.5)

    def test_world_velocity_and_gravity_remain_separate_from_emitter_axes(self):
        data = particle_data(1)
        data['InitialModule']['gravityModifier'] = constant(1)
        data['VelocityModule'] = dict(enabled=True, inWorldSpace=True, x=constant(3))
        particle = sim.particles(data, 2)[0]
        self.assertEqual(particle['local_position'], [0, 0, 2])
        self.assertEqual(particle['world_offset'], [3, -4.905, 0])

    def test_half_arc_does_not_emit_into_the_other_half(self):
        shape = dict(enabled=True,type=10,radius=dict(value=1),arc=dict(value=180))
        for seed in range(80):
            point, _ = sim.shape_point(shape, random.Random(seed))
            self.assertGreaterEqual(point[1], -1e-9)

    def test_disabled_shape_ignores_its_preserved_transform(self):
        point, direction = sim.shape_point(dict(enabled=False,m_Position=dict(y=50),m_Rotation=dict(y=90)),random.Random(1))
        self.assertEqual(point, (0,0,0))
        self.assertEqual(direction, (0,0,1))

    def test_full_capacity_keeps_existing_particles_until_they_die(self):
        data=particle_data(10)
        data['InitialModule'].update(maxNumParticles=2,startLifetime=constant(3))
        particles=sim.particles(data,2)
        self.assertEqual([p['id'] for p in particles],[1,2])
        self.assertEqual([round(p['age'],1) for p in particles],[1.9,1.8])
        self.assertEqual([p['id'] for p in sim.particles(data,3.25)],[31,32])


if __name__ == '__main__': unittest.main()

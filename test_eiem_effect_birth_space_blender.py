"""World particles sample explicit mounts and Mesh owners at their birth time."""
import importlib.util
from pathlib import Path
import sys

import bpy

folder = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('birth_space_test_addon', folder/'__init__.py',
                                             submodule_search_locations=[str(folder)])
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()
preview = addon.eiem_blender_addon.effect_editor.preview
scene = bpy.context.scene
scene.render.fps = 24
scene.frame_start = 1

def object_at(name, data=None, parent=None):
    obj = bpy.data.objects.new(name, data)
    scene.collection.objects.link(obj)
    obj.parent = parent
    return obj

parent = object_at('weapon parent')
owner = object_at('weapon Mesh', bpy.data.meshes.new('weapon'), parent)
mount = object_at('separate authored mount', parent=owner)
root = object_at('effect root')
node = object_at('emitter', parent=root)
root.eff_effect.owner = owner
root.eff_effect.mount = mount
data = dict(moveWithTransform=1, simulationSpeed=1., startDelay=dict(minMaxState=0,scalar=0.))
particles = [dict(born=t) for t in (.25,.5,.75,1.)]

def animate(obj):
    obj.location.x = 0
    obj.keyframe_insert('location', frame=1)
    obj.location.x = 2
    obj.keyframe_insert('location', frame=49)

def assert_birth_history(source_frame=None):
    scene.frame_set(61, subframe=.25)
    matrices = preview.birth_matrices(node, particles, scene, data, root, source_frame)
    assert scene.frame_current == 61 and scene.frame_subframe == .25, 'birth sampling changed the author frame'
    assert len({round(m.translation.x,6) for m in matrices.values()}) > 1, 'world preview reused the current mount for all particle births'
    for frame, matrix in matrices.items():
        scene.frame_set(int(frame), subframe=frame-int(frame))
        expected = source_frame.at(node,(frame-scene.frame_start)/24) if source_frame else preview.emitter_matrix(root,node)
        assert max(abs(matrix[i][j]-expected[i][j]) for i in range(4) for j in range(4)) < 1e-6
    scene.frame_set(61, subframe=.25)

animate(mount)
assert_birth_history()

# Source clip evaluation must combine with the separately animated author mount.
class SourceFrame:
    transforms = {'source animation is active': {}}
    def at(self, target, time):
        matrix = preview.emitter_matrix(root,target)
        matrix.translation.y += time*.2
        return matrix
assert_birth_history(SourceFrame())
mount.animation_data_clear()
mount.location.x = 0

root.eff_effect.mount = None
for target in (owner,parent):
    animate(target)
    assert_birth_history()
    target.animation_data_clear()
    target.location.x = 0

data['moveWithTransform'] = 0
assert preview.birth_matrices(node,particles,scene,data,root) == {}, 'local particles should use the current mount'
addon.unregister()
print('PASS: explicit mount, Mesh owner and parent birth history; source clip composition; local space; frame restoration')

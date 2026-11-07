"""Codec and validation tests for the standalone weapon bone slice."""
import importlib.util
from pathlib import Path


MODULE = Path(__file__).with_name("eiem_bone_animation.py")
spec = importlib.util.spec_from_file_location("eiem_bone_animation_test", MODULE)
bone = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bone)


nodes = [
    ("WeaponRoot", -1, (0, 0, 0), (0, 0, 0, 1), (1, 1, 1)),
    ("WeaponRoot/Tip", 0, (0, 0, 1), (0, 0, 0, 1), (1, 1, 1)),
]
assert bone.decode_skeleton(bone.encode_skeleton(nodes))["nodes"] == nodes

clip = {
    "id": "weapon_swing",
    "skeleton": "weapon_rig",
    "duration": 1.0,
    "sample_rate": 30,
    "loop": True,
    "tracks": [{"path": "WeaponRoot/Tip", "samples": [
        (0, 0, 0, 0, 0, 0, 1, 1, 1, 1),
        (0, 0.5, 0, 0, 0.3826834, 0, 0.9238795, 1, 1, 1),
    ]}],
}
decoded = bone.decode_animation(bone.encode_animation(clip))
assert decoded["id"] == clip["id"] and decoded["skeleton"] == clip["skeleton"]
assert bone.sample_track(decoded["tracks"][0]["samples"], 0.5)[1] == 0.25

for invalid in (
    [("WeaponRoot", -1, (0, 0, 0), (0, 0, 0, 1), (1, 1, 1)),
     ("Wrong/Tip", 0, (0, 0, 0), (0, 0, 0, 1), (1, 1, 1))],
    [("WeaponRoot", -1, (0, 0, 0), (0, 0, 0, 2), (1, 1, 1))],
):
    try:
        bone.validate_skeleton(invalid)
        raise AssertionError("invalid skeleton accepted")
    except ValueError:
        pass

try:
    bad = dict(clip, tracks=[{"path": "WeaponRoot/Tip", "samples": [
        (0, 0, 0, 0, 0, 0, 2, 1, 1, 1),
    ]}])
    bone.validate_animation(bad)
    raise AssertionError("invalid animation accepted")
except ValueError:
    pass

print("EFF_BONE_ANIMATION_CODEC_OK")

"""Blender-independent Skeleton and sampled Bone-track codec.

The encoded Bone payload is an export-internal contribution merged into a
shared Controls Animation Clip; Format 3 does not publish a separate
BoneAnimation resource. The game DLL still needs runtime Transform playback.
"""

import math
import struct


MAGIC_SKELETON = b"EFFSKA1\0"
MAGIC_ANIMATION = b"EFFBAN1\0"
VERSION = 1
COORDINATE = "unity-y-up-left-handed"


def _finite(values, label):
    if not all(math.isfinite(float(value)) for value in values):
        raise ValueError("non-finite " + label)


def _string(value, label):
    value = str(value)
    encoded = value.encode("utf-8", "strict")
    if not encoded or b"\0" in encoded or len(encoded) > 65535:
        raise ValueError("invalid " + label)
    return encoded


def _path(value, label="bone path"):
    value = str(value)
    if (not value or value.startswith("/") or value.endswith("/") or
            "\\" in value or "//" in value or any(part in ("", ".", "..")
                                                    for part in value.split("/"))):
        raise ValueError("invalid " + label + ": " + value)
    return value


def _node_tuple(node):
    if isinstance(node, dict):
        return (node["path"], int(node["parent"]), tuple(node["position"]),
                tuple(node["rotation"]), tuple(node["scale"]))
    if len(node) != 5:
        raise ValueError("skeleton node shape differs")
    return (node[0], int(node[1]), tuple(node[2]), tuple(node[3]), tuple(node[4]))


def validate_skeleton(nodes):
    nodes = [_node_tuple(node) for node in nodes]
    if not nodes or len(nodes) > 16384:
        raise ValueError("invalid author skeleton node count")
    paths = set()
    for index, (path, parent, position, rotation, scale) in enumerate(nodes):
        path = _path(path)
        if path in paths:
            raise ValueError("duplicate author skeleton path: " + path)
        paths.add(path)
        if index == 0:
            if parent != -1 or "/" in path:
                raise ValueError("author skeleton root must have no parent")
        elif parent < 0 or parent >= index or nodes[parent][0] != path.rpartition("/")[0]:
            raise ValueError("author skeleton path/parent mismatch: " + path)
        if len(position) != 3 or len(rotation) != 4 or len(scale) != 3:
            raise ValueError("author skeleton transform shape differs: " + path)
        _finite(position, "skeleton position")
        _finite(rotation, "skeleton rotation")
        _finite(scale, "skeleton scale")
        if abs(sum(float(value) * float(value) for value in rotation) - 1.0) > 0.001:
            raise ValueError("author skeleton rotation is not normalized: " + path)
        if min(float(value) for value in scale) <= 0:
            raise ValueError("author skeleton scale is not positive: " + path)
    return nodes


def _put_string(out, value, label):
    encoded = _string(value, label)
    out.extend(struct.pack("<H", len(encoded)))
    out.extend(encoded)


def _read_string(data, offset, label):
    if offset + 2 > len(data):
        raise ValueError("truncated " + label)
    size = struct.unpack_from("<H", data, offset)[0]
    offset += 2
    if not size or offset + size > len(data):
        raise ValueError("invalid " + label)
    try:
        value = data[offset:offset + size].decode("utf-8", "strict")
    except UnicodeDecodeError as error:
        raise ValueError("invalid " + label) from error
    return value, offset + size


def encode_skeleton(nodes, coordinate=COORDINATE):
    nodes = validate_skeleton(nodes)
    if coordinate != COORDINATE:
        raise ValueError("unsupported author skeleton coordinate space")
    out = bytearray(MAGIC_SKELETON)
    out.extend(struct.pack("<II", VERSION, len(nodes)))
    _put_string(out, coordinate, "skeleton coordinate space")
    for path, parent, position, rotation, scale in nodes:
        _put_string(out, path, "skeleton path")
        out.extend(struct.pack("<i", parent))
        out.extend(struct.pack("<3f4f3f", *(float(v) for v in (*position, *rotation, *scale))))
    return bytes(out)


def decode_skeleton(data):
    data = bytes(data)
    if len(data) < 16 or data[:8] != MAGIC_SKELETON:
        raise ValueError("not an author skeleton")
    version, count = struct.unpack_from("<II", data, 8)
    if version != VERSION or not count or count > 16384:
        raise ValueError("invalid author skeleton header")
    offset = 16
    coordinate, offset = _read_string(data, offset, "skeleton coordinate space")
    if coordinate != COORDINATE:
        raise ValueError("unsupported author skeleton coordinate space")
    nodes = []
    for _ in range(count):
        path, offset = _read_string(data, offset, "skeleton path")
        if offset + 4 + 40 > len(data):
            raise ValueError("truncated author skeleton node")
        parent = struct.unpack_from("<i", data, offset)[0]
        offset += 4
        values = struct.unpack_from("<3f4f3f", data, offset)
        offset += 40
        nodes.append((path, parent, values[:3], values[3:7], values[7:]))
    if offset != len(data):
        raise ValueError("author skeleton has trailing bytes")
    return {"version": version, "coordinate": coordinate, "nodes": validate_skeleton(nodes)}


def _sample(sample):
    if len(sample) != 10:
        raise ValueError("bone animation TRS sample must contain 10 values")
    sample = tuple(float(value) for value in sample)
    _finite(sample, "bone animation sample")
    if abs(sum(value * value for value in sample[3:7]) - 1.0) > 0.001:
        raise ValueError("bone animation rotation is not normalized")
    if min(sample[7:]) <= 0:
        raise ValueError("bone animation scale is not positive")
    return sample


def validate_animation(clip):
    skeleton = _string(clip["skeleton"], "animation skeleton id").decode("utf-8")
    duration = float(clip["duration"])
    sample_rate = int(clip["sample_rate"])
    if not math.isfinite(duration) or duration <= 0 or sample_rate <= 0:
        raise ValueError("invalid bone animation timing")
    tracks = clip.get("tracks", [])
    if not tracks or len(tracks) > 16384:
        raise ValueError("invalid bone animation track count")
    seen = set()
    normalized = []
    for track in tracks:
        path = _path(track["path"], "animation bone path")
        if path in seen:
            raise ValueError("duplicate bone animation track: " + path)
        seen.add(path)
        samples = [_sample(sample) for sample in track.get("samples", [])]
        if len(samples) < 2 or len(samples) > 1_000_000:
            raise ValueError("invalid bone animation sample count: " + path)
        normalized.append({"path": path, "samples": samples})
    return {"id": str(clip["id"]), "skeleton": skeleton, "duration": duration,
            "sample_rate": sample_rate, "loop": bool(clip.get("loop", True)),
            "tracks": normalized}


def encode_animation(clip):
    clip = validate_animation(clip)
    out = bytearray(MAGIC_ANIMATION)
    out.extend(struct.pack("<I", VERSION))
    _put_string(out, clip["id"], "animation id")
    _put_string(out, clip["skeleton"], "animation skeleton id")
    out.extend(struct.pack("<fIBI", float(clip["duration"]), clip["sample_rate"],
                           int(clip["loop"]), len(clip["tracks"])))
    for track in clip["tracks"]:
        _put_string(out, track["path"], "animation bone path")
        out.extend(struct.pack("<I", len(track["samples"])))
        for sample in track["samples"]:
            out.extend(struct.pack("<10f", *sample))
    return bytes(out)


def decode_animation(data):
    data = bytes(data)
    if len(data) < 12 or data[:8] != MAGIC_ANIMATION:
        raise ValueError("not a bone animation")
    version = struct.unpack_from("<I", data, 8)[0]
    if version != VERSION:
        raise ValueError("unsupported bone animation version")
    offset = 12
    identity, offset = _read_string(data, offset, "animation id")
    skeleton, offset = _read_string(data, offset, "animation skeleton id")
    if offset + 13 > len(data):
        raise ValueError("truncated bone animation header")
    duration, sample_rate, loop, count = struct.unpack_from("<fIBI", data, offset)
    offset += 13
    tracks = []
    for _ in range(count):
        path, offset = _read_string(data, offset, "animation bone path")
        if offset + 4 > len(data):
            raise ValueError("truncated bone animation track")
        sample_count = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        byte_count = sample_count * 40
        if sample_count > 1_000_000 or offset + byte_count > len(data):
            raise ValueError("invalid bone animation track extent")
        samples = [struct.unpack_from("<10f", data, offset + i * 40)
                   for i in range(sample_count)]
        offset += byte_count
        tracks.append({"path": path, "samples": samples})
    if offset != len(data):
        raise ValueError("bone animation has trailing bytes")
    return {"version": version, **validate_animation({"id": identity,
            "skeleton": skeleton, "duration": duration, "sample_rate": sample_rate,
            "loop": bool(loop), "tracks": tracks})}


def _nlerp(a, b, amount):
    dot = sum(x * y for x, y in zip(a, b))
    if dot < 0:
        b = tuple(-value for value in b)
    value = tuple(x + (y - x) * amount for x, y in zip(a, b))
    length = math.sqrt(sum(item * item for item in value))
    return tuple(item / length for item in value)


def sample_track(samples, phase):
    if not 0 <= phase <= 1:
        raise ValueError("animation phase is outside [0, 1]")
    position = phase * (len(samples) - 1)
    left = min(int(position), len(samples) - 2)
    amount = position - left
    a, b = samples[left], samples[left + 1]
    pos = tuple(x + (y - x) * amount for x, y in zip(a[:3], b[:3]))
    rot = _nlerp(a[3:7], b[3:7], amount)
    scale = tuple(x + (y - x) * amount for x, y in zip(a[7:], b[7:]))
    return pos + rot + scale

"""Pure EIEM package format readers, writers, and path validation.

This module deliberately has no Blender dependency so protocol behavior can be
tested and reused without starting Blender.
"""

import configparser
import math
import struct
from pathlib import Path

MAGIC_MESH = b"EIEMESH\0"
MAGIC_SKEL = b"EIESKEL\0"

class Reader:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def take(self, size):
        if size < 0 or self.pos + size > len(self.data):
            raise ValueError("truncated EIEM file")
        value = self.data[self.pos:self.pos + size]
        self.pos += size
        return value

    def u8(self):
        return self.take(1)[0]

    def i32(self):
        return struct.unpack("<i", self.take(4))[0]

    def u32(self):
        return struct.unpack("<I", self.take(4))[0]

    def f32(self):
        return struct.unpack("<f", self.take(4))[0]

    def string(self):
        value = 0
        shift = 0
        while True:
            byte = self.u8()
            value |= (byte & 0x7f) << shift
            if not byte & 0x80:
                break
            shift += 7
            if shift > 28:
                raise ValueError("invalid EIEM string length")
        return self.take(value).decode("utf-8", "replace")

    def floats(self):
        count = self.i32()
        if count < 0 or count > 100000000:
            raise ValueError("invalid EIEM array length")
        return self.array("f", count)

    def array(self, code, count):
        """Read a homogeneous block with one bounds check and unpack."""
        size = struct.calcsize("<" + code) * count
        if count < 0 or self.pos + size > len(self.data):
            raise ValueError("truncated EIEM file")
        values = list(struct.unpack_from("<%d%s" % (count, code), self.data, self.pos))
        self.pos += size
        return values

    def records(self, layout, count):
        """Iterate fixed-width records without slicing/copying each scalar."""
        record = struct.Struct(layout)
        size = record.size * count
        if count < 0 or self.pos + size > len(self.data):
            raise ValueError("truncated EIEM file")
        view = memoryview(self.data)[self.pos:self.pos + size]
        self.pos += size
        return record.iter_unpack(view)


class Writer:
    def __init__(self):
        self.data = bytearray()

    def raw(self, value):
        self.data.extend(value)

    def i32(self, value):
        self.raw(struct.pack("<i", int(value)))

    def u32(self, value):
        self.raw(struct.pack("<I", int(value)))

    def f32(self, value):
        self.raw(struct.pack("<f", float(value)))

    def string(self, value):
        encoded = str(value or "").encode("utf-8")
        length = len(encoded)
        while length >= 0x80:
            self.raw(bytes(((length & 0x7f) | 0x80,)))
            length >>= 7
        self.raw(bytes((length,)))
        self.raw(encoded)

    def floats(self, values):
        values = list(values or [])
        self.i32(len(values))
        if values:
            self.raw(struct.pack("<%df" % len(values), *values))

    def uints(self, values):
        """Write a raw uint32 block; its count belongs to the caller."""
        if values:
            self.raw(struct.pack("<%dI" % len(values), *values))

    def skin(self, values):
        pack = struct.Struct("<4f4I").pack
        for weights, bones in values:
            self.raw(pack(*weights, *bones))

    def blend_vertices(self, values):
        pack = struct.Struct("<I9f").pack
        for index, position, normal, tangent in values:
            self.raw(pack(index, *position, *normal, *tangent))


def parse_ini(root):
    # Runtime Render sections may contain EIEM's conditional program syntax.
    # ConfigParser accepts the indented assignments inside those blocks, but
    # bare ``if``/``endif`` statements are not INI options and make an
    # exported package impossible to import back into Blender.  The importer
    # needs the declared resources and bindings, not execution of the switch
    # program, so remove only control-flow statements before parsing.
    lines = []
    conditional_depth = 0
    control_words = {"if", "elif", "else", "endif"}
    with open(Path(root) / "mod.ini", "r", encoding="utf-8-sig") as stream:
        for line in stream:
            stripped = line.strip()
            word = stripped.split(None, 1)[0].lower() if stripped else ""
            if word == "if":
                conditional_depth += 1
                continue
            if word == "endif":
                conditional_depth = max(0, conditional_depth - 1)
                continue
            if word in control_words or conditional_depth:
                continue
            lines.append(line)
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    parser.read_string("".join(lines), source=str(Path(root) / "mod.ini"))
    return parser


def parse_material_resources(root):
    """Read static declarations, not the Mod's executable Render/Key bodies."""
    lines, include = [], False
    with open(Path(root) / "mod.ini", "r", encoding="utf-8-sig") as stream:
        for line in stream:
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                include = stripped[1:-1].lower().startswith(("material", "texture"))
            if include:
                lines.append(line)
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read_string("".join(lines))
    return {s: dict(parser.items(s)) for s in parser.sections()}


def read_flat_properties(path):
    values = {}
    with open(path, "r", encoding="utf-8-sig") as stream:
        for line in stream:
            line = line.strip()
            if not line or line.startswith((";", "#")) or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def safe_path(root, relative):
    path = (Path(root) / relative).resolve()
    root = Path(root).resolve()
    if path != root and root not in path.parents:
        raise ValueError("EIEM path escapes package directory")
    return path


def read_mesh(path):
    reader = Reader(path.read_bytes())
    if reader.take(8) != MAGIC_MESH:
        raise ValueError("not an EIEM mesh")
    version = reader.i32()
    if version != 6:
        raise ValueError(f"unsupported EIEM mesh version {version}; re-export with the current AnimeStudio")
    coordinate = reader.string()
    source = reader.string()
    name = reader.string()
    vertex_count = reader.i32()
    if vertex_count < 0:
        raise ValueError("invalid vertex count")
    vertices = reader.floats()
    normals = reader.floats()
    tangents = reader.floats()
    colors = reader.floats()
    uvs = [reader.floats() for _ in range(8)]
    index_count = reader.i32()
    indices = reader.array("I", max(0, index_count))
    submesh_count = reader.i32()
    submeshes = list(reader.records("<i5I", max(0, submesh_count)))
    skin_count = reader.i32()
    skin = [(list(record[:4]), list(record[4:]))
            for record in reader.records("<4f4I", max(0, skin_count))]
    bind_count = reader.i32()
    bindposes = [list(record) for record in reader.records("<16f", max(0, bind_count))]
    bone_hashes = reader.array("I", max(0, reader.i32()))
    bone_paths = [reader.string() for _ in range(max(0, reader.i32()))]
    bone_index_paths = [reader.string() for _ in range(max(0, reader.i32()))]
    bone_sources = [(reader.string(), reader.string(), reader.u32())
                    for _ in range(max(0, reader.i32()))]
    candidate_slots = reader.i32()
    if candidate_slots < 0 or candidate_slots != len(bindposes):
        raise ValueError("invalid mesh bone source candidate count")
    bone_source_candidates = []
    for _ in range(candidate_slots):
        candidate_count = reader.i32()
        if candidate_count < 0 or candidate_count > 1024:
            raise ValueError("invalid mesh bone source candidate count")
        bone_source_candidates.append([
            (reader.string(), reader.string(), reader.u32())
            for _ in range(candidate_count)
        ])
    blend_vertex_count = reader.i32()
    blend_vertices = [(record[0], record[1:4], record[4:7], record[7:10])
                      for record in reader.records("<I9f", max(0, blend_vertex_count))]
    blend_frame_count = reader.i32()
    blend_frames = []
    for _ in range(max(0, blend_frame_count)):
        blend_frames.append((
            reader.string(), reader.u32(), reader.u32(),
            bool(reader.u8()), bool(reader.u8()), bool(reader.u8()),
        ))
    blend_channel_count = reader.i32()
    blend_channels = []
    for _ in range(max(0, blend_channel_count)):
        blend_channels.append((reader.string(), reader.u32(), reader.u32(), reader.u32()))
    blend_weights = reader.floats()
    additional_count = reader.i32()
    additional = list(reader.records("<3f", max(0, additional_count)))
    if vertex_count and len(vertices) < vertex_count * 3:
        raise ValueError("mesh vertex data is incomplete")
    if bone_paths and len(bone_paths) != len(bindposes):
        raise ValueError("mesh bone path palette does not match its bind poses")
    if bone_index_paths and len(bone_index_paths) != len(bindposes):
        raise ValueError("mesh bone hierarchy-index palette does not match its bind poses")
    if bone_sources and len(bone_sources) != len(bindposes):
        raise ValueError("mesh bone source palette does not match its bind poses")
    if skin and len(bone_source_candidates) != len(bindposes):
        raise ValueError("mesh bone source candidates do not match its bind poses")
    if any(len(candidate) != 3 or not candidate[0] or not candidate[1]
           for slot in bone_source_candidates for candidate in slot):
        raise ValueError("mesh bone source candidate has an invalid identity")
    if len(blend_weights) != len(blend_frames):
        raise ValueError("mesh BlendShape weights do not match its frames")
    if reader.pos != len(reader.data):
        raise ValueError("unexpected trailing EIEM mesh data")
    return locals()


def read_skeleton(path):
    reader = Reader(path.read_bytes())
    if reader.take(8) != MAGIC_SKEL:
        raise ValueError("not an EIEM skeleton")
    version = reader.i32()
    if version != 2:
        raise ValueError("unsupported EIEM skeleton version")
    coordinate = reader.string()
    count = reader.i32()
    if coordinate != "unity-y-up-left-handed" or not 0 < count <= 16384:
        raise ValueError("invalid skeleton coordinate space or node count")
    nodes = []
    for _ in range(max(0, count)):
        nodes.append((reader.string(), reader.i32(),
                      struct.unpack("<3f", reader.take(12)),
                      struct.unpack("<4f", reader.take(16)),
                      struct.unpack("<3f", reader.take(12))))
    bone_count = reader.i32()
    if not 0 <= bone_count <= 16384:
        raise ValueError("invalid skeleton palette count")
    bones = [reader.i32() for _ in range(max(0, bone_count))]
    root_bone = reader.i32()
    if reader.i32() != len(nodes):
        raise ValueError("skeleton provenance count mismatch")
    flags = list(reader.take(len(nodes)))
    if any(flag not in (0, 1) for flag in flags):
        raise ValueError("invalid skeleton provenance")
    source_nodes = [bool(flag) for flag in flags]
    if reader.pos != len(reader.data):
        raise ValueError("unexpected trailing EIEM skeleton data")
    validate_skeleton_nodes(nodes, source_nodes)
    return locals()


def validate_skeleton_nodes(nodes, source_nodes):
    if not 0 < len(nodes) <= 16384 or len(nodes) != len(source_nodes):
        raise ValueError("invalid skeleton node count")
    seen = set()
    for i, (path, parent, position, rotation, scale) in enumerate(nodes):
        if (path in seen or len(path.encode("utf-8")) > 4096 or "\\" in path or "\0" in path or
                any(part in ("", ".", "..") for part in path.split("/")) and path != ""):
            raise ValueError("invalid or duplicate skeleton path: " + path)
        seen.add(path)
        if i == 0:
            if parent != -1 or not source_nodes[i] or "/" in path:
                raise ValueError("skeleton root must reference the source hierarchy")
        else:
            if not 0 <= parent < i or not path or nodes[parent][0] != path.rpartition("/")[0]:
                raise ValueError("skeleton path/parent mismatch: " + path)
            if source_nodes[i] and not source_nodes[parent]:
                raise ValueError("source bone cannot be reparented under a new bone: " + path)
        if (not all(math.isfinite(v) for v in (*position, *rotation, *scale)) or
                abs(sum(v*v for v in rotation)-1) > .001 or min(scale) <= 0):
            raise ValueError("invalid skeleton transform: " + path)

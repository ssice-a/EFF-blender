"""Pure EFF package format readers, writers, and path validation.

This module deliberately has no Blender dependency so protocol behavior can be
tested and reused without starting Blender.
"""

import configparser
import hashlib
import io
import json
import math
import struct
import zipfile
from pathlib import Path

MAGIC_MESH = b"EFFMESH\0"
MAGIC_SKEL = b"EFFSKEL\0"
MAGIC_PHYSICS_AUTHOR3 = b"EFFPHS3\0"
PHYSICS_AUTHOR3_HEADER = struct.Struct("<8sIQQ")
MAGIC_EFFECT_AUTHOR3 = b"EFFFXA3\0"
EFFECT_AUTHOR3_HEADER = struct.Struct("<8sIQQ32s32s")

class Reader:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def take(self, size):
        if size < 0 or self.pos + size > len(self.data):
            raise ValueError("truncated EFF file")
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
                raise ValueError("invalid EFF string length")
        return self.take(value).decode("utf-8", "replace")

    def floats(self):
        count = self.i32()
        if count < 0:
            raise ValueError("invalid EFF array length")
        return self.array("f", count)

    def array(self, code, count):
        """Read a homogeneous block with one bounds check and unpack."""
        size = struct.calcsize("<" + code) * count
        if count < 0 or self.pos + size > len(self.data):
            raise ValueError("truncated EFF file")
        values = list(struct.unpack_from("<%d%s" % (count, code), self.data, self.pos))
        self.pos += size
        return values

    def records(self, layout, count):
        """Iterate fixed-width records without slicing/copying each scalar."""
        record = struct.Struct(layout)
        size = record.size * count
        if count < 0 or self.pos + size > len(self.data):
            raise ValueError("truncated EFF file")
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
    # Runtime Render sections may contain EFF's conditional program syntax.
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
        raise ValueError("EFF path escapes package directory")
    return path


def read_mesh(path):
    return decode_mesh(path.read_bytes())


def decode_mesh(data):
    reader = Reader(data)
    if reader.take(8) != MAGIC_MESH:
        raise ValueError("not an EFF mesh")
    version = reader.i32()
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
        if candidate_count < 0 or candidate_count > (len(reader.data)-reader.pos)//6:
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
        raise ValueError("unexpected trailing EFF mesh data")
    return locals()


class _AuthorMetadataReader:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def integer(self):
        if self.pos + 8 > len(self.data):
            raise ValueError("truncated format-3 author metadata")
        value, = struct.unpack_from("<Q", self.data, self.pos)
        self.pos += 8
        return value

    def string(self):
        size = self.integer()
        if size > len(self.data) - self.pos:
            raise ValueError("format-3 metadata string extent differs")
        value = self.data[self.pos:self.pos + size].decode("utf-8", "strict")
        self.pos += size
        return value

    def strings(self):
        count = self.integer()
        if count > (len(self.data) - self.pos) // 8:
            raise ValueError("format-3 metadata string count differs")
        return [self.string() for _ in range(count)]

    def skip(self, size):
        if size < 0 or size > len(self.data) - self.pos:
            raise ValueError("format-3 metadata extent differs")
        self.pos += size

    def end(self):
        if self.pos != len(self.data):
            raise ValueError("format-3 metadata has trailing bytes")


def read_effmat_snapshot(data):
    """Decode the portable, editable EFFMAT author snapshot."""
    try:
        text = data.decode("utf-8-sig", "strict")
    except UnicodeDecodeError as error:
        raise ValueError("format-3 Material author snapshot is not UTF-8") from error
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith((";", "#")):
            continue
        if "=" not in line:
            raise ValueError("format-3 Material author snapshot has an invalid line")
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if not key or key in values:
            raise ValueError("format-3 Material author snapshot has an empty or duplicate key")
        values[key] = value
    if (values.get("format") != "EFFMAT" or values.get("version") != "1" or
            values.get("overrides", "").lower() != "true" or not values.get("source", "").strip()):
        raise ValueError("format-3 Material author snapshot header differs")
    return values


def bind_effmat_texture_ids(data, texture_edits):
    """Store the same stable Texture IDs in EFFMAT and mod.ini."""
    bindings = {}
    for edit in texture_edits:
        key = "texture." + str(edit["property"])
        texture_id = str(edit["texture"])
        if key in bindings or not texture_id or "\n" in texture_id or "\r" in texture_id:
            raise ValueError("format-3 Material texture binding cannot be mapped: " + key)
        bindings[key] = texture_id
    try:
        lines = data.decode("utf-8-sig", "strict").splitlines()
    except UnicodeDecodeError as error:
        raise ValueError("format-3 Material author snapshot is not UTF-8") from error
    output = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith((";", "#")):
            output.append(line)
            continue
        if "=" not in line:
            raise ValueError("format-3 Material author snapshot has an invalid line")
        key, value = line.split("=", 1)
        key = key.strip()
        if key in bindings:
            value = bindings.pop(key)
        output.append(key + "=" + value.strip())
    if bindings:
        raise ValueError("format-3 Material texture binding cannot be mapped: " + next(iter(bindings)))
    return ("\n".join(output) + "\n").encode("utf-8")


def read_format3_mesh_resource(path):
    """Read the editable EFFMESH snapshot stored after the native Mesh bytes."""
    path = Path(path)
    header_struct = struct.Struct("<16sIIQQ32s")
    with path.open("rb") as stream:
        header = stream.read(header_struct.size)
        if len(header) != header_struct.size:
            raise ValueError("format-3 Mesh resource header is truncated")
        magic, version, kind, metadata_bytes, payload_bytes, digest = header_struct.unpack(header)
        if magic != b"EFF_AUTHOR_FILE3" or version != 3 or kind != 1:
            raise ValueError("format-3 Mesh resource envelope differs")
        if header_struct.size + metadata_bytes + payload_bytes != path.stat().st_size:
            raise ValueError("format-3 Mesh resource extent differs")
        metadata = stream.read(metadata_bytes)
        if len(metadata) != metadata_bytes or hashlib.sha256(metadata).digest() != digest:
            raise ValueError("format-3 Mesh metadata checksum differs")
        reader = _AuthorMetadataReader(metadata)
        if (reader.string(), reader.integer()) != ("EFF_AUTHOR_RESOURCE_METADATA/3", 1):
            raise ValueError("format-3 Mesh metadata structure differs")
        part, _file, _logical_path, _marker, _native_hash, _material = (
            reader.string() for _ in range(6))
        vertex_count, index_count = reader.integer(), reader.integer()
        reader.strings()  # bone paths
        reader.strings()  # shape names
        count = reader.integer()
        if count > (len(metadata) - reader.pos) // 8:
            raise ValueError("format-3 Mesh binding hash count differs")
        reader.skip(count * 8)
        count = reader.integer()
        if count > (len(metadata) - reader.pos) // 48:
            raise ValueError("format-3 Mesh bone bounds count differs")
        reader.skip(count * 48)
        reader.strings()  # selected native fields
        native_bytes, author_bytes, author_hash = (
            reader.integer(), reader.integer(), reader.string())
        reader.end()
        if (not part or native_bytes <= 0 or author_bytes <= 0 or
                native_bytes + author_bytes != payload_bytes or len(author_hash) != 64):
            raise ValueError("format-3 Mesh native/editable payload ranges differ")
        stream.seek(header_struct.size + metadata_bytes + native_bytes)
        author_data = stream.read(author_bytes)
        if len(author_data) != author_bytes or hashlib.sha256(author_data).hexdigest().upper() != author_hash.upper():
            raise ValueError("format-3 editable Mesh snapshot checksum differs")
    payload = decode_mesh(author_data)
    if payload["vertex_count"] != vertex_count or len(payload["indices"]) != index_count:
        raise ValueError("format-3 editable Mesh geometry counts differ from native Mesh metadata")
    payload["part"] = part
    payload["author_hash"] = author_hash.upper()
    return payload


def read_format3_material_resource(path, expected_id=None, expected_file=None):
    """Recover sparse EFFMAT edits from an independently addressable resource."""
    path = Path(path)
    header_struct = struct.Struct("<16sIIQQ32s")
    with path.open("rb") as stream:
        header = stream.read(header_struct.size)
        if len(header) != header_struct.size:
            raise ValueError("format-3 Material resource header is truncated")
        magic, version, kind, metadata_bytes, payload_bytes, digest = header_struct.unpack(header)
        if magic != b"EFF_AUTHOR_FILE3" or version != 3 or kind != 2:
            raise ValueError("format-3 Material resource envelope differs")
        if header_struct.size + metadata_bytes + payload_bytes != path.stat().st_size:
            raise ValueError("format-3 Material resource extent differs")
        metadata = stream.read(metadata_bytes)
        if len(metadata) != metadata_bytes or hashlib.sha256(metadata).digest() != digest:
            raise ValueError("format-3 Material metadata checksum differs")
        reader = _AuthorMetadataReader(metadata)
        if reader.string() != "EFF_AUTHOR_MATERIAL_EDITS/1":
            raise ValueError("format-3 Material metadata structure differs")
        material_id, resource_file, source_path, source_asset = (
            reader.string() for _ in range(4))
        author_bytes, author_hash = reader.integer(), reader.string()
        reader.end()
        if (not material_id or not resource_file or not source_path or not source_asset or
                len(author_hash) != 64 or author_hash.upper() != author_hash or
                author_bytes <= 0 or author_bytes != payload_bytes):
            raise ValueError("format-3 Material edit payload range differs")
        if expected_id is not None and material_id != expected_id:
            raise ValueError("format-3 Material resource ID differs from mod.ini")
        if expected_file is not None and resource_file != expected_file:
            raise ValueError("format-3 Material resource path differs from mod.ini")
        author_data = stream.read(author_bytes)
        if len(author_data) != author_bytes or hashlib.sha256(author_data).hexdigest().upper() != author_hash:
            raise ValueError("format-3 Material author snapshot checksum differs")
    values = read_effmat_snapshot(author_data)
    if values["source"] != source_path:
        raise ValueError("format-3 Material author source identity differs")
    values["_source_asset"] = source_asset
    values["_author_hash"] = author_hash
    return values


def encode_format3_physics_snapshot(document, skeleton):
    """Bundle the editable Physics document with the skeleton it references."""
    document, skeleton = bytes(document), bytes(skeleton)
    if not document.startswith(b"EFFPHYS\0") or not skeleton.startswith(MAGIC_SKEL):
        raise ValueError("format-3 Physics author inputs have invalid signatures")
    return PHYSICS_AUTHOR3_HEADER.pack(
        MAGIC_PHYSICS_AUTHOR3, 1, len(document), len(skeleton)) + document + skeleton


def decode_format3_physics_snapshot(data):
    if len(data) < PHYSICS_AUTHOR3_HEADER.size:
        raise ValueError("format-3 Physics author snapshot is truncated")
    magic, version, document_bytes, skeleton_bytes = PHYSICS_AUTHOR3_HEADER.unpack_from(data)
    if magic != MAGIC_PHYSICS_AUTHOR3 or version != 1:
        raise ValueError("format-3 Physics author snapshot structure differs")
    start = PHYSICS_AUTHOR3_HEADER.size
    if document_bytes <= 0 or skeleton_bytes <= 0 or document_bytes + skeleton_bytes != len(data) - start:
        raise ValueError("format-3 Physics author snapshot extents differ")
    document = data[start:start + document_bytes]
    skeleton = data[start + document_bytes:]
    if not document.startswith(b"EFFPHYS\0") or not skeleton.startswith(MAGIC_SKEL):
        raise ValueError("format-3 Physics author snapshot dependencies differ")
    return document, skeleton


def read_format3_physics_resource(path, expected_id=None, expected_file=None):
    """Read the runtime Physics input and editable author snapshot from one resource."""
    path = Path(path)
    header_struct = struct.Struct("<16sIIQQ32s")
    with path.open("rb") as stream:
        header = stream.read(header_struct.size)
        if len(header) != header_struct.size:
            raise ValueError("format-3 Physics resource header is truncated")
        magic, version, kind, metadata_bytes, payload_bytes, digest = header_struct.unpack(header)
        if magic != b"EFF_AUTHOR_FILE3" or version != 3 or kind != 4:
            raise ValueError("format-3 Physics resource envelope differs")
        if header_struct.size + metadata_bytes + payload_bytes != path.stat().st_size:
            raise ValueError("format-3 Physics resource extent differs")
        metadata = stream.read(metadata_bytes)
        if len(metadata) != metadata_bytes or hashlib.sha256(metadata).digest() != digest:
            raise ValueError("format-3 Physics metadata checksum differs")
        reader = _AuthorMetadataReader(metadata)
        if (reader.string(), reader.integer()) != ("EFF_AUTHOR_RESOURCE_METADATA/3", 4):
            raise ValueError("format-3 Physics metadata structure differs")
        resource_id, resource_file, native_hash = (reader.string() for _ in range(3))
        resource_kind, native_bytes = reader.integer(), reader.integer()
        count = reader.integer()
        if count > (len(metadata) - reader.pos) // 16:
            raise ValueError("format-3 Physics target metadata is truncated")
        targets = [(reader.string(), reader.string()) for _ in range(count)]
        author_bytes, author_hash = reader.integer(), reader.string()
        reader.end()
        if (not resource_id or not resource_file or resource_kind != 5 or not targets or
                len(native_hash) != 64 or native_hash.upper() != native_hash or
                native_bytes <= 0 or author_bytes <= 0 or native_bytes + author_bytes != payload_bytes or
                len(author_hash) != 64 or author_hash.upper() != author_hash):
            raise ValueError("format-3 Physics payload declarations differ")
        if expected_id is not None and resource_id != expected_id:
            raise ValueError("format-3 Physics resource ID differs from mod.ini")
        if expected_file is not None and resource_file != expected_file:
            raise ValueError("format-3 Physics resource path differs from mod.ini")
        native = stream.read(native_bytes)
        author = stream.read(author_bytes)
        if (len(native) != native_bytes or hashlib.sha256(native).hexdigest().upper() != native_hash or
                len(author) != author_bytes or hashlib.sha256(author).hexdigest().upper() != author_hash):
            raise ValueError("format-3 Physics native/author checksum differs")
    document, skeleton = decode_format3_physics_snapshot(author)
    return {"id": resource_id, "file": resource_file, "targets": targets,
            "document": document, "skeleton": skeleton}


def encode_format3_effect_snapshot(source_archive, workspace, attachment):
    source_archive = bytes(source_archive)
    author = json.dumps(
        {"format": "EFF_EFFECT_AUTHOR3", "version": 1,
         "workspace": workspace, "attachment": attachment},
        ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return EFFECT_AUTHOR3_HEADER.pack(
        MAGIC_EFFECT_AUTHOR3, 1, len(source_archive), len(author),
        hashlib.sha256(source_archive).digest(), hashlib.sha256(author).digest()
    ) + source_archive + author


def decode_format3_effect_snapshot(data):
    if len(data) < EFFECT_AUTHOR3_HEADER.size:
        raise ValueError("format-3 Effect author snapshot is truncated")
    magic, version, archive_bytes, author_bytes, archive_hash, author_hash = EFFECT_AUTHOR3_HEADER.unpack_from(data)
    start = EFFECT_AUTHOR3_HEADER.size
    if (magic != MAGIC_EFFECT_AUTHOR3 or version != 1 or not archive_bytes or not author_bytes or
            archive_bytes + author_bytes != len(data) - start):
        raise ValueError("format-3 Effect author snapshot extents differ")
    archive = data[start:start + archive_bytes]
    author = data[start + archive_bytes:]
    if (hashlib.sha256(archive).digest() != archive_hash or
            hashlib.sha256(author).digest() != author_hash):
        raise ValueError("format-3 Effect author snapshot checksum differs")
    try:
        value = json.loads(author.decode("utf-8", "strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("format-3 Effect author document is invalid") from error
    if (value.get("format") != "EFF_EFFECT_AUTHOR3" or value.get("version") != 1 or
            not isinstance(value.get("workspace"), dict) or
            not isinstance(value.get("attachment"), dict)):
        raise ValueError("format-3 Effect author document structure differs")
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as package:
            names = package.namelist()
            if (not names or len(names) != len(set(names)) or
                    any(not name or "\\" in name or ":" in name or
                        any(part in ("", ".", "..") for part in name.rstrip("/").split("/"))
                        for name in names)):
                raise ValueError("format-3 Effect source archive path is invalid")
            if "effect.json" not in names or "source/manifest.json" not in names:
                raise ValueError("format-3 Effect source archive is incomplete")
            bad = package.testzip()
            if bad:
                raise ValueError("format-3 Effect source archive CRC differs: " + bad)
    except zipfile.BadZipFile as error:
        raise ValueError("format-3 Effect source archive is invalid") from error
    value["_source_archive"] = archive
    return value


def encode_format3_effect_source(source, workspace, attachment):
    """Create an editable Effect snapshot directly from its author directory."""
    import stat
    source = Path(source).resolve()
    if not source.is_dir():
        raise ValueError("format-3 Effect source directory is absent")
    attachment = {key: value for key, value in attachment.items() if key != "build"}
    archive_stream = io.BytesIO()
    with zipfile.ZipFile(archive_stream, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6) as package:
        for file in sorted(source.rglob("*")):
            mode = file.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise ValueError("Effect source contains a symbolic link: " + str(file))
            if file.is_dir():
                continue
            if not stat.S_ISREG(mode):
                raise ValueError("Effect source contains a non-file entry: " + str(file))
            package.write(file, file.relative_to(source).as_posix())
    return encode_format3_effect_snapshot(archive_stream.getvalue(), workspace, attachment)


def write_format3_effect_snapshot(path, source, workspace, attachment):
    """Write a validated EFFFX workspace archive and its editable state."""
    import os
    import tempfile
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = encode_format3_effect_source(source, workspace, attachment)
    temporary_output = path.with_name(path.name + ".tmp")
    try:
        temporary_output.write_bytes(payload)
        os.replace(temporary_output, path)
    finally:
        if temporary_output.exists():
            temporary_output.unlink()


def _author_effect_metadata(data):
    reader = _AuthorMetadataReader(data)
    if (reader.string(), reader.integer()) != ("EFF_AUTHOR_RESOURCE_METADATA/3", 7):
        raise ValueError("format-3 Effect author metadata structure differs")
    resource_id, resource_file = reader.string(), reader.string()
    author_bytes, author_hash = reader.integer(), reader.string()
    reader.end()
    if (not resource_id.startswith("ResourceEffectAuthor") or not resource_file or
            author_bytes <= 0 or len(author_hash) != 64 or
            author_hash.upper() != author_hash):
        raise ValueError("format-3 Effect author resource declaration differs")
    return resource_id, resource_file, author_bytes, author_hash


def read_format3_effect_author_resource(path, expected_id=None, expected_file=None):
    path = Path(path)
    with path.open("rb") as stream:
        header = stream.read(72)
        if len(header) != 72:
            raise ValueError("format-3 Effect author resource header is truncated")
        magic, version, kind, metadata_bytes, payload_bytes, digest = struct.unpack("<16sIIQQ32s", header)
        if magic != b"EFF_AUTHOR_FILE3" or version != 3 or kind != 7:
            raise ValueError("format-3 Effect author resource envelope differs")
        if 72 + metadata_bytes + payload_bytes != path.stat().st_size:
            raise ValueError("format-3 Effect author resource extent differs")
        metadata = stream.read(metadata_bytes)
        if len(metadata) != metadata_bytes or hashlib.sha256(metadata).digest() != digest:
            raise ValueError("format-3 Effect author metadata checksum differs")
        resource_id, resource_file, author_bytes, author_hash = _author_effect_metadata(metadata)
        if expected_id is not None and resource_id != expected_id:
            raise ValueError("format-3 Effect author resource ID differs from mod.ini")
        if expected_file is not None and resource_file != expected_file:
            raise ValueError("format-3 Effect author resource path differs from mod.ini")
        author = stream.read(payload_bytes)
        if (len(author) != payload_bytes or payload_bytes != author_bytes or
                hashlib.sha256(author).hexdigest().upper() != author_hash):
            raise ValueError("format-3 Effect author payload checksum/extent differs")
    result = decode_format3_effect_snapshot(author)
    result.update(id=resource_id, file=resource_file)
    return result


def materialize_format3_effect_author_resource(path, expected_id, expected_file, cache_root):
    import stat
    result = read_format3_effect_author_resource(path, expected_id, expected_file)
    source = Path(cache_root).resolve() / result["id"]
    if not (source / "effect.json").is_file() or not (source / "source/manifest.json").is_file():
        temporary = source.with_name(source.name + ".tmp")
        if temporary.exists():
            import shutil
            shutil.rmtree(temporary)
        temporary.mkdir(parents=True)
        try:
            with zipfile.ZipFile(io.BytesIO(result["_source_archive"])) as package:
                total = 0
                for info in package.infolist():
                    name = info.filename
                    if (info.is_dir() or not name or "\\" in name or ":" in name or
                            any(part in ("", ".", "..") for part in name.split("/"))):
                        if info.is_dir() and name.endswith("/"):
                            continue
                        raise ValueError("format-3 Effect source archive entry is invalid: " + name)
                    mode = info.external_attr >> 16
                    if stat.S_ISLNK(mode):
                        raise ValueError("format-3 Effect source archive contains a symbolic link")
                    total += info.file_size
                    if total > 2 * 1024 * 1024 * 1024:
                        raise ValueError("format-3 Effect source archive exceeds the import size limit")
                    target = temporary.joinpath(*name.split("/"))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(package.read(info))
            if not (temporary / "effect.json").is_file() or not (temporary / "source/manifest.json").is_file():
                raise ValueError("format-3 Effect source archive is incomplete")
            source.parent.mkdir(parents=True, exist_ok=True)
            if source.exists():
                import shutil
                shutil.rmtree(source)
            temporary.replace(source)
        except BaseException:
            if temporary.exists():
                import shutil
                shutil.rmtree(temporary, ignore_errors=True)
            raise
    workspace = result["workspace"]
    workspace["source"] = str(source)
    return workspace, result["attachment"], source


def encode_mesh(payload):
    """Encode one complete editable Mesh snapshot, independent of Blender."""
    writer = Writer()
    writer.raw(MAGIC_MESH)
    writer.i32(int(payload.get("version", 6)))
    writer.string(payload["coordinate"])
    writer.string(payload.get("source", ""))
    writer.string(payload.get("name", ""))
    writer.i32(payload["vertex_count"])
    for name in ("vertices", "normals", "tangents", "colors"):
        writer.floats(payload.get(name, ()))
    for values in payload["uvs"]:
        writer.floats(values)
    indices = payload["indices"]
    writer.i32(len(indices))
    writer.uints(indices)
    writer.i32(len(payload["submeshes"]))
    for topology, start, count, base, first, vertex_count in payload["submeshes"]:
        writer.i32(topology)
        writer.u32(start)
        writer.u32(count)
        writer.u32(base)
        writer.u32(first)
        writer.u32(vertex_count)
    skin = payload["skin"]
    writer.i32(len(skin))
    writer.skin(skin)
    bindposes = payload["bindposes"]
    writer.i32(len(bindposes))
    for matrix in bindposes:
        values = list(matrix)[:16]
        values += [0.0] * (16 - len(values))
        for value in values:
            writer.f32(value)
    bone_hashes = payload["bone_hashes"]
    writer.i32(len(bone_hashes))
    writer.uints(bone_hashes)
    for name in ("bone_paths", "bone_index_paths"):
        values = payload.get(name, ())
        writer.i32(len(values))
        for value in values:
            writer.string(value)
    bone_sources = payload.get("bone_sources", ())
    writer.i32(len(bone_sources))
    for source in bone_sources:
        writer.string(source[0])
        writer.string(source[1])
        writer.u32(source[2])
    candidates = payload.get("bone_source_candidates", ())
    writer.i32(len(candidates))
    for slot in candidates:
        writer.i32(len(slot))
        for source in slot:
            writer.string(source[0])
            writer.string(source[1])
            writer.u32(source[2])
    blend_vertices = payload.get("blend_vertices", ())
    writer.i32(len(blend_vertices))
    writer.blend_vertices(blend_vertices)
    frames = payload.get("blend_frames", ())
    writer.i32(len(frames))
    for name, first, count, has_normals, has_tangents, has_additional in frames:
        writer.string(name)
        writer.u32(first)
        writer.u32(count)
        writer.raw(bytes((int(has_normals), int(has_tangents), int(has_additional))))
    channels = payload.get("blend_channels", ())
    writer.i32(len(channels))
    for name, name_hash, first, count in channels:
        writer.string(name)
        writer.u32(name_hash)
        writer.u32(first)
        writer.u32(count)
    writer.floats(payload.get("blend_weights", ()))
    additional = payload.get("additional", ())
    writer.i32(len(additional))
    for value in additional:
        for component in value:
            writer.f32(component)
    encoded = bytes(writer.data)
    decode_mesh(encoded)
    return encoded


def read_skeleton(path):
    reader = Reader(path.read_bytes())
    if reader.take(8) != MAGIC_SKEL:
        raise ValueError("not an EFF skeleton")
    version = reader.i32()
    coordinate = reader.string()
    count = reader.i32()
    if coordinate != "unity-y-up-left-handed" or not 0 < count or count > (len(reader.data)-reader.pos)//45:
        raise ValueError("invalid skeleton coordinate space or node count")
    nodes = []
    for _ in range(max(0, count)):
        nodes.append((reader.string(), reader.i32(),
                      struct.unpack("<3f", reader.take(12)),
                      struct.unpack("<4f", reader.take(16)),
                      struct.unpack("<3f", reader.take(12))))
    bone_count = reader.i32()
    if not 0 <= bone_count or bone_count > (len(reader.data)-reader.pos)//4:
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
        raise ValueError("unexpected trailing EFF skeleton data")
    validate_skeleton_nodes(nodes, source_nodes)
    return locals()


def validate_skeleton_nodes(nodes, source_nodes):
    if not 0 < len(nodes) or len(nodes) != len(source_nodes):
        raise ValueError("invalid skeleton node count")
    seen = set()
    for i, (path, parent, position, rotation, scale) in enumerate(nodes):
        if (path in seen or "\\" in path or "\0" in path or
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

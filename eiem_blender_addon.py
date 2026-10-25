bl_info = {
    "name": "EFF Resource Package",
    "author": "EFF",
    "version": (0, 49, 1),
    "blender": (5, 0, 0),
    "location": "File > Import/Export > EFF package",
    "category": "Import-Export",
}

import configparser
import hashlib
import json
import os
import re
import shutil
import struct
import sys
import zlib
import math
import uuid
import importlib.util
import threading
import subprocess
import numpy as np
from pathlib import Path
from collections import defaultdict

import bpy
import bmesh
from bpy.props import StringProperty, BoolProperty, PointerProperty, FloatProperty, IntProperty, CollectionProperty, EnumProperty
from bpy_extras.io_utils import ImportHelper, ExportHelper
from mathutils import Matrix, Quaternion, Vector


# Blender does not expose a persistent revision number for every editable
# Mesh/Image field. Keep content revisions in this process so native snapshot
# reuse never crosses a Blender restart or enters a published Mod.
_EXPORT_DIRTY_SERIAL = defaultdict(int)
_EXPORT_IN_PROGRESS = False
_EXPORT_SKELETON_TOKENS = None


@bpy.app.handlers.persistent
def _eiem_export_dirty_depsgraph_update(_scene, depsgraph):
    """Record changed Blender IDs without doing work during an export."""
    if _EXPORT_IN_PROGRESS:
        return
    for update in depsgraph.updates:
        owner = getattr(update, "id", None)
        if owner is None or not hasattr(owner, "as_pointer"):
            continue
        # Depsgraph updates expose evaluated copies. Cache keys use the
        # authoring datablock, so always fold the update back to ``original``
        # when Blender provides it.
        original = getattr(owner, "original", None)
        if original is not None and hasattr(original, "as_pointer"):
            owner = original
        _EXPORT_DIRTY_SERIAL[int(owner.as_pointer())] += 1


@bpy.app.handlers.persistent
def _eiem_export_load_post(_filepath):
    # Blender removes non-persistent handlers when opening a project. Cache
    # state belongs to one author scene, including its datablock pointers.
    _EXPORT_DIRTY_SERIAL.clear()
    native_export._NATIVE_SNAPSHOT_CACHE.clear()
    native_export._NATIVE_MATERIAL_FIELDS_CACHE.clear()
    native_export._NATIVE_IMAGE_CACHE.clear()


def _owner_property_digest(owner):
    """Hash ID properties that are not covered by depsgraph revisions."""
    if owner is None or not hasattr(owner, "keys"):
        return ""
    digest = hashlib.sha1()
    for key in sorted(str(key) for key in owner.keys()):
        digest.update(key.encode("utf-8", "replace"))
        digest.update(b"=")
        try:
            value = owner[key]
            # RNA wrappers' repr describes their type/extent, not contents.
            # Rest bindings can be edited in place without a depsgraph event.
            if hasattr(value, 'to_list'):
                value = value.to_list()
            elif hasattr(value, 'to_dict'):
                value = value.to_dict()
            value = repr(value)
        except (KeyError, RuntimeError, TypeError):
            value = "<unreadable>"
        digest.update(value.encode("utf-8", "replace"))
        digest.update(b";")
    return digest.hexdigest()


def _owner_revision(owner):
    if owner is None or not hasattr(owner, "as_pointer"):
        return ("", 0, "")
    return (
        type(owner).__name__,
        int(owner.as_pointer()),
        _EXPORT_DIRTY_SERIAL.get(int(owner.as_pointer()), 0),
        _owner_property_digest(owner),
    )


def _vertex_group_digest(obj):
    """Return a stable digest of the authored skin weights on *obj*.

    Blender does not expose a revision counter for vertex-group membership or
    weights.  Depsgraph updates cover most edits, but weight edits can leave a
    session cache looking clean.  The mesh resource cache must therefore carry
    a compact content fingerprint so an export can never reuse a skin payload
    after the author changes a group, weight, or group name.
    """
    groups = getattr(obj, "vertex_groups", None)
    mesh = getattr(obj, "data", None)
    if groups is None or mesh is None:
        return ""
    digest = hashlib.sha1()
    digest.update(struct.pack("<II", len(groups), len(mesh.vertices)))
    for group in groups:
        encoded = str(group.name).encode("utf-8", "replace")
        digest.update(struct.pack("<I", len(encoded)))
        digest.update(encoded)
    for vertex in mesh.vertices:
        entries = sorted(
            (int(item.group), float(item.weight)) for item in vertex.groups)
        digest.update(struct.pack("<I", len(entries)))
        for group_index, weight in entries:
            digest.update(struct.pack("<If", group_index, weight))
    return digest.hexdigest()


def _shape_key_digest(mesh):
    """Fingerprint shape-key payloads serialized into a Mesh resource."""
    shape_keys = getattr(mesh, "shape_keys", None)
    if shape_keys is None:
        return ""
    digest = hashlib.sha1()
    blocks = list(getattr(shape_keys, "key_blocks", ()))
    digest.update(struct.pack("<I", len(blocks)))
    for block in blocks:
        for value in (
                str(block.name),
                str(block.relative_key.name if block.relative_key else ""),
                str(block.vertex_group)):
            encoded = value.encode("utf-8", "replace")
            digest.update(struct.pack("<I", len(encoded)))
            digest.update(encoded)
        points = block.data
        digest.update(struct.pack("<I", len(points)))
        coordinates = np.empty(len(points) * 3, dtype='<f4')
        points.foreach_get('co', coordinates)
        digest.update(coordinates.tobytes())
    return digest.hexdigest()


def _mesh_slot_digest(obj):
    """Fingerprint material slots and polygon assignments used by write_mesh."""
    mesh = getattr(obj, "data", None)
    if mesh is None:
        return ""
    digest = hashlib.sha1()
    materials = list(mesh.materials)
    digest.update(struct.pack("<I", len(materials)))
    for material in materials:
        digest.update(struct.pack(
            "<Q", int(material.as_pointer()) if material else 0))
        encoded = str(material.name if material else "").encode(
            "utf-8", "replace")
        digest.update(struct.pack("<I", len(encoded)))
        digest.update(encoded)
    polygons = mesh.polygons
    digest.update(struct.pack("<I", len(polygons)))
    slots = np.empty(len(polygons), dtype='<i4')
    polygons.foreach_get('material_index', slots)
    digest.update(slots.tobytes())
    return digest.hexdigest()


def _skeleton_export_token(armature):
    """Fingerprint rest binding inputs, independently of pose evaluation."""
    if armature is None:
        return None
    cache = globals().get('_EXPORT_SKELETON_TOKENS')
    key = int(armature.as_pointer())
    if cache is not None and key in cache:
        return cache[key]
    digest = hashlib.sha1()
    for bone in armature.data.bones:
        for value in (bone.name, bone.parent.name if bone.parent else '',
                      _owner_property_digest(bone)):
            encoded = value.encode('utf-8', 'replace')
            digest.update(struct.pack('<I', len(encoded)))
            digest.update(encoded)
        digest.update(struct.pack('<16d', *(v for row in bone.matrix_local for v in row)))
    result = (int(armature.as_pointer()), int(armature.data.as_pointer()),
            _owner_property_digest(armature), _owner_property_digest(armature.data),
            digest.hexdigest())
    if cache is not None:
        cache[key] = result
    return result


def _mesh_resource_token(obj):
    if _eiem_export_dirty_depsgraph_update not in bpy.app.handlers.depsgraph_update_post:
        return None  # Reuse is unsafe without the edit revision tracker.
    mesh = getattr(obj, "data", None)
    armature = next((modifier.object for modifier in obj.modifiers
                     if modifier.type == "ARMATURE" and modifier.object), None)
    shape_keys = getattr(mesh, "shape_keys", None)
    return (
        "mesh-v4",
        (int(obj.as_pointer()), _owner_property_digest(obj)),
        _owner_revision(mesh),
        _owner_revision(shape_keys),
        _skeleton_export_token(armature),
        _vertex_group_digest(obj),
        _shape_key_digest(mesh),
        _mesh_slot_digest(obj),
    )


if __package__:
    from . import eiem_release as release_check
    from . import eiem_format as format_io
    from . import eiem_lod as lod
    from . import eiem_native_export as native_export
    from . import eiem_offline_reload as offline_reload
    from . import eiem_physics_authoring as physics_authoring
    from . import eiem_material_editor as material_editor
    from . import eiem_effect_editor as effect_editor
    from . import eiem_blender_controls as controls
    from . import eiem_shape_animation as shape_animation
    from . import eiem_material_animation as material_animation
    from . import eiem_animation as animation_io
else:
    _release_spec = importlib.util.spec_from_file_location(
        "eiem_release", Path(__file__).with_name("eiem_release.py"))
    release_check = importlib.util.module_from_spec(_release_spec)
    _release_spec.loader.exec_module(release_check)
    _format_spec = importlib.util.spec_from_file_location(
        "eiem_format", Path(__file__).with_name("eiem_format.py"))
    format_io = importlib.util.module_from_spec(_format_spec)
    _format_spec.loader.exec_module(format_io)
    _lod_spec = importlib.util.spec_from_file_location(
        "eiem_lod", Path(__file__).with_name("eiem_lod.py"))
    lod = importlib.util.module_from_spec(_lod_spec)
    _lod_spec.loader.exec_module(lod)
    _native_spec = importlib.util.spec_from_file_location(
        "eiem_native_export", Path(__file__).with_name("eiem_native_export.py"))
    native_export = importlib.util.module_from_spec(_native_spec)
    _native_spec.loader.exec_module(native_export)
    _offline_spec = importlib.util.spec_from_file_location(
        "eiem_offline_reload", Path(__file__).with_name("eiem_offline_reload.py"))
    offline_reload = importlib.util.module_from_spec(_offline_spec)
    _offline_spec.loader.exec_module(offline_reload)
    _physics_spec = importlib.util.spec_from_file_location(
        "eiem_physics_authoring", Path(__file__).with_name("eiem_physics_authoring.py"))
    physics_authoring = importlib.util.module_from_spec(_physics_spec)
    _physics_spec.loader.exec_module(physics_authoring)
    _material_spec = importlib.util.spec_from_file_location(
        "eiem_material_editor", Path(__file__).with_name("eiem_material_editor.py"))
    material_editor = importlib.util.module_from_spec(_material_spec)
    _material_spec.loader.exec_module(material_editor)
    import eiem_effect_editor as effect_editor
    _controls_spec = importlib.util.spec_from_file_location(
        "eiem_blender_controls", Path(__file__).with_name("eiem_blender_controls.py"))
    controls = importlib.util.module_from_spec(_controls_spec)
    _controls_spec.loader.exec_module(controls)


if not __package__:
    _shape_animation_spec = importlib.util.spec_from_file_location("eiem_shape_animation", Path(__file__).with_name("eiem_shape_animation.py"))
    shape_animation = importlib.util.module_from_spec(_shape_animation_spec)
    sys.modules[_shape_animation_spec.name] = shape_animation
    _shape_animation_spec.loader.exec_module(shape_animation)
    import eiem_material_animation as material_animation
    _animation_spec = importlib.util.spec_from_file_location(
        "eiem_animation", Path(__file__).with_name("eiem_animation.py"))
    animation_io = importlib.util.module_from_spec(_animation_spec)
    _animation_spec.loader.exec_module(animation_io)


# Preserve the standalone add-on's public Python surface for authoring scripts
# while keeping control behavior in one focused module.
parse_json_property = controls.parse_json_property
author_identity = controls.author_identity
add_shape_control = controls.add_shape_control
sync_new_shape_controls = controls.sync_new_shape_controls
shape_channel_name = controls.shape_channel_name
switch_groups = controls.switch_groups
switch_states = controls.switch_states
switch_state_values = controls.switch_state_values
switch_meshes = controls.switch_meshes
switch_members = controls.switch_members
capture_switch_state = controls.capture_switch_state
validate_switch_key = controls.validate_switch_key
switch_key_from_event = controls.switch_key_from_event
classify_switch_key_event = controls.classify_switch_key_event
set_switch_group_key = controls.set_switch_group_key
material_switch_definition = controls.material_switch_definition
material_switch_export_options = controls.material_switch_export_options
set_shape_control_hotkey = controls.set_shape_control_hotkey
add_switch_state = controls.add_switch_state
set_switch_state_order = controls.set_switch_state_order
reorder_switch_state = controls.reorder_switch_state
set_switch_default = controls.set_switch_default
restore_switch_preview = controls.restore_switch_preview
preview_switch = controls.preview_switch
assign_switch_meshes = controls.assign_switch_meshes
add_switch_members = controls.add_switch_members
remove_switch_members = controls.remove_switch_members
create_switch_group = controls.create_switch_group
restore_mesh_source_binding = controls.restore_mesh_source_binding
delete_switch_group = controls.delete_switch_group
mesh_source_identity = controls.mesh_source_identity
plan_switch_export = controls.plan_switch_export
plan_shape_controls = controls.plan_shape_controls


# Keep the existing add-on scripting API while LOD policy lives in one module.
LOD_TOKEN_RE = lod.LOD_TOKEN_RE
mesh_lod_level = lod.mesh_lod_level
mesh_lod_family = lod.mesh_lod_family
mesh_export_template = lod.mesh_export_template


def _lod_candidates(objects):
    candidates = list(bpy.data.objects)
    packages = {str(obj.get('eiem_author_package', '')) for obj in objects}
    for package in sorted(packages - {''}):
        manifest = Path(package) / 'source/manifest.json'
        if manifest.is_file():
            resources = json.loads(manifest.read_text('utf-8-sig'))['resources']
            candidates.extend(lod.source_mesh_candidates(package, resources))
    return candidates


def discover_mesh_lods(obj, candidates=None):
    return lod.discover_mesh_lods(
        obj, candidates if candidates is not None else _lod_candidates([obj]))


def expand_lod_plan(plan, target_levels, candidates=None):
    return lod.expand_lod_plan(
        plan, target_levels,
        candidates if candidates is not None else _lod_candidates(plan['objects']),
        mesh_source_identity)


def lod_levels_for_export(mesh_objects, selected_levels=None, all_levels=False):
    mesh_objects = list(mesh_objects)
    return lod.lod_levels_for_export(
        mesh_objects, _lod_candidates(mesh_objects), selected_levels, all_levels)


MAGIC_MESH = format_io.MAGIC_MESH
MAGIC_SKEL = format_io.MAGIC_SKEL

def is_unity_left_handed(coordinate):
    value = str(coordinate or "").lower()
    # model-z-up-left-handed was the experimental marker emitted by older
    # AnimeStudio builds. Those files still contain raw Unity buffers, so keep
    # it as a compatibility alias while new files use the explicit marker.
    return value in {"unity-y-up-left-handed", "model-z-up-left-handed"}


def unity_to_blender(value):
    """Convert Unity's left-handed Y-up vectors to Blender's right-handed space."""
    x, y, z = value
    return (-x, y, z)


def blender_to_unity(value):
    """Inverse of unity_to_blender for exported mesh channels."""
    x, y, z = value
    return (-x, y, z)


def unity_transform_matrix_to_blender_basis():
    return Matrix(((-1.0, 0.0, 0.0, 0.0),
                   (0.0, 0.0, -1.0, 0.0),
                   (0.0, 1.0, 0.0, 0.0),
                   (0.0, 0.0, 0.0, 1.0)))


def unity_transform_matrix_to_blender(matrix):
    """Move a Unity Y-up Transform matrix into the Z-up mesh editing space.

    Endfield's serialized Mesh buffers are already authored in Z-up model
    space, while prefab Transform TRS uses Unity's Y-up space.  The X flip
    changes handedness and the -Z/Y mapping puts both resources in the same
    Blender basis.  Conjugating the complete matrix preserves rotation and
    non-uniform scale instead of trying to remap quaternion components.
    """
    basis = unity_transform_matrix_to_blender_basis()
    return basis @ matrix @ basis.inverted()


# Re-export the stable scripting surface while the implementation lives in the
# Blender-independent format module.
Reader = format_io.Reader
Writer = format_io.Writer
parse_ini = format_io.parse_ini
parse_material_resources = format_io.parse_material_resources
read_flat_properties = format_io.read_flat_properties
safe_path = format_io.safe_path
read_mesh = format_io.read_mesh
read_skeleton = format_io.read_skeleton
validate_skeleton_nodes = format_io.validate_skeleton_nodes

def load_material(root, section, values, textures=None):
    # Material names and INI sections are local to their source package. Two
    # imported Mods can legitimately contain ``MaterialBody``; reusing the
    # global Blender name used to overwrite the first Mod's material silently.
    package = str(Path(root).resolve())
    material = next((item for item in bpy.data.materials
                     if str(item.get("eiem_author_package", "")) == package
                     and str(item.get("eiem_section", "")) == section), None)
    material = material or bpy.data.materials.new(section)
    material.use_nodes = True
    for key in list(material.keys()):
        if key.startswith("eiem_"):
            del material[key]
    # Put the paths users edit most often first in Blender's Custom Properties.
    # The original Texture section identity is retained separately so an
    # untouched material still round-trips to the same package resource.
    keys = list(values)
    ordered_keys = (
        [key for key in keys if key.startswith("texture.")] +
        [key for key in keys if key.startswith(("texture_scale.", "texture_offset."))] +
        [key for key in keys if not key.startswith(("texture.", "texture_scale.",
                                                    "texture_offset."))]
    )
    texture_bindings = {}
    for key in ordered_keys:
        value = values[key]
        if key in ("path", "target.path", "target.asset", "overrides"):
            continue
        if key.startswith("texture.") and textures and value in textures:
            texture_bindings[key] = str(textures[value].get("eiem_section", value))
            image_path = bpy.path.abspath(textures[value].filepath,
                                          library=textures[value].library)
            value = str(Path(image_path).resolve())
        material["eiem_" + key] = value
    material["eiem_section"] = section
    material["eiem_author_package"] = package
    if values.get("source"):
        material["eiem_source"] = values["source"]
    material["eiem_target_path"] = values.get("target.path", "")
    material["eiem_target_asset"] = values.get("target.asset", "")
    material["eiem_texture_sections_json"] = json.dumps(
        texture_bindings, separators=(",", ":"))
    # This authoring baseline is not a runtime setting. It lets a later export
    # write only properties changed in Blender while the Material payload still
    # clones the original game material for every property left untouched.
    baseline = {
        key: str(value) for key, value in values.items()
        if key not in ("path", "target.path", "target.asset", "overrides")
    }
    for key, value in texture_bindings.items():
        baseline[key] = value
    if str(values.get("overrides", "false")).lower() == "true":
        baseline = {"source": values.get("source", "")}  # file is already an authored delta, not a game snapshot
    material["eiem_baseline_json"] = json.dumps(
        baseline, sort_keys=True, separators=(",", ":"))
    return material


def import_material_file(path, obj):
    """Import an EFF material + package texture declarations into one slot."""
    path = Path(path).resolve()
    if obj is None or obj.type != "MESH":
        raise ValueError('请先选择要指定材质的网格')
    values = read_flat_properties(path)
    if values.get("format") != "EFFMAT" or not values.get("source", "").strip():
        raise ValueError("请选择 EFF 导出的 .mat（EFFMAT version=1，包含 source），不是 Unity YAML 或节点材质")
    # Only use an enclosing package that actually declares this file.
    resources, root = {}, path.parent
    for parent in path.parents:
        if not (parent / "mod.ini").is_file():
            continue
        sections = parse_material_resources(parent)
        if any(s.lower().startswith("material") and v.get("path") and
               safe_path(parent, v["path"]) == path for s, v in sections.items()):
            resources, root = sections, parent
            break
    textures = {}
    for key, value in values.items():
        if not key.startswith("texture.") or value.replace("\\", "/").lower().startswith("assets/"):
            continue
        declaration = resources.get(value)
        if not declaration or not value.lower().startswith("texture") or not declaration.get("path"):
            raise ValueError("缺少贴图声明 %s：请保留材质所属资源包的 mod.ini 和 Texture 文件" % value)
        disk = safe_path(root, declaration["path"])
        if not disk.is_file():
            raise ValueError("贴图文件不存在：" + str(disk))
        textures[value] = (disk, declaration)
    # Allocation happens only after reference validation. Failure leaves the
    # selected slot and every old Material/Image data block unchanged.
    created_images, material = [], None
    try:
        images = {}
        used = {str(i.get("eiem_section")): i for i in bpy.data.images if i.get("eiem_section")}
        texture_remap = {}
        for old_section, (disk, declaration) in textures.items():
            image = bpy.data.images.load(str(disk), check_existing=False)
            created_images.append(image)
            if image.size[0] <= 0 or image.size[1] <= 0:
                raise ValueError("无法解码贴图：" + str(disk))
            section = unique_texture_section(disk, used)
            image["eiem_section"] = section
            used[section] = image
            texture_remap[old_section] = section
            image["eiem_source"] = declaration.get("source", "")
            image["eiem_name"] = declaration.get("name", disk.stem)
            for key, default in (("linear", "false"), ("mipmaps", "true"), ("filter", "1"),
                                 ("wrap", "0"), ("aniso", "1"), ("mip_bias", "0")):
                image["eiem_" + key] = declaration.get(key, default)
            image.colorspace_settings.name = "Non-Color" if image["eiem_linear"].lower() == "true" else "sRGB"
            images[section] = image
        values = dict(values)
        for key, value in list(values.items()):
            if key.startswith("texture.") and value in texture_remap:
                values[key] = texture_remap[value]
        used_sections = {str(m.get("eiem_section", m.name)).lower() for m in bpy.data.materials}
        used_sections.update(m.name.lower() for m in bpy.data.materials)
        section = unique_export_section("Material" + path.stem, used_sections, "Material")
        material = load_material(root, section, values, images)
        material["eiem_material_file"] = str(path)
        if obj.data.materials:
            obj.active_material = material
        else:
            obj.data.materials.append(material)
        return material
    except Exception:
        if material is not None:
            bpy.data.materials.remove(material)
        for image in created_images:
            bpy.data.images.remove(image)
        raise


def set_point_attribute(mesh, name, data_type, values, member):
    """Store one source value per Unity vertex in an editable Mesh attribute."""
    existing = mesh.attributes.get(name)
    if existing:
        mesh.attributes.remove(existing)
    attribute = mesh.attributes.new(name=name, type=data_type, domain="POINT")
    values = list(values)
    if hasattr(attribute.data, "foreach_set"):
        if member == "value":
            attribute.data.foreach_set(member, [float(value) for value in values])
        else:
            attribute.data.foreach_set(member, [component for value in values for component in value])
        return attribute
    for item, value in zip(attribute.data, values):
        setattr(item, member, value)
    return attribute


def get_point_attribute(mesh, name, member):
    attribute = mesh.attributes.get(name)
    if not attribute or attribute.domain != "POINT" or len(attribute.data) != len(mesh.vertices):
        return None
    if not attribute.data:
        return []
    if hasattr(attribute.data, "foreach_get"):
        if member == "value":
            values = [0.0] * len(attribute.data)
            attribute.data.foreach_get(member, values)
            return values
        width = len(getattr(attribute.data[0], member)) if attribute.data else 0
        values = [0.0] * (len(attribute.data) * width)
        attribute.data.foreach_get(member, values)
        return [tuple(values[index:index + width])
                for index in range(0, len(values), width)]
    return [tuple(getattr(item, member)) if member != "value" else float(item.value)
            for item in attribute.data]


# Blender's native UV layers are only two-dimensional.  EFF meshes may carry
# Z/W components and authored tangent frames, so those extra streams live in
# regular POINT attributes.  The mapping below is author data: it records
# which attribute belongs to which semantic/UV slot instead of making export
# depend on an attribute's display name.  Names are kept only for Blender's
# UI and for compatibility with older .blend files.
AUTHOR_ATTRIBUTE_MAP = "author_attribute_map_json"
UV_SLOT_MAP = "author_uv_slots_json"
LEGACY_ATTRIBUTE_MAP = "eff_attribute_map_json"
LEGACY_UV_SLOT_MAP = "eff_uv_slots_json"
AUTHOR_UV_DIMENSIONS = "author_uv_dimensions_json"
SOURCE_NORMAL_ATTRIBUTE = "SourceNormal"
TANGENT_ATTRIBUTE = "Tangent"
TANGENT_SIGN_ATTRIBUTE = "TangentSign"


def _attribute_map(mesh):
    value = parse_json_property(mesh, AUTHOR_ATTRIBUTE_MAP, None)
    if value is None:
        value = parse_json_property(mesh, LEGACY_ATTRIBUTE_MAP, {})
    return value if isinstance(value, dict) else {}


def _attribute_map_name(mesh, category, key):
    mapping = _attribute_map(mesh)
    values = mapping.get(category, {})
    if not isinstance(values, dict):
        return ""
    value = values.get(str(key), "")
    return str(value) if value else ""


def _attribute_is_point(mesh, name, data_type=None):
    attribute = mesh.attributes.get(name) if name else None
    if attribute is None or attribute.domain != "POINT":
        return False
    if len(attribute.data) != len(mesh.vertices):
        return False
    return data_type is None or attribute.data_type == data_type


def resolve_author_attribute(mesh, category, key, data_type=None):
    """Resolve an authored attribute through its semantic mapping.

    Imported meshes carry the mapping in ``author_attribute_map_json``.  When
    an older .blend has no map, use the semantic suffix only as a one-time
    compatibility fallback; the normal export path never requires a project
    prefix in the attribute name.
    """
    mapped = _attribute_map_name(mesh, category, key)
    if _attribute_is_point(mesh, mapped, data_type):
        return mapped

    if category == "uv_zw":
        channel = int(key)
        aliases = {
            "UV%d_ZW" % channel,
            "EFF_UV%d_ZW" % channel,
        }
        suffix = "_uv%d_zw" % channel
        candidates = [
            attribute.name for attribute in mesh.attributes
            if attribute.domain == "POINT"
            and (data_type is None or attribute.data_type == data_type)
            and (attribute.name in aliases
                 or attribute.name.casefold().endswith(suffix))
        ]
    else:
        aliases = {
            "source_normal": {"SourceNormal", "EFF_SourceNormal"},
            "tangent": {"Tangent", "EFF_Tangent"},
            "tangent_sign": {"TangentSign", "EFF_TangentSign"},
        }.get(str(key), set())
        candidates = [
            attribute.name for attribute in mesh.attributes
            if attribute.domain == "POINT"
            and (data_type is None or attribute.data_type == data_type)
            and attribute.name in aliases
        ]
    for candidate in candidates:
        if _attribute_is_point(mesh, candidate, data_type):
            return candidate
    return ""


def _set_author_attribute(mesh, category, key, name, data_type, values, member):
    attribute = set_point_attribute(mesh, name, data_type, values, member)
    mapping = _attribute_map(mesh)
    mapping.setdefault(category, {})[str(key)] = attribute.name
    mesh[AUTHOR_ATTRIBUTE_MAP] = json.dumps(mapping, separators=(",", ":"))
    return attribute


def _uv_slot_records(mesh):
    value = parse_json_property(mesh, UV_SLOT_MAP, None)
    if value is None:
        value = parse_json_property(mesh, LEGACY_UV_SLOT_MAP, [])
    return value if isinstance(value, list) else []


def normal_state_crc(mesh):
    """Fingerprint Blender's currently evaluated per-corner normals.

    Blender stores custom normals in an encoded split-normal space, so reading
    them back can introduce a small representation error. The fingerprint lets
    an untouched import export the exact source values while still making a
    real Blender normal edit observable to the writer.
    """
    if hasattr(mesh.loops, "foreach_get") and hasattr(mesh.corner_normals, "foreach_get"):
        # Preserve the original interleaved <I3f CRC, including saved .blend
        # baselines, while fetching RNA arrays only once. NumPy ships with Blender.
        count = min(len(mesh.loops), len(mesh.corner_normals))
        indices = np.empty(len(mesh.loops), dtype=np.int32)
        normals = np.empty(len(mesh.corner_normals) * 3, dtype=np.float32)
        mesh.loops.foreach_get("vertex_index", indices)
        mesh.corner_normals.foreach_get("vector", normals)
        records = np.empty(count, dtype=[("vertex", "<u4"), ("normal", "<f4", (3,))])
        records["vertex"] = indices[:count]
        records["normal"] = normals.reshape(-1, 3)[:count]
        return "%08x" % (zlib.crc32(records) & 0xFFFFFFFF)
    checksum = 0
    for loop, corner in zip(mesh.loops, mesh.corner_normals):
        checksum = zlib.crc32(
            struct.pack("<I3f", loop.vertex_index, *corner.vector), checksum)
    return "%08x" % (checksum & 0xFFFFFFFF)


def normal_state_matches_source(mesh, source_normals, minimum_alignment=0.9998):
    """Ignore Blender's harmless split-normal re-encoding after save/update.

    A real normal edit still changes direction beyond the same tolerance used
    by the import round-trip check. Undefined zero corner normals may use a
    valid source point normal; missing or invalid source values still mismatch.
    """
    if source_normals is None or len(source_normals) != len(mesh.vertices):
        return False
    for loop, corner in zip(mesh.loops, mesh.corner_normals):
        source = Vector(source_normals[loop.vertex_index])
        current = Vector(corner.vector)
        if not all(math.isfinite(value) for value in source) or not all(
                math.isfinite(value) for value in current):
            return False
        source_length_squared = source.length_squared
        current_length_squared = current.length_squared
        if source_length_squared <= 1e-20:
            return False
        if current_length_squared <= 1e-20:
            continue
        if source.normalized().dot(current.normalized()) < minimum_alignment:
            return False
    return True


def unique_shape_key_name(obj, requested):
    requested = requested or "Shape"
    if not obj.data.shape_keys or requested not in obj.data.shape_keys.key_blocks:
        return requested
    index = 2
    while "%s.%03d" % (requested, index) in obj.data.shape_keys.key_blocks:
        index += 1
    return "%s.%03d" % (requested, index)


def import_blend_shapes(obj, payload):
    channels = payload.get("blend_channels") or []
    frames = payload.get("blend_frames") or []
    vertices = payload.get("blend_vertices") or []
    weights = payload.get("blend_weights") or []
    if not channels:
        return

    basis = obj.shape_key_add(name="Basis", from_mix=False)
    metadata = []
    frame_serial = 0
    for channel_name, name_hash, frame_index, frame_count in channels:
        channel_metadata = {"name": channel_name, "hash": int(name_hash), "frames": []}
        for offset in range(frame_count):
            source_frame_index = frame_index + offset
            if source_frame_index >= len(frames):
                raise ValueError("blend-shape channel references a missing frame")
            frame_name, first_vertex, vertex_count, has_normals, has_tangents, has_additional = frames[source_frame_index]
            weight = float(weights[source_frame_index]) if source_frame_index < len(weights) else 100.0
            requested = channel_name if frame_count == 1 else "%s@%g" % (channel_name, weight)
            key_name = unique_shape_key_name(obj, requested)
            key = obj.shape_key_add(name=key_name, from_mix=False)
            normal_values = [(0.0, 0.0, 0.0)] * len(obj.data.vertices)
            tangent_values = [(0.0, 0.0, 0.0)] * len(obj.data.vertices)
            end = min(len(vertices), first_vertex + vertex_count)
            for vertex_index, delta_position, delta_normal, delta_tangent in vertices[first_vertex:end]:
                if vertex_index >= len(obj.data.vertices):
                    raise ValueError("blend-shape vertex index is outside the mesh")
                converted = unity_to_blender(delta_position) if is_unity_left_handed(payload["coordinate"]) else delta_position
                key.data[vertex_index].co = basis.data[vertex_index].co + Vector(converted)
                if has_normals:
                    normal_values[vertex_index] = (unity_to_blender(delta_normal)
                                                   if is_unity_left_handed(payload["coordinate"])
                                                   else delta_normal)
                if has_tangents:
                    tangent_values[vertex_index] = (unity_to_blender(delta_tangent)
                                                    if is_unity_left_handed(payload["coordinate"])
                                                    else delta_tangent)
            normal_attribute = ""
            tangent_attribute = ""
            if has_normals:
                normal_attribute = "EFF_MorphNormal_%03d" % frame_serial
                set_point_attribute(obj.data, normal_attribute, "FLOAT_VECTOR", normal_values, "vector")
            if has_tangents:
                tangent_attribute = "EFF_MorphTangent_%03d" % frame_serial
                set_point_attribute(obj.data, tangent_attribute, "FLOAT_VECTOR", tangent_values, "vector")
            channel_metadata["frames"].append({
                "key": key_name,
                "weight": weight,
                "source_name": frame_name,
                "normal_attribute": normal_attribute,
                "tangent_attribute": tangent_attribute,
                "has_additional_normals": bool(has_additional),
            })
            frame_serial += 1
        metadata.append(channel_metadata)
    obj.data["eiem_blend_shapes_json"] = json.dumps(metadata, separators=(",", ":"))
    obj.data["eiem_blend_additional_json"] = json.dumps(
        payload.get("additional") or [], separators=(",", ":"))


def make_mesh(section, payload):
    count = payload["vertex_count"]
    raw_vertices = [(payload["vertices"][i * 3], payload["vertices"][i * 3 + 1],
                     payload["vertices"][i * 3 + 2]) for i in range(count)]
    verts = [unity_to_blender(value) if is_unity_left_handed(payload["coordinate"])
             else value for value in raw_vertices]
    faces = []
    face_submesh = []
    reverse_winding = is_unity_left_handed(payload["coordinate"])
    for submesh_index, (_, start, length, *_rest) in enumerate(payload["submeshes"]):
        end = min(len(payload["indices"]), start + length)
        for index in range(start, end - 2, 3):
            tri = payload["indices"][index:index + 3]
            if len(tri) == 3 and all(value < count for value in tri):
                if reverse_winding:
                    tri = (tri[0], tri[2], tri[1])
                faces.append(tuple(tri))
                face_submesh.append(submesh_index)
    if not faces:
        for index in range(0, len(payload["indices"]) - 2, 3):
            tri = payload["indices"][index:index + 3]
            if all(value < count for value in tri):
                if reverse_winding:
                    tri = (tri[0], tri[2], tri[1])
                faces.append(tuple(tri)); face_submesh.append(0)
    mesh = bpy.data.meshes.new(section)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    loop_vertices = mesh_loop_vertex_indices(mesh)
    mesh["eiem_section"] = section
    mesh["eiem_source"] = payload["source"]
    mesh["eiem_coordinate_space"] = payload["coordinate"]
    mesh["eiem_asset"] = payload["name"]
    # EFF meshes store one authored normal per vertex. Put those values into
    # Blender's native custom split-normal channel so the viewport, modifiers
    # and normal-editing tools use the game normals rather than a hidden copy.
    # Smooth faces are required for custom normals to drive shading.
    if len(payload["normals"]) == count * 3:
        normals = [tuple(payload["normals"][index * 3:index * 3 + 3]) for index in range(count)]
        if is_unity_left_handed(payload["coordinate"]):
            normals = [unity_to_blender(value) for value in normals]
        if hasattr(mesh.polygons, "foreach_set"):
            mesh.polygons.foreach_set("use_smooth", [True] * len(mesh.polygons))
        else:
            for polygon in mesh.polygons:
                polygon.use_smooth = True
        mesh.normals_split_custom_set([normals[index] for index in loop_vertices])
        mesh.update()
        # Lossless round-trip backup only. Viewport shading and normal-editing
        # tools use the native custom split normals set above.
        _set_author_attribute(
            mesh, "point", "source_normal", SOURCE_NORMAL_ATTRIBUTE,
            "FLOAT_VECTOR", normals, "vector")
    if len(payload["tangents"]) == count * 4:
        tangents = []
        signs = []
        for index in range(count):
            tangent = tuple(payload["tangents"][index * 4:index * 4 + 3])
            if is_unity_left_handed(payload["coordinate"]):
                tangent = unity_to_blender(tangent)
            tangents.append(tangent)
            signs.append(float(payload["tangents"][index * 4 + 3]))
        _set_author_attribute(
            mesh, "point", "tangent", TANGENT_ATTRIBUTE,
            "FLOAT_VECTOR", tangents, "vector")
        _set_author_attribute(
            mesh, "point", "tangent_sign", TANGENT_SIGN_ATTRIBUTE,
            "FLOAT", signs, "value")

    uv_dimensions = []
    uv_slots = []
    for channel, values in enumerate(payload["uvs"]):
        if not values:
            uv_dimensions.append(0)
            continue
        if not count or len(values) % count:
            raise ValueError("UV%d has an invalid component count" % channel)
        dimension = len(values) // count
        if dimension < 2 or dimension > 4:
            raise ValueError("UV%d uses unsupported dimension %d" % (channel, dimension))
        uv_dimensions.append(dimension)
        layer = mesh.uv_layers.new(name="UV%d" % channel)
        loop_uvs = [component
                    for vertex in loop_vertices
                    for component in values[vertex * dimension:vertex * dimension + 2]]
        if hasattr(layer.data, "foreach_set"):
            layer.data.foreach_set("uv", loop_uvs)
        else:
            for loop_index, vertex in enumerate(loop_vertices):
                start = vertex * dimension
                layer.data[loop_index].uv = values[start:start + 2]
        if dimension > 2:
            extras = []
            for vertex in range(count):
                start = vertex * dimension
                extras.append((float(values[start + 2]),
                               float(values[start + 3]) if dimension == 4 else 0.0))
            attribute_name = "UV%d_ZW" % channel
            _set_author_attribute(
                mesh, "uv_zw", channel, attribute_name,
                "FLOAT2", extras, "vector")
        else:
            attribute_name = ""
        uv_slots.append({
            "channel": channel,
            "layer": layer.name,
            "dimension": dimension,
            "zw": attribute_name,
        })
    while len(uv_dimensions) < 8:
        uv_dimensions.append(0)
    mesh[AUTHOR_UV_DIMENSIONS] = json.dumps(uv_dimensions, separators=(",", ":"))
    # Keep the old key for .blend files and tools that still inspect it.
    mesh["eiem_uv_dimensions_json"] = json.dumps(uv_dimensions, separators=(",", ":"))
    mesh[UV_SLOT_MAP] = json.dumps(uv_slots, separators=(",", ":"))

    colors = payload["colors"]
    if len(colors) >= count * 4 and hasattr(mesh, "color_attributes"):
        values = [tuple(colors[index * 4:index * 4 + 4]) for index in range(count)]
        attr = mesh.color_attributes.new(name="Color", type="FLOAT_COLOR", domain="POINT")
        if hasattr(attr.data, "foreach_set"):
            attr.data.foreach_set("color", colors[:count * 4])
        else:
            for item, value in zip(attr.data, values):
                item.color = value
    if hasattr(mesh.polygons, "foreach_set"):
        mesh.polygons.foreach_set("material_index", face_submesh)
    else:
        for polygon, submesh in zip(mesh.polygons, face_submesh):
            polygon.material_index = submesh
    if resolve_author_attribute(mesh, "point", "source_normal", "FLOAT_VECTOR"):
        mesh["eiem_normal_baseline_crc"] = normal_state_crc(mesh)
    return mesh


def skeleton_hierarchy_key(payload):
    return tuple(
        (path, parent, tuple(position), tuple(rotation), tuple(scale), bool(source))
        for (path, parent, position, rotation, scale), source in zip(
            payload["nodes"], payload.get("source_nodes", [True] * len(payload["nodes"])))
    )


def armature_bone_hash_lookup(armature):
    """Map Unity CRC32 path hashes to the shared armature's node indices."""
    lookup = {}
    for index, bone in enumerate(armature.data.bones):
        path = str(bone.get("eiem_path", bone.name))
        parts = path.split("/")
        for start in range(len(parts)):
            suffix = "/".join(parts[start:])
            digest = zlib.crc32(suffix.encode("utf-8")) & 0xffffffff
            candidates = lookup.setdefault(digest, [])
            if index not in candidates:
                candidates.append(index)
    return lookup


def resolve_mesh_bone_palette(payload, armature, legacy_palette=None):
    hashes = payload.get("bone_hashes") or []
    bindposes = payload.get("bindposes") or []
    if not payload.get("skin"):
        return []
    if not hashes or len(hashes) != len(bindposes):
        raise ValueError(
            "%s has skin data but no complete bone hash palette" % payload.get("name", "Mesh"))
    paths = payload.get("bone_paths") or []
    if paths:
        if len(paths) != len(bindposes):
            raise ValueError("%s has an incomplete bone path palette" % payload.get("name", "Mesh"))
        by_path = {str(bone.get("eiem_path", bone.name)): index
                   for index, bone in enumerate(armature.data.bones)}
        missing = [path for path in paths if path not in by_path]
        if missing:
            raise ValueError("%s bone path is absent from the shared skeleton: %s" %
                             (payload.get("name", "Mesh"), missing[0]))
        return [by_path[path] for path in paths]
    if legacy_palette is not None:
        palette = [int(value) for value in legacy_palette]
        if len(palette) != len(bindposes) or any(
                value < 0 or value >= len(armature.data.bones) for value in palette):
            raise ValueError("%s has an invalid renderer bone palette" % payload.get("name", "Mesh"))
        return palette
    lookup = armature_bone_hash_lookup(armature)
    palette = []
    for value in hashes:
        candidates = lookup.get(int(value), [])
        if not candidates:
            raise ValueError(
                "%s bone hash %u is absent from the shared skeleton" %
                (payload.get("name", "Mesh"), int(value)))
        if len(candidates) != 1:
            raise ValueError(
                "%s bone hash %u is ambiguous in the shared skeleton" %
                (payload.get("name", "Mesh"), int(value)))
        palette.append(candidates[0])
    return palette


def bind_mesh_weights(obj, payload, armature, palette):
    """Create Blender vertex groups from the EFF bone-index palette."""
    if not obj or not armature or not payload.get("skin"):
        return
    bones = list(armature.data.bones)
    # BoneWeight indices address the Mesh's compact bone palette, not the
    # complete prefab hierarchy. The palette is resolved from Mesh bone hashes
    # and belongs to the Mesh/armature binding, never to the Armature resource.
    groups = {}
    for palette_index, node_index in enumerate(palette):
        if 0 <= node_index < len(bones):
            bone = bones[node_index]
            groups[palette_index] = obj.vertex_groups.get(bone.name) or obj.vertex_groups.new(name=bone.name)
    for vertex_index, (weights, indices) in enumerate(payload["skin"]):
        if vertex_index >= len(obj.data.vertices):
            break
        for weight, bone_index in zip(weights, indices):
            if weight <= 0.0 or bone_index not in groups:
                continue
            groups[bone_index].add([vertex_index], float(weight), "REPLACE")


def make_armature(section, payload, collection):
    armature = bpy.data.armatures.new(section)
    obj = bpy.data.objects.new(section, armature)
    collection.objects.link(obj)
    obj["eiem_section"] = section
    obj["eiem_coordinate_space"] = payload["coordinate"]
    obj["eiem_source"] = ""
    obj["eiem_root_bone_index"] = int(payload.get("root_bone", -1))
    edit = bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bones = []
    source_world_matrices = []
    edit_world_matrices = []
    for index, (path, parent, position, rotation, scale) in enumerate(payload["nodes"]):
        # Skeleton node transforms are local to their parent. Blender edit
        # bones use armature-space coordinates, so compose the hierarchy here
        # instead of placing every child at an unrelated local position.
        quaternion = Quaternion((rotation[3], rotation[0], rotation[1], rotation[2]))
        local = Matrix.LocRotScale(Vector(position), quaternion, Vector(scale))
        if 0 <= parent < len(source_world_matrices):
            local = source_world_matrices[parent] @ local
        source_world_matrices.append(local)
        edit_matrix = (unity_transform_matrix_to_blender(local)
                       if is_unity_left_handed(payload["coordinate"])
                       else local.copy())
        edit_world_matrices.append(edit_matrix)
        bone = armature.edit_bones.new(path.rsplit("/", 1)[-1] or "root")
        bone.head = edit_matrix.translation
        bone.tail = edit_matrix.translation + (edit_matrix.to_3x3() @ Vector((0.0, 0.05, 0.0)))
        if (bone.tail - bone.head).length < 0.0001:
            bone.tail = bone.head + Vector((0.0, 0.05, 0.0))
        bone["eiem_path"] = path
        bone["eiem_skeleton_source"] = bool(payload.get("source_nodes", [True] * len(payload["nodes"]))[index])
        bone["eiem_source_parent"] = payload["nodes"][parent][0] if parent >= 0 else ""
        # Edit bones expose head/tail/roll, which cannot reconstruct the
        # serialized local quaternion and scale. Preserve source TRS so an
        # unchanged round trip does not turn the armature into a T-pose.
        bone["eiem_local_position"] = list(position)
        bone["eiem_local_rotation"] = list(rotation)
        bone["eiem_local_scale"] = list(scale)
        bones.append(bone)
    for index, node in enumerate(payload["nodes"]):
        parent = node[1]
        if 0 <= parent < len(bones):
            bones[index].parent = bones[parent]
    # Apply the composed source rest matrices after parenting so the viewport
    # shows the same bone orientation as the game. The serialized local TRS
    # remains in custom properties for lossless export, including scale that
    # Blender's EditBone display cannot represent directly.
    for index, bone in enumerate(bones):
        bone.matrix = Matrix(edit_world_matrices[index])
    bpy.ops.object.mode_set(mode="OBJECT")
    for bone in armature.bones:
        bone["eiem_rest_display"] = [bone.matrix_local[r][c] for r in range(4) for c in range(4)]
    obj.select_set(False)
    return obj


def clear_collection_objects(collection):
    for obj in list(collection.all_objects):
        bpy.data.objects.remove(obj, do_unlink=True)


def remove_collection_tree(collection):
    """Remove an import subtree after its objects have been unlinked."""
    for child in list(collection.children):
        remove_collection_tree(child)
    bpy.data.collections.remove(collection)


def clear_eiem_imports(collection):
    """Clear imported package trees while retaining the stable EFF root."""
    clear_collection_objects(collection)
    for child in list(collection.children):
        remove_collection_tree(child)


def import_child_collection(parent, label, role, import_id):
    child = bpy.data.collections.new(parent.name + " " + label)
    child["eiem_collection_role"] = role
    child["eiem_import_id"] = import_id
    parent.children.link(child)
    return child


def package_import_collection(root_collection, root):
    source = str(Path(root).resolve())
    label = Path(root).name
    if label.lower().endswith(".eff"):
        label = label[:-5]
    import_id = uuid.uuid4().hex
    package = bpy.data.collections.new("EFF " + (label or "Package"))
    package.color_tag = "COLOR_04"
    package["eiem_collection_role"] = "PACKAGE"
    package["eiem_author_package"] = source
    package["eiem_import_id"] = import_id
    root_collection.children.link(package)
    return package, import_id


def mesh_resource_collection(root_collection, section, payload):
    identity = "%s %s %s" % (section, payload.get("name", ""), payload.get("source", ""))
    match = re.search(r"(?:^|[^a-z0-9])lod([0-9]+)(?:$|[^a-z0-9])", identity, re.IGNORECASE)
    name = "LOD%d" % int(match.group(1)) if match else "Other"
    collection = bpy.data.collections.get("%s %s" % (root_collection.name, name))
    if collection is None:
        collection = bpy.data.collections.new("%s %s" % (root_collection.name, name))
    if collection.name not in root_collection.children:
        root_collection.children.link(collection)
    return collection


def import_package(root, clean=False, include_physics=False, physics_file=""):
    parser = parse_ini(root)
    collection = bpy.data.collections.get("EFF") or bpy.data.collections.new("EFF")
    if collection.name not in bpy.context.scene.collection.children:
        bpy.context.scene.collection.children.link(collection)
    if clean:
        clear_eiem_imports(collection)
        for material in [item for item in bpy.data.materials if item.get("eiem_section")]:
            bpy.data.materials.remove(material, do_unlink=True)
        for image in [item for item in bpy.data.images if item.get("eiem_section")]:
            bpy.data.images.remove(image, do_unlink=True)
    package_collection, import_id = package_import_collection(collection, root)
    mesh_collection = import_child_collection(package_collection, "Meshes", "MESHES", import_id)
    skeleton_collection = import_child_collection(package_collection, "Skeletons", "SKELETONS", import_id)
    resources = {section: dict(parser.items(section)) for section in parser.sections()}
    render_owners = {}
    for section, values in resources.items():
        if not section.lower().startswith("prefab"):
            continue
        prefab_path = values.get("path", "").strip()
        if not prefab_path:
            raise ValueError("%s has no game Prefab path" % section)
        for key, render_section in values.items():
            if not key.lower().startswith("render."):
                continue
            if render_section not in resources or not render_section.lower().startswith("render"):
                raise ValueError("%s references missing Render section %s" %
                                 (section, render_section))
            if render_section in render_owners:
                raise ValueError("Render section %s belongs to more than one Prefab" %
                                 render_section)
            render_owners[render_section] = (section, prefab_path)
    textures = {}
    for section, values in resources.items():
        if not section.lower().startswith("texture") or not values.get("path"):
            continue
        try:
            # Do not let Blender's global filepath cache alias two imported
            # Mod packages. Their section/source metadata is package-local,
            # even when both happen to point at the same disk PNG.
            image = bpy.data.images.load(str(safe_path(root, values["path"])), check_existing=False)
            used = {str(item.get("eiem_section")): item for item in bpy.data.images
                    if item.get("eiem_section") and item is not image}
            image_section = section
            if section in used:
                image_section = unique_texture_section(safe_path(root, values["path"]), used)
            image["eiem_section"] = image_section
            image["eiem_source_section"] = section
            image["eiem_author_package"] = str(Path(root).resolve())
            image["eiem_relative_path"] = values["path"]
            image["eiem_source"] = values.get("source", "")
            image["eiem_name"] = values.get("name", image.name)
            image["eiem_target_path"] = values.get("target.path", "")
            image["eiem_target_asset"] = values.get("target.asset", "")
            image["eiem_linear"] = values.get("linear", "false")
            image["eiem_mipmaps"] = values.get("mipmaps", "true")
            image["eiem_filter"] = values.get("filter", "1")
            image["eiem_wrap"] = values.get("wrap", "0")
            image["eiem_aniso"] = values.get("aniso", "1")
            image["eiem_mip_bias"] = values.get("mip_bias", "0")
            textures[section] = image
        except Exception:
            continue
    materials = {}
    for section, values in resources.items():
        if section.lower().startswith("material") and values.get("path"):
            material_values = read_flat_properties(safe_path(root, values["path"]))
            # The Material file owns shader properties. The resource section
            # owns package location and preserves original-game identity as
            # authoring metadata; live replacement is always a Render action.
            material_values["path"] = values["path"]
            # Preserve offline identity as authoring metadata. A Material
            # declaration never applies itself: exported Render material slots
            # decide which consumers receive the edited clone.
            material_values["target.path"] = values.get(
                "target.path", material_values.get("source", ""))
            material_values["target.asset"] = values.get(
                "target.asset", material_values.get("name", ""))
            materials[section] = load_material(
                root, section, material_values, textures)
    # A package can contain many renderer-specific Skeleton sections exported
    # by an older AnimeStudio.  Those sections repeat the same character
    # hierarchy and differ only in the renderer's compact bone palette.  The
    # hierarchy is the resource; a palette belongs to a Mesh binding.  Create
    # exactly one Armature for each unique hierarchy and make every legacy
    # section name an alias of it.
    skeletons = {}
    skeleton_payloads = {}
    armatures_by_hierarchy = {}
    for section, values in resources.items():
        if section.lower().startswith("skeleton") and values.get("path"):
            payload = read_skeleton(safe_path(root, values["path"]))
            hierarchy = skeleton_hierarchy_key(payload)
            armature = armatures_by_hierarchy.get(hierarchy)
            if armature is None:
                armature = make_armature(section, payload, skeleton_collection)
                armature["eiem_source"] = values.get("source", "")
                armature["eiem_target_path"] = values.get("target.path", "")
                armature["eiem_target_asset"] = values.get("target.asset", "")
                armature["eiem_author_package"] = str(Path(root).resolve())
                armature["eiem_import_id"] = import_id
                armatures_by_hierarchy[hierarchy] = armature
            skeletons[section] = armature
            skeleton_payloads[section] = payload
    meshes = {}
    mesh_payloads = {}
    for section, values in resources.items():
        if not section.lower().startswith("mesh") or not values.get("path"):
            continue
        payload = read_mesh(safe_path(root, values["path"]))
        mesh = make_mesh(section, payload)
        # Preserve offline identity for authoring and for the generated Render
        # selector. Resource declarations do not apply replacements themselves.
        mesh["eiem_target_path"] = values.get(
            "target.path", payload.get("source", ""))
        mesh["eiem_target_asset"] = values.get(
            "target.asset", payload.get("name", ""))
        obj = bpy.data.objects.new(section, mesh)
        obj["eiem_author_package"] = str(Path(root).resolve())
        obj["eiem_import_id"] = import_id
        mesh_resource_collection(mesh_collection, section, payload).objects.link(obj)
        meshes[section] = obj
        mesh_payloads[section] = payload
        # Keep the source palette available for a lossless round trip. Vertex
        # groups remain the editable source of truth after the user changes a
        # mesh; these values preserve bindposes and hashes that Blender does
        # not derive from topology.
        obj["eiem_bindposes_json"] = json.dumps(payload["bindposes"], separators=(",", ":"))
        obj["eiem_bone_hashes_json"] = json.dumps(payload["bone_hashes"], separators=(",", ":"))
        obj["eiem_bone_index_paths_json"] = json.dumps(
            payload.get("bone_index_paths", []), separators=(",", ":"))
        obj["eiem_bone_sources_json"] = json.dumps(
            payload.get("bone_sources", []), separators=(",", ":"))
        obj["eiem_bone_source_candidates_json"] = json.dumps(
            payload.get("bone_source_candidates", []), separators=(",", ":"))
        obj.data["eiem_original_vertex_count"] = int(payload["vertex_count"])
        import_blend_shapes(obj, payload)
    for section, values in resources.items():
        if not section.lower().startswith("render") or not values.get("mesh"):
            continue
        obj = meshes.get(values["mesh"])
        if not obj:
            continue
        if section in render_owners:
            prefab_section, prefab_path = render_owners[section]
            obj["eiem_prefab_section"] = prefab_section
            obj["eiem_prefab_path"] = prefab_path
        obj["eiem_render_section"] = section
        obj["eiem_render_path"] = values.get("path", "")
        obj["eiem_render_asset"] = values.get("asset", "")
        original_materials = {}
        if values.get("skeleton") in skeletons:
            skeleton = skeletons[values["skeleton"]]
            obj["eiem_skeleton"] = skeleton["eiem_section"]
            skeleton_payload = skeleton_payloads[values["skeleton"]]
            palette = resolve_mesh_bone_palette(
                mesh_payloads.get(values["mesh"], {}), skeleton,
                skeleton_payload.get("bones") or None)
            obj["eiem_bone_palette_json"] = json.dumps(palette, separators=(",", ":"))
            paths = [str(skeleton.data.bones[index].get("eiem_path", skeleton.data.bones[index].name))
                     for index in palette]
            obj["eiem_bone_paths_json"] = json.dumps(paths, separators=(",", ":"))
            modifier = obj.modifiers.get("EFF Armature") or obj.modifiers.new("EFF Armature", "ARMATURE")
            modifier.object = skeleton
            bind_mesh_weights(obj, mesh_payloads.get(values["mesh"], {}), skeleton, palette)
        for key, material_section in values.items():
            if key.startswith("material.") and material_section in materials:
                try:
                    slot = int(key.split(".", 1)[1])
                    while len(obj.data.materials) <= slot:
                        obj.data.materials.append(None)
                    obj.data.materials[slot] = materials[material_section]
                    original_materials[str(slot)] = material_section
                except ValueError:
                    pass
        obj["eiem_original_material_sections_json"] = json.dumps(
            original_materials, sort_keys=True, separators=(",", ":"))
    for armature in armatures_by_hierarchy.values():
        armature.data["eiem_source_bindings_json"] = json.dumps(shared_skin_bindings(armature), separators=(",", ":"))
    if include_physics:
        physics_resources = [
            (section, safe_path(root, values["path"]))
            for section, values in resources.items()
            if section.lower().startswith("physics") and values.get("path")
        ]
        if physics_file:
            physics_resources = [("", Path(physics_file))]
        if not physics_resources:
            candidate = Path(root) / "physics" / "components.json"
            if candidate.is_file():
                physics_resources = [("", candidate)]
        if not physics_resources:
            raise ValueError("资源包未包含物理文件，请指定解包的 components.json 或 .physics")
        physics_rigs = {}
        for section, values in resources.items():
            if not section.lower().startswith("render"):
                continue
            physics_section = values.get("physics", "")
            skeleton = skeletons.get(values.get("skeleton", ""))
            if not physics_section or skeleton is None:
                continue
            previous = physics_rigs.get(physics_section)
            if previous is not None and previous is not skeleton:
                raise ValueError("Physics %s 被多个不同 Skeleton 引用" % physics_section)
            physics_rigs[physics_section] = skeleton
        rigs = list(armatures_by_hierarchy.values())
        for section, file in physics_resources:
            rig = physics_rigs.get(section)
            if rig is None:
                if len(rigs) != 1:
                    raise ValueError(
                        "无法从 Render.physics 确定 %s 使用的 Skeleton" %
                        (section or Path(file).name))
                rig = rigs[0]
            if file.suffix.lower() == ".physics":
                physics_authoring.import_physics(file, rig)
            else:
                physics_authoring.native.import_source(file, rig)
    return len(meshes)


def mesh_loop_vertex_indices(mesh):
    values = [0] * len(mesh.loops)
    mesh.loops.foreach_get("vertex_index", values)
    return values


def mesh_vertex_coordinates(mesh):
    values = [0.0] * (len(mesh.vertices) * 3)
    mesh.vertices.foreach_get("co", values)
    return [tuple(values[index:index + 3]) for index in range(0, len(values), 3)]


def export_corner_map(mesh, channels, include_loose=True):
    """Split only serialized vertices, using all CORNER streams together.

    Original vertex IDs (including loose vertices) remain in place. Additional
    corner variants are appended; every point stream uses source_vertices,
    while triangle indices and corner streams use the corresponding loop map.
    Native snapshots omit loose vertices after corner splitting, before any
    point, skin or shape stream is mapped. The editable writer keeps them.
    No Blender data is changed. Float32 keys avoid merging distinct authored
    values or inventing a tolerance-dependent UV/normal average.
    """
    source_vertices = list(range(len(mesh.vertices)))
    source_loops = [None] * len(mesh.vertices)
    loop_vertices = [0] * len(mesh.loops)
    seen = {}
    width = sum(len(values[0]) for values in channels
                if values is not None and len(values))
    # Pack all corner streams in one contiguous float32 buffer. The old path
    # called struct.pack once per loop and unpacked every channel through a
    # Python generator; on a 220k-vertex clothing Mesh that dominated export.
    # Float32 little-endian bytes preserve the exact key semantics while
    # leaving only the unavoidable dictionary lookup loop in Python.
    packed_channels = np.empty((len(mesh.loops), width), dtype="<f4")
    offset = 0
    for values in channels:
        if values is None or not len(values):
            continue
        values_array = np.asarray(values, dtype="<f4")
        channel_width = len(values[0])
        values_array = values_array.reshape(len(mesh.loops), channel_width)
        packed_channels[:, offset:offset + channel_width] = values_array
        offset += channel_width
    packed = packed_channels.tobytes(order="C")
    record_size = width * 4
    for loop_index, vertex_index in enumerate(mesh_loop_vertex_indices(mesh)):
        start = loop_index * record_size
        signature = packed[start:start + record_size]
        key = (vertex_index, signature)
        target = seen.get(key)
        if target is None:
            target = vertex_index
            if source_loops[target] is not None:
                target = len(source_vertices)
                source_vertices.append(vertex_index)
                source_loops.append(loop_index)
            else:
                source_loops[target] = loop_index
            seen[key] = target
        loop_vertices[loop_index] = target
    if not include_loose and None in source_loops:
        kept = [i for i, loop in enumerate(source_loops) if loop is not None]
        remap = np.full(len(source_vertices), -1, dtype=np.int64)
        remap[kept] = np.arange(len(kept))
        source_vertices = [source_vertices[i] for i in kept]
        source_loops = [source_loops[i] for i in kept]
        loop_vertices = remap[loop_vertices].tolist()
    return source_vertices, source_loops, loop_vertices


def mesh_export_uv_channels(mesh):
    """Read mapped, possibly sparse UV channels without collapsing corners."""
    dimensions = parse_json_property(mesh, AUTHOR_UV_DIMENSIONS, [])
    if not dimensions:
        # Compatibility for .blend files authored before the mapping metadata.
        dimensions = parse_json_property(mesh, "eiem_uv_dimensions_json", [])
    slots = {}
    for record in _uv_slot_records(mesh):
        try:
            slots[int(record["channel"])] = record
        except (KeyError, TypeError, ValueError):
            continue
    named_slots = any(
        re.fullmatch(r"UV\d+", str(layer.name), re.IGNORECASE)
        for layer in mesh.uv_layers
    )
    output = []
    for channel in range(8):
        record = slots.get(channel, {})
        layer = mesh.uv_layers.get(str(record.get("layer", "")))
        if layer is None:
            # Legacy fallback: the old importer encoded sparse slots in the
            # layer name.  If that is absent, use the native layer order.
            layer = mesh.uv_layers.get("UV%d" % channel)
        if layer is None and not slots and not named_slots and channel < len(mesh.uv_layers):
            layer = mesh.uv_layers[channel]
        if layer is None:
            output.append(None)
            continue
        dimension = int(record.get(
            "dimension",
            dimensions[channel] if channel < len(dimensions) and dimensions[channel] else 2,
        ))
        if dimension < 2 or dimension > 4:
            raise ValueError("UV%d uses unsupported dimension %d" % (channel, dimension))
        if hasattr(layer.data, "foreach_get"):
            flat_xy = [0.0] * (len(layer.data) * 2)
            layer.data.foreach_get("uv", flat_xy)
            xy = np.asarray(flat_xy, dtype="<f4").reshape(-1, 2)
        else:
            xy = [tuple(item.uv) for item in layer.data]
        zw = None
        if dimension > 2:
            attribute_name = str(record.get("zw", ""))
            if not attribute_name:
                attribute_name = resolve_author_attribute(
                    mesh, "uv_zw", channel, "FLOAT2")
            zw = get_point_attribute(mesh, attribute_name, "vector")
            if zw is None:
                # Blender's native UV layer is always a valid 2D source.  A
                # missing Z/W author attribute therefore exports the data we
                # actually have instead of failing because an old custom
                # attribute name disappeared during a .blend migration.
                dimension = 2
        output.append((dimension, xy, zw))
    return output


def mesh_export_colors(mesh):
    if not hasattr(mesh, "color_attributes"):
        return None, []
    attribute = mesh.color_attributes.get("Color")
    if attribute is None:
        return None, []
    if attribute.domain not in {"POINT", "CORNER"}:
        raise ValueError("Color must use the POINT or CORNER domain")
    if hasattr(attribute.data, "foreach_get"):
        flat_values = [0.0] * (len(attribute.data) * 4)
        attribute.data.foreach_get("color", flat_values)
        values = [tuple(flat_values[index:index + 4])
                  for index in range(0, len(flat_values), 4)]
    else:
        values = [tuple(item.color) for item in attribute.data]
    return attribute.domain, values


def _orthogonalize_missing_tangents(mesh_name, generated, normals):
    """Batch only generated frames; authored tangent bits never enter here."""
    frames = np.asarray(generated, dtype=np.float64).reshape(-1, 4).copy()
    normals = np.asarray(normals, dtype=np.float64).reshape(-1, 3)
    if len(frames) != len(normals):
        raise ValueError(mesh_name + ' tangent/normal corner count differs')
    if not len(frames):
        return []
    valid_frame = (np.isfinite(frames).all(axis=1) &
                   (frames[:, :3] != 0).any(axis=1) &
                   ((frames[:, 3] == -1) | (frames[:, 3] == 1)))
    nx, ny, nz = normals.T
    tx, ty, tz = frames[:, :3].T
    normal_length = nx*nx + ny*ny + nz*nz
    dot = np.zeros(len(frames), dtype=np.float64)
    np.divide(tx*nx + ty*ny + tz*nz, normal_length, out=dot,
              where=normal_length > 0)
    tx -= nx*dot; ty -= ny*dot; tz -= nz*dot
    tangent_length = tx*tx + ty*ty + tz*tz
    valid_basis = ((tangent_length > 0) & np.isfinite(normal_length) &
                   np.isfinite(normals).all(axis=1) & np.isfinite(tangent_length))
    invalid = np.flatnonzero(~(valid_frame & valid_basis))
    if len(invalid):
        corner = int(invalid[0])
        if not valid_frame[corner]:
            raise ValueError('%s corner %d cannot generate a usable tangent; check UV0 and normals' %
                             (mesh_name, corner))
        raise ValueError('%s corner %d tangent is parallel to its exported normal' %
                         (mesh_name, corner))
    scale = np.power(tangent_length, -.5)
    frames[:, :3] *= scale[:, None]
    # Keep contiguous storage through corner splitting and stream gathering.
    # A nested Python list here costs more than the numeric work on large rigs.
    return frames


def _recover_zero_uv_tangents(mesh, generated, normals, missing):
    """Recover MikkTSpace zero frames from actual float64 UV derivatives.

    Very thin but nonzero triangles can produce a zero Mikk frame at one
    corner. Only replace missing frames, using that corner's triangle and
    exported normal; undefined UVs/geometry remain validation errors.
    """
    frames = np.asarray(generated, dtype=np.float64)
    candidates = np.asarray(missing, dtype=np.int64)
    values = frames[candidates]
    invalid = (~np.isfinite(values).all(axis=1) |
               ~(values[:, :3] != 0).any(axis=1) |
               ~np.isin(values[:, 3], (-1, 1)))
    pending = set(map(int, candidates[invalid]))
    if not pending:
        return generated
    uv = mesh.uv_layers['UV0'].data
    normals = np.asarray(normals, dtype=np.float64)
    handedness = -1 if is_unity_left_handed(
        mesh.get('eiem_coordinate_space', 'unity-y-up-left-handed')) else 1
    recovered = 0
    mesh.calc_loop_triangles()
    for triangle in mesh.loop_triangles:
        corners = pending.intersection(triangle.loops)
        if not corners:
            continue
        positions = np.asarray([mesh.vertices[v].co for v in triangle.vertices], dtype=np.float64)
        coords = np.asarray([uv[i].uv for i in triangle.loops], dtype=np.float64)
        e1, e2 = positions[1:] - positions[0]
        d1, d2 = coords[1:] - coords[0]
        determinant = d1[0]*d2[1] - d1[1]*d2[0]
        if (not np.isfinite(determinant) or determinant == 0 or
                not np.isfinite(positions).all() or
                not np.any(np.cross(e1, e2))):
            continue
        tangent = (e1*d2[1] - e2*d1[1]) / determinant
        bitangent = (e2*d1[0] - e1*d2[0]) / determinant
        for corner in sorted(corners):
            normal = normals[corner]
            normal_length = np.dot(normal, normal)
            if not np.isfinite(normal_length) or normal_length == 0:
                continue
            value = tangent - normal * (np.dot(normal, tangent) / normal_length)
            length = np.linalg.norm(value)
            orientation = np.dot(np.cross(normal, value), bitangent)
            if (not np.isfinite(length) or length == 0 or
                    not np.isfinite(orientation) or orientation == 0):
                continue
            generated[corner] = (*value / length, (1 if orientation > 0 else -1) * handedness)
            pending.remove(corner)
            recovered += 1
        if not pending:
            break
    if recovered:
        print('[EFF] %s: recovered %d zero tangent corners from UV0 triangle derivatives' %
              (mesh.name, recovered))
    return generated


def mesh_export_tangents(mesh, source_normals, normal_corners, preserve_normals):
    """Retain usable authored frames; generate only missing frames from UV0.

    Authored point signs are stored in the source coordinate convention by
    make_mesh, whereas newly computed signs start in Blender's convention.
    Corner frames participate in serialization splitting (mirrored UVs can
    disagree even at corners with identical position, normal and UV).
    """
    tangent_name = resolve_author_attribute(
        mesh, "point", "tangent", "FLOAT_VECTOR")
    sign_name = resolve_author_attribute(
        mesh, "point", "tangent_sign", "FLOAT")
    tangent_attr = mesh.attributes.get(tangent_name) if tangent_name else None
    sign_attr = mesh.attributes.get(sign_name) if sign_name else None
    points = []
    if tangent_attr is not None or sign_attr is not None:
        if tangent_attr is None or sign_attr is None:
            raise ValueError("Tangent and TangentSign author attributes must both be present")
        if (tangent_attr.domain != "POINT" or tangent_attr.data_type != "FLOAT_VECTOR"
                or sign_attr.domain != "POINT" or sign_attr.data_type != "FLOAT"):
            raise ValueError("Tangent/TangentSign must be POINT vector/float attributes")
        vectors = get_point_attribute(mesh, tangent_name, "vector")
        signs = get_point_attribute(mesh, sign_name, "value")
        points = [(*vector, sign) for vector, sign in zip(vectors, signs)]

    def usable(value):
        # Do not normalize/orthogonalize valid custom source frames. In
        # particular, zero-filled attributes on joined geometry are missing
        # data, not valid tangents merely because the attribute exists.
        return (all(math.isfinite(x) for x in value)
                and any(x != 0 for x in value[:3]) and value[3] in (-1, 1))

    loop_vertices = mesh_loop_vertex_indices(mesh)

    def export_normal(index):
        value = (source_normals[loop_vertices[index]] if preserve_normals
                 else normal_corners[index])
        return Vector(value)

    def matches_normal(value, normal_value):
        normal = Vector(normal_value)
        tangent = Vector(value[:3])
        if normal.length_squared == 0.0 or tangent.length_squared == 0.0:
            return False
        # A tangent frame is meaningful only relative to the normal that will
        # actually be serialized. Joined meshes can retain a non-zero source
        # tangent while their final custom normal has changed.
        cosine = abs(normal.dot(tangent)) / (normal.length * tangent.length)
        return cosine <= 2.0e-3

    point_valid = [usable(value) for value in points] if points else [False] * len(mesh.vertices)
    # Imported/joined meshes often carry an attribute pair filled with zeroes.
    # Treat that exactly like an absent tangent stream; otherwise the partial
    # author-data path needlessly validates every corner before regenerating
    # every tangent anyway.
    all_missing = not points or not any(point_valid)
    if preserve_normals and not all_missing:
        # Every corner of a source vertex exports the same authored normal.
        # Validate that frame once per vertex rather than once per face corner.
        point_matches = [valid and matches_normal(value, normal)
                         for valid, value, normal in zip(point_valid, points, source_normals)]
        missing = ([index for index, vertex in enumerate(loop_vertices)
                    if not point_valid[vertex] or not point_matches[vertex]]
                   )
    elif not all_missing:
        missing = [index for index, vertex in enumerate(loop_vertices)
                   if not point_valid[vertex]
                   or not matches_normal(points[vertex], normal_corners[index])]
    else:
        missing = range(len(loop_vertices))
    if not missing:
        return points, []
    if mesh.uv_layers.get("UV0") is None:
        if points:
            raise ValueError("%s has missing tangent frames but no UV0 to generate them" % mesh.name)
        # UV-less/untextured geometry is legal. Do not fabricate UV0 or a
        # tangent basis unrelated to any texture coordinates.
        return [], []

    # Blender's tangent cache is derived runtime data, not an authored Mesh
    # attribute.  For the common triangle/quad path we can calculate it on the
    # source Mesh directly and avoid copying every vertex, UV and custom
    # attribute.  The cache is discarded by Blender on the next edit and does
    # not change the serialized author data.  N-gons still use a temporary
    # triangulated copy because Blender refuses calc_tangents on them.
    has_ngons = any(len(polygon.vertices) > 4 for polygon in mesh.polygons)
    work = mesh.copy() if has_ngons else mesh
    source_loop_indices = list(range(len(mesh.loops)))
    try:
        # Blender refuses calc_tangents on n-gons even though the exporter
        # already serializes every face through loop_triangles.  Triangulate
        # only this temporary tangent mesh and retain a corner provenance
        # attribute so the generated frame can be mapped back to the original
        # loop indices.  The authored Mesh, shape keys and topology remain
        # untouched.
        if has_ngons:
            attr_name = "__EFF_TangentSourceLoop"
            suffix = 1
            while work.attributes.get(attr_name) is not None:
                attr_name = "__EFF_TangentSourceLoop.%d" % suffix
                suffix += 1
            source_attr = work.attributes.new(
                name=attr_name, type="INT", domain="CORNER")
            for index, item in enumerate(source_attr.data):
                item.value = index
            bmesh_data = bmesh.new()
            try:
                bmesh_data.from_mesh(work)
                bmesh.ops.triangulate(
                    bmesh_data,
                    faces=[face for face in bmesh_data.faces if len(face.verts) > 4],
                    quad_method="BEAUTY",
                    ngon_method="BEAUTY")
                bmesh_data.to_mesh(work)
            finally:
                bmesh_data.free()
            source_attr = work.attributes.get(attr_name)
            source_loop_indices = [None] * len(mesh.loops)
            for index, item in enumerate(source_attr.data):
                source_index = int(item.value)
                if 0 <= source_index < len(source_loop_indices):
                    # An n-gon corner can occur in more than one generated
                    # triangle. Any copy has the same source vertex/UV frame.
                    if source_loop_indices[source_index] is None:
                        source_loop_indices[source_index] = index
            if any(index is None for index in source_loop_indices):
                raise ValueError("%s tangent corner provenance is incomplete" % mesh.name)
            work.update()
        work.calc_tangents(uvmap="UV0")
        # Reading the whole tangent stream through RNA once is substantially
        # faster than crossing the Python/RNA boundary for every loop on a
        # large clothing Mesh.  Keep the same source-loop mapping used by the
        # N-gon path below.
        tangent_values = [0.0] * (len(work.loops) * 3)
        tangent_signs = [0.0] * len(work.loops)
        work.loops.foreach_get("tangent", tangent_values)
        work.loops.foreach_get("bitangent_sign", tangent_signs)
        handedness = -1 if is_unity_left_handed(
            mesh.get("eiem_coordinate_space", "unity-y-up-left-handed")) else 1
        generated = [
            (tangent_values[source_loop_indices[index] * 3],
             tangent_values[source_loop_indices[index] * 3 + 1],
             tangent_values[source_loop_indices[index] * 3 + 2],
             tangent_signs[source_loop_indices[index]] * handedness)
            for index in range(len(mesh.loops))
        ]
    finally:
        if has_ngons:
            bpy.data.meshes.remove(work)
    normal_values = (np.asarray(source_normals)[loop_vertices]
                     if preserve_normals else normal_corners)
    generated = _recover_zero_uv_tangents(mesh, generated, normal_values, missing)
    # The no-authored-tangent path is by far the common case for newly edited
    # geometry.  Avoid allocating a mathutils.Vector and calling a helper for
    # every corner; the scalar form produces the same orthogonalized frame and
    # is several times cheaper on clothing meshes with hundreds of thousands
    # of loops.  The general branch below remains for partially authored
    # tangent attributes.
    if all_missing:
        generated = _orthogonalize_missing_tangents(mesh.name, generated, normal_values)
    else:
        for loop_index in missing:
            value = generated[loop_index]
            if not usable(value):
                raise ValueError("%s corner %d cannot generate a usable tangent; check UV0 and normals" %
                                 (mesh.name, loop_index))
            normal = export_normal(loop_index)
            tangent = Vector(value[:3])
            # calc_tangents uses Blender's evaluated normals. Orthogonalize against
            # the exact normal selected for export in case a lossless source-normal
            # backup differs slightly from Blender's encoded custom-normal result.
            # A zero custom normal has no projection axis, so retain the UV tangent.
            normal_length_squared = normal.length_squared
            if not math.isfinite(normal_length_squared):
                raise ValueError("%s corner %d has a non-finite exported normal" %
                                 (mesh.name, loop_index))
            if normal_length_squared > 0.0:
                tangent -= normal * (tangent.dot(normal) / normal_length_squared)
            if tangent.length_squared == 0.0:
                raise ValueError("%s corner %d tangent is parallel to its exported normal" %
                                 (mesh.name, loop_index))
            tangent.normalize()
            generated[loop_index] = (*tangent, value[3])
    # Unreferenced loose vertices have no face/UV derivative. Keep valid source
    # data there, or a zero sentinel rather than inventing a direction.
    points = [points[i] if point_valid[i] else (0., 0., 0., 0.) for i in range(len(mesh.vertices))]
    if all_missing:
        corners = generated
    else:
        missing_set = set(missing)
        corners = [generated[index] if index in missing_set else points[vertex]
                   for index, vertex in enumerate(loop_vertices)]
    print("[EFF] %s: tangents retained on %d corners; generated %d corners from UV0" %
          (mesh.name, len(mesh.loops) - len(missing), len(missing)))
    return points, corners


def export_blend_shapes(obj, to_source, source_vertices):
    mesh = obj.data
    shape_keys = mesh.shape_keys
    if not shape_keys:
        return [], [], [], [], []
    basis = shape_keys.reference_key
    if not shape_keys.use_relative:
        raise ValueError("EFF 暂不支持绝对形态键，请使用相对 Basis 的形态键")
    for key in shape_keys.key_blocks:
        if key != basis and (key.relative_key != basis or key.vertex_group):
            raise ValueError("形态键 %s 必须相对 Basis，且不能使用顶点组遮罩" % key.name)
    metadata = parse_json_property(mesh, "eiem_blend_shapes_json", [])
    used = set()
    vertices = []
    frames = []
    channels = []
    weights = []

    def append_frame(key, frame_metadata, channel_name):
        first = len(vertices)
        normal_attribute = str(frame_metadata.get("normal_attribute", ""))
        tangent_attribute = str(frame_metadata.get("tangent_attribute", ""))
        normal_values = get_point_attribute(mesh, normal_attribute, "vector") if normal_attribute else None
        tangent_values = get_point_attribute(mesh, tangent_attribute, "vector") if tangent_attribute else None
        for index, source in enumerate(source_vertices):
            basis_point, shape_point = basis.data[source], key.data[source]
            delta_position = shape_point.co - basis_point.co
            delta_normal = Vector(normal_values[source]) if normal_values else Vector((0.0, 0.0, 0.0))
            delta_tangent = Vector(tangent_values[source]) if tangent_values else Vector((0.0, 0.0, 0.0))
            if (delta_position.length_squared == 0.0 and
                    delta_normal.length_squared == 0.0 and
                    delta_tangent.length_squared == 0.0):
                continue
            vertices.append((index, to_source(delta_position), to_source(delta_normal),
                             to_source(delta_tangent)))
        frames.append((
            # Modern Unity frames have no name field. The preserved legacy
            # source_name may be empty or label a frame rather than its channel.
            # EFF's frame identity is the owning channel, including multi-frame keys.
            channel_name, first, len(vertices) - first,
            normal_values is not None, tangent_values is not None,
            bool(frame_metadata.get("has_additional_normals", False)),
        ))
        weights.append(float(frame_metadata.get("weight", 100.0)))

    for channel_metadata in metadata:
        name = str(channel_metadata.get("name", "Shape"))
        first_frame = len(frames)
        for frame_metadata in channel_metadata.get("frames", []):
            key_name = str(frame_metadata.get("key", ""))
            key = shape_keys.key_blocks.get(key_name)
            if key is None or key == basis:
                continue
            used.add(key.name)
            append_frame(key, frame_metadata, name)
        frame_count = len(frames) - first_frame
        if frame_count:
            channels.append((name, int(channel_metadata.get(
                "hash", zlib.crc32(name.encode("utf-8")) & 0xffffffff)),
                first_frame, frame_count))

    # Shape Keys created by the user are valid new channels even though they
    # have no imported metadata.
    for key in shape_keys.key_blocks:
        if key == basis or key.name in used:
            continue
        first_frame = len(frames)
        append_frame(key, {"weight": 100.0}, key.name)
        channels.append((key.name, zlib.crc32(key.name.encode("utf-8")) & 0xffffffff,
                         first_frame, 1))

    additional = parse_json_property(mesh, "eiem_blend_additional_json", [])
    original_count = int(mesh.get("eiem_original_vertex_count", len(mesh.vertices)))
    if additional and (original_count != len(mesh.vertices)
                       or len(source_vertices) != len(mesh.vertices)):
        # This game-specific auxiliary stream has no verified index mapping.
        # Standard shape position/normal/tangent deltas above are remapped;
        # do not guess a layout for opaque additional-normal data.
        raise ValueError("blend-shape additional normals have no verified mapping for topology changes or seam splitting")
    converted_additional = [to_source(value) for value in additional]
    return vertices, frames, channels, weights, converted_additional


def source_skin_binding(obj, armature):
    """Original binding metadata, keyed by path rather than mutable bone order."""
    poses = parse_json_property(obj, "eiem_bindposes_json", [])
    paths = parse_json_property(obj, "eiem_bone_paths_json", [])
    if not paths:
        bones = list(armature.data.bones)
        palette = parse_json_property(obj, "eiem_bone_palette_json", [])
        if any(not 0 <= int(i) < len(bones) for i in palette):
            raise ValueError("%s 的原骨骼映射引用了不存在的骨骼" % obj.name)
        paths = [str(bones[int(i)].get("eiem_path", bones[int(i)].name)) for i in palette]
    if len(paths) != len(poses) or len(set(paths)) != len(paths):
        raise ValueError("%s 的骨骼路径与绑定矩阵不完整或重复" % obj.name)
    if any(len(pose) != 16 or not all(math.isfinite(x) for x in pose) for pose in poses):
        raise ValueError("%s 的骨骼绑定矩阵无效" % obj.name)
    return dict(zip(paths, poses))


def shared_skin_bindings(armature):
    # Imported source records live on the shared skeleton, so deleting a
    # donor object does not delete its binding information. Existing projects
    # contribute the same original metadata from their surviving objects.
    records = parse_json_property(armature.data, "eiem_source_bindings_json", [])
    for other in sorted(bpy.data.objects, key=lambda item: item.name):
        if other.type == "MESH" and other.find_armature() == armature:
            record = source_skin_binding(other, armature)
            if record and record not in records:
                records.append(record)
    return records


def _unique_source_donors(values):
    donors = []
    for value in values:
        if (not isinstance(value, (list, tuple)) or len(value) != 3 or
                not str(value[0]).strip() or not str(value[1]).strip()):
            continue
        try:
            slot = int(value[2])
        except (TypeError, ValueError):
            continue
        if slot < 0:
            continue
        candidate = (str(value[0]).replace("\\", "/").strip(),
                     str(value[1]).strip(), slot)
        if not candidate[0] or not candidate[1]:
            continue
        if candidate not in donors:
            donors.append(candidate)
    return donors


def _source_candidates_for_object(obj, armature):
    """Return source Renderer palette donors keyed by authored bone path.

    A replacement may add a group that was present on a sibling source Mesh.
    The donor identity is kept as Mesh path/asset/slot metadata. It is never
    inferred from a runtime Transform name or from this object's local LOD
    order.
    """
    paths = parse_json_property(obj, "eiem_bone_paths_json", [])
    if not paths:
        return {}
    raw_candidates = parse_json_property(
        obj, "eiem_bone_source_candidates_json", [])
    raw_sources = parse_json_property(obj, "eiem_bone_sources_json", [])
    original_palette = parse_json_property(obj, "eiem_bone_palette_json", [])
    source_path = str(obj.data.get("eiem_target_path",
                                  obj.data.get("eiem_source", ""))).strip()
    source_asset = str(obj.data.get("eiem_target_asset",
                                  obj.data.get("eiem_asset", obj.name))).strip()
    result = {}
    for slot, path in enumerate(paths):
        values = []
        if len(raw_candidates) == len(paths) and isinstance(raw_candidates[slot], list):
            values = raw_candidates[slot]
        elif slot < len(raw_sources):
            values = [raw_sources[slot]]
        elif (not raw_sources and source_path and source_asset and
              slot < len(original_palette)):
            values = [[source_path, source_asset, slot]]
        donors = _unique_source_donors(values)
        if donors:
            result[str(path)] = donors
    return result


def shared_bone_source_candidates(armature):
    """Collect every native Mesh/slot donor in this Blender armature scene."""
    records = parse_json_property(
        armature.data, "eiem_source_bone_candidates_json", {})
    if not isinstance(records, dict):
        records = {}
    # JSON restores tuple donors as lists. Canonicalise the saved catalog on
    # every export so tuple donors from scene objects cannot multiply it.
    records = {str(path): donors for path, values in records.items()
               if (donors := _unique_source_donors(values if isinstance(values, list) else []))}
    for other in sorted(bpy.data.objects, key=lambda item: item.name):
        if other.type != "MESH" or other.find_armature() != armature:
            continue
        for path, candidates in _source_candidates_for_object(other, armature).items():
            target = records.setdefault(path, [])
            for candidate in candidates:
                if candidate not in target:
                    target.append(candidate)
    # Keep the authoring-side donor catalog with the shared Armature.  A user
    # may hide or delete a source object after importing it; exports still
    # retain the exact source identities observed before that edit.
    armature.data["eiem_source_bone_candidates_json"] = json.dumps(
        records, separators=(",", ":"))
    return records


def _skin_export_context(armature, cache=None):
    """Share validated skeleton data only within one synchronous export."""
    key = int(armature.as_pointer())
    if cache is not None and key in cache:
        return cache[key]
    nodes = skeleton_author_nodes(armature)
    sibling_indices, child_counts = {}, {}
    # Bone collection order is the serialized child order, including unused
    # bones. Do not sort names or prune zero-weight siblings.
    for bone in armature.data.bones:
        parent = bone.parent.name if bone.parent else None
        sibling_indices[bone.name] = child_counts.get(parent, 0)
        child_counts[parent] = sibling_indices[bone.name] + 1
    context = dict(nodes=nodes,
                   by_path={record[0]: bone for bone, record, _ in nodes},
                   by_name={bone.name: record[0] for bone, record, _ in nodes},
                   records={bone.name: record for bone, record, _ in nodes},
                   authored={record[0]: bool(source) for _, record, source in nodes},
                   sibling_indices=sibling_indices, index_paths={})
    if cache is not None:
        cache[key] = context
    return context


def explicit_skeleton_bone_index_paths(armature, paths, context=None):
    """Serialize paths for an explicit Mod-owned Skeleton resource.

    Ordinary Mesh replacement does not use these paths. They remain in the
    v6 payload so an explicit Skeleton can address its own generated nodes.
    """
    context = context if context is not None else _skin_export_context(armature)
    by_path = context['by_path']
    memo = context['index_paths']
    result = []
    for path in paths:
        bone = by_path.get(path)
        if bone is None:
            raise ValueError("共享骨架中不存在骨骼路径：" + path)
        # Skeleton resources contain an empty serialization root above the
        # first named Transform (usually ``Root``).  Runtime resolution starts
        # from that named Transform, so make the identity relative to the
        # named root instead of emitting the empty-root child index as a
        # spurious leading component.
        root_path = path.split("/", 1)[0] if path else ""
        root = by_path.get(root_path)
        if root is None:
            raise ValueError("共享骨架中不存在根骨骼路径：" + root_path)
        pending = []
        while bone != root and (root.name, bone.name) not in memo:
            if bone.parent is None:
                raise ValueError("骨骼不属于其声明的根路径：" + path)
            try:
                index = context['sibling_indices'][bone.name]
            except KeyError:
                raise ValueError("无法确定骨骼的同级顺序：" + path)
            pending.append((bone.name, index))
            bone = bone.parent
        value = '' if bone == root else memo[(root.name, bone.name)]
        for name, index in reversed(pending):
            value = value + '/' + str(index) if value else str(index)
            memo[(root.name, name)] = value
        result.append(value)
    return result


def export_skin_binding(obj, armature, source_vertices, skin_cache=None):
    """Keep original slots; extend with bones from this shared armature."""
    if not armature:
        # A static consumer can reuse a source Mesh carrying unused skin
        # metadata. Without a renderer bone palette none of it is a binding.
        if not parse_json_property(obj, "eiem_bone_paths_json", []):
            return ([], [], [], [], [], [], [])
        raise ValueError("%s 保留了蒙皮绑定，但缺少有效的 Armature 修改器；请将修改器关联到原骨架 %s" %
                         (obj.name, obj.get('eiem_skeleton', '')))
    original = source_skin_binding(obj, armature)
    hashes = [int(x) for x in parse_json_property(obj, "eiem_bone_hashes_json", [])]
    if not original or len(hashes) != len(original):
        raise ValueError("%s 缺少完整的原骨骼绑定信息" % obj.name)
    context = _skin_export_context(armature, skin_cache)
    by_path = context['by_path']
    missing = set(original) - by_path.keys()
    if missing:
        raise ValueError("原骨骼已不在共享骨架中：" + sorted(missing)[0])
    by_name = context['by_name']
    paths = list(original)
    # An empty vertex group is not a skinning dependency. In particular, a
    # copied garment can carry names of new physics chains it never uses.
    # Read RNA influences once and reuse them for final slot assignment.
    group_names = [group.name for group in obj.vertex_groups]
    weighted_vertices = [[(int(g.group), float(g.weight)) for g in vertex.groups
                          if g.weight > 0] for vertex in obj.data.vertices]
    used_groups = {index for influences in weighted_vertices for index, weight in influences}
    additions = sorted({by_name[group_names[index]] for index in used_groups
                        if 0 <= index < len(group_names) and group_names[index] in by_name} - original.keys())
    matrices = dict(original)
    if additions:
        def matrix(values):
            return Matrix([values[row::4] for row in range(4)])
        def flat(value):
            return [value[row][column] for column in range(4) for row in range(4)]
        if 'bindings' not in context:
            context['bindings'] = shared_skin_bindings(armature)
        pending = list(context['bindings'])
        while pending:
            progress = False
            for record in list(pending):
                common = sorted(matrices.keys() & record.keys())
                if not common:
                    continue
                anchor = common[0]
                basis = matrix(record[anchor]).inverted() @ matrix(matrices[anchor])
                converted = {p: flat(matrix(v) @ basis) for p, v in record.items()}
                if any(max(abs(a-b) for a,b in zip(converted[p], matrices[p])) > 1e-4 for p in common):
                    raise ValueError("共享骨架的原生绑定姿态不一致，不能合并骨骼：" + anchor)
                for p, v in converted.items():
                    if p not in matrices:
                        matrices[p] = v
                pending.remove(record)
                progress = True
            if not progress:
                break
        # A bone never used by a source mesh has no authored inverse bind
        # matrix. Generate only that new slot from the skeleton's rest TRS,
        # calibrated to an existing bind frame; never overwrite original slots.
        world = {}
        def rest(bone):
            if bone.name not in world:
                record = context['records'][bone.name]
                p, q, s = record[2:]
                local = Matrix.LocRotScale(Vector(p), Quaternion((q[3],q[0],q[1],q[2])), Vector(s))
                world[bone.name] = rest(bone.parent) @ local if bone.parent else local
            return world[bone.name]
        for p in additions:
            if p not in matrices:
                bone = by_path[p]
                ancestor = bone.parent
                while ancestor and by_name[ancestor.name] not in matrices:
                    ancestor = ancestor.parent
                anchor = by_name[ancestor.name] if ancestor else next(iter(original))
                matrices[p] = flat(rest(bone).inverted() @ rest(by_path[anchor]) @ matrix(matrices[anchor]))
        paths.extend(additions)
        hashes.extend(zlib.crc32(p.encode("utf-8")) & 0xffffffff for p in additions)
    slot = {by_path[p].name:i for i,p in enumerate(paths)}
    # Resolve vertex-group names once. The old path read every Blender RNA
    # vertex-group name twice: once for validation and again while building
    # the exported influences. Large clothing meshes can have hundreds of
    # thousands of vertex/group pairs, so keep the same validation while
    # constructing the final influence list in one pass.
    group_slots = {index: slot[name] for index, name in enumerate(group_names)
                   if name in slot}
    skin_by_vertex = []
    reduced = 0
    used = {loop.vertex_index for loop in obj.data.loops}
    for vertex in obj.data.vertices:
        influences = []
        for group_index, weight in weighted_vertices[vertex.index]:
            influence_slot = group_slots.get(group_index)
            if influence_slot is None:
                group_name = (group_names[group_index]
                              if 0 <= group_index < len(group_names)
                              else "<invalid>")
                raise ValueError(
                    "%s \u9876\u70b9 %d \u7684\u9876\u70b9\u7ec4 %s \u5728\u5171\u4eab\u9aa8\u67b6\u4e2d\u4e0d\u5b58\u5728" %
                    (obj.name, vertex.index, group_name))
            influences.append((weight, influence_slot))
        if not influences and vertex.index in used:
            raise ValueError("%s 顶点 %d 没有骨骼权重" % (obj.name, vertex.index))
        influences.sort(key=lambda pair:(-pair[0],pair[1]))
        if len(influences) > 4:
            reduced += 1
            influences = influences[:4]
            total = sum(w for w,i in influences)
            influences = [(w/total,i) for w,i in influences]
        # Preserve valid source floats. Normalize only genuinely unnormalised
        # author weights, not float32 rounding in untouched source assets.
        total = sum(w for w,i in influences)
        if total and abs(total-1) > 1e-5:
            influences = [(w/total,i) for w,i in influences]
        influences += [(0.,0)] * (4-len(influences))
        skin_by_vertex.append((tuple(w for w,i in influences),tuple(i for w,i in influences)))
    if reduced:
        print("[EFF] %s: %d vertices reduced to the four strongest normalized skin influences" % (obj.name,reduced))
    if 'candidates' not in context:
        context['candidates'] = shared_bone_source_candidates(armature)
    catalog = context['candidates']
    authored_nodes = context['authored']
    source_candidates = []
    for path in paths:
        candidates = list(catalog.get(path, []))
        if not candidates and authored_nodes.get(path, True):
            raise ValueError(
                "%s 骨骼 %s 没有任何原生 Mesh 供体槽；当前 Mesh 阶段不能伪造槽号"
                % (obj.name, path))
        source_candidates.append(candidates)
    # Every exported palette slot must have a complete source identity for
    # ordinary native Mesh replacement. Empty entries are reserved for slots
    # supplied by an explicit Mod-owned Skeleton resource.
    if (len(source_candidates) != len(paths) or any(
            any(not isinstance(candidate, tuple) or len(candidate) != 3 or
                not candidate[0] or not candidate[1] or candidate[2] < 0
                for candidate in candidates)
            for candidates in source_candidates)):
        raise ValueError("%s source Renderer bone candidate table is incomplete" % obj.name)
    sources = [candidates[0] if candidates else ("", "", 0)
               for candidates in source_candidates]
    return ([skin_by_vertex[i] for i in source_vertices],
            [matrices[p] for p in paths], hashes, paths,
            explicit_skeleton_bone_index_paths(armature, paths, context), sources,
            source_candidates)


def write_mesh(path, obj, return_snapshot=False, skin_cache=None):
    mesh = obj.data
    mesh.calc_loop_triangles()
    coordinate = mesh.get("eiem_coordinate_space", "unity-y-up-left-handed")
    to_source = blender_to_unity if is_unity_left_handed(coordinate) else (lambda value: tuple(value))
    # EFF stores one normal per vertex, whereas Blender evaluates custom
    # normals per face corner. Keep the original float32 values for a lossless
    # untouched round trip; after a native Blender normal edit, export the
    # evaluated state instead of silently falling back to that backup.
    source_normal_name = resolve_author_attribute(
        mesh, "point", "source_normal", "FLOAT_VECTOR")
    source_normals = get_point_attribute(mesh, source_normal_name, "vector")
    baseline_crc = str(mesh.get("eiem_normal_baseline_crc", ""))
    preserve_normals = (source_normals is not None and baseline_crc and
                        (normal_state_crc(mesh) == baseline_crc or
                         normal_state_matches_source(mesh, source_normals)))
    normal_corners = [] if preserve_normals else [tuple(c.vector) for c in mesh.corner_normals]
    uv_channels = mesh_export_uv_channels(mesh)
    color_domain, color_values = mesh_export_colors(mesh)
    tangent_points, tangent_corners = mesh_export_tangents(
        mesh, source_normals, normal_corners, preserve_normals)
    corner_channels = [channel[1] for channel in uv_channels if channel is not None]
    if not preserve_normals:
        corner_channels.append(normal_corners)
    if color_domain == "CORNER":
        corner_channels.append(color_values)
    if len(tangent_corners):
        corner_channels.append(tangent_corners)
    source_vertices, source_loops, loop_vertices = export_corner_map(
        mesh, corner_channels, include_loose=not return_snapshot)

    coordinates = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get('co', coordinates)
    reflected = is_unity_left_handed(coordinate)
    vertex_values = coordinates.reshape(-1, 3)[source_vertices].astype(np.float64)
    if reflected:
        vertex_values[:, 0] *= -1
    vertices = vertex_values.ravel().tolist()
    normal_values = [source_normals[source] if preserve_normals else
                     normal_corners[loop] if loop is not None else tuple(mesh.vertices[source].normal)
                     for source, loop in zip(source_vertices, source_loops)]
    normal_array = np.asarray(normal_values, dtype=np.float64).reshape(-1, 3).copy()
    if reflected:
        normal_array[:, 0] *= -1
    normals = normal_array.ravel().tolist()

    tangents = []
    if tangent_points:
        tangent_array = np.asarray(tangent_points, dtype=np.float64)[source_vertices].copy()
        if len(tangent_corners):
            present = np.fromiter((i is not None for i in source_loops), dtype=np.bool_, count=len(source_loops))
            loop_indices = np.fromiter((i for i in source_loops if i is not None), dtype=np.int64)
            tangent_array[present] = np.asarray(tangent_corners, dtype=np.float64)[loop_indices]
        if reflected:
            tangent_array[:, 0] *= -1
        tangents = tangent_array.ravel().tolist()
    source_ids = np.asarray(source_vertices, dtype=np.int64)
    corner_ids = np.fromiter((loop if loop is not None else -1 for loop in source_loops),
                             dtype=np.int64, count=len(source_loops))
    corner_mask = corner_ids >= 0
    colors = []
    if color_values:
        color_array = np.asarray(color_values, dtype=np.float64)
        if color_domain == "POINT":
            colors = color_array[source_ids].ravel().tolist()
        else:
            output_colors = np.zeros((len(source_ids), 4), dtype=np.float64)
            output_colors[corner_mask] = color_array[corner_ids[corner_mask]]
            colors = output_colors.ravel().tolist()
    uv_layers = []
    for channel in uv_channels:
        values = []
        if channel is not None:
            dimension, xy, zw = channel
            output_uvs = np.zeros((len(source_ids), dimension), dtype=np.float64)
            output_uvs[corner_mask, :2] = np.asarray(xy)[corner_ids[corner_mask]]
            if dimension > 2:
                output_uvs[:, 2:] = np.asarray(zw)[source_ids, :dimension - 2]
            values = output_uvs.ravel().tolist()
        uv_layers.append(values)

    indices = []
    triangle_loops = np.empty(len(mesh.loop_triangles)*3, dtype=np.int32)
    mesh.loop_triangles.foreach_get('loops', triangle_loops)
    triangle_materials = np.empty(len(mesh.loop_triangles), dtype=np.int32)
    mesh.loop_triangles.foreach_get('material_index', triangle_materials)
    triangle_vertices = np.asarray(loop_vertices)[triangle_loops].reshape(-1, 3)
    if reflected:
        triangle_vertices = triangle_vertices[:, (0, 2, 1)]
    submeshes = []
    # A submesh's position is its material slot. Keep empty slots before an
    # occupied slot instead of renumbering faces after the user deletes them.
    for submesh in range(int(triangle_materials.max()) + 1 if len(triangle_materials) else 0):
        submesh_vertices = triangle_vertices[triangle_materials == submesh].ravel()
        values = submesh_vertices.tolist()
        start = len(indices)
        indices.extend(values)
        first_vertex = int(submesh_vertices.min()) if len(values) else 0
        vertex_count = int(submesh_vertices.max()) - first_vertex + 1 if len(values) else 0
        submeshes.append((0, start, len(values), 0, first_vertex, vertex_count))

    armature = next((modifier.object for modifier in obj.modifiers
                     if modifier.type == "ARMATURE" and modifier.object), None)
    (skin, bindposes, bone_hashes, bone_paths, bone_index_paths,
     bone_sources, bone_source_candidates) = export_skin_binding(
        obj, armature, source_vertices, skin_cache)
    blend_vertices, blend_frames, blend_channels, blend_weights, additional = export_blend_shapes(obj, to_source, source_vertices)

    if return_snapshot:
        # Same corner/skin/shape mapping as the editable writer, without an
        # intermediate author file or merged-submesh runtime workaround.
        return dict(vertex_count=len(source_vertices), vertices=vertices,
                    normals=normals, tangents=tangents, colors=colors, uvs=uv_layers,
                    indices=indices, index_count=len(indices), submeshes=submeshes,
                    skin=skin, bindposes=bindposes, bone_hashes=bone_hashes,
                    bone_paths=bone_paths, bone_index_paths=bone_index_paths,
                    bone_sources=bone_sources, bone_source_candidates=bone_source_candidates,
                    blend_vertices=blend_vertices, blend_frames=blend_frames,
                    blend_channels=blend_channels, blend_weights=blend_weights,
                    additional=additional)
    writer = Writer(); writer.raw(MAGIC_MESH); writer.i32(6)
    writer.string(coordinate)
    writer.string(obj.data.get("eiem_source", "")); writer.string(obj.data.get("eiem_asset", obj.name))
    writer.i32(len(source_vertices)); writer.floats(vertices); writer.floats(normals)
    writer.floats(tangents); writer.floats(colors)
    for values in uv_layers: writer.floats(values)
    writer.i32(len(indices))
    writer.uints(indices)
    writer.i32(len(submeshes))
    for topology, start, count, base, first, vertex_count in submeshes:
        writer.i32(topology); writer.u32(start); writer.u32(count)
        writer.u32(base); writer.u32(first); writer.u32(vertex_count)
    writer.i32(len(skin))
    writer.skin(skin)
    writer.i32(len(bindposes))
    for matrix in bindposes:
        values = list(matrix)[:16]
        values += [0.0] * (16 - len(values))
        for value in values: writer.f32(value)
    writer.i32(len(bone_hashes))
    writer.uints(bone_hashes)
    writer.i32(len(bone_paths))
    for value in bone_paths: writer.string(value)
    writer.i32(len(bone_index_paths))
    for value in bone_index_paths: writer.string(value)
    writer.i32(len(bone_sources))
    for source in bone_sources:
        writer.string(source[0]); writer.string(source[1]); writer.u32(source[2])
    writer.i32(len(bone_source_candidates))
    for candidates in bone_source_candidates:
        writer.i32(len(candidates))
        for source in candidates:
            writer.string(source[0]); writer.string(source[1]); writer.u32(source[2])
    writer.i32(len(blend_vertices))
    writer.blend_vertices(blend_vertices)
    writer.i32(len(blend_frames))
    for name, first, count, has_normals, has_tangents, has_additional in blend_frames:
        writer.string(name); writer.u32(first); writer.u32(count)
        writer.raw(bytes((int(has_normals), int(has_tangents), int(has_additional))))
    writer.i32(len(blend_channels))
    for name, name_hash, first, count in blend_channels:
        writer.string(name); writer.u32(name_hash); writer.u32(first); writer.u32(count)
    writer.floats(blend_weights)
    writer.i32(len(additional))
    for value in additional:
        for component in value: writer.f32(component)
    path.write_bytes(writer.data)


def skeleton_author_nodes(obj):
    """Source nodes are references, new nodes carry actual parent-local TRS.

    This is shared by Mesh bind-pose generation and Skeleton serialization.
    A source bone edit is rejected, never silently replaced with stale metadata.
    """
    records, paths, indices, source_world = [], {}, {}, {}
    seen_paths = set()
    visiting = set()
    def visit(bone):
        if bone.name in indices:
            return
        if bone.name in visiting:
            raise ValueError("骨架存在循环父级：" + bone.name)
        visiting.add(bone.name)
        if bone.parent:
            visit(bone.parent)
        source = bool(bone.get("eiem_skeleton_source", "eiem_path" in bone))
        parent_path = paths[bone.parent.name] if bone.parent else ""
        path = str(bone.get("eiem_path", bone.name)) if source else (
            parent_path + "/" + bone.name if parent_path else bone.name)
        if path in seen_paths:
            raise ValueError("共享骨架中存在重复路径：" + path + "；复制的骨骼仍带有源身份，请使用新建骨骼")
        seen_paths.add(path)
        if source:
            p, q, s = (bone.get("eiem_local_" + key) for key in ("position", "rotation", "scale"))
            if p is None or q is None or s is None:
                raise ValueError("源骨骼缺少局部绑定数据：" + bone.name)
            expected_parent = str(bone.get("eiem_source_parent", path.rsplit("/", 1)[0] if "/" in path else ""))
            if parent_path != expected_parent:
                raise ValueError("暂不支持改变源骨骼父级：" + bone.name)
            local = Matrix.LocRotScale(Vector(p), Quaternion((q[3],q[0],q[1],q[2])), Vector(s))
            source_world[bone.name] = source_world[bone.parent.name] @ local if bone.parent else local
            baseline = bone.get("eiem_rest_display")
            expected = (Matrix([baseline[i:i+4] for i in range(0,16,4)]) if baseline else
                        unity_transform_matrix_to_blender(source_world[bone.name]))
            if not baseline:
                # Older author files have original TRS but no display snapshot.
                # Blender bones strip axis lengths; compare their normalized basis.
                expected = Matrix.LocRotScale(expected.translation, expected.to_quaternion(), Vector((1,1,1)))
            if max(abs(expected[r][c]-bone.matrix_local[r][c]) for r in range(4) for c in range(4)) > 1e-4:
                raise ValueError("源骨骼绑定姿态已改变，当前仅支持增加骨骼：" + bone.name)
        else:
            if not bone.parent:
                raise ValueError("新增骨骼必须挂在已有共享骨架内：" + bone.name)
            if "/" in bone.name or "\\" in bone.name:
                raise ValueError("骨骼名称不能包含路径分隔符：" + bone.name)
            # EditBone discards source axis scale. Use the preserved native
            # parent world, not its display-only Blender matrix, to derive TRS.
            basis = unity_transform_matrix_to_blender_basis()
            authored_world = basis.inverted() @ bone.matrix_local @ basis
            local = source_world[bone.parent.name].inverted() @ authored_world
            position, rotation, scale = local.decompose()
            if min(scale) <= 0 or max(abs(local[r][c]-Matrix.LocRotScale(position,rotation,scale)[r][c])
                                     for r in range(4) for c in range(4)) > 1e-4:
                raise ValueError("新增骨骼含不可表达的缩放或剪切：" + bone.name)
            p, q, s = tuple(position), (rotation.x,rotation.y,rotation.z,rotation.w), tuple(scale)
            source_world[bone.name] = source_world[bone.parent.name] @ local
        indices[bone.name] = len(records)
        paths[bone.name] = path
        records.append((bone, (path, indices.get(bone.parent.name, -1) if bone.parent else -1,
                              tuple(p), tuple(q), tuple(s)), source))
        visiting.remove(bone.name)
    for bone in obj.data.bones:
        visit(bone)
    validate_skeleton_nodes([record for bone, record, source in records],
                            [source for bone, record, source in records])
    return records


def write_skeleton(path, obj):
    records = skeleton_author_nodes(obj)
    writer = Writer(); writer.raw(MAGIC_SKEL); writer.i32(2)
    writer.string(obj.get("eiem_coordinate_space", "unity-y-up-left-handed"))
    writer.i32(len(records))
    for bone, record, source in records:
        bone_path, parent, position, rotation, scale = record
        writer.string(bone_path)
        writer.i32(parent)
        writer.raw(struct.pack("<3f", *(float(value) for value in position)))
        writer.raw(struct.pack("<4f", *(float(value) for value in rotation)))
        writer.raw(struct.pack("<3f", *(float(value) for value in scale)))
    # This file represents one shared skeleton hierarchy.  Renderer-local bone
    # palettes are serialized in their Mesh resources as hashes/bindposes and
    # must never turn into duplicate Armatures.
    writer.i32(0)
    writer.i32(-1)
    writer.i32(len(records))
    writer.raw(bytes(int(source) for bone, record, source in records))
    path.write_bytes(writer.data)


def write_texture(path, image):
    """Write the Blender image used by a Texture resource as a PNG.

    Unedited imported images are copied byte-for-byte. Dirty/generated images
    are encoded from Blender's current pixel buffer, so texture painting and
    image replacement both survive package export.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    source = ""
    if image.source == "FILE" and image.filepath:
        source = bpy.path.abspath(image.filepath, library=image.library)
    if not image.is_dirty and source and Path(source).is_file():
        if Path(source).resolve() != path.resolve():
            shutil.copy2(source, path)
        return

    old_path = image.filepath_raw
    old_format = image.file_format
    try:
        image.filepath_raw = str(path)
        image.file_format = "PNG"
        image.save()
    finally:
        image.filepath_raw = old_path
        image.file_format = old_format


def image_absolute_path(image):
    if not image or not image.filepath:
        return ""
    return str(Path(bpy.path.abspath(image.filepath, library=image.library)).resolve())


def texture_section_base(path):
    """Return a stable INI section name without doubling a Texture prefix."""
    fragment = re.sub(r"[^A-Za-z0-9_]", "_", Path(path).stem).strip("_") or "External"
    fragment = fragment[:1].upper() + fragment[1:]
    return fragment if fragment.lower().startswith("texture") else "Texture" + fragment


def unique_texture_section(path, images_by_section):
    base = texture_section_base(path)
    used = {str(section).lower() for section in images_by_section}
    candidate = base
    serial = 2
    while candidate.lower() in used:
        candidate = "%s%d" % (base, serial)
        serial += 1
    return candidate


def file_content_digest(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.digest()


def texture_images_equal(first, second):
    """Compare two unedited image resources by their actual source bytes."""
    if first is None or second is None:
        return False
    if first is second:
        return not first.is_dirty
    if first.is_dirty or second.is_dirty:
        return False
    first_path = image_absolute_path(first)
    second_path = image_absolute_path(second)
    if first_path and second_path:
        first_file, second_file = Path(first_path), Path(second_path)
        if first_file.is_file() and second_file.is_file():
            if os.path.normcase(str(first_file)) == os.path.normcase(str(second_file)):
                return True
            if first_file.stat().st_size != second_file.stat().st_size:
                return False
            return file_content_digest(first_file) == file_content_digest(second_file)
    first_source = str(first.get("eiem_source", "") or first.get("eiem_target_path", "")).strip()
    second_source = str(second.get("eiem_source", "") or second.get("eiem_target_path", "")).strip()
    return bool(first_source and first_source.lower() == second_source.lower())


def canonical_texture_section(image, images_by_section):
    """Collapse import-only section suffixes while retaining distinct images."""
    path = image_absolute_path(image)
    identity = path or str(image.get("eiem_name", image.name))
    preferred = texture_section_base(identity)
    owner = images_by_section.get(preferred)
    if owner is None or owner is image:
        images_by_section[preferred] = image
        return preferred
    if texture_images_equal(owner, image):
        return preferred
    current = str(image.get("eiem_section", "")).strip()
    return current or unique_texture_section(identity, images_by_section)


def texture_output_filename(image):
    """Keep the user's source stem; Texture section prefixes are not filenames."""
    path = image_absolute_path(image)
    source_name = Path(path).name if path else str(image.get("eiem_name", image.name))
    stem = Path(source_name).stem
    if not stem:
        raise ValueError("Texture image has no usable filename: " + image.name)
    return stem + ".png"


def texture_property_uses_linear_data(property_name):
    """Return whether a shader property stores vector data rather than colour."""
    name = str(property_name).lower()
    return "bump" in name or "normal" in name


def native_texture_family(section):
    """Ignore package-local numeric IDs on an extracted game Texture section."""
    text = str(section).strip().lower()
    if not text.startswith("texturet_"):
        return ""
    return re.sub(r"_+\d+$", "", text)


def resolve_material_texture(material, property_name, value, images_by_section,
                             original_bindings):
    """Resolve a Blender-facing image path to an exported Texture section."""
    value = str(value).strip()
    if value in images_by_section:
        section = canonical_texture_section(images_by_section[value], images_by_section)
        original_bindings[property_name] = section
        return section

    requested = Path(bpy.path.abspath(os.path.expandvars(value))).resolve()
    if not requested.is_file():
        raise ValueError(
            "%s %s texture path does not exist: %s" %
            (material.name, property_name, requested))

    requested_key = os.path.normcase(str(requested))
    for section, image in list(images_by_section.items()):
        existing = image_absolute_path(image)
        if existing and os.path.normcase(existing) == requested_key:
            section = canonical_texture_section(image, images_by_section)
            original_bindings[property_name] = section
            return section

    # Image datablocks carry package ownership metadata; Blender's global
    # filepath cache must not alias an image imported for another Mod.
    image = bpy.data.images.load(str(requested), check_existing=False)
    existing_section = image.get("eiem_section", "")
    if existing_section and existing_section in images_by_section:
        section = canonical_texture_section(image, images_by_section)
        original_bindings[property_name] = section
        return section

    section = unique_texture_section(requested, images_by_section)
    template = images_by_section.get(original_bindings.get(property_name, ""))
    defaults = {
        "eiem_linear": "true" if texture_property_uses_linear_data(property_name) else "false",
        "eiem_mipmaps": "true",
        "eiem_filter": "1", "eiem_wrap": "0", "eiem_aniso": "1",
        "eiem_mip_bias": "0",
    }
    image["eiem_section"] = section
    image["eiem_relative_path"] = "textures/%s.png" % section
    image["eiem_source"] = ""
    image["eiem_name"] = requested.stem
    image["eiem_target_path"] = ""
    image["eiem_target_asset"] = ""
    for key, default in defaults.items():
        # Imported legacy packages did not record Texture2D.linear.  In
        # particular they report a native bump map as false by default, which
        # must not make a newly assigned normal map use sRGB sampling.
        image[key] = (default if key == "eiem_linear" else
                      template.get(key, default) if template else default)
    images_by_section[section] = image
    original_bindings[property_name] = section
    return section


def _validated_mod_folder_name(value):
    """Validate the user-facing Mod folder name before joining it to a root."""
    name = str(value or "").strip()
    if not name:
        raise ValueError("EFF export validation failed")
    if name in {".", ".."} or any(ord(char) < 32 for char in name):
        raise ValueError("EFF export validation failed")
    if any(char in name for char in '<>:"/\\|?*') or name.rstrip(" .") != name:
        raise ValueError("EFF export validation failed")
    return name


def mod_export_directory(directory, filepath=None, mod_name=None):
    """Resolve the package root from the export dialog.

    ``mod_name`` is the explicit UI value and takes precedence over Blender's
    filename field. The legacy filename fallback remains for direct callers
    and older scripts; new exports therefore never depend on a typed filename.
    """
    raw_filepath = str(filepath or "").strip()
    selected = Path(
        directory or (os.path.dirname(raw_filepath) if raw_filepath else "")
        or os.getcwd()).expanduser().resolve()
    if mod_name is not None:
        return (selected / _validated_mod_folder_name(mod_name)).resolve()
    if not raw_filepath:
        return selected
    candidate = Path(bpy.path.abspath(os.path.expandvars(raw_filepath))).expanduser().resolve()
    if candidate == selected:
        return selected
    if candidate.name and candidate.parent == selected:
        return candidate
    if not directory and candidate.name:
        return candidate
    return selected


def visible_eiem_resources(context=None):
    """Return EFF meshes visible in the current Blender view."""
    context = context or bpy.context
    return sorted([
        obj for obj in context.scene.objects
        if obj.type == "MESH"
        and obj.data.get("eiem_section")
        and obj.visible_get(view_layer=context.view_layer)
        and not obj.hide_viewport
        and not obj.hide_get(view_layer=context.view_layer)
    ], key=lambda obj: (
        str(obj.data.get("eiem_section", "")), obj.name))


def export_meshes_for_scope(context, selected_meshes, resource_scope,
                            effect_only=False, physics_only=False):
    return list(selected_meshes)


def selected_eiem_resources(context=None,allow_empty=False):
    context = context or bpy.context
    selected_objects = list(context.selected_objects)
    meshes = sorted(
        (obj for obj in selected_objects
         if obj.type == "MESH" and obj.data.get("eiem_section")
         and effect_editor.root_of(obj) is None),
        key=lambda obj: str(obj.data.get("eiem_section", "")),
    )
    armatures = sorted(
        (obj for obj in selected_objects
         if obj.type == "ARMATURE" and obj.get("eiem_section")),
        key=lambda obj: str(obj.get("eiem_section", "")),
    )
    if not meshes and not allow_empty:
        raise ValueError("No EFF mesh objects selected")
    return meshes, armatures


def selected_export_resources(context=None):
    context=context or bpy.context
    meshes,rigs=selected_eiem_resources(context,allow_empty=True)
    effects=effect_editor.export_roots(context.selected_objects,meshes,sys.modules[__name__])
    if not meshes and not effects and not selected_eiem_physics(context):
        raise ValueError('请选择 EFF 网格或特效根 / 发射器')
    return meshes,rigs,effects


def export_scope_resources(context=None, resource_scope='ALL'):
    context = context or bpy.context
    selected_meshes, rigs = selected_eiem_resources(context, allow_empty=True)
    physics = selected_eiem_physics(context)
    effects = effect_editor.export_roots(
        context.selected_objects, selected_meshes, sys.modules[__name__])
    if not selected_meshes and effects and physics:
        raise ValueError('请分别导出特效和 Physics')
    effect_only = not selected_meshes and bool(effects)
    physics_only = not selected_meshes and not effects and bool(physics)
    if effect_only:
        resource_scope = 'ALL'
    elif physics_only:
        resource_scope = 'PHYSICS'
    elif resource_scope == 'PHYSICS':
        resource_scope = 'ALL'
    meshes = export_meshes_for_scope(
        context, selected_meshes, resource_scope, effect_only, physics_only)
    if resource_scope == 'ALL' and not effect_only and not physics_only:
        effects = effect_editor.export_roots(
            context.selected_objects, meshes, sys.modules[__name__])
    if not meshes and not effects and not physics:
        raise ValueError('No selected EFF mesh objects or selected effects/physics')
    return meshes, rigs, effects, physics, effect_only, physics_only, resource_scope


def plan_mesh_only_export(mesh_objects):
    """Build the smallest export graph for an explicit Mesh-only export.

    Mesh-only validation must not inspect switch groups, shape controls, rigs or
    physics. The selected Mesh still needs its source identity so sibling parts
    can be merged onto the source Renderer exactly like a normal export.
    """
    selected = list(mesh_objects)
    if not selected:
        raise ValueError("No EFF mesh objects selected")
    if any(obj.type != "MESH" or not obj.data.get("eiem_section")
           for obj in selected):
        raise ValueError("Selected objects contain a non-EFF Mesh")
    sources = {obj: mesh_source_identity(obj) for obj in selected}
    for obj in selected:
        if not sources[obj][2]:
            raise ValueError("Mesh %s is missing its source Mesh name" % obj.name)
    grouped = {}
    selectors = {}
    for obj in sorted(selected, key=lambda item: (sources[item], item.name)):
        identity = sources[obj]
        if identity[2] in selectors and selectors[identity[2]] != identity:
            raise ValueError(
                "Same-name Meshes come from different source assets: "
                + identity[2])
        selectors[identity[2]] = identity
        grouped.setdefault(identity, []).append(obj)
    return {
        "objects": [obj for objects in grouped.values() for obj in objects],
        "sources": list(grouped.values()),
        "groups": [],
        "bindings": {},
        "hidden": {obj for obj in selected if obj.hide_render},
    }


def selected_eiem_physics(context=None):
    context = context or bpy.context
    selected_rigs = {obj for obj in context.selected_objects if obj.type == 'ARMATURE'}
    return sorted(
        (obj for obj in bpy.data.objects
         if obj.eiem_physics.kind == "GROUP" and
         (obj.select_get() or obj.eiem_physics.rig in selected_rigs)),
        key=lambda obj: str(obj.eiem_physics.identity),
    )


def material_override_payload(material, images_by_section, force=False):
    """Return the minimal clone payload and the Texture sections it needs."""
    source = str(material.get("eiem_source", "")).strip()
    if not source:
        raise ValueError("Material %s has no source game material" % material.name)

    has_baseline = "eiem_baseline_json" in material
    baseline = parse_json_property(material, "eiem_baseline_json", {})
    source_snapshot = any(
        key.startswith(("float.", "value4.", "color.", "int.", "keyword."))
        for key in baseline
    )
    texture_bindings = parse_json_property(
        material, "eiem_texture_sections_json", {})
    reserved = {
        "eiem_section", "eiem_source", "eiem_shader", "eiem_format",
        "eiem_version", "eiem_name", "eiem_target_path", "eiem_target_asset",
        "eiem_texture_sections_json", "eiem_baseline_json",
        "eiem_material_file", "eiem_author_package", "eiem_native_id",
    }
    overrides = []
    referenced_images = set()
    for key, raw_value in material.items():
        if not key.startswith("eiem_") or key in reserved or key.startswith('eiem_editor_'):
            continue
        property_name = key[5:]
        value = str(raw_value)
        if property_name.startswith("texture."):
            if not value.strip():
                continue  # An empty authored slot inherits the source value.
            if value == str(baseline.get(property_name, "")) and value.replace("\\", "/").lower().startswith("assets/"):
                continue  # unchanged game logical texture: inherited from the cloned source
            original = str(baseline.get(property_name, "") if has_baseline else
                           texture_bindings.get(property_name, ""))
            # Capture the baseline owner before canonicalization adds an alias
            # for a legacy section such as TextureTextureBody -> TextureBody.
            # A missing owner means the local file is an authored override,
            # even when its preferred section spelling equals the old string.
            original_image = images_by_section.get(original)
            value = resolve_material_texture(
                material, property_name, value, images_by_section,
                texture_bindings)
            current_image = images_by_section.get(value)
            import_alias = (
                source_snapshot and original.lower().startswith(value.lower()) and
                original[len(value):].isdigit()
            )
            original_family = native_texture_family(original)
            if (source_snapshot and original_family and
                    original_family == native_texture_family(value)):
                import_alias = True
            changed = bool(current_image is not None and current_image.is_dirty)
            if not changed:
                if not source_snapshot:
                    # Short/legacy material files are authored deltas even if
                    # they predate overrides=true. Their explicit textures
                    # must survive another export.
                    changed = True
                elif original_image is not None:
                    changed = value != original and not texture_images_equal(
                        current_image, original_image)
                else:
                    changed = value != original and not import_alias
            if changed:
                if (current_image is not None and
                        texture_property_uses_linear_data(property_name)):
                    current_image["eiem_linear"] = "true"
                referenced_images.add(value)
        else:
            # Version 0.3.x scenes do not carry a baseline. Preserve their
            # authored scalar/vector edits once, while still omitting original
            # Texture files whose section identity did not change.
            changed = (not has_baseline or
                       value != str(baseline.get(property_name, "")))
        if changed:
            overrides.append((property_name, value))

    material["eiem_texture_sections_json"] = json.dumps(
        texture_bindings, sort_keys=True, separators=(",", ":"))
    source_changed = has_baseline and source != str(baseline.get("source", source))
    if not force and not source_changed and not overrides:
        return None, set()

    values = [
        "format=EFFMAT",
        "version=1",
        "overrides=true",
        "source=" + source,
        "name=" + str(material.get("eiem_name", material.name)),
    ]
    shader = str(material.get("eiem_shader", "")).strip()
    if shader:
        values.append("shader=" + shader)
    values.extend("%s=%s" % item for item in overrides)
    return values, referenced_images


def unique_export_section(requested, used, prefix):
    name = re.sub(r"[^a-zA-Z0-9_]", "_", str(requested))
    if not name.lower().startswith(prefix.lower()):
        name = prefix + name
    name = name[:70] or prefix
    candidate, index = name, 2
    while candidate.lower() in used:
        candidate = name + "_" + str(index)
        index += 1
    used.add(candidate.lower())
    return candidate


def export_package(root, mesh_objects=None, armatures=None, physics_objects=None,
                   mesh_only=False, lod_levels=None, include_switches=True,
                   source_baseline='', resource_scope='ALL', game_directory='',effect_roots=None):
    """Export through the single format-2 author pipeline used by the UI.

    Selection and Blender dependency updates stay in this facade. Native
    encoding, author publication and offline compilation live in their focused
    modules; callers cannot accidentally select the removed format-1 runtime
    replacement writer.
    """
    if mesh_objects is None:
        mesh_objects, selected_armatures,selected_effects = selected_export_resources()
        if effect_roots is None:effect_roots=selected_effects
        if armatures is None:
            armatures = selected_armatures
        if physics_objects is None:
            physics_objects = selected_eiem_physics()
    if mesh_only:
        armatures, physics_objects = [], []
    if not source_baseline:
        source_baseline = getattr(bpy.context.scene, 'eiem_source_baseline', '')
    if bpy.context.mode != 'OBJECT':
        raise ValueError('请回到物体模式后导出')
    if resource_scope == 'PHYSICS':
        physics_objects=list(physics_objects or selected_eiem_physics())
        if not physics_objects:
            raise ValueError('EFF Physics export requires a selected physics group')
        packages={str(group.eiem_physics.rig.get('eiem_author_package','')).strip()
                  for group in physics_objects if group.eiem_physics.rig}
        packages.discard('')
        if len(packages)>1:
            raise ValueError('Selected Physics Rigs come from different unpacked source packages')
        if packages:
            rig_package=Path(next(iter(packages))).resolve()
            if source_baseline and Path(source_baseline).resolve()!=rig_package:
                raise ValueError('Source directory does not match the selected Physics Rig')
            source_baseline=str(rig_package)
        elif not source_baseline:
            raise ValueError('Physics Rig must identify one unpacked source package')
        game_directory=export_game_directory(root,game_directory)
        bpy.context.view_layer.update()
        return native_export.export_native_package(
            sys.modules[__name__],root,[],armatures=[],physics_objects=physics_objects,
            mesh_only=False,lod_levels=None,include_switches=include_switches,
            source_baseline=source_baseline,resource_scope='PHYSICS',
            game_directory=game_directory,effect_roots=[])
    if not mesh_objects:
        if resource_scope!='ALL':raise ValueError('仅选择特效时请使用全部资源导出')
        prefs=_addon_preferences(bpy.context)
        return effect_editor.ExportBatch(effect_roots or (),root,game_directory or (prefs.game_directory if prefs else '')).run()
    game_directory = export_game_directory(root, game_directory)
    # Consume user edits before looking up the snapshot cache. Updates produced
    # by triangulation/tangent evaluation are consumed while suppressed so they
    # do not turn an unchanged second export into a false miss.
    bpy.context.view_layer.update()
    global _EXPORT_IN_PROGRESS, _EXPORT_SKELETON_TOKENS
    previous_export = _EXPORT_IN_PROGRESS
    previous_tokens = _EXPORT_SKELETON_TOKENS
    # All Mesh snapshots in this synchronous export see the same rest rig.
    # Recheck it on the next export, including direct bone-property edits.
    _EXPORT_SKELETON_TOKENS = {}
    _EXPORT_IN_PROGRESS = True
    try:
        result = native_export.export_native_package(
            sys.modules[__name__], root, list(mesh_objects or []),
            armatures=list(armatures or []), physics_objects=list(physics_objects or []),
            mesh_only=mesh_only, lod_levels=lod_levels,
            include_switches=include_switches, source_baseline=source_baseline,
            resource_scope=resource_scope, game_directory=game_directory,
            effect_roots=effect_roots)
        costs=result.get('exportCostsMs',{})
        print('[EFF] 导出耗时：整理 %.2fs / 编译 %.2fs / 发布 %.2fs；网格缓存 %d 命中 / %d 重建'%(
            costs.get('author',0)/1000,costs.get('compile',0)/1000,costs.get('publish',0)/1000,
            result.get('meshSnapshotHits',0),result.get('meshSnapshotMisses',0)))
        return result
    finally:
        try:
            bpy.context.view_layer.update()
        finally:
            _EXPORT_IN_PROGRESS = previous_export
            _EXPORT_SKELETON_TOKENS = previous_tokens


def export_visible_package(root, context=None):
    """Export the current visible EFF mesh view using format 2."""
    meshes = visible_eiem_resources(context)
    if not meshes:
        raise ValueError('No visible EFF mesh objects found')
    return export_package(root, meshes, [], [], mesh_only=True)


EFF_INTERNAL_MATERIAL_PROPERTIES = {
    "eiem_baseline_json",
    "eiem_texture_sections_json",
    "eiem_material_file",
}


def eiem_id_property_path(key):
    escaped = str(key).replace("\\", "\\\\").replace('"', '\\"')
    return '["%s"]' % escaped


def draw_eiem_property(layout, owner, key, label, factor=0.24):
    """Draw one editable ID property with a consistently left-aligned label."""
    if owner is None or key not in owner:
        return False
    row = layout.row(align=True)
    split = row.split(factor=factor, align=True)
    label_column = split.column(align=True)
    label_column.alignment = "LEFT"
    label_column.label(text=label)
    value_column = split.column(align=True)
    value_column.prop(owner, eiem_id_property_path(key), text="")
    return True


def draw_eiem_rna_property(layout, owner, key, label, factor=0.24):
    row = layout.row(align=True)
    split = row.split(factor=factor, align=True)
    label_column = split.column(align=True)
    label_column.alignment = "LEFT"
    label_column.label(text=label)
    value_column = split.column(align=True)
    value_column.prop(owner, key, text="")


class EFF_OT_import_material(ImportHelper, bpy.types.Operator):
    bl_idname = "eiem.import_material"
    bl_label = '导入 EFF 材质'
    bl_options = {"REGISTER", "UNDO"}
    filter_glob: StringProperty(default="*.mat", options={"HIDDEN"})

    def execute(self, context):
        try:
            material = import_material_file(self.filepath, context.object)
            self.report({"INFO"}, "已载入材质：" + material.name)
            return {"FINISHED"}
        except (ValueError, OSError, RuntimeError, configparser.Error) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}


class EFF_PT_material_properties(bpy.types.Panel):
    bl_label = 'EFF 材质'
    bl_idname = "MATERIAL_PT_eiem_properties"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "material"

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "object", None)
        return obj is not None and obj.type == "MESH"

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = False
        layout.use_property_decorate = False
        material = context.material
        if material is None or not getattr(material, 'eff_effect_author', None):
            layout.operator("eiem.import_material", icon="IMPORT")
        if material is None or not material.get("eiem_section"):
            layout.label(text="导入后分配到当前材质槽，不覆盖原材质数据块")
            return
        material_editor.draw(layout, material)


class EFF_PG_shape_control(bpy.types.PropertyGroup):
    shape: StringProperty(name="形态键")
    enabled: BoolProperty(name='导出控制变量', default=True)
    automatic: BoolProperty(name='使用 Blender 当前权重', default=False)
    identity: StringProperty(options={"HIDDEN"})
    label: StringProperty(name='显示名称')
    default: FloatProperty(name="默认值", default=0.0)
    minimum: FloatProperty(name="最小值", default=0.0)
    maximum: FloatProperty(name="最大值", default=1.0)
    hotkey_increase: StringProperty(
        name='增大按键', default="",
        description="按住时以设定速度向形态键最大值移动；留空则不生成该按键")
    hotkey_decrease: StringProperty(
        name='减小按键', default="",
        description="按住时以设定速度向形态键最小值移动；留空则不生成该按键")
    hotkey_speed: FloatProperty(
        name="变化速度/秒", default=1.0, min=0.001,
        description="形态键实际权重每秒向按键指定的目标变化多少")


class EFF_PG_material_switch_option(bpy.types.PropertyGroup):
    material: PointerProperty(name="材质", type=bpy.types.Material)


class EFF_PG_material_switch(bpy.types.PropertyGroup):
    identity: StringProperty(options={"HIDDEN"})
    label: StringProperty(name="切换组名")
    key: StringProperty(name="游戏快捷键", default="F7")
    slot_index: IntProperty(name="材质槽", default=0, min=0)
    default_option: IntProperty(name="默认项", default=0, min=0)
    options: CollectionProperty(type=EFF_PG_material_switch_option)


class EFF_OT_shape_control(bpy.types.Operator):
    bl_idname = "eiem.shape_control"
    bl_label = "接管当前形态键"
    bl_options = {"REGISTER", "UNDO"}
    remove_index: IntProperty(default=-1, options={"HIDDEN"})
    sync: BoolProperty(default=False, options={"HIDDEN"})

    def execute(self, context):
        obj = context.object
        try:
            if self.sync:
                sync_new_shape_controls(obj)
            elif self.remove_index >= 0:
                obj.data.eiem_shape_controls.remove(self.remove_index)
            else:
                key = obj.active_shape_key
                add_shape_control(obj, key.name if key else "")
        except (ValueError, AttributeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class EFF_OT_shape_key_record(bpy.types.Operator):
    bl_idname = "eiem.shape_key_record"
    bl_label = "录制形态键按键"
    bl_description = "为增大或减小动作录制独立按键；Esc 取消"
    bl_options = {"REGISTER", "UNDO"}
    control_index: IntProperty(options={"HIDDEN"})
    direction: StringProperty(options={"HIDDEN"})
    clear: BoolProperty(default=False, options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        obj = context.object
        return bool(obj and obj.type == "MESH"
                    and hasattr(obj.data, "eiem_shape_controls"))

    def _status(self, context, text=None):
        workspace = getattr(context, "workspace", None)
        if workspace and hasattr(workspace, "status_text_set"):
            workspace.status_text_set(text=text)

    def _control(self, context):
        controls = context.object.data.eiem_shape_controls
        if not 0 <= self.control_index < len(controls):
            raise ValueError("形态键控制已改变，请重新操作")
        if self.direction not in {"INCREASE", "DECREASE"}:
            raise ValueError("形态键按键方向无效")
        return controls[self.control_index]

    def invoke(self, context, event):
        if self.clear:
            return self.execute(context)
        try:
            control = self._control(context)
        except ValueError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        self._object = context.object
        direction = '增大' if self.direction == "INCREASE" else '减小'
        self._status(context, "录制形态键%s按键：请按一个键或组合键；Esc 取消" % direction)
        context.window_manager.modal_handler_add(self)
        self.report({'INFO'}, "请按下用于%s %s 的游戏快捷键；Esc 取消" %
                    (direction, control.shape))
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.value != "PRESS":
            return {'RUNNING_MODAL'}
        if event.type == "ESC" and not (event.ctrl or event.shift or event.alt):
            self._status(context)
            return {'CANCELLED'}
        if event.type in {"LEFT_CTRL", "RIGHT_CTRL", "LEFT_SHIFT", "RIGHT_SHIFT",
                          "LEFT_ALT", "RIGHT_ALT", "OSKEY"}:
            return {'RUNNING_MODAL'}
        try:
            if context.object != self._object:
                raise ValueError("当前网格已改变，请重新录制")
            control = self._control(context)
            key = switch_key_from_event(event)
            set_shape_control_hotkey(
                context.object, control, key, self.direction, context.scene)
        except ValueError as error:
            self._status(context, "无法录制：%s；请按其他键，Esc 取消" % error)
            self.report({'WARNING'}, str(error))
            return {'RUNNING_MODAL'}
        self._status(context)
        self.report({'INFO'}, "%s → %s（%s）" % (
            key, control.shape,
            '增大' if self.direction == "INCREASE" else '减小'))
        return {'FINISHED'}

    def execute(self, context):
        try:
            control = self._control(context)
            set_shape_control_hotkey(
                context.object, control, "", self.direction, context.scene)
            return {'FINISHED'}
        except ValueError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}

    def cancel(self, context):
        self._status(context)


def draw_shape_hotkey_rows(layout, control, index):
    """Draw the same directional key editor in Properties and the N-panel."""
    for direction, attribute, label, icon in (
            ("INCREASE", "hotkey_increase", "增大到最大值", "TRIA_UP"),
            ("DECREASE", "hotkey_decrease", "减小到最小值", "TRIA_DOWN")):
        row = layout.row(align=True)
        key_value = getattr(control, attribute) or "未设置"
        row.label(text="%s：%s" % (label, key_value), icon=icon)
        op = row.operator("eiem.shape_key_record", text='录制', icon="REC")
        op.control_index, op.direction = index, direction
        if getattr(control, attribute):
            op = row.operator("eiem.shape_key_record", text='清除', icon="X")
            op.control_index, op.direction, op.clear = index, direction, True
    if control.hotkey_increase or control.hotkey_decrease:
        draw_eiem_rna_property(layout, control, "hotkey_speed",
                               "变化速度/秒", factor=0.32)
        layout.label(text="增大键只到最大值；减小键只到最小值")


class EFF_PT_shape_controls(bpy.types.Panel):
    bl_label = "EFF 形态键控制"
    bl_idname = "DATA_PT_eiem_shape_controls"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "data"

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "object", None)
        return obj is not None and obj.type == "MESH" and bool(obj.data.get("eiem_section"))

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = False
        layout.use_property_decorate = False
        obj = context.object
        layout.operator("eiem.shape_control", icon="ADD")
        layout.operator("eiem.shape_control", text="刷新新增形态键列表", icon="FILE_REFRESH").sync = True
        for index, control in enumerate(obj.data.eiem_shape_controls):
            box = layout.box()
            row = box.row(align=True)
            row.prop(control, "enabled")
            row.operator("eiem.shape_control", text="", icon="X").remove_index = index
            body = box.column()
            body.enabled = control.enabled
            if obj.data.shape_keys:
                body.prop_search(control, "shape", obj.data.shape_keys, "key_blocks", text="形态键")
            else:
                body.label(text="缺少形态键", icon="ERROR")
            body.prop(control, "automatic")
            key = obj.data.shape_keys.key_blocks.get(control.shape) if obj.data.shape_keys else None
            if control.automatic and key:
                draw_eiem_rna_property(body, key, "value", "默认值（当前权重）")
            for prop, label in (("label", "显示名称"), ("default", "默认值"),
                                ("minimum", "最小值"), ("maximum", "最大值")):
                if control.automatic and prop != "label":
                    continue
                draw_eiem_rna_property(body, control, prop, label)
            body.separator()
            body.label(text="独立按键控制", icon="EVENT_F")
            draw_shape_hotkey_rows(body, control, index)
        layout.label(text="新增形态键会自动导出为滑块；快捷键按需设置")


class EFF_OT_mesh_restore_source(bpy.types.Operator):
    bl_idname = "eiem.mesh_restore_source"
    bl_label = "恢复来源绑定"
    bl_description = "按物体保留的原 Renderer 来源恢复丢失的 Mesh 绑定，不改几何和控制"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "object", None)
        return (context.mode == "OBJECT" and obj is not None and obj.type == "MESH"
                and not obj.data.get("eiem_section"))

    def execute(self, context):
        try:
            section = restore_mesh_source_binding(context.object, context.scene)
            self.report({'INFO'}, "已恢复来源绑定：" + section)
            return {'FINISHED'}
        except ValueError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


class EFF_PT_mesh_properties(bpy.types.Panel):
    bl_label = 'EFF 网格'
    bl_idname = "DATA_PT_eiem_properties"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "data"

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "object", None)
        return (obj is not None and obj.type == "MESH" and
                bool(obj.data.get("eiem_section") or obj.get("eiem_render_section")))

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = False
        layout.use_property_decorate = False
        obj = context.object
        mesh = obj.data
        if not mesh.get("eiem_section"):
            layout.label(text="Mesh 数据块缺少来源绑定", icon="ERROR")
            layout.operator("eiem.mesh_restore_source", icon="FILE_REFRESH")

        # Logical paths and selectors are placed first because they are the
        # only identity fields an author is expected to inspect or change.
        draw_eiem_property(layout, obj, "eiem_render_asset", '命中 Mesh')
        draw_eiem_property(layout, mesh, "eiem_source", "源 Mesh 路径")
        draw_eiem_property(layout, mesh, "eiem_asset", 'Mesh 名称')

        box = layout.box()
        heading = box.row()
        heading.alignment = "LEFT"
        heading.label(text='资源信息')
        for owner, key, label in (
            (mesh, "eiem_section", "Mesh 资源段"),
            (obj, "eiem_prefab_path", "旧版 PFB 路径"),
            (obj, "eiem_prefab_section", "PFB 资源段"),
            (obj, "eiem_render_section", "Render 资源段"),
            (obj, "eiem_render_path", "旧版 Render 路径"),
            (obj, "eiem_skeleton", "骨架资源段"),
            (mesh, "eiem_coordinate_space", '坐标空间'),
            (mesh, "eiem_coordinate_space", "坐标空间"),
            (mesh, "eiem_target_path", "原 Mesh 路径"),
        ):
            draw_eiem_property(box, owner, key, label)


class EFF_PT_image_properties(bpy.types.Panel):
    bl_label = 'EFF 贴图'
    bl_idname = "IMAGE_PT_eiem_properties"
    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "UI"
    bl_category = "EFF"

    @classmethod
    def poll(cls, context):
        image = getattr(getattr(context, "space_data", None), "image", None)
        return image is not None and bool(image.get("eiem_section"))

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = False
        layout.use_property_decorate = False
        image = context.space_data.image
        draw_eiem_rna_property(layout, image, "filepath", "本地贴图路径")
        draw_eiem_property(layout, image, "eiem_source", "源贴图路径")
        draw_eiem_property(layout, image, "eiem_target_path", "原贴图路径")
        draw_eiem_property(layout, image, "eiem_target_asset", "原贴图名称")

        box = layout.box()
        heading = box.row()
        heading.alignment = "LEFT"
        heading.label(text='采样参数')
        for key, label in (
            ("eiem_linear", "Linear"),
            ("eiem_mipmaps", "Mipmaps"),
            ("eiem_filter", "Filter"),
            ("eiem_wrap", "Wrap"),
            ("eiem_aniso", "Aniso"),
            ("eiem_mip_bias", "Mip Bias"),
        ):
            draw_eiem_property(box, image, key, label)

        box = layout.box()
        heading = box.row()
        heading.alignment = "LEFT"
        heading.label(text='资源信息')
        for key, label in (
            ("eiem_section", "资源段"),
            ("eiem_name", "贴图名"),
            ("eiem_relative_path", "包内路径"),
        ):
            draw_eiem_property(box, image, key, label)


class EFF_OT_switch_create(bpy.types.Operator):
    bl_idname = "eiem.switch_create"
    bl_label = '从所选创建切换组'
    bl_description = "先按下游戏快捷键，再确认组名并创建切换组；Esc 取消"
    bl_options = {"REGISTER", "UNDO"}
    group_name: StringProperty(name='组名', default='部件切换')
    key: StringProperty(name="游戏快捷键", default="F6", options={"HIDDEN"})
    object_names: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and bool(context.selected_objects)

    def invoke(self, context, event):
        used = {g.get("eiem_key", "").upper() for g in switch_groups(context.scene)}
        self.key = next(("F%d" % i for i in range(6, 25) if i != 10 and "F%d" % i not in used), "")
        self.object_names = json.dumps(
            [obj.name for obj in context.selected_objects], ensure_ascii=False)
        self._status(context, "创建切换组：请按游戏快捷键；Esc 取消")
        context.window_manager.modal_handler_add(self)
        self.report({'INFO'}, "请按下要用于新切换组的游戏快捷键；Esc 取消")
        return {'RUNNING_MODAL'}

    def _status(self, context, text=None):
        workspace = getattr(context, "workspace", None)
        if workspace and hasattr(workspace, "status_text_set"):
            workspace.status_text_set(text=text)

    def modal(self, context, event):
        result, key = classify_switch_key_event(event)
        if result == "WAIT":
            return {'RUNNING_MODAL'}
        if result == "CANCEL":
            self._status(context)
            return {'CANCELLED'}
        if result == "INVALID":
            self._status(context, "此键不能用于游戏切换；请按其他键，Esc 取消")
            self.report({'WARNING'}, "此键不能用于游戏切换")
            return {'RUNNING_MODAL'}
        self.key = key
        self._status(context)
        bpy.ops.eiem.switch_create_confirm(
            'INVOKE_DEFAULT', group_name=self.group_name, key=self.key,
            object_names=self.object_names)
        return {'CANCELLED'}

    def draw(self, context):
        self.layout.prop(self, "group_name")
        self.layout.prop(self, "key", text="游戏快捷键")
        self.layout.label(text="按键已录制，可在创建前修改", icon="EVENT_F")

    def execute(self, context):
        objects = [bpy.data.objects.get(name) for name in json.loads(self.object_names)]
        objects = [obj for obj in objects if obj is not None]
        try:
            create_switch_group(self.group_name, self.key, objects, context.scene)
            return {'FINISHED'}
        except ValueError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


class EFF_OT_switch_create_confirm(bpy.types.Operator):
    bl_idname = "eiem.switch_create_confirm"
    bl_label = "创建切换组"
    bl_options = {"REGISTER", "UNDO"}
    group_name: StringProperty(name="组名", default="部件切换")
    key: StringProperty(name="游戏快捷键", default="F6")
    object_names: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        self.layout.prop(self, "group_name")
        self.layout.prop(self, "key", text="游戏快捷键")

    def execute(self, context):
        objects = [bpy.data.objects.get(name) for name in json.loads(self.object_names)]
        objects = [obj for obj in objects if obj is not None]
        try:
            create_switch_group(self.group_name, self.key, objects, context.scene)
            return {'FINISHED'}
        except ValueError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


class EFF_OT_switch_key_record(bpy.types.Operator):
    bl_idname = "eiem.switch_key_record"
    bl_label = '录制切换按键'
    bl_description = "点击后按下键盘按键或组合键；Esc 取消"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        group = context.scene.eiem_switch_active
        return bool(group and group.get("eiem_switch_group"))

    def _status(self, context, text=None):
        workspace = getattr(context, "workspace", None)
        if workspace and hasattr(workspace, "status_text_set"):
            workspace.status_text_set(text=text)

    def invoke(self, context, event):
        self._target = context.scene.eiem_switch_active
        self._status(context, "录制切换按键：请按一个键或组合键；Esc 取消")
        context.window_manager.modal_handler_add(self)
        self.report({'INFO'}, "请按下要用于 %s 的游戏快捷键；Esc 取消" % self._target.name)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.value != "PRESS":
            return {'RUNNING_MODAL'}
        if event.type == "ESC" and not (event.ctrl or event.shift or event.alt):
            self._status(context)
            return {'CANCELLED'}
        if event.type in {"LEFT_CTRL", "RIGHT_CTRL", "LEFT_SHIFT", "RIGHT_SHIFT",
                          "LEFT_ALT", "RIGHT_ALT", "OSKEY"}:
            return {'RUNNING_MODAL'}
        try:
            key = switch_key_from_event(event)
            if not self._target or not self._target.get("eiem_switch_group"):
                raise ValueError('切换组已删除')
            set_switch_group_key(self._target, key, context.scene)
        except ValueError as error:
            self._status(context, "无法录制：%s；请按其他键，Esc 取消" % error)
            self.report({'WARNING'}, str(error))
            return {'RUNNING_MODAL'}
        self._status(context)
        self.report({'INFO'}, "%s → %s" % (key, self._target.name))
        return {'FINISHED'}

    def cancel(self, context):
        self._status(context)


class EFF_UL_switch_states(bpy.types.UIList):
    """Compact, ordered outfit-state list."""

    def draw_item(self, context, layout, data, item, icon, active_data,
                  active_propname, index):
        if not item.get("eiem_switch_state"):
            layout.label(text=item.name, icon="COLLECTION_NEW")
            return
        row = layout.row(align=True)
        drag = row.operator("eiem.switch_state_drag", text="", icon="GRIP",
                            emboss=False)
        drag.state_name = item.name
        default = row.operator(
            "eiem.switch_state", text="", icon=(
                "RADIOBUT_ON" if item.get("eiem_default") else "RADIOBUT_OFF"),
            emboss=False)
        default.action, default.state_name = "DEFAULT", item.name
        row.prop(item, "name", text="", emboss=False)
        row.label(text="%d/%d" % (
            len(switch_meshes(item)), len(switch_members(data))))
        preview = row.operator("eiem.switch_state", text="", icon="HIDE_OFF",
                               emboss=False)
        preview.action, preview.state_name = "PREVIEW", item.name


class EFF_OT_switch_state_drag(bpy.types.Operator):
    bl_idname = "eiem.switch_state_drag"
    bl_label = '拖动款式排序'
    bl_description = "按住并上下拖动，改变该快捷键的循环顺序"
    bl_options = {"REGISTER", "UNDO", "BLOCKING"}
    state_name: StringProperty(options={"HIDDEN"})

    def _status(self, context, text=None):
        workspace = getattr(context, "workspace", None)
        if workspace and hasattr(workspace, "status_text_set"):
            workspace.status_text_set(text=text)

    def invoke(self, context, event):
        group = context.scene.eiem_switch_active
        state = next((candidate for candidate in switch_states(group)
                      if candidate.name == self.state_name), None) if group else None
        if state is None:
            self.report({'ERROR'}, "款式不存在，请重新操作")
            return {'CANCELLED'}
        self._group = group
        self._state = state
        self._original = switch_states(group)
        self._anchor_y = event.mouse_y
        self._step = max(16, round(
            20 * float(context.preferences.system.ui_scale)))
        self._status(context, "上下拖动调整款式循环顺序；松开完成，Esc 取消")
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type in {"ESC", "RIGHTMOUSE"}:
            try:
                set_switch_state_order(self._group, self._original)
                self._group.eiem_switch_state_index = self._original.index(
                    self._state)
            except (ReferenceError, ValueError):
                pass
            self._status(context)
            if context.area:
                context.area.tag_redraw()
            return {'CANCELLED'}
        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            self._status(context)
            return {'FINISHED'}
        if event.type != "MOUSEMOVE":
            return {'RUNNING_MODAL'}
        try:
            states = switch_states(self._group)
            current = states.index(self._state)
            delta = event.mouse_y - self._anchor_y
            while delta >= self._step and current > 0:
                current = reorder_switch_state(
                    self._group, self._state, current - 1)
                self._anchor_y += self._step
                delta = event.mouse_y - self._anchor_y
            while delta <= -self._step and current < len(states) - 1:
                current = reorder_switch_state(
                    self._group, self._state, current + 1)
                self._anchor_y -= self._step
                delta = event.mouse_y - self._anchor_y
            self._group.eiem_switch_state_index = current
        except (ReferenceError, ValueError) as error:
            self._status(context)
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        if context.area:
            context.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        self._status(context)


class EFF_OT_switch_member_add(bpy.types.Operator):
    bl_idname = "eiem.switch_member_add"
    bl_label = "加入当前组"
    bl_description = "将当前选中的 EFF Mesh 加入当前切换组，并放入当前款式"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        group = getattr(context.scene, "eiem_switch_active", None)
        return bool(group and group.get("eiem_switch_group") and
                    context.mode == "OBJECT" and context.selected_objects)

    def execute(self, context):
        group = context.scene.eiem_switch_active
        states = switch_states(group)
        try:
            active = states[max(0, min(group.eiem_switch_state_index,
                                      len(states) - 1))] if states else None
            added = add_switch_members(group, context.selected_objects,
                                       context.scene, active)
            self.report({"INFO"}, "已加入 %d 个 Mesh" % len(added))
            return {"FINISHED"}
        except ValueError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}


class EFF_OT_switch_member_remove(bpy.types.Operator):
    bl_idname = "eiem.switch_member_remove"
    bl_label = "移出当前组"
    bl_description = "从当前切换组移出 Mesh，不删除 Blender 对象"
    bl_options = {"REGISTER", "UNDO"}
    object_name: StringProperty(options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        group = getattr(context.scene, "eiem_switch_active", None)
        return bool(group and group.get("eiem_switch_group"))

    def execute(self, context):
        group = context.scene.eiem_switch_active
        member = next((obj for obj in switch_members(group)
                       if obj.name == self.object_name), None)
        if member is None:
            self.report({"WARNING"}, "Mesh 不在当前组中")
            return {"CANCELLED"}
        try:
            remove_switch_members(group, [member], context.scene, context)
            self.report({"INFO"}, "已移出 %s" % member.name)
            return {"FINISHED"}
        except ValueError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}


class EFF_OT_switch_member_remove_selected(bpy.types.Operator):
    bl_idname = "eiem.switch_member_remove_selected"
    bl_label = "移出所选 Mesh"
    bl_description = "从当前切换组移出当前选中的组内 Mesh"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        group = getattr(context.scene, "eiem_switch_active", None)
        return bool(group and group.get("eiem_switch_group") and
                    context.mode == "OBJECT" and context.selected_objects)

    def execute(self, context):
        group = context.scene.eiem_switch_active
        members = set(switch_members(group))
        selected = [obj for obj in context.selected_objects if obj in members]
        if not selected:
            self.report({"WARNING"}, "当前选中的 Mesh 不在当前组中")
            return {"CANCELLED"}
        try:
            remove_switch_members(group, selected, context.scene, context)
            self.report({"INFO"}, "已移出 %d 个 Mesh" % len(selected))
            return {"FINISHED"}
        except ValueError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}


class EFF_OT_switch_state(bpy.types.Operator):
    bl_idname = "eiem.switch_state"
    bl_label = "编辑切换状态"
    bl_options = {"REGISTER", "UNDO"}
    action: StringProperty()
    state_name: StringProperty()

    def execute(self, context):
        group = context.scene.eiem_switch_active
        if not group or not group.get("eiem_switch_group"):
            return {'CANCELLED'}
        try:
            if self.action == "DELETE_GROUP":
                delete_switch_group(group, context.scene, context)
                return {'FINISHED'}
            states = switch_states(group)
            if self.action == "REMOVE_ACTIVE" and states:
                index = max(0, min(group.eiem_switch_state_index,
                                   len(states) - 1))
                state = states[index]
            else:
                state = next((s for s in states
                              if s.name == self.state_name), None)
                if state is None and states and not self.state_name:
                    index = max(0, min(group.eiem_switch_state_index,
                                       len(states) - 1))
                    state = states[index]
            if self.action == "ADD":
                state = add_switch_state(group, '款式 %d' % (len(switch_states(group)) + 1))
                capture_switch_state(group, state, context)
                group.eiem_switch_state_index = len(switch_states(group)) - 1
            elif self.action == "SELECT_MEMBERS":
                bpy.ops.object.select_all(action="DESELECT")
                for obj in switch_members(group):
                    if obj.name in context.view_layer.objects:
                        obj.select_set(True)
            elif state is None:
                raise ValueError("状态不存在，请重新选择")
            elif self.action == "DEFAULT":
                set_switch_default(group, state)
            elif self.action == "CAPTURE":
                capture_switch_state(group, state, context)
            elif self.action == "PREVIEW":
                preview_switch(group, state, context)
            elif self.action in {"REMOVE", "REMOVE_ACTIVE"}:
                if len(switch_states(group)) <= 2:
                    raise ValueError('至少保留两个款式')
                if state.children:
                    raise ValueError("请先移出状态的子集合再删除状态")
                restore_switch_preview(switch_members(group), context)
                for obj in list(state.objects):
                    state.objects.unlink(obj)
                was_default = state.get("eiem_default")
                bpy.data.collections.remove(state)
                if was_default:
                    set_switch_default(group, switch_states(group)[0])
                group.eiem_switch_state_index = min(
                    group.eiem_switch_state_index,
                    len(switch_states(group)) - 1)
            return {'FINISHED'}
        except ValueError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


class EFF_OT_switch_restore(bpy.types.Operator):
    bl_idname = "eiem.switch_restore_preview"
    bl_label = "恢复预览前状态"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        restore_switch_preview(context=context)
        return {'FINISHED'}


class EFF_OT_material_switch_group(bpy.types.Operator):
    bl_idname = "eiem.material_switch_group"
    bl_label = "新增材质切换组"
    bl_options = {"REGISTER", "UNDO"}
    remove_index: IntProperty(default=-1, options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "object", None)
        return bool(obj and obj.type == "MESH" and obj.get("eiem_section"))

    def execute(self, context):
        obj = context.object
        groups = obj.eiem_material_switches
        if self.remove_index >= 0:
            if self.remove_index < len(groups):
                groups.remove(self.remove_index)
            return {"FINISHED"}
        slot_index = int(obj.active_material_index)
        if not 0 <= slot_index < len(obj.material_slots):
            self.report({"ERROR"}, "请先为当前 Mesh 分配材质")
            return {"CANCELLED"}
        if any(group.slot_index == slot_index for group in groups):
            self.report({"ERROR"}, "该材质槽已经有切换组")
            return {"CANCELLED"}
        material = obj.material_slots[slot_index].material
        if material is None or not material.get("eiem_section"):
            self.report({"ERROR"}, "当前材质不是已导入的 EFF 材质")
            return {"CANCELLED"}
        group = groups.add()
        group.identity = uuid.uuid4().hex[:16]
        group.label = obj.name + " 材质"
        group.key = "F7"
        group.slot_index = slot_index
        group.default_option = 0
        option = group.options.add()
        option.material = material
        return {"FINISHED"}


class EFF_OT_material_switch_option(bpy.types.Operator):
    bl_idname = "eiem.material_switch_option"
    bl_label = "材质选项"
    bl_options = {"REGISTER", "UNDO"}
    group_index: IntProperty(options={"HIDDEN"})
    option_index: IntProperty(default=-1, options={"HIDDEN"})
    set_default: BoolProperty(default=False, options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "object", None)
        return bool(obj and obj.type == "MESH" and hasattr(obj, "eiem_material_switches"))

    def execute(self, context):
        groups = context.object.eiem_material_switches
        if not 0 <= self.group_index < len(groups):
            return {"CANCELLED"}
        group = groups[self.group_index]
        if self.set_default:
            if 0 <= self.option_index < len(group.options):
                group.default_option = self.option_index
        elif self.option_index < 0:
            group.options.add()
        elif self.option_index < len(group.options):
            group.options.remove(self.option_index)
            group.default_option = max(0, min(group.default_option, len(group.options) - 1))
        return {"FINISHED"}


class EFF_OT_material_switch_key_record(bpy.types.Operator):
    bl_idname = "eiem.material_switch_key_record"
    bl_label = "录制材质切换按键"
    bl_options = {"REGISTER", "UNDO"}
    group_index: IntProperty(options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "object", None)
        return bool(obj and obj.type == "MESH" and hasattr(obj, "eiem_material_switches"))

    def invoke(self, context, event):
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        result, key = classify_switch_key_event(event)
        if result == "WAIT":
            return {"RUNNING_MODAL"}
        if result == "CANCEL":
            return {"CANCELLED"}
        if result == "INVALID":
            self.report({"WARNING"}, "此键不能用于材质切换")
            return {"RUNNING_MODAL"}
        groups = context.object.eiem_material_switches
        if not 0 <= self.group_index < len(groups):
            self.report({"ERROR"}, "材质切换组已改变")
            return {"CANCELLED"}
        groups[self.group_index].key = key
        return {"FINISHED"}


class EFF_PT_material_switches(bpy.types.Panel):
    bl_label = "EFF 材质切换"
    bl_idname = "OBJECT_PT_eiem_material_switches"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "object"

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "object", None)
        return bool(obj and obj.type == "MESH" and obj.get("eiem_section"))

    def draw(self, context):
        layout = self.layout
        obj = context.object
        layout.operator("eiem.material_switch_group", icon="ADD")
        for index, group in enumerate(obj.eiem_material_switches):
            box = layout.box()
            row = box.row(align=True)
            row.prop(group, "label", text="组名")
            remove = row.operator("eiem.material_switch_group", text="", icon="TRASH")
            remove.remove_index = index
            box.prop(group, "key", text="快捷键")
            key = box.operator("eiem.material_switch_key_record", text="录制快捷键", icon="REC")
            key.group_index = index
            slot_name = (obj.material_slots[group.slot_index].name
                         if group.slot_index < len(obj.material_slots) else "缺失材质槽")
            box.label(text="目标槽：%s" % slot_name, icon="MATERIAL")
            for option_index, option in enumerate(group.options):
                row = box.row(align=True)
                default = row.operator("eiem.material_switch_option", text="", icon=(
                    "RADIOBUT_ON" if option_index == group.default_option else "RADIOBUT_OFF"),
                    emboss=False)
                default.group_index = index
                default.option_index = option_index
                default.set_default = True
                row.prop(option, "material", text="默认" if option_index == group.default_option else "材质")
                remove_option = row.operator("eiem.material_switch_option", text="", icon="X")
                remove_option.group_index = index
                remove_option.option_index = option_index
            add = box.operator("eiem.material_switch_option", text="添加材质", icon="ADD")
            add.group_index = index


class EFF_PT_switches(bpy.types.Panel):
    bl_label = '网格切换'
    bl_idname = "VIEW3D_PT_eiem_switches"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "EFF"

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = False
        layout.use_property_decorate = False
        layout.operator("eiem.switch_create", icon="ADD")
        obj = getattr(context, "object", None)
        if obj is not None and obj.type == "MESH" and not obj.data.get("eiem_section"):
            layout.label(text=obj.name + " 缺少来源绑定", icon="ERROR")
            layout.operator("eiem.mesh_restore_source", icon="FILE_REFRESH")
        draw_eiem_rna_property(layout, context.scene, "eiem_switch_active", "当前组", factor=0.3)
        groups = switch_groups(context.scene)
        if groups:
            if len(groups) > 1:
                mapping = layout.box()
                mapping.label(text="游戏按键 → 切换组", icon="EVENT_F")
                for candidate in groups:
                    mapping.label(text="%s  →  %s" % (
                        candidate.get("eiem_key", "未设置"), candidate.name))
        group = context.scene.eiem_switch_active
        if group and group.get("eiem_switch_group"):
            name_row = layout.row(align=True)
            draw_eiem_rna_property(name_row, group, "name", '组名', factor=0.3)
            op = name_row.operator("eiem.switch_state", text="删除当前组", icon="TRASH")
            op.action = "DELETE_GROUP"
            key_row = layout.row(align=True)
            key_row.label(text="当前按键：" + str(group.get("eiem_key", "未设置")))
            key_row.operator("eiem.switch_key_record", text='录制按键', icon="REC")
            members = switch_members(group)
            row = layout.row(align=True)
            op = row.operator("eiem.switch_state", text='选择组内物体')
            op.action = "SELECT_MEMBERS"
            layout.label(text="%d 个受控网格；调整眼睛显隐后记录款式" % len(members),
                         icon="INFO")
            row.operator("eiem.switch_member_add", text="\u52a0\u5165\u6240\u9009 Mesh", icon="ADD")
            row.operator("eiem.switch_member_remove_selected",
                         text="\u79fb\u51fa\u6240\u9009 Mesh", icon="REMOVE")
            layout.label(text="\u5f53\u524d\u7ec4\u5185 Mesh\uff1a%d \u4e2a\uff1b\u6b3e\u5f0f\u4e2d\u7684\u663e\u9690\u4ecd\u53ef\u5355\u72ec\u8bb0\u5f55" % len(members),
                         icon="INFO")
            member_box = layout.box()
            member_box.label(text="\u5f53\u524d\u7ec4\u5185 Mesh", icon="MESH_DATA")
            if members:
                for member in sorted(members,
                                     key=lambda item: item.name.casefold()):
                    member_row = member_box.row(align=True)
                    member_row.label(text=member.name, icon="MESH_DATA")
                    remove = member_row.operator(
                        "eiem.switch_member_remove", text="", icon="X")
                    remove.object_name = member.name
            else:
                member_box.label(text="\u6682\u65e0 Mesh\uff1b\u5148\u9009\u62e9 Mesh \u5e76\u70b9\u51fb\u2018\u52a0\u5165\u6240\u9009 Mesh\u2019")
            states = switch_states(group)
            if states:
                active_index = max(
                    0, min(group.eiem_switch_state_index, len(states) - 1))
                row = layout.row()
                row.template_list(
                    "EFF_UL_switch_states", "", group, "children", group,
                    "eiem_switch_state_index", rows=max(3, min(7, len(states))))
                buttons = row.column(align=True)
                buttons.operator("eiem.switch_state", text="", icon="ADD").action = "ADD"
                buttons.operator(
                    "eiem.switch_state", text="", icon="REMOVE").action = "REMOVE_ACTIVE"
                layout.label(text="拖动每行左侧手柄可改变游戏内循环顺序")
                current = states[active_index]
                row = layout.row(align=True)
                op = row.operator("eiem.switch_state", text="用当前视图覆盖", icon="REC")
                op.action, op.state_name = "CAPTURE", current.name
                op = row.operator("eiem.switch_state", text="预览当前款式", icon="HIDE_OFF")
                op.action, op.state_name = "PREVIEW", current.name
        layout.operator("eiem.switch_restore_preview", icon="LOOP_BACK")
        name_row = layout.row(align=True)
        name_row.prop(context.scene, "eiem_export_mod_name", text='Mod 文件夹名')
        layout.label(text='导出时自动创建或刷新该文件夹')
        layout.operator("eiem.export_package", text='导出所选 Mod', icon="EXPORT")
        layout.operator("eiem.export_update", text='快速更新所选 Mod', icon="FILE_REFRESH")


def draw_update_status(layout):
    prefs = _addon_preferences(bpy.context)
    if prefs is not None:
        layout.prop(prefs, 'update_channel', text='更新来源')
    row = layout.row(align=True)
    row.enabled = _update_state["status"] not in ('checking', 'downloading', 'prepared')
    row.operator("eiem.check_update", text="检查更新", icon="FILE_REFRESH")
    status = _update_state["status"]
    if status == "checking":
        layout.label(text="正在检查更新…")
    elif status == "latest":
        layout.label(text="已是最新版本")
    elif status == "available":
        layout.label(text=_update_state.get('label') or "发现新版本 " + _update_state["tag"])
        row = layout.row(align=True)
        if _update_state.get('channel') == 'MAIN':
            row.operator('eiem.install_update', text='下载并准备更新', icon='IMPORT')
        row.operator("eiem.open_update_release", text='查看更改', icon="URL")
    elif status == "ignored":
        layout.label(text="已忽略 " + _update_state["tag"])
        layout.operator("eiem.check_update", text="仍要查看此版本").force = True
    elif status == "error":
        layout.label(text="更新失败：" + _update_state["message"], icon="ERROR")
    elif status == 'downloading':
        layout.label(text=_update_state.get('message') or '正在下载并校验更新包…')
    elif status == 'prepared':
        layout.label(text='更新已校验，保存工程后关闭全部 Blender')
        layout.label(text='关闭后自动安装并备份，下次启动生效')


class EFF_PT_updates(bpy.types.Panel):
    bl_label = 'EFF 更新'
    bl_idname = "VIEW3D_PT_eiem_updates"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "EFF"

    def draw(self, context):
        draw_update_status(self.layout)


def operator_lod_levels(operator, meshes):
    selected = [index for index, enabled in enumerate((
        operator.lod0, operator.lod1, operator.lod2,
        operator.lod3, operator.lod4)) if enabled]
    return lod_levels_for_export(meshes, selected, operator.lod_all)


def draw_lod_options(layout, operator):
    box = layout.box()
    box.prop(operator, "lod_all")
    row = box.row(align=True)
    row.enabled = not operator.lod_all
    for prop in ("lod0", "lod1", "lod2", "lod3", "lod4"):
        row.prop(operator, prop)


class EFF_OT_import_animation(ImportHelper, bpy.types.Operator):
    bl_idname = 'eiem.import_animation'
    bl_label = '导入 EFF 动画到选中骨架'
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = '.eanim'
    filter_glob: StringProperty(default='*.eanim', options={'HIDDEN'})
    kind: EnumProperty(name='动画类型', default='ALL', items=(
        ('ALL', '全部', '所有兼容的动画与姿态'),
        ('EMOTION', '情绪姿态', '静态情绪基础姿态'),
        ('POSE', '局部姿态', '口型及局部表情'),
        ('ANIM', '动态片段', '随时间变化的骨骼动画')))
    name_filter: StringProperty(name='名称包含', description='留空导入全部；可填写 blink、happy 等筛选')
    preview: BoolProperty(name='预览首个动作', default=False,
                         description='启用后替换当前骨架正在使用的 Action；原 Action 仍保留')

    def draw(self, context):
        self.layout.prop(self, 'kind')
        self.layout.prop(self, 'name_filter')
        self.layout.prop(self, 'preview')

    def execute(self, context):
        try:
            rig = animation_io.target_armature(context)
            result = animation_io.import_animations(self.filepath, rig,
                unity_transform_matrix_to_blender_basis(), self.kind, self.name_filter, self.preview)
            self.report({'INFO'}, '导入 %d 个动作，复用 %d 个，补入 %d 条来源辅助骨骼' %
                        (result['created'], result['reused'], len(result['helpers'])))
            return {'FINISHED'}
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


class EFF_OT_import(ImportHelper, bpy.types.Operator):
    bl_idname = "eiem.import_package"
    bl_label = "Import EFF package"
    directory: StringProperty(subtype="DIR_PATH")
    filter_glob: StringProperty(default="mod.ini", options={"HIDDEN"})
    clean: BoolProperty(name="Clear EFF collection", default=False)
    include_physics: BoolProperty(name="导入物理骨骼与碰撞体", default=False,
                                 description="可选；物理数据不影响只导出 Mesh")
    physics_file: StringProperty(name="物理源文件", subtype="FILE_PATH",
                                description="可选 components.json 或 .physics；留空使用包内声明")

    def draw(self, context):
        self.layout.prop(self, "clean")
        self.layout.prop(self, "include_physics")
        if self.include_physics:
            self.layout.prop(self, "physics_file")

    def execute(self, context):
        try:
            root = self.directory or os.path.dirname(self.filepath)
            count = import_package(root, self.clean, self.include_physics, self.physics_file)
            self.report({'INFO'}, "Imported %d EFF mesh resource(s)" % count)
            return {'FINISHED'}
        except Exception as error:
            self.report({'ERROR'}, str(error)); return {'CANCELLED'}


class EFF_OT_export(ExportHelper, bpy.types.Operator):
    bl_idname = "eiem.export_package"
    bl_label = "Export EFF package"
    filename_ext = ""
    directory: StringProperty(subtype="DIR_PATH")
    scope_message: StringProperty(options={"HIDDEN"})
    effect_only: BoolProperty(options={'HIDDEN'})
    physics_only: BoolProperty(options={'HIDDEN'})
    package_directory: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    _effect_job=None
    _timer=None
    resource_scope: EnumProperty(
        name='导出资源', default='ALL',
        items=(('ALL', '全部资源', '导出所选 EFF 网格，以及引用材质、贴图、挂载特效和物理依赖'),
               ('MESH', '仅 Mesh', '更新已有 Mod 中所选网格的几何、骨骼、形态和形态动画'),
               ('MATERIALS', '仅材质与贴图', '更新所选网格引用的材质、贴图及 INI 声明'),
               ('TEXTURES', '仅贴图', '读取最新图片，更新贴图及绑定声明'),
               ('PHYSICS', '仅物理', '只更新选中 Rig 的物理资源，保留 Mesh、材质和贴图')))
    include_switches: BoolProperty(
        name='导出按键切换', default=True,
        description="导出款式切换及快捷键；取消后只导出默认款式，保留形态键与滑块控制")
    lod_all: BoolProperty(name='导出已发现的全部 LOD', default=True)
    lod0: BoolProperty(name="LOD0", default=True)
    lod1: BoolProperty(name="LOD1", default=False)
    lod2: BoolProperty(name="LOD2", default=False)
    lod3: BoolProperty(name="LOD3", default=False)
    lod4: BoolProperty(name="LOD4", default=False)

    def _lod_levels(self, meshes):
        return operator_lod_levels(self, meshes)

    def invoke(self, context, event):
        # Only quick update (EXEC_DEFAULT) supplies an exact package root.
        # A normal picker must also clear overrides remembered by older sessions.
        self.package_directory = ''
        try:
            (meshes, _, effects, physics, self.effect_only, self.physics_only,
             self.resource_scope) = export_scope_resources(context, self.resource_scope)
            plan = plan_mesh_only_export(meshes) if meshes else dict(objects=[],hidden=[],sources=[])
            levels = self._lod_levels(meshes) if meshes else []
            scope_label = '所选'
            self.scope_message = "%s %d 个网格 / %d 个物理组 / 隐藏 %d 个 / %d 个源资源 / %d 个切换组" % (
                scope_label, len(plan["objects"]), len(physics), len(plan["hidden"]),
                len(plan["sources"]), sum(
                    bool(set(switch_members(group)) & set(meshes))
                    for group in switch_groups(context.scene)))
            self.scope_message += " / LOD: " + (
                ",".join(str(level) for level in levels) or "无")
            self.scope_message += ' / %d 个特效'%len(effects)
        except ValueError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        prefill_export_game_directory(context, self.directory or self.filepath)
        previous=getattr(context.scene,'eiem_export_last_directory','')
        if previous and not self.filepath:self.filepath=previous
        return ExportHelper.invoke(self, context, event)

    def draw(self, context):
        self.layout.label(text=self.scope_message)
        draw_export_game_directory(self.layout, context)
        self.layout.prop(context.scene, "eiem_export_mod_name", text='Mod 文件夹名')
        if self.effect_only:
            self.layout.label(text='仅导出所选特效及其引用资源')
            self.layout.label(text='已有角色包：追加 / 更新所选特效，保留角色与其他特效')
        elif self.physics_only:
            self.layout.label(text='仅更新选中 Rig 的全部新建物理组')
            self.layout.label(text='保留目标 Mod 的其他资源')
            self.layout.prop(context.scene, "eiem_source_baseline", text='原生来源目录')
        else:
            self.layout.prop(context.scene, "eiem_source_baseline", text='原生来源目录')
            self.layout.prop(self, "resource_scope")
        if not self.effect_only and self.resource_scope == 'ALL':
            self.layout.prop(self, "include_switches")
            draw_lod_options(self.layout, self)

    def execute(self, context):
        try:
            (meshes, rigs, effects, physics, self.effect_only, self.physics_only,
             self.resource_scope) = export_scope_resources(context, self.resource_scope)
            destination=(Path(self.package_directory).resolve() if self.package_directory else
                mod_export_directory(self.directory or os.path.dirname(self.filepath),
                    self.filepath,context.scene.eiem_export_mod_name))
            self._destination=str(destination)
            if self.effect_only:
                prefs=_addon_preferences(context)
                self._effect_job=effect_editor.ExportBatch(effects,destination,prefs.game_directory if prefs else '')
                if bpy.app.background:
                    stats=self._effect_job.run();self._finish(context,stats);return {'FINISHED'}
                if self._effect_job.poll():
                    self._finish(context,self._effect_job.stats);return {'FINISHED'}
                self._timer=context.window_manager.event_timer_add(.1,window=context.window)
                context.window_manager.modal_handler_add(self)
                return {'RUNNING_MODAL'}
            levels = self._lod_levels(meshes) if self.resource_scope == 'ALL' else None
            stats = export_package(
                destination,
                mesh_objects=meshes,
                armatures=rigs,
                physics_objects=physics if self.resource_scope in ('ALL','PHYSICS') else [],
                lod_levels=levels, include_switches=self.include_switches,
                source_baseline=context.scene.eiem_source_baseline,
                resource_scope=self.resource_scope,effect_roots=effects)
            self._finish(context,stats)
            return {'FINISHED'}
        except Exception as error:
            self._cleanup(context)
            self.report({'ERROR'}, str(error)); return {'CANCELLED'}

    def modal(self,context,event):
        if event.type=='ESC':self._cleanup(context);return {'CANCELLED'}
        if event.type!='TIMER':return {'PASS_THROUGH'}
        try:
            if not self._effect_job.poll():return {'PASS_THROUGH'}
            self._finish(context,self._effect_job.stats);return {'FINISHED'}
        except Exception as error:
            self._cleanup(context);self.report({'ERROR'},str(error));return {'CANCELLED'}

    def _finish(self,context,stats):
        context.scene.eiem_export_last_directory=self._destination
        self._cleanup(context)
        if stats.get('characterEffectUpdate'):
            self.report({'INFO'},'已更新角色包中的 %d 个特效（%.2f 秒）'%(stats['effects'],stats['exportSeconds']))
            return
        if stats.get('resourceScope')=='PHYSICS':
            self.report({'INFO'},'已更新 %d 个物理组 / %d 个骨架（%.2f 秒）'%(
                stats.get('physics',0),stats.get('skeletons',0),stats['exportSeconds']))
            return
        self.report({'INFO'},'已导出 %d 个 Mesh、%d 个材质、%d 张贴图、%d 个特效（%.2f 秒）'%(
            stats['meshes'],stats['materials'],stats['textures'],stats.get('effects',0),stats['exportSeconds']))

    def _cleanup(self,context):
        if self._timer:context.window_manager.event_timer_remove(self._timer);self._timer=None
        if self._effect_job:self._effect_job.close();self._effect_job=None

    def cancel(self,context):self._cleanup(context)


class EFF_OT_export_update(bpy.types.Operator):
    bl_idname='eiem.export_update'
    bl_label='快速更新所选 Mod'
    bl_description='按当前选择更新上次 Mod 目录；支持一个或多个特效，以及人物与特效一起导出'

    @classmethod
    def poll(cls,context):
        return bool(context.mode=='OBJECT' and getattr(context.scene,'eiem_export_last_directory','')
            and not effect_editor._BUILD_RUNNING)

    def execute(self,context):
        destination=Path(bpy.path.abspath(context.scene.eiem_export_last_directory)).resolve()
        try:
            meshes,_,effects=selected_export_resources(context)
            physics=selected_eiem_physics(context)
        except ValueError as error:
            self.report({'ERROR'},str(error))
            return {'CANCELLED'}
        if not meshes and effects and physics:
            self.report({'ERROR'},'请分别导出特效和 Physics')
            return {'CANCELLED'}
        scope='PHYSICS' if not meshes and not effects and physics else 'ALL'
        result=bpy.ops.eiem.export_package('EXEC_DEFAULT',directory=str(destination.parent),
            filepath=str(destination),package_directory=str(destination),resource_scope=scope)
        return {'FINISHED'} if result=={'RUNNING_MODAL'} else result


class EFF_OT_export_mesh_only(ExportHelper, bpy.types.Operator):
    """Update Mesh resources in an existing complete Mod."""
    bl_idname = "eiem.export_mesh_only"
    bl_label = "Export Mesh Only"
    filename_ext = ""
    directory: StringProperty(subtype="DIR_PATH")
    scope_message: StringProperty(options={"HIDDEN"})
    lod_all: BoolProperty(name='导出已发现的全部 LOD', default=True)
    lod0: BoolProperty(name="LOD0", default=True)
    lod1: BoolProperty(name="LOD1", default=False)
    lod2: BoolProperty(name="LOD2", default=False)
    lod3: BoolProperty(name="LOD3", default=False)
    lod4: BoolProperty(name="LOD4", default=False)

    def _lod_levels(self, meshes):
        return operator_lod_levels(self, meshes)

    def invoke(self, context, event):
        try:
            meshes, _ = selected_eiem_resources(context)
            self.scope_message = "LOD: " + ",".join(
                str(level) for level in self._lod_levels(meshes))
        except ValueError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        prefill_export_game_directory(context, self.directory or self.filepath)
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        try:
            meshes, _ = selected_eiem_resources(context)
            levels = self._lod_levels(meshes)
            stats = export_package(
                mod_export_directory(
                    self.directory or os.path.dirname(self.filepath), self.filepath,
                    context.scene.eiem_export_mod_name),
                mesh_objects=meshes, armatures=[], physics_objects=[],
                resource_scope='MESH', lod_levels=levels,
                source_baseline=context.scene.eiem_source_baseline)
            self.report(
                {'INFO'},
                "已导出 %(meshes)d 个 Mesh、%(materials)d 个材质、"
                "%(textures)d 张贴图（%(exportSeconds).1f 秒）" % stats)
            return {'FINISHED'}
        except Exception as error:
            self.report({'ERROR'}, str(error)); return {'CANCELLED'}

    def draw(self, context):
        if self.scope_message:
            self.layout.label(text=self.scope_message)
        draw_export_game_directory(self.layout, context)
        self.layout.prop(context.scene, "eiem_export_mod_name", text='Mod 文件夹名')
        self.layout.prop(context.scene, "eiem_source_baseline", text='原生来源目录')
        draw_lod_options(self.layout, self)


_update_state = {"status": "idle", "tag": "", "url": "", "message": ""}
_update_worker = None


def _addon_preferences(context):
    name = (__package__ or __name__).split(".")[0]
    addon = context.preferences.addons.get(name)
    return addon.preferences if addon else None


def game_directory_changed(self, context):
    if not bpy.app.background:
        # This is an installation preference, not part of the author's .blend.
        # Keep it even when Blender's automatic preference saving is disabled.
        try:
            bpy.ops.wm.save_userpref()
        except RuntimeError:
            pass


def export_game_directory(destination, configured=''):
    prefs = _addon_preferences(bpy.context)
    selected = configured or (prefs.game_directory if prefs else '')
    game = offline_reload.resolve_game_directory(
        destination, bpy.path.abspath(selected) if selected else '')
    if prefs and prefs.game_directory != str(game):
        prefs.game_directory = str(game)
    return str(game)


def prefill_export_game_directory(context, destination):
    prefs = _addon_preferences(context)
    if prefs and not prefs.game_directory:
        try:
            export_game_directory(destination or Path.cwd())
        except (OSError, ValueError):
            # Keep the picker available for missing or ambiguous installations.
            pass


def draw_export_game_directory(layout, context):
    prefs = _addon_preferences(context)
    if prefs is None:
        return
    box = layout.box()
    row = box.row(align=True)
    row.prop(prefs, 'game_directory', text='游戏目录')
    row.operator('eiem.detect_game_directory', text='', icon='VIEWZOOM')
    box.label(text='选择一次即可记住；Mod 可导出到任意文件夹')


def game_installation_items(self, context):
    return getattr(self, '_game_items', [])


class EFF_OT_detect_game_directory(bpy.types.Operator):
    bl_idname = 'eiem.detect_game_directory'
    bl_label = '自动识别游戏目录'
    bl_description = '从本机安装记录、修复工具设置和常见启动器目录查找终末地'
    installation: EnumProperty(name='游戏安装', items=game_installation_items)

    def invoke(self, context, event):
        games = offline_reload.discover_game_directories()
        if len(games) > 1:
            self._game_items = [(str(index), str(game), '') for index, game in enumerate(games)]
            self.installation = '0'
            return context.window_manager.invoke_props_dialog(self, width=650)
        return self.execute(context)

    def draw(self, context):
        self.layout.label(text='发现多份游戏安装，请选择用于本次导出的资源：')
        self.layout.prop(self, 'installation', text='游戏目录')

    def execute(self, context):
        try:
            items = getattr(self, '_game_items', [])
            selected = items[int(self.installation)][1] if items else ''
            game = offline_reload.resolve_game_directory(configured=selected) if selected else None
            if game is None:
                games = offline_reload.discover_game_directories()
                if len(games) != 1:
                    raise ValueError('未能唯一识别游戏目录，请点击路径旁的文件夹按钮选择安装目录')
                game = games[0]
            prefs = _addon_preferences(context)
            if prefs is None:
                raise ValueError('请先在偏好设置中启用 EFF 插件')
            prefs.game_directory = str(game)
            self.report({'INFO'}, '游戏目录：' + str(game))
            return {'FINISHED'}
        except (OSError, ValueError, IndexError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


class EFF_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = (__package__ or __name__).split(".")[0]

    ignored_release_tag: StringProperty(name="Ignored release", default="")
    update_channel: EnumProperty(name='更新来源', items=(
        ('MAIN', '仓库更新（自动测试）', '跟踪 main 的已通过测试完整更新包，无需作者逐次发布版本'),
        ('RELEASE', '正式 Release', '只提示正式发布版本，手动下载安装')), default='MAIN')
    game_directory: StringProperty(
        name='游戏目录', subtype='DIR_PATH', default='', update=game_directory_changed,
        description='终末地安装目录（包含 Endfield.exe）；独立于 Mod 导出位置，留空自动识别')

    def draw(self, context):
        draw_export_game_directory(self.layout, context)
        draw_update_status(self.layout)


class EFF_OT_check_update(bpy.types.Operator):
    bl_idname = "eiem.check_update"
    bl_label = "检查 EFF 更新"
    force: BoolProperty(default=False)

    def execute(self, context):
        global _update_worker
        if _update_worker and _update_worker.is_alive():
            return {'CANCELLED'}
        prefs = _addon_preferences(context)
        ignored = prefs.ignored_release_tag if prefs else ""
        force = bool(self.force)
        channel = prefs.update_channel if prefs else 'MAIN'
        directory = Path(__file__).resolve().parent
        _update_state.update(status="checking", tag="", url="", message="", label='', feed=None, channel=channel)

        def work():
            try:
                result = (release_check.check_repository(directory, bl_info['version'], ignored, force)
                          if channel == 'MAIN' else release_check.check_release(bl_info['version'], ignored, force))
                if channel == 'RELEASE':
                    result['channel'] = 'RELEASE'
                _update_state.update(result, message="")
            except Exception as error:
                _update_state.update(status="error", message=str(error)[:120])

        _update_worker = threading.Thread(target=work, name="EFF release check", daemon=True)
        _update_worker.start()

        def redraw_when_done():
            if _update_worker and _update_worker.is_alive():
                return 0.1
            for window in bpy.context.window_manager.windows:
                for area in window.screen.areas:
                    area.tag_redraw()
            return None

        bpy.app.timers.register(redraw_when_done, first_interval=0.1)
        return {'FINISHED'}


class EFF_OT_install_update(bpy.types.Operator):
    bl_idname = 'eiem.install_update'
    bl_label = '下载并准备 EFF 更新'
    bl_description = '下载校验完整插件，保存工程并退出全部 Blender 后自动安装，保留旧插件备份'

    def execute(self, context):
        global _update_worker
        if _update_state['status'] != 'available' or _update_state.get('channel') != 'MAIN':
            return {'CANCELLED'}
        directory = Path(__file__).absolute().parent
        try:
            release_check.check_install_target(directory)
            python = Path(sys.prefix) / 'bin/python.exe'
            if os.name != 'nt' or not python.is_file():
                raise ValueError('自动安装需要 Windows Blender 自带的 Python；可从正式 Release 手动安装')
        except (OSError, ValueError) as error:
            self.report({'ERROR'}, str(error)); return {'CANCELLED'}
        feed = dict(_update_state['feed'])
        _update_state.update(status='downloading', message='正在下载并校验更新包…')

        def work():
            try:
                def progress(done, total):
                    _update_state['message'] = '下载更新 %.1f / %.1f MB' % (done / 1048576, total / 1048576)
                job = release_check.prepare_update(directory, feed, progress=progress)
                with (job.parent / 'install.log').open('wb') as log:
                    subprocess.Popen([str(python), str(job.parent / 'eiem_update_worker.py'), str(job)],
                                     cwd=job.parent, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), close_fds=True)
                _update_state.update(status='prepared', message='', job=str(job))
            except Exception as error:
                _update_state.update(status='error', message=str(error)[:180])
        _update_worker = threading.Thread(target=work, name='EFF update download', daemon=True)
        _update_worker.start()
        def redraw():
            for window in bpy.context.window_manager.windows:
                for area in window.screen.areas:
                    area.tag_redraw()
            return 0.25 if _update_state['status'] == 'downloading' else None
        bpy.app.timers.register(redraw, first_interval=0.1)
        return {'FINISHED'}


class EFF_OT_open_update_release(bpy.types.Operator):
    bl_idname = "eiem.open_update_release"
    bl_label = '打开 EFF Release'

    def execute(self, context):
        if _update_state["status"] != "available":
            return {'CANCELLED'}
        bpy.ops.wm.url_open(url=_update_state["url"])
        return {'FINISHED'}


class EFF_OT_ignore_update_release(bpy.types.Operator):
    bl_idname = "eiem.ignore_update_release"
    bl_label = "忽略此 EFF 版本"

    def execute(self, context):
        prefs = _addon_preferences(context)
        if not prefs or _update_state["status"] != "available":
            return {'CANCELLED'}
        prefs.ignored_release_tag = _update_state["tag"]
        _update_state["status"] = "ignored"
        bpy.ops.wm.save_userpref()
        return {'FINISHED'}


def menu_import(self, context):
    self.layout.operator(EFF_OT_import.bl_idname, text="EFF Mod 包")
    self.layout.operator(EFF_OT_import_animation.bl_idname, text='EFF 动画（选中骨架）')


def menu_export(self, context):
    self.layout.operator(EFF_OT_export.bl_idname, text="EFF Mod 包")
    self.layout.operator(EFF_OT_export_update.bl_idname, text='EFF 快速更新所选 Mod')
    self.layout.operator(EFF_OT_export_mesh_only.bl_idname, text='EFF 更新 Mesh')


classes = (
    EFF_AddonPreferences,
    EFF_OT_detect_game_directory,
    EFF_OT_check_update,
    EFF_OT_install_update,
    EFF_OT_open_update_release,
    EFF_OT_ignore_update_release,
    EFF_OT_import_material,
    EFF_PG_shape_control,
    EFF_PG_material_switch_option,
    EFF_PG_material_switch,
    EFF_OT_shape_control,
    EFF_OT_shape_key_record,
    EFF_PT_shape_controls,
    EFF_OT_switch_create,
    EFF_OT_switch_create_confirm,
    EFF_OT_switch_key_record,
    EFF_UL_switch_states,
    EFF_OT_switch_state_drag,
    EFF_OT_switch_member_add,
    EFF_OT_switch_member_remove,
    EFF_OT_switch_member_remove_selected,
    EFF_OT_switch_state,
    EFF_OT_switch_restore,
    EFF_OT_material_switch_group,
    EFF_OT_material_switch_option,
    EFF_OT_material_switch_key_record,
    EFF_PT_material_switches,
    EFF_PT_switches,
    EFF_PT_updates,
    EFF_OT_import,
    EFF_OT_import_animation,
    EFF_OT_export,
    EFF_OT_export_mesh_only,
    EFF_OT_export_update,
    EFF_PT_material_properties,
    EFF_PT_mesh_properties,
    EFF_OT_mesh_restore_source,
    EFF_PT_image_properties,
)


def register():
    _eiem_export_load_post(None)
    if _eiem_export_dirty_depsgraph_update not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(
            _eiem_export_dirty_depsgraph_update)
    if _eiem_export_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_eiem_export_load_post)
    for cls in classes: bpy.utils.register_class(cls)
    material_editor.register()
    shape_animation.register(sys.modules[__name__])
    material_animation.register()
    physics_authoring.register(globals())
    effect_editor.register()
    bpy.types.Mesh.eiem_shape_controls = CollectionProperty(type=EFF_PG_shape_control)
    bpy.types.Object.eiem_material_switches = CollectionProperty(type=EFF_PG_material_switch)
    bpy.types.Collection.eiem_switch_state_index = IntProperty(
        name='当前款式', default=0, min=0)
    bpy.types.Scene.eiem_switch_active = PointerProperty(
        name="Switch group", type=bpy.types.Collection,
        poll=lambda self, collection: bool(collection.get("eiem_switch_group")))
    bpy.types.Scene.eiem_source_baseline = StringProperty(
        name='原生来源目录', subtype='DIR_PATH',
        description='包含 source/manifest.json 的解包目录；留空则使用对象导入来源')
    bpy.types.Scene.eiem_export_mod_name = StringProperty(
        name="Mod folder name", default="",
        description="Create or refresh the selected Mod folder")
    bpy.types.Scene.eiem_export_last_directory = StringProperty(
        name='上次 Mod 导出目录',subtype='DIR_PATH',options={'HIDDEN'})
    bpy.types.TOPBAR_MT_file_import.append(menu_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_export)


def unregister():
    if _eiem_export_dirty_depsgraph_update in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(
            _eiem_export_dirty_depsgraph_update)
    if _eiem_export_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_eiem_export_load_post)
    _eiem_export_load_post(None)
    effect_editor.unregister()
    physics_authoring.unregister()
    shape_animation.unregister()
    material_animation.unregister()
    material_editor.unregister()
    bpy.types.TOPBAR_MT_file_import.remove(menu_import)
    bpy.types.TOPBAR_MT_file_export.remove(menu_export)
    del bpy.types.Scene.eiem_switch_active
    del bpy.types.Object.eiem_material_switches
    del bpy.types.Scene.eiem_export_mod_name
    del bpy.types.Scene.eiem_export_last_directory
    del bpy.types.Scene.eiem_source_baseline
    del bpy.types.Collection.eiem_switch_state_index
    del bpy.types.Mesh.eiem_shape_controls
    for cls in reversed(classes): bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()

"""Restore Format 3 INI controls into editable Blender authoring data."""

import hashlib
import json
import math

import bpy
from mathutils import Matrix, Quaternion, Vector


def expression_tuple(node):
    operator = node[0]
    if operator == "literal":
        return ("number", float(node[1]),
                "", None, None)
    if operator == "var":
        return ("variable", 0.0, node[1], None, None)
    if operator == "!":
        return ("not", 0.0, "", expression_tuple(node[1]), None)
    if operator == 'negative':
        return ('negative', 0.0, '', expression_tuple(node[1]), None)
    operators = {
        "==": "equal", "!=": "not_equal", "<": "less",
        "<=": "less_equal", ">": "greater", ">=": "greater_equal",
        "||": "or", "&&": "and",
        '+': 'add', '-': 'subtract', '*': 'multiply', '/': 'divide', '%': 'modulo',
    }
    if operator not in operators:
        raise ValueError("Unsupported Format 3 visibility operator: " + operator)
    return (operators[operator], 0.0, "", expression_tuple(node[1]),
            expression_tuple(node[2]))


def _action_curves(action, target_id_type, target_name):
    if hasattr(action, "fcurves"):
        return action.fcurves, 0
    slot = action.slots.new(id_type=target_id_type, name=target_name)
    layer = action.layers.new("EFF Animation")
    strip = layer.strips.new(type="KEYFRAME")
    bag = strip.channelbag(slot, ensure=True)
    return bag.fcurves, slot.handle


def _existing_action_curves(action, slot_handle, target_id_type):
    if hasattr(action, "fcurves"):
        return action.fcurves
    slot = next((item for item in action.slots
                 if item.handle == slot_handle and item.target_id_type == target_id_type), None)
    if slot is None:
        raise ValueError("Imported Animation action has no matching Blender slot")
    for layer in action.layers:
        for strip in layer.strips:
            if hasattr(strip, "channelbag"):
                bag = strip.channelbag(slot)
                if bag:
                    return bag.fcurves
    raise ValueError("Imported Animation action has no keyframe channel bag")


def _write_curve(curves, data_path, index, values, start, end):
    curve = curves.new(data_path=data_path, index=index)
    count = len(values)
    curve.keyframe_points.add(count)
    curve.keyframe_points.foreach_set(
        "co", [coordinate for i, value in enumerate(values)
               for coordinate in (start + (end - start) * i / (count - 1), value)])
    for point in curve.keyframe_points:
        point.interpolation = "LINEAR"
    curve.update()
    return curve


def _settings(clip, sample_count, fps):
    duration = float(clip["duration"])
    rate = int(clip.get("sampleRate") or round((sample_count - 1) / duration))
    return dict(identity=clip["id"], label=clip["label"], enabled=True,
                speed_variable=clip["speedVariable"],
                start=1.0, end=1.0 + duration * fps, loop=clip["loop"],
                autoplay=clip["autoplay"], speed=clip["speed"],
                sample_rate=max(1, rate), hotkey=clip["key"])


def _shape_bindings(state, meshes):
    by_variable = {}
    for section, part in state["parts"].items():
        obj = meshes.get(part.get("mesh", ""))
        if obj is None or not obj.data.shape_keys:
            continue
        for channel, variable in part["shapes"].items():
            if variable in state.get('program_bindings', ()):
                continue
            key = obj.data.shape_keys.key_blocks.get(channel)
            if key is None:
                raise ValueError("INI shape channel is missing from imported Mesh: " + channel)
            control = next((item for item in obj.data.eiem_shape_controls
                            if item.shape == key.name), None)
            if control is None:
                control = obj.data.eiem_shape_controls.add()
            definition = state["controls"][variable]
            control.shape = key.name
            control.enabled = True
            control.automatic = False
            control.identity = variable.rsplit("_", 1)[-1]
            if len(control.identity) != 16:
                control.identity = hashlib.sha256(variable.encode("utf-8")).hexdigest()[:16]
            control.label = definition["label"] or key.name
            control.default = state["defaults"][variable]
            control.minimum = definition["minimum"]
            control.maximum = definition["maximum"]
            key.value = control.default
            control.variable = variable
            by_variable.setdefault(variable, []).append((obj, control, key))
    for binding in state["bindings"]:
        targets = by_variable.get(binding["variable"], ())
        if not targets or binding["type"] != "hold" or not _editable_binding(binding, state):
            continue
        if len(binding["values"]) != 1:
            continue
        value = binding["values"][0]
        for _, control, _ in targets:
            if math.isclose(value, control.maximum, abs_tol=1e-6):
                control.hotkey_increase = binding["key"]
                control.hotkey_speed = binding["speed"]
            elif math.isclose(value, control.minimum, abs_tol=1e-6):
                control.hotkey_decrease = binding["key"]
                control.hotkey_speed = binding["speed"]
    return by_variable


def _add_animation_group(obj, clip, fps, channels):
    samples = next(iter(channels.values()))
    settings = _settings(clip, len(samples), fps)
    collection = obj.data.eiem_shape_animations
    existing = next((item for item in collection if item.identity == clip["id"]), None)
    if existing is not None:
        return existing
    item = collection.add()
    for key, value in settings.items():
        setattr(item, key, value)
    action = bpy.data.actions.new("EFF_" + clip["label"])
    curves, slot_handle = _action_curves(action, "KEY", obj.name)
    item.action = action
    item.slot_handle = slot_handle
    animated_id = obj.data.shape_keys
    animated_id.animation_data_create().action = action
    if hasattr(action, "slots"):
        animated_id.animation_data.action_slot = next(
            slot for slot in action.slots if slot.handle == slot_handle)
    keys = obj.data.shape_keys
    for channel, values in channels.items():
        key = keys.key_blocks.get(channel)
        if key is None:
            raise ValueError("Animation references a missing Shape Key: " + channel)
        _write_curve(curves, key.path_from_id("value"), 0, values,
                     item.start, item.end)
        entry = item.channels.add()
        entry.name = channel
        entry.enabled = True
    obj.data.eiem_shape_animation_index = len(collection) - 1
    return item


def _restore_shape_animations(state, meshes, shape_bindings, fps):
    for clip in state["animations"].values():
        channels_by_mesh = {}
        for variable, samples in clip["tracks"].items():
            target = shape_bindings.get(variable)
            if target is None:
                continue
            for obj, _control, key in target:
                channels_by_mesh.setdefault(obj, {})[key.name] = samples
        for obj, channels in channels_by_mesh.items():
            _add_animation_group(obj, clip, fps, channels)


def _restore_material_animations(state, materials, fps):
    by_material = {}
    for clip in state["animations"].values():
        for variable, samples in clip["tracks"].items():
            track = state["material_tracks"].get(variable)
            if track is None:
                continue
            material = materials.get(track["material"])
            if material is None:
                continue
            by_material.setdefault(material, {}).setdefault(clip["id"], {
                "clip": clip, "tracks": []})["tracks"].append((variable, track, samples))
    for material, clips in by_material.items():
        action = bpy.data.actions.new("EFF_" + material.name + "_Materials")
        curves, slot_handle = _action_curves(action, "MATERIAL", material.name)
        material.animation_data_create().action = action
        for identity, group in clips.items():
            clip = group["clip"]
            properties = {}
            variables = {}
            for variable, track, samples in group["tracks"]:
                properties.setdefault((track["property"], track["kind"]), {})[
                    track["component"]] = samples
                variables.setdefault((track["property"], track["kind"]), {})[
                    str(track["component"])] = variable
            for (property_name, kind), components_by_index in properties.items():
                samples_count = len(next(iter(components_by_index.values())))
                expected_components = 1 if kind == "FLOAT" else 4
                if set(components_by_index) != set(range(expected_components)):
                    raise ValueError("INI material animation has an incomplete channel: " + property_name)
                settings = _settings(clip, samples_count, fps)
                item = material.eiem_material_animations.add()
                item.identity = identity + "_" + hashlib.sha256(property_name.encode("utf-8")).hexdigest()[:12]
                item.label = clip["label"]
                item.enabled = True
                item.hotkey = clip["key"]
                item.start = settings["start"]
                item.end = settings["end"]
                item.loop = clip["loop"]
                item.autoplay = clip["autoplay"]
                item.speed = clip["speed"]
                item.sample_rate = settings["sample_rate"]
                item.runtime_id = identity
                item.speed_variable = clip["speedVariable"]
                item.runtime_variables = json.dumps(variables[(property_name, kind)])
                item.property = property_name
                item.kind = kind
                if expected_components == 1:
                    item.value = components_by_index[0][0]
                else:
                    item.vector = tuple(components_by_index[index][0]
                                        for index in range(expected_components))
                path = "eiem_material_animations[%d].%s" % (
                    len(material.eiem_material_animations) - 1,
                    "value" if expected_components == 1 else "vector")
                for component in range(expected_components):
                    _write_curve(curves, path, component,
                                 components_by_index[component], item.start, item.end)
        material.animation_data.action_slot = next(
            slot for slot in action.slots if slot.handle == slot_handle)


def _editable_binding(binding, state):
    """Bindings represented by editable widgets are regenerated on export."""
    for part in state["parts"].values():
        if binding["variable"] not in part["shapes"].values():
            continue
        control = state["controls"][binding["variable"]]
        if (binding["type"] == "hold" and len(binding["values"]) == 1
                and any(math.isclose(binding["values"][0], control[key], abs_tol=1e-6)
                        for key in ("minimum", "maximum"))):
            endpoints = {"minimum": [], "maximum": []}
            for candidate in state["bindings"]:
                if (candidate["variable"] != binding["variable"]
                        or candidate["type"] != "hold" or len(candidate["values"]) != 1):
                    continue
                for key in endpoints:
                    if math.isclose(candidate["values"][0], control[key], abs_tol=1e-6):
                        endpoints[key].append(candidate)
            # The Blender widget has one key per direction and one speed.
            # More elaborate INI bindings stay intact as authored INI state.
            return (all(len({item["key"] for item in rows}) <= 1
                        for rows in endpoints.values())
                    and len({item["speed"] for rows in endpoints.values()
                             for item in rows}) <= 1)
    return any(binding["id"] == "KeyAnimation" + clip["id"]
               and binding["variable"] == clip["speedVariable"]
               and binding["type"] == "press" and binding["key"] == clip["key"]
               and binding["values"] == [1.0]
               for clip in state["animations"].values())


def restore_format3_controls(state, meshes, skeletons, skeleton_payloads,
                             materials, package_collection, scene, basis,
                             condition_parser):
    """Rebuild editable controls while keeping their INI variable identities."""
    visibility = {}
    part_sections = {}
    for section, part in state["parts"].items():
        obj = meshes.get(part.get("mesh", ""))
        if obj is None:
            continue
        obj["eiem_controls_mesh_id"] = part["mesh"]
        part_sections.setdefault(obj, []).append(section)
        expression = expression_tuple(condition_parser(part["visible"]).tree)
        key = "%d|%s" % (part.get("slot", 0), part.get("material", ""))
        visibility.setdefault(part["mesh"], {})[key] = expression

    for obj, sections in part_sections.items():
        if len(sections) == 1:
            obj["eiem_part_section"] = sections[0]

    package_collection["eiem_controls_ini_json"] = json.dumps({
        "defaults": {k: v for k, v in state["defaults"].items() if k not in state.get('program_bindings', ())},
        "persistent": sorted(state["persistent"]),
        "bindings": [binding for binding in state["bindings"]
                     if not _editable_binding(binding, state)],
        "controls": {k: v for k, v in state["controls"].items() if k not in state.get('program_bindings', ())},
        "program_source": state.get('program_source', {}),
        "visibility": visibility,
        "key_switch_enabled": state.get("key_switch_enabled", True),
    }, separators=(",", ":"))
    fps = scene.render.fps / scene.render.fps_base
    shape_bindings = _shape_bindings(state, meshes)
    _restore_shape_animations(state, meshes, shape_bindings, fps)
    _restore_material_animations(state, materials, fps)

    for clip in state["animations"].values():
        for track in clip["boneTracks"]:
            skeleton_id = track["skeleton"]
            section = "Skeleton" + skeleton_id
            rig = skeletons.get(section)
            payload = skeleton_payloads.get(section)
            if rig is None or payload is None:
                raise ValueError("Animation references a missing imported Skeleton: " + skeleton_id)
            bone = next((item for item in rig.data.bones
                         if item.get("eiem_path", item.name) == track["path"]), None)
            if bone is None:
                raise ValueError("Animation references a missing imported bone: " + track["path"])
            samples = track["samples"]
            settings = _settings(clip, len(samples), fps)
            group = next((item for item in rig.eiem_bone_animations
                          if item.identity == clip["id"]), None)
            if group is None:
                group = rig.eiem_bone_animations.add()
                for key, value in settings.items():
                    setattr(group, key, value)
                action = bpy.data.actions.new("EFF_" + clip["label"] + "_Bones")
                curves, slot_handle = _action_curves(action, "OBJECT", rig.name)
                group.action = action
                group.slot_handle = slot_handle
                rig.animation_data_create().action = action
                if hasattr(action, "slots"):
                    rig.animation_data.action_slot = next(
                        slot for slot in action.slots if slot.handle == slot_handle)
                rig.eiem_bone_animation_index = len(rig.eiem_bone_animations) - 1
            entry = group.channels.add()
            entry.name = bone.name
            entry.enabled = True
            curves = _existing_action_curves(group.action, group.slot_handle, "OBJECT")
            _write_bone_track(curves, rig, payload, track, samples,
                              settings["start"], settings["end"], basis)


def _write_bone_track(curves, rig, payload, track, samples, start, end, basis):
    node_index = next((index for index, node in enumerate(payload["nodes"])
                       if node[0] == track["path"]), None)
    if node_index is None:
        raise ValueError("Animation references a missing skeleton path: " + track["path"])
    path, parent_index, position, rotation, scale = payload["nodes"][node_index]
    pose_bone = next((item for item in rig.pose.bones
                      if item.bone.get("eiem_path", item.name) == path), None)
    if pose_bone is None:
        raise ValueError("Animation references a missing pose bone: " + path)

    def trs(values):
        quat = Quaternion((values[6], values[3], values[4], values[5]))
        return Matrix.LocRotScale(Vector(values[:3]), quat, Vector(values[7:10]))

    nodes = payload["nodes"]
    local_values = []
    inverse = basis.inverted()
    for sample_index in range(len(samples)):
        worlds = []
        for node in nodes:
            local = trs(samples[sample_index]) if node[0] == path else trs(
                tuple(node[2]) + tuple(node[3]) + tuple(node[4]))
            parent = node[1]
            worlds.append(worlds[parent] @ local if parent >= 0 else local)
        world = worlds[node_index]
        pose_matrix = basis @ world @ inverse
        kwargs = {"invert": True}
        if pose_bone.parent:
            parent_world = worlds[parent_index]
            kwargs.update(parent_matrix=basis @ parent_world @ inverse,
                          parent_matrix_local=pose_bone.parent.bone.matrix_local)
        matrix = pose_bone.bone.convert_local_to_pose(
            pose_matrix, pose_bone.bone.matrix_local, **kwargs)
        location, quaternion, scale = matrix.decompose()
        quaternion.normalize()
        local_values.append((tuple(location), tuple(quaternion), tuple(scale)))
    pose_bone.rotation_mode = "QUATERNION"
    prefix = pose_bone.path_from_id()
    for component in range(3):
        _write_curve(curves, prefix + ".location", component,
                     [row[0][component] for row in local_values], start, end)
        _write_curve(curves, prefix + ".scale", component,
                     [row[2][component] for row in local_values], start, end)
    for component in range(4):
        _write_curve(curves, prefix + ".rotation_quaternion", component,
                     [row[1][component] for row in local_values], start, end)
    pose_bone.rotation_mode = "QUATERNION"

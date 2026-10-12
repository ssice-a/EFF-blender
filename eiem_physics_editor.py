"""One sidebar editor; RNA controls project the existing serialized fields.

This module does not build game physics or maintain a second solver model.
Legacy compact author values remain import/export adapters for old projects.
"""
import copy
import json
import math

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty, FloatVectorProperty,
                       IntProperty, PointerProperty, StringProperty)
from mathutils import Vector

AUTHOR = None
NATIVE = None
GROUPS = {"GROUP", "NATIVE_GROUP"}
COLLIDERS = {"COLLIDER", "NATIVE_COLLIDER"}
_ENUM_ITEM_CACHE = {}

# Each field belongs to exactly one section. Curves stay next to their base.
SECTIONS = (
    ("DYNAMICS", "基础动力", (
        ("gravity", "重力加速度", "FLOAT"),
        ("gravityDirection", "重力世界方向", "VECTOR"),
        ("gravityFalloff", "姿态重力衰减", "FLOAT"),
        ("damping", "阻尼", "CURVE"),
        ("stablizationTimeAfterReset", "重置稳定时间（秒）", "FLOAT"))),
    ("DYNAMICS", "姿态与混合", (
        ("blendWeight", "输出物理混合", "FLOAT"),
        ("animationPoseRatio", "恢复参考：初始 → 动画", "FLOAT"))),
    ("DYNAMICS", "角度恢复", (
        ("angleRestorationConstraint.useAngleRestoration", "启用角度恢复", "BOOL"),
        ("angleRestorationConstraint.stiffness", "恢复强度", "CURVE"),
        ("angleRestorationConstraint.velocityAttenuation", "恢复速度衰减", "FLOAT"),
        ("angleRestorationConstraint.gravityFalloff", "恢复姿态重力衰减", "FLOAT"))),
    ("DYNAMICS", "角度限制", (
        ("angleLimitConstraint.useAngleLimit", "启用角度限制", "BOOL"),
        ("angleLimitConstraint.limitAngle", "允许角度（°）", "CURVE"),
        ("angleLimitConstraint.stiffness", "限制强度", "FLOAT"))),
    ("DYNAMICS", "形状保持", (
        ("distanceConstraint.stiffness", "长度恢复", "CURVE"),
        ("tetherConstraint.distanceCompression", "允许向根压缩比例", "FLOAT"),
        ("triangleBendingConstraint.stiffness", "三角弯曲恢复", "FLOAT"))),
    ("DYNAMICS", "惯性", (
        ("inertiaConstraint.worldInertia", "整体移动影响", "FLOAT"),
        ("inertiaConstraint.localInertia", "动画局部影响", "FLOAT"),
        ("inertiaConstraint.movementInertiaSmoothing", "整体惯性平滑", "FLOAT"),
        ("inertiaConstraint.depthInertia", "根部局部惯性抑制", "FLOAT"),
        ("inertiaConstraint.anchorInertia", "参考物体影响", "FLOAT"),
        ("inertiaConstraint.centrifualAcceleration", "离心增强", "FLOAT"))),
    ("DYNAMICS", "速度限制", (
        ("inertiaConstraint.movementSpeedLimit", "整体移动上限（单位/秒）", "LIMIT"),
        ("inertiaConstraint.rotationSpeedLimit", "整体转速上限（°/秒）", "LIMIT"),
        ("inertiaConstraint.localMovementSpeedLimit", "局部移动上限（单位/秒）", "LIMIT"),
        ("inertiaConstraint.localRotationSpeedLimit", "局部转速上限（°/秒）", "LIMIT"),
        ("inertiaConstraint.particleSpeedLimit", "模拟点速度上限（单位/秒）", "LIMIT"))),
    ("DYNAMICS", "运动范围", (
        ("motionConstraint.useMaxDistance", "启用最大偏移", "BOOL"),
        ("motionConstraint.maxDistance", "最大偏移", "CURVE"),
        ("motionConstraint.useBackstop", "启用回挡", "BOOL"),
        ("motionConstraint.backstopRadius", "回挡半径", "FLOAT"),
        ("motionConstraint.backstopDistance", "回挡距离", "CURVE"),
        ("motionConstraint.stiffness", "运动约束强度", "FLOAT"))),
    ("DYNAMICS", "风", tuple(("wind." + path, label, "FLOAT") for path, label in (
        ("influence", "风影响"), ("frequency", "风频率"), ("turbulence", "扰动"),
        ("blend", "风混合"), ("synchronization", "同步"),
        ("depthWeight", "深度影响"), ("movingWind", "移动风")))),
    ("COLLISION", "身体碰撞", (
        ("colliderCollisionConstraint.mode", "检测模式", "ENUM"),
        ("radius", "模拟点／边厚度", "CURVE"),
        ("colliderCollisionConstraint.friction", "摩擦", "FLOAT"))),
    ("COLLISION", "自碰撞与组间碰撞", (
        ("selfCollisionConstraint.selfMode", "自碰撞模式", "ENUM"),
        ("selfCollisionConstraint.surfaceThickness", "自碰撞厚度", "CURVE"),
        ("selfCollisionConstraint.syncMode", "组间碰撞模式", "ENUM"),
        ("selfCollisionConstraint.clothMass", "相对质量", "FLOAT"))),
    ("COLLISION", "BoneSpring 碰撞", (
        ("colliderCollisionConstraint.limitDistance", "碰撞限制距离", "CURVE"),)),
    ("ADVANCED", "运行与旋转", (
        ("updateMode", "更新模式", "ENUM"),
        ("clothSimulateWeight", "游戏模拟权重", "FLOAT"),
        ("rotationalInterpolation", "旋转插值", "FLOAT"),
        ("rootRotation", "根旋转", "FLOAT"),
        ("resetSimulationToAnimationPoseWhenWeightLow", "低权重重置", "BOOL"),
        ("resetSimulationToAnimationPoseWeightThreshold", "低权重阈值", "FLOAT"))),
    ("ADVANCED", "游戏 LOD", (
        ("clothAnimatorAbilityLODThreshold", "Ability LOD 阈值", "INT"),
        ("clothAnimatorLODThreshold", "Animator LOD 阈值", "INT"),
        ("clothLodFadeTime", "LOD 淡出时间", "FLOAT"))),
    ("ADVANCED", "传送", (
        ("inertiaConstraint.teleportMode", "传送处理", "ENUM"),
        ("inertiaConstraint.teleportDistance", "单帧传送距离", "FLOAT"),
        ("inertiaConstraint.teleportRotation", "单帧传送角度（°）", "FLOAT"))),
    ("ADVANCED", "剔除", (
        ("cullingSettings.cameraCullingMode", "相机剔除模式", "ENUM"),
        ("cullingSettings.cameraCullingMethod", "Renderer 选择", "ENUM"),
        ("cullingSettings.distanceCullingLength", "距离剔除", "LIMIT"),
        ("cullingSettings.distanceCullingFadeRatio", "距离剔除淡出比例", "FLOAT"))),
    ("ADVANCED", "BoneSpring", tuple(("springConstraint." + path, label, kind)
        for path, label, kind in (("useSpring", "启用弹簧", "BOOL"),
        ("springPower", "弹簧强度", "FLOAT"), ("limitDistance", "弹簧限制距离", "FLOAT"),
        ("normalLimitRatio", "法线限制比例", "FLOAT"), ("springNoise", "弹簧扰动", "FLOAT")))),
)
ENUMS = {
    "colliderCollisionConstraint.mode": ((0, "关闭"), (1, "Point · 模拟点"), (2, "Edge · 连线")),
    "selfCollisionConstraint.selfMode": ((0, "关闭"), (2, "FullMesh")),
    "selfCollisionConstraint.syncMode": ((0, "关闭"), (2, "FullMesh")),
    "updateMode": ((0, "Normal"), (1, "UnityPhysics"), (2, "Unscaled"), (10, "AnimatorLinkage")),
    "inertiaConstraint.teleportMode": ((0, "关闭"), (1, "重置"), (2, "保持")),
    "cullingSettings.cameraCullingMode": ((0, "关闭"), (10, "重置"), (20, "保持"), (30, "AnimatorLinkage")),
    "cullingSettings.cameraCullingMethod": ((0, "自动 Renderer"), (10, "手动 Renderer")),
    "direction": ((0, "X"), (1, "Y"), (2, "Z")),
}
COLLIDER_FIELDS = (("size.x", "起始半径", "FLOAT"), ("size.y", "末端半径", "FLOAT"),
    ("size.z", "总长（含圆头）", "FLOAT"), ("direction", "游戏局部轴向", "ENUM"),
    ("reverseDirection", "反向", "BOOL"), ("radiusSeparation", "区分两端半径", "BOOL"),
    ("alignedOnCenter", "以中心对齐", "BOOL"))


def full_path(path):
    return path if path.startswith("center.") or path in {item[0] for item in COLLIDER_FIELDS} else "serializeData." + path


def property_name(path):
    return "p_" + path.replace(".", "_")


def read_number(obj, path):
    field = NATIVE.native_field(obj, full_path(path))
    if field is not None:
        return float(field.value) if field.floating else int(field.integer)
    if obj.eiem_physics.kind == "GROUP":
        compact = {"radius.value": "node_radius", "radius.useCurve": "radius_use_curve"}
        name = compact.get(path, path if path in AUTHOR.document.PARAMETERS else None)
        if name:
            return getattr(obj.eiem_physics, name)
    return 0


def _write_number(obj, path, value):
    AUTHOR.document.require(math.isfinite(value), "物理参数必须是有限数值")
    if NATIVE.native_field(obj, full_path(path)) is not None:
        NATIVE.set_native_number(obj, full_path(path), value)
        return
    if obj.eiem_physics.kind == "GROUP":
        name = {"radius.value": "node_radius", "radius.useCurve": "radius_use_curve"}.get(
            path, path if path in AUTHOR.document.PARAMETERS else None)
        if name:
            setattr(obj.eiem_physics, name, value)
            return
    raise ValueError("Physics: 当前组没有此参数，请先应用完整原生参数模板：" + path)


def write_number(obj, path, value):
    """A single field edit affects only that field on explicitly checked groups."""
    targets = [obj]
    if obj.eiem_physics.kind in GROUPS:
        selected = [item for item in objects_for(obj.eiem_physics.rig, "GROUPS")
                    if item.eiem_physics.editor_selected]
        if obj in selected:
            targets = selected
    for target in targets:
        AUTHOR.document.require(has_parameter(target, path), "批量目标缺少参数：" + path)
    old = [read_number(target, path) for target in targets]
    try:
        for target in targets:
            _write_number(target, path, value)
    except Exception:
        for target, previous in zip(targets, old):
            _write_number(target, path, previous)
        raise


def has_parameter(obj, path):
    if NATIVE.native_field(obj, full_path(path)) is not None:
        return True
    return obj.eiem_physics.kind == "GROUP" and (
        path in AUTHOR.document.PARAMETERS or path in {"radius.value", "radius.useCurve"})


def vector_get(self, path):
    basis = NATIVE.API["unity_transform_matrix_to_blender_basis"]().to_3x3()
    value = Vector([read_number(self.id_data, path + "." + axis) for axis in "xyz"])
    if path == "center":
        return basis @ value + self.id_data.location
    return basis @ value


def vector_set(self, path, value):
    basis = NATIVE.API["unity_transform_matrix_to_blender_basis"]().to_3x3()
    source_value = basis.inverted() @ Vector(value)
    if path == "center":
        obj = self.id_data
        state = obj.eiem_native_physics
        ready = state.ready
        state.ready = False
        try:
            # Fold the legacy additive offset into the one serialized center.
            obj.location = (0, 0, 0)
            for axis, component in zip("xyz", source_value):
                _write_number(obj, path + "." + axis, component)
        finally:
            state.ready = ready
        if ready:
            NATIVE.rebuild(obj)
    else:
        for axis, component in zip("xyz", source_value):
            write_number(self.id_data, path + "." + axis, component)


def author_length_get(self):
    p = self.id_data.eiem_physics
    return p.span + p.radius + (p.end_radius if p.separate_radii else p.radius)


def author_length_set(self, value):
    p = self.id_data.eiem_physics
    p.span = max(0, value - p.radius - (p.end_radius if p.separate_radii else p.radius))


def enum_items(path):
    def items(self, context):
        values = ENUMS[path]
        current = int(read_number(self.id_data, path))
        if current not in {number for number, label in values}:
            values = values + ((current, "来源值 %d（未确认）" % current),)
        # Blender keeps pointers to dynamic enum strings; retain their lifetime.
        key = (path, current)
        if key not in _ENUM_ITEM_CACHE:
            _ENUM_ITEM_CACHE[key] = [(str(number), label, label, number) for number, label in values]
        return _ENUM_ITEM_CACHE[key]
    return items


class EFF_PG_physics_editor_values(bpy.types.PropertyGroup):
    """Only get/set projections, never another saved copy of parameter values."""


class EFF_PG_physics_editor_preset(bpy.types.PropertyGroup):
    name: StringProperty(name="预设名称")
    payload: StringProperty(options={"HIDDEN"})


def define_property(path, label, kind):
    name = property_name(path)
    if kind == "VECTOR":
        def get(self):
            return vector_get(self, path)

        def set(self, value):
            vector_set(self, path, value)
        value = FloatVectorProperty(name=label, size=3,
                                    get=get, set=set)
    else:
        cast = {"BOOL": bool, "INT": int, "ENUM": int, "FLOAT": float}[kind]
        def get(self):
            return cast(read_number(self.id_data, path))

        def set(self, value):
            write_number(self.id_data, path, value)
        args = dict(name=label, get=get, set=set)
        if kind == "ENUM":
            args["items"] = enum_items(path)
        prop = {"BOOL": BoolProperty, "INT": IntProperty, "ENUM": EnumProperty, "FLOAT": FloatProperty}[kind]
        value = prop(**args)
    EFF_PG_physics_editor_values.__annotations__[name] = value


EFF_PG_physics_editor_values.__annotations__ = {}
for _tab, _label, _fields in SECTIONS:
    for _path, _name, _kind in _fields:
        if _kind in {"CURVE", "LIMIT"}:
            define_property(_path + ".value", _name, "FLOAT")
            define_property(_path + (".useCurve" if _kind == "CURVE" else ".use"), "沿链变化" if _kind == "CURVE" else "启用", "BOOL")
        else:
            define_property(_path, _name, _kind)
for _path, _name, _kind in COLLIDER_FIELDS:
    define_property(_path, _name, _kind)
EFF_PG_physics_editor_values.__annotations__["collider_center"] = FloatVectorProperty(
    name="局部中心（Blender 坐标）", size=3,
    get=lambda self: vector_get(self, "center"),
    set=lambda self, value: vector_set(self, "center", value))
EFF_PG_physics_editor_values.__annotations__["collider_length"] = FloatProperty(
    name="总长（含圆头）", min=0, get=author_length_get, set=author_length_set)


def rig_of_context(context):
    active = AUTHOR.rig_of(context.object)
    if active:
        return active
    group = AUTHOR.group_of(context)
    return group.eiem_physics.rig if group else None


def role_collection(rig, category):
    if not rig:
        return None
    key = rig.get("eiem_physics_collection_id")
    root = next((collection for collection in bpy.data.collections
                 if key and collection.get("eiem_physics_collection_id") == key and
                 collection.get("eiem_collection_role") == "PHYSICS"), None)
    return next((child for child in root.children
                 if child.get("eiem_physics_category") == category), None) if root else None


def objects_for(rig, category):
    collection = role_collection(rig, category)
    return list(collection.objects) if collection else []


def active_item(context):
    obj = getattr(context.scene, "eiem_physics_editor_item", None)
    if obj and obj.eiem_physics.rig == rig_of_context(context):
        return obj
    return AUTHOR.group_of(context)


def search_updated(self, context):
    query = self.eiem_physics_editor_search.casefold().strip()
    if query:
        matching = [tab for tab, label, fields in SECTIONS
                    if any(query in (path + name).casefold() for path, name, kind in fields)]
        if matching and self.eiem_physics_editor_tab not in matching:
            self.eiem_physics_editor_tab = matching[0]


def list_index_updated(self, context):
    if context is None:
        return
    items = objects_for(rig_of_context(context), self.eiem_physics_editor_category)
    index = self.eiem_physics_editor_index
    if 0 <= index < len(items):
        item = items[index]
        if item.eiem_physics.kind in GROUPS:
            self.eiem_physics_group = item
        self.eiem_physics_editor_item = item


def parameter_targets(context, active):
    selected = [obj for obj in objects_for(active.eiem_physics.rig, "GROUPS")
                if obj.eiem_physics.editor_selected]
    return selected or [active]


def scalar_paths(scope):
    return {full_path(path) for tab, label, fields in SECTIONS if scope == "ALL" or tab == scope
            for path, name, kind in fields}


def copyable_parameter(record, scope):
    try:
        AUTHOR.document.native_parameter(record)
    except ValueError:
        return False
    path = record["path"]
    if ".m_FileID" in path:
        return False
    return scope == "ALL" or any(path == prefix or path.startswith(prefix + ".")
                                 for prefix in scalar_paths(scope))


def capture_parameters(obj, scope="ALL"):
    AUTHOR.document.require(obj and obj.eiem_physics.kind in GROUPS, "请选择物理组")
    if NATIVE.is_parameter_group(obj):
        NATIVE.build_curve_mappings(obj)
    fields = obj.eiem_native_physics.fields
    prefixes = scalar_paths(scope)
    records, curves = [], {}
    for field in fields:
        path = field.label
        record = {"path": path, "floating": bool(field.floating),
                  "value": float(field.value) if field.floating else int(field.integer)}
        if not copyable_parameter(record, scope):
            continue
        if any(path.startswith(parameter + ".curve.m_Curve.")
               for parameter in NATIVE.CURVE_PARAMETER_INDEX):
            continue
        records.append(record)
    for parameter, label in NATIVE.CURVE_PARAMETERS:
        if parameter in prefixes and NATIVE.native_field(obj, parameter + ".value"):
            curves[parameter] = copy.deepcopy(NATIVE.source_curve_keys(obj, parameter)[1])
    if not fields:
        records = [{"path": full_path(path), "floating": True,
                    "value": float(getattr(obj.eiem_physics, path))}
                   for path in AUTHOR.document.PARAMETERS if full_path(path) in prefixes]
        if full_path("radius") in prefixes:
            radius = AUTHOR.author_radius_record(obj)
            records.extend(({"path": full_path("radius.value"), "floating": True, "value": radius["value"]},
                            {"path": full_path("radius.useCurve"), "floating": False, "value": int(radius["useCurve"])}))
            curves[full_path("radius")] = copy.deepcopy(radius["keys"])
    return {"fields": records, "curves": curves, "scope": scope}


def validate_plan(obj, payload):
    AUTHOR.document.require(isinstance(payload, dict) and set(payload) in (
                            {"fields", "curves", "scope"}, {"fields", "curves", "scope", "colliderRefs"}),
                            "物理参数剪贴板无效")
    records = payload["fields"]
    AUTHOR.document.require(isinstance(records, list) and isinstance(payload["curves"], dict), "参数剪贴板结构无效")
    permitted = scalar_paths(payload["scope"])
    AUTHOR.document.require(payload["scope"] in {"ALL", "DYNAMICS", "COLLISION", "ADVANCED"}, "参数复制区域无效")
    wanted = set()
    for record in records:
        AUTHOR.document.native_parameter(record)
        path = record["path"]
        AUTHOR.document.require(path not in wanted and copyable_parameter(record, payload["scope"]),
                                "参数剪贴板含重复或非编辑参数")
        wanted.add(path)
        field = NATIVE.native_field(obj, path)
        if field is not None:
            AUTHOR.document.require(field.floating == record["floating"], "参数类型不匹配：" + path)
        elif obj.eiem_physics.kind == "NATIVE_GROUP":
            raise ValueError("Physics: 目标组缺少参数：" + path)
        if obj.eiem_physics.kind == "GROUP" and path == "serializeData.clothType":
            AUTHOR.document.require(record["value"] == 1,
                                    "新增骨链使用 BoneCloth，不能将其他模拟类型当作相同模板复制")
    for parameter, keys in payload["curves"].items():
        AUTHOR.document.require(parameter in permitted and parameter in NATIVE.CURVE_PARAMETER_INDEX and
                                isinstance(keys, list) and len(keys) >= 2, "曲线参数无效")
        previous = -1.0
        for key in keys:
            AUTHOR.document.require(isinstance(key, dict) and math.isfinite(key["time"]) and
                                    previous < key["time"] <= 1 and key["time"] >= 0, "曲线深度必须递增且位于 0～1")
            previous = key["time"]
            for suffix in ("value", "inSlope", "outSlope", "inWeight", "outWeight"):
                AUTHOR.document.require(math.isfinite(key[suffix]), "曲线数值无效")
            AUTHOR.document.require(key["weightedMode"] in (0, 1, 2, 3), "曲线权重模式无效")
    if not obj.eiem_native_physics.fields:
        AUTHOR.document.require(payload["scope"] == "ALL" or not payload["curves"] or
                                set(payload["curves"]) == {full_path("radius")},
                                "请先将完整参数模板应用到此新建组，再粘贴局部参数")
    # Accept the earlier clipboard shape, but never apply its colliderRefs.
    # Each chain keeps the collision set explicitly assigned to it.


def apply_parameters(obj, payload):
    state = obj.eiem_native_physics
    previous_ready = bool(state.ready)
    state.ready = False
    try:
        if not len(state.fields):
            expanded = copy.deepcopy(payload["fields"])
            for parameter, keys in payload["curves"].items():
                for index, key in enumerate(keys):
                    for suffix, value in key.items():
                        expanded.append({"path": "%s.curve.m_Curve.%d.%s" % (parameter, index, suffix),
                                         "floating": suffix != "weightedMode", "value": value})
            AUTHOR.load_author_native_fields(obj, expanded)
            previous_ready = True
            state.ready = False
        for parameter, keys in payload["curves"].items():
            NATIVE.replace_curve_key_fields(obj, parameter, keys)
            NATIVE.mark_curve_mapping_edited(obj, parameter)
            node = NATIVE.curve_mapping_node(obj, parameter)
            if node:
                NATIVE.load_curve_mapping(obj, parameter, keys)
        for record in payload["fields"]:
            if NATIVE.native_field(obj, record["path"]) is None and obj.eiem_physics.kind == "GROUP":
                field = state.fields.add()
                field.label = record["path"]
                field.path = json.dumps(record["path"])
                field.original = json.dumps(record["value"])
                field.floating = record["floating"]
            NATIVE.set_native_number(obj, record["path"], record["value"])
    finally:
        state.ready = previous_ready
    if obj.eiem_physics.kind == "GROUP":
        if NATIVE.is_parameter_group(obj):
            AUTHOR.sync_author_controls_from_native_fields(obj, rebuild_preview=False)
        else:
            state.ready = False
            try:
                for name in AUTHOR.document.PARAMETERS:
                    field = NATIVE.native_field(obj, full_path(name))
                    if field is not None:
                        setattr(obj.eiem_physics, name, field.value)
            finally:
                state.ready = previous_ready
            if NATIVE.native_field(obj, full_path("radius.value")):
                AUTHOR.load_author_radius(obj, NATIVE.mapped_radius(obj))
            AUTHOR.persist_author_native_fields(obj)
    if NATIVE.is_parameter_group(obj):
        NATIVE.build_curve_mappings(obj)
    changed = {record["path"] for record in payload["fields"]} | set(payload["curves"])
    if any(path in NATIVE.ANGLE_VISUAL_FIELDS or path.startswith("serializeData.angleLimitConstraint.") or
           path.startswith("serializeData.radius") for path in changed):
        if obj.eiem_physics.kind == "GROUP":
            AUTHOR.rebuild_group(obj)
        else:
            NATIVE.schedule_group_preview(obj)


def backup_parameters(obj):
    return {"fields": [{"path": field.path, "label": field.label, "original": field.original,
                         "floating": bool(field.floating), "value": field.value,
                         "integer": field.integer} for field in obj.eiem_native_physics.fields],
            "compact": {name: getattr(obj.eiem_physics, name) for name in AUTHOR.document.PARAMETERS},
            "radius": AUTHOR.author_radius_record(obj) if obj.eiem_physics.kind == "GROUP" else None,
            "snapshot": obj.eiem_physics.native_parameter_snapshot,
            "curves": obj.get(NATIVE.CURVE_MAPPING_EDITED),
            "ready": obj.eiem_native_physics.ready}


def restore_parameters(obj, backup):
    state = obj.eiem_native_physics
    state.ready = False
    try:
        state.fields.clear()
        for record in backup["fields"]:
            field = state.fields.add()
            for key, value in record.items():
                setattr(field, key, value)
        for name, value in backup["compact"].items():
            setattr(obj.eiem_physics, name, value)
        if backup["radius"]:
            AUTHOR.load_author_radius(obj, backup["radius"])
        obj.eiem_physics.native_parameter_snapshot = backup["snapshot"]
        if backup["curves"] is None:
            if NATIVE.CURVE_MAPPING_EDITED in obj:
                del obj[NATIVE.CURVE_MAPPING_EDITED]
        else:
            obj[NATIVE.CURVE_MAPPING_EDITED] = backup["curves"]
        if NATIVE.is_parameter_group(obj):
            for parameter, label in NATIVE.CURVE_PARAMETERS:
                NATIVE.load_curve_mapping(obj, parameter, NATIVE.source_curve_keys(obj, parameter)[1])
    finally:
        state.ready = backup["ready"]


def paste_to_targets(targets, payload):
    AUTHOR.document.require(bool(targets), "没有粘贴目标")
    for obj in targets:
        validate_plan(obj, payload)
    # Validate all targets first. Operators also provide one Blender Undo step.
    backups = [backup_parameters(obj) for obj in targets]
    try:
        for obj in targets:
            apply_parameters(obj, payload)
    except Exception:
        for obj, backup in zip(targets, backups):
            restore_parameters(obj, backup)
        raise


def delete_author_chain(obj):
    AUTHOR.document.require(obj and obj.eiem_physics.kind == "GROUP", "请选择新增物理链；源物理链请使用禁用组件")
    rig, identity = obj.eiem_physics.rig, obj.eiem_physics.identity
    tree = obj.eiem_native_physics.curve_mapping_tree
    action = obj.animation_data.action if obj.animation_data else None
    owned_action = action and (action.get(AUTHOR.AUTHOR_CURVE_ACTION_MARKER) == identity or
                              action.get(NATIVE.CURVE_ACTION_MARKER) == obj.eiem_native_physics.source_key)
    remaining = [item for item in objects_for(rig, "GROUPS") if item != obj]
    for scene in bpy.data.scenes:
        if scene.eiem_physics_editor_item == obj:
            scene.eiem_physics_editor_item = remaining[0] if remaining else None
        if scene.eiem_physics_group == obj:
            scene.eiem_physics_group = remaining[0] if remaining else None
    AUTHOR.remove_generated_visuals(obj)
    bpy.data.objects.remove(obj, do_unlink=True)
    if tree and tree.users == 0 and tree.get(NATIVE.CURVE_MAPPING_TREE_MARKER) == identity:
        bpy.data.node_groups.remove(tree)
    if owned_action and action.users == int(action.use_fake_user):
        bpy.data.actions.remove(action)
    # Bones and shared colliders belong to the rig, not to this group's UI.
    NATIVE.apply_visibility(bpy.context.scene)


class EFF_OT_physics_editor_action(bpy.types.Operator):
    bl_idname = "eiem.physics_editor_action"
    bl_label = "物理编辑操作"
    bl_options = {"REGISTER", "UNDO"}
    action: StringProperty()
    target: StringProperty()
    path: StringProperty()
    scope: StringProperty(default="ALL")

    def execute(self, context):
        try:
            obj = bpy.data.objects.get(self.target) if self.target else active_item(context)
            if self.action == "ACTIVATE":
                AUTHOR.document.require(obj is not None, "物理条目已删除")
                context.scene.eiem_physics_editor_item = obj
                if obj.eiem_physics.kind in GROUPS:
                    context.scene.eiem_physics_group = obj
                if context.mode == "OBJECT":
                    for selected in context.selected_objects:
                        selected.select_set(False)
                    obj.hide_set(False)
                    obj.select_set(True)
                    context.view_layer.objects.active = obj
                return {"FINISHED"}
            AUTHOR.document.require(obj is not None, "请选择物理条目")
            if self.action == "COPY":
                payload = capture_parameters(obj, self.scope)
                context.scene.eiem_physics_editor_clipboard = json.dumps(payload, ensure_ascii=False, allow_nan=False)
                self.report({"INFO"}, "已复制 %d 项参数、%d 条曲线；目标链的骨骼和碰撞引用保留" %
                            (len(payload["fields"]), len(payload["curves"])))
            elif self.action == "PASTE":
                payload = json.loads(context.scene.eiem_physics_editor_clipboard)
                targets = parameter_targets(context, obj)
                paste_to_targets(targets, payload)
                self.report({"INFO"}, "已粘贴到 %d 个物理组" % len(targets))
            elif self.action == "DELETE_CHAIN":
                delete_author_chain(obj)
                self.report({"INFO"}, "已删除新增物理链及预览；骨骼和共享碰撞体保留")
            elif self.action == "SAVE_PRESET":
                name = context.scene.eiem_physics_editor_preset_name.strip()
                AUTHOR.document.require(bool(name), "请填写预设名称")
                presets = context.scene.eiem_physics_editor_presets
                preset = next((preset for preset in presets if preset.name == name), None)
                if preset is None:
                    preset = presets.add()
                preset.name = name
                preset.payload = json.dumps(capture_parameters(obj), ensure_ascii=False, allow_nan=False)
                context.scene.eiem_physics_editor_preset_index = next(index for index, item in enumerate(presets) if item == preset)
                self.report({"INFO"}, "参数预设已保存到当前工程；不含骨骼或碰撞引用")
            elif self.action in {"APPLY_PRESET", "DELETE_PRESET"}:
                presets = context.scene.eiem_physics_editor_presets
                index = context.scene.eiem_physics_editor_preset_index
                AUTHOR.document.require(0 <= index < len(presets), "请选择参数预设")
                if self.action == "APPLY_PRESET":
                    paste_to_targets(parameter_targets(context, obj), json.loads(presets[index].payload))
                else:
                    presets.remove(index)
                    context.scene.eiem_physics_editor_preset_index = max(0, min(index, len(presets) - 1))
            elif self.action == "RESET":
                if NATIVE.is_native(obj):
                    raw = NATIVE.source.get_field(NATIVE.record(obj)["fields"], tuple(self.path.split(".")))
                    if isinstance(raw, dict) and "m_Curve" in raw.get("curve", {}):
                        payload = {"scope": "ALL", "fields": [
                            {"path": self.path + ".value", "floating": True, "value": raw["value"]},
                            {"path": self.path + ".useCurve", "floating": False, "value": int(raw["useCurve"])}],
                            "curves": {self.path: copy.deepcopy(raw["curve"]["m_Curve"])}}
                        apply_parameters(obj, payload)
                    elif isinstance(raw, dict) and set(raw) == {"x", "y", "z"}:
                        for axis in "xyz":
                            NATIVE.set_native_number(obj, self.path + "." + axis, raw[axis])
                    elif isinstance(raw, dict) and set(raw) == {"value", "use"}:
                        NATIVE.set_native_number(obj, self.path + ".value", raw["value"])
                        NATIVE.set_native_number(obj, self.path + ".use", raw["use"])
                    else:
                        NATIVE.set_native_number(obj, self.path, raw)
                else:
                    field = NATIVE.native_field(obj, self.path)
                    if field is not None:
                        NATIVE.set_native_number(obj, self.path, json.loads(field.original))
                    else:
                        name = self.path.removeprefix("serializeData.")
                        write_number(obj, name, AUTHOR.document.DEFAULTS[name])
            else:
                raise ValueError("未知物理编辑操作")
            return {"FINISHED"}
        except Exception as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}


class EFF_UL_physics_items(bpy.types.UIList):
    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        query = self.filter_name.casefold().strip()
        return ([self.bitflag_filter_item if not query or query in
                 (item.name + item.eiem_physics.label).casefold() else 0 for item in items], [])

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item.eiem_physics, "editor_selected", text="")
        op = row.operator("eiem.physics_editor_action", text=item.eiem_physics.label or item.name,
                          emboss=False, depress=context.scene.eiem_physics_editor_item == item)
        op.action, op.target = "ACTIVATE", item.name
        row.label(text="源" if NATIVE.is_native(item) else "新增")
        row.prop(item.eiem_physics, "editor_pinned", text="", icon="PINNED", emboss=False)
        row.prop(item.eiem_physics, "editor_hidden", text="", icon="HIDE_ON" if item.eiem_physics.editor_hidden else "HIDE_OFF", emboss=False)


def draw_parameter(layout, obj, path, label, kind):
    if kind == "VECTOR":
        available = all(has_parameter(obj, path + "." + axis) for axis in "xyz")
    else:
        available = has_parameter(obj, path + ".value" if kind in {"CURVE", "LIMIT"} else path)
    if not available:
        return
    row = layout.row(align=True)
    if obj.eiem_physics.kind in GROUPS:
        targets = parameter_targets(bpy.context, obj)
        checked_path = path + ".value" if kind in {"CURVE", "LIMIT"} else path
        if kind != "VECTOR" and len(targets) > 1 and any(
                not has_parameter(target, checked_path) or read_number(target, checked_path) != read_number(obj, checked_path)
                for target in targets):
            row.label(text="混合", icon="SELECT_SUBTRACT")
    proxy = obj.eiem_physics_editor_values
    if kind == "LIMIT":
        row.prop(proxy, property_name(path + ".use"), text="")
        values = row.row()
        values.enabled = bool(read_number(obj, path + ".use"))
        values.prop(proxy, property_name(path + ".value"), text=label)
        reset_path = full_path(path)
        if NATIVE.is_native(obj):
            op = row.operator("eiem.physics_editor_action", text="", icon="LOOP_BACK")
            op.action, op.path, op.target = "RESET", reset_path, obj.name
        return
    if kind == "CURVE":
        row.prop(proxy, property_name(path + ".value"), text=label)
        row.prop(proxy, property_name(path + ".useCurve"), text="曲线", toggle=True)
        reset_path = full_path(path) if NATIVE.is_native(obj) else full_path(path + ".value")
    else:
        row.prop(proxy, property_name(path), text=label)
        reset_path = full_path(path)
    if NATIVE.is_native(obj) or NATIVE.native_field(obj, reset_path) or path in AUTHOR.document.PARAMETERS:
        op = row.operator("eiem.physics_editor_action", text="", icon="LOOP_BACK")
        op.action, op.path, op.target = "RESET", reset_path, obj.name
    if kind == "CURVE" and read_number(obj, path + ".useCurve"):
        node = NATIVE.curve_mapping_node(obj, full_path(path))
        if node:
            box = layout.box()
            box.label(text="组内基线深度 0～1 → 倍率")
            if len(parameter_targets(bpy.context, obj)) > 1:
                box.label(text="曲线编辑当前组；批量曲线使用复制/粘贴")
            box.template_curve_mapping(node, "mapping", type="NONE")
            NATIVE.draw_curve_distribution(box, obj, full_path(path))


def draw_group_structure(layout, obj, context):
    if obj.eiem_physics.kind == "GROUP":
        layout.operator("eiem.physics_edit", text="选择本组骨骼").action = "SELECT_BONES"
        for item in obj.eiem_physics.nodes:
            found = AUTHOR.bones(obj.eiem_physics.rig).get(item.bone_id)
            layout.prop(item, "role", text=found[0].name if found else "骨骼已删除")
        row = layout.row(align=True)
        row.operator("eiem.physics_edit", text="加入所选骨骼").action = "ADD_NODES"
        row.operator("eiem.physics_edit", text="移除所选骨骼").action = "REMOVE_NODES"
        layout.label(text="支持多个固定根；各分支共享本组参数")
    else:
        payload, component = NATIVE.snapshot(obj), NATIVE.record(obj)
        transforms = {t["identity"]: t for t in payload["transforms"]}
        for identity in NATIVE.source.referenced_sources(component, NATIVE.source.ROOT_REFERENCE, "Transform"):
            path = transforms[identity]["bone"]
            row = layout.row(align=True)
            row.label(text=path.rsplit("/", 1)[-1] or "根")
            op = row.operator("eiem.select_native_physics_bones", text="选择链")
            op.root_source, op.target = identity, obj.name
        ignored = NATIVE.source.referenced_sources(component, NATIVE.source.IGNORE_REFERENCE, "Transform")
        layout.label(text="排除子树：%d · 来源结构保留" % len(ignored))
        layout.label(text="源节点选择写回待确认，当前仅显示映射")
        if obj.get(NATIVE.SELECTION_PREVIEW_ERROR):
            layout.label(text="源点绑定未确认；显示原始点与骨骼层级", icon="INFO")
            layout.label(text="本组骨骼深度/半径/角度预览不可用；参数仍可编辑")
    sample = AUTHOR.active_group_node_sample(context, obj)
    if sample:
        box = layout.box()
        box.label(text="当前骨骼：%s · %s" % (sample["bone"].name, {"FIXED": "固定", "MOVE": "运动", "IGNORE": "忽略"}[sample["role"]]))
        box.label(text="预览基线深度 %.3f" % sample["depth"])
        for parameter in ("radius", "angleLimitConstraint.limitAngle"):
            if has_parameter(obj, parameter + ".value"):
                depth = sample["depth"]
                value = NATIVE.curve_parameter_value(obj, full_path(parameter), depth) if NATIVE.is_parameter_group(obj) else read_number(obj, parameter + ".value")
                box.label(text="%s：%.5g（配置预览）" % ("模拟厚度" if parameter == "radius" else "允许角度", value))


def draw_collision_list(layout, obj):
    box = layout.box()
    box.label(text="本组引用的碰撞体：%d" % len(obj.eiem_physics.colliders))
    for index, ref in enumerate(obj.eiem_physics.colliders):
        row = box.row(align=True)
        if ref.object:
            op = row.operator("eiem.physics_editor_action", text=ref.object.eiem_physics.label or ref.object.name)
            op.action, op.target = "ACTIVATE", ref.object.name
        else:
            row.label(text="引用已失效", icon="ERROR")
        if obj.eiem_physics.kind == "GROUP":
            op = row.operator("eiem.physics_edit", text="", icon="X")
            op.action, op.index = "UNLINK", index
    if obj.eiem_physics.kind == "GROUP":
        row = box.row(align=True)
        row.prop(obj.eiem_physics, "collider_candidate", text="")
        row.operator("eiem.physics_edit", text="加入").action = "LINK_CANDIDATE"
    else:
        box.label(text="来源引用保留；结构写回接入后开放修改")


def draw_collider(layout, obj):
    if obj.eiem_physics.kind == "COLLIDER":
        p = obj.eiem_physics
        layout.prop(p, "shape")
        layout.prop(obj, "location", text="局部中心")
        layout.prop(p, "radius")
        if p.shape == "CAPSULE":
            layout.prop(p, "separate_radii")
            if p.separate_radii:
                layout.prop(p, "end_radius")
            layout.prop(obj.eiem_physics_editor_values, "collider_length")
            layout.prop(p, "aligned_on_center")
        row = layout.row(align=True)
        row.operator("eiem.physics_edit", text="重绑活动骨骼").action = "REBIND_ACTIVE"
        row.operator("eiem.physics_edit", text="删除及解除引用").action = "DELETE_COLLIDER"
    else:
        kind = NATIVE.record(obj)["type"]
        layout.label(text="绑定骨骼：" + NATIVE.record(obj)["bone"])
        layout.prop(obj.eiem_physics_editor_values, "collider_center")
        if kind != "BeyondBonePlaneCollider":
            for path, label, field_kind in COLLIDER_FIELDS:
                if kind == "BeyondBoneSphereCollider" and path != "size.x":
                    continue
                if path == "size.y" and not read_number(obj, "radiusSeparation"):
                    continue
                row = layout.row()
                row.prop(obj.eiem_physics_editor_values, property_name(path), text="半径" if kind == "BeyondBoneSphereCollider" else label)
        else:
            layout.label(text="无限平面；预览方块不限定碰撞范围")
    users = [group for group in objects_for(obj.eiem_physics.rig, "GROUPS")
             if any(ref.object == obj for ref in group.eiem_physics.colliders)]
    layout.label(text="此碰撞体被 %d 个组共享" % len(users))
    for group in users:
        op = layout.operator("eiem.physics_editor_action", text=group.eiem_physics.label or group.name)
        op.action, op.target = "ACTIVATE", group.name


class EFF_PT_physics_editor(bpy.types.Panel):
    bl_label = "物理"
    bl_idname = "EFF_PT_physics_editor"
    bl_space_type, bl_region_type, bl_category = "VIEW_3D", "UI", "EFF"

    def draw(self, context):
        layout, scene = self.layout, context.scene
        rig = rig_of_context(context)
        layout.prop(scene, "eiem_physics_group", text="当前组")
        layout.menu("EFF_MT_physics_create", text="创建 / 导入", icon="ADD")
        if not rig:
            layout.label(text="选择 EFF 骨架或物理条目")
            return
        layout.label(text="骨架：" + rig.name)
        layout.prop(scene, "eiem_physics_editor_category", expand=True)
        collection = role_collection(rig, scene.eiem_physics_editor_category)
        if collection:
            layout.template_list("EFF_UL_physics_items", "", collection, "objects", scene,
                                 "eiem_physics_editor_index", rows=4)
        obj = active_item(context)
        if obj:
            layout.label(text="编辑：" + (obj.eiem_physics.label or obj.name))
            layout.prop(obj.eiem_physics, "label")
            if obj.eiem_physics.kind == "GROUP":
                op = layout.operator("eiem.physics_editor_action", text="删除物理链", icon="TRASH")
                op.action, op.target = "DELETE_CHAIN", obj.name
            if NATIVE.is_native(obj):
                layout.prop(obj.eiem_native_physics, "disabled", text="禁用游戏组件")
            if obj.eiem_physics.kind in COLLIDERS:
                draw_collider(layout, obj)
            elif obj.eiem_physics.kind in GROUPS:
                row = layout.row(align=True)
                op = row.operator("eiem.physics_editor_action", text="复制全部参数")
                op.action, op.scope = "COPY", "ALL"
                op = row.operator("eiem.physics_editor_action", text="粘贴到所选（%d）" % len(parameter_targets(context, obj)))
                op.action = "PASTE"
                layout.label(text="复制参数不复制骨骼拓扑、动画或游戏运行时权重", icon="INFO")
                header, body = layout.panel("eiem_physics_presets", default_closed=True)
                header.label(text="参数预设（当前工程）")
                if body:
                    body.template_list("UI_UL_list", "eiem_physics_presets", scene, "eiem_physics_editor_presets",
                                       scene, "eiem_physics_editor_preset_index", rows=2)
                    body.prop(scene, "eiem_physics_editor_preset_name", text="名称")
                    row = body.row(align=True)
                    row.operator("eiem.physics_editor_action", text="保存 / 更新").action = "SAVE_PRESET"
                    row.operator("eiem.physics_editor_action", text="应用到所选").action = "APPLY_PRESET"
                    row.operator("eiem.physics_editor_action", text="", icon="X").action = "DELETE_PRESET"
                layout.prop(scene, "eiem_physics_editor_tab", expand=True)
                layout.prop(scene, "eiem_physics_editor_search", text="查找参数", icon="VIEWZOOM")
                tab = scene.eiem_physics_editor_tab
                if tab == "STRUCTURE":
                    draw_group_structure(layout, obj, context)
                else:
                    row = layout.row(align=True)
                    op = row.operator("eiem.physics_editor_action", text="复制当前区参数")
                    op.action, op.scope = "COPY", tab
                    search = scene.eiem_physics_editor_search.casefold()
                    for section_tab, label, fields in SECTIONS:
                        if section_tab != tab:
                            continue
                        if "BoneSpring" in label and read_number(obj, "clothType") != 10:
                            continue
                        filtered = [item for item in fields if not search or search in (item[0] + item[1]).casefold()]
                        if not filtered:
                            continue
                        if search:
                            body = layout.box()
                            body.label(text=label)
                        else:
                            header, body = layout.panel("eiem_physics_section_" + label, default_closed=label not in {"基础动力", "角度恢复", "角度限制", "身体碰撞"})
                            header.label(text=label)
                        if body:
                            for path, name, kind in filtered:
                                draw_parameter(body, obj, path, name, kind)
                    if tab == "COLLISION":
                        draw_collision_list(layout, obj)
                    elif tab == "ADVANCED" and len(obj.eiem_native_physics.fields):
                        NATIVE.draw_parameter_fields(layout, obj, "BeyondBoneCloth")
        header, body = layout.panel("eiem_physics_visibility", default_closed=True)
        header.label(text="视口显示（不影响游戏启用）")
        if body:
            body.prop(scene, "eiem_physics_visibility", text="范围")
            body.prop(scene, "eiem_physics_preview_style", text="样式")
            body.prop(scene, "eiem_physics_xray")
        header, body = layout.panel("eiem_physics_files", default_closed=True)
        header.label(text="物理作者文件")
        if body:
            body.operator("eiem.export_physics", text="导出所选物理作者数据")
        layout.label(text="当前为物理作者编辑；新增物理游戏接入待完成", icon="INFO")


CLASSES = (EFF_PG_physics_editor_values, EFF_PG_physics_editor_preset, EFF_UL_physics_items,
           EFF_OT_physics_editor_action, EFF_PT_physics_editor)
SCENE_PROPERTIES = ("eiem_physics_editor_item", "eiem_physics_editor_category", "eiem_physics_editor_index",
                    "eiem_physics_editor_tab", "eiem_physics_editor_search", "eiem_physics_editor_clipboard",
                    "eiem_physics_editor_presets", "eiem_physics_editor_preset_index", "eiem_physics_editor_preset_name")


def register(author, native):
    global AUTHOR, NATIVE
    AUTHOR, NATIVE = author, native
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Object.eiem_physics_editor_values = PointerProperty(type=EFF_PG_physics_editor_values)
    bpy.types.Scene.eiem_physics_editor_item = PointerProperty(type=bpy.types.Object,
        poll=lambda self, obj: obj.eiem_physics.kind in GROUPS | COLLIDERS,
        update=NATIVE.visibility_updated)
    bpy.types.Scene.eiem_physics_editor_category = EnumProperty(items=(
        ("GROUPS", "物理组", ""), ("COLLIDERS", "碰撞体", "")), update=list_index_updated)
    bpy.types.Scene.eiem_physics_editor_index = IntProperty(default=0, update=list_index_updated)
    bpy.types.Scene.eiem_physics_editor_tab = EnumProperty(items=(
        ("STRUCTURE", "结构", ""), ("DYNAMICS", "动力", ""),
        ("COLLISION", "碰撞", ""), ("ADVANCED", "高级", "")), default="DYNAMICS")
    bpy.types.Scene.eiem_physics_editor_search = StringProperty(update=search_updated)
    bpy.types.Scene.eiem_physics_editor_clipboard = StringProperty(options={"HIDDEN"})
    bpy.types.Scene.eiem_physics_editor_presets = CollectionProperty(type=EFF_PG_physics_editor_preset)
    bpy.types.Scene.eiem_physics_editor_preset_index = IntProperty(default=0)
    bpy.types.Scene.eiem_physics_editor_preset_name = StringProperty(name="预设名称")


def unregister():
    del bpy.types.Object.eiem_physics_editor_values
    for name in SCENE_PROPERTIES:
        delattr(bpy.types.Scene, name)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    global AUTHOR, NATIVE
    AUTHOR = NATIVE = None
    _ENUM_ITEM_CACHE.clear()

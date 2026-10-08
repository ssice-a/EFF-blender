"""LOD discovery and export planning independent of Blender UI."""

import re
from collections import defaultdict

# LOD is part of the imported resource identity, not Blender's display name.
# Keep the pattern deliberately narrow so names such as "lodger" do not become
# an invented game LOD.  The exporter exposes levels 0..4, while discovery can
# still report an out-of-range level as unsupported metadata.
LOD_TOKEN_RE = re.compile(r"(?<![a-z0-9])lod([0-9]+)(?![a-z0-9])", re.IGNORECASE)


class _SourceMesh:
    type = 'MESH'

    def __init__(self, package, row):
        self.name = row['name']
        self.data = dict(eiem_section='Mesh' + row['name'], eiem_asset=row['name'],
                         eiem_source=row['logicalPath'], eiem_target_asset=row['name'],
                         eiem_target_path=row['logicalPath'])
        self._values = dict(eiem_author_package=str(package), eiem_render_asset=row['name'],
                            eiem_render_section='Render' + row['name'])

    def get(self, key, default=None):
        return self._values.get(key, default)


def source_mesh_candidates(package, resources):
    """Target metadata from the unpacked source, without importing geometry."""
    return [_SourceMesh(package, row) for row in resources if row['type'] == 'Mesh']


class _VariantData:
    """Read-only metadata view over a Blender Mesh for one target LOD."""

    def __init__(self, source, overrides):
        self._source = source
        self._overrides = dict(overrides)

    def get(self, key, default=None):
        if key in self._overrides:
            return self._overrides[key]
        return self._source.get(key, default)

    def __getitem__(self, key):
        if key in self._overrides:
            return self._overrides[key]
        return self._source[key]

    def __contains__(self, key):
        return key in self._overrides or key in self._source

    def __getattr__(self, name):
        return getattr(self._source, name)


def mesh_export_template(obj):
    """Return the authored Mesh whose buffers a LOD view exports.

    A LOD view changes only the target Renderer identity. Geometry, skin
    weights, bind poses, and v5 source-slot provenance remain owned by the
    selected authored Mesh, so several Render rules can share one resource.
    """
    return obj._source if isinstance(obj, _MeshExportVariant) else obj


class _MeshExportVariant:
    """Delegate Blender object data while exposing another LOD identity.

    Export functions only read the object.  A view avoids mutating the .blend
    and lets one authored template feed several concrete LOD resources.
    """

    def __init__(self, source, target_level, overrides, data_overrides):
        self._source = source
        self._target_level = target_level
        self._overrides = dict(overrides)
        self._data = _VariantData(source.data, data_overrides)
        self.name = "%s_LOD%d" % (source.name, target_level)

    @property
    def data(self):
        return self._data

    def get(self, key, default=None):
        if key in self._overrides:
            return self._overrides[key]
        return self._source.get(key, default)

    def __getitem__(self, key):
        if key in self._overrides:
            return self._overrides[key]
        return self._source[key]

    def __contains__(self, key):
        return key in self._overrides or key in self._source

    def __getattr__(self, name):
        return getattr(self._source, name)


def _lod_values(obj):
    values = [
        obj.get("eiem_render_asset", ""),
        obj.data.get("eiem_target_asset", ""),
        obj.data.get("eiem_target_path", ""),
        obj.data.get("eiem_source", ""),
        obj.data.get("eiem_section", ""),
        obj.get("eiem_render_section", ""),
    ]
    return [str(value).strip() for value in values if str(value).strip()]


def mesh_lod_level(obj):
    """Return the single LOD encoded by imported resource metadata."""
    levels = {
        int(match.group(1))
        for value in _lod_values(obj)
        for match in LOD_TOKEN_RE.finditer(value)
    }
    if len(levels) > 1:
        raise ValueError("Mesh %s has inconsistent LOD metadata: %s" %
                         (obj.name, ", ".join(str(value) for value in sorted(levels))))
    return next(iter(levels), None)


def _replace_lod(value, target_level):
    value = str(value or "")
    def replace(match):
        token = match.group(0)
        return token[:3] + str(int(target_level))
    return LOD_TOKEN_RE.sub(replace, value)


def mesh_lod_family(obj):
    """Return a package-scoped identity with its LOD token normalized.

    A game's LOD0 may be serialized in a dedicated ``.asset`` while later
    levels live in a shared FBX.  The logical target asset is therefore the
    stable family key; source path is only a fallback for legacy records that
    have no asset identity.
    """
    package = str(obj.get("eiem_author_package", "")).replace("\\", "/").lower()
    asset = str(obj.get("eiem_render_asset", "") or
                obj.data.get("eiem_target_asset", obj.data.get("eiem_asset", "")))
    source = str(obj.data.get("eiem_target_path", "") or
                 obj.data.get("eiem_source", ""))
    if asset.strip() and LOD_TOKEN_RE.search(asset):
        return (package, "asset", LOD_TOKEN_RE.sub("{lod}", asset.lower()))
    if source.strip():
        return (package, "source", LOD_TOKEN_RE.sub("{lod}", source.replace("\\", "/").lower()))
    return (package, "asset", asset.lower())


def discover_mesh_lods(obj, candidates):
    """Discover LODs actually imported for the same resource family."""
    family = mesh_lod_family(obj)
    levels = set()
    for candidate in candidates:
        if candidate.type != "MESH" or not candidate.data.get("eiem_section"):
            continue
        if mesh_lod_family(candidate) != family:
            continue
        level = mesh_lod_level(candidate)
        if level is not None:
            levels.add(level)
    return levels


def _lod_variant(obj, target_level, candidates):
    source_level = mesh_lod_level(obj)
    if source_level is None:
        raise ValueError("Mesh %s has no LOD token to replicate" % obj.name)
    observed = next((candidate for candidate in candidates
                     if candidate.type == "MESH"
                     and candidate.data.get("eiem_section")
                     and mesh_lod_family(candidate) == mesh_lod_family(obj)
                     and mesh_lod_level(candidate) == target_level), None)
    object_overrides = {}
    data_overrides = {}
    metadata = observed or obj
    for owner, key in ((object_overrides, "eiem_render_asset"),
                       (object_overrides, "eiem_render_section"),
                       (data_overrides, "eiem_section"),
                       (data_overrides, "eiem_source"),
                       (data_overrides, "eiem_asset"),
                       (data_overrides, "eiem_target_path"),
                       (data_overrides, "eiem_target_asset")):
        current = (metadata.get(key, "") if owner is object_overrides else
                   metadata.data.get(key, ""))
        if str(current).strip():
            owner[key] = (current if observed else
                          _replace_lod(current, target_level))
    return _MeshExportVariant(obj, target_level, object_overrides, data_overrides)


def expand_lod_plan(plan, target_levels, candidates, source_identity):
    """Expand a normal export plan into independently addressable LOD views.

    A selected object is emitted for a target only when that target LOD was
    observed in the imported package family. Objects without an LOD token are
    stable resources and remain in every plan. Switch bindings and hidden
    declarations are copied to each view, so all LODs share one state variable.
    """
    target_levels = {int(level) for level in (target_levels or ())}
    if any(level < 0 or level > 4 for level in target_levels):
        raise ValueError("LOD export supports levels 0 through 4")
    variants = []
    bindings = {}
    hidden = set()
    for obj in plan["objects"]:
        source_level = mesh_lod_level(obj)
        if source_level is None:
            emitted = [obj]
        else:
            available = discover_mesh_lods(obj, candidates)
            emitted = []
            for target in sorted(target_levels):
                if target not in available:
                    continue
                emitted.append(obj if target == source_level else
                               _lod_variant(obj, target, candidates))
        for variant in emitted:
            variants.append(variant)
            if obj in plan["bindings"]:
                bindings[variant] = plan["bindings"][obj]
            if obj in plan["hidden"]:
                hidden.add(variant)
    if not variants:
        raise ValueError("所选目标 LOD 在当前导入资源中不存在")

    grouped = defaultdict(list)
    for obj in sorted(variants, key=lambda item: (source_identity(item), mesh_export_template(item).name)):
        grouped[source_identity(obj)].append(obj)
    return {
        "objects": [obj for values in grouped.values() for obj in values],
        "sources": list(grouped.values()),
        "groups": plan["groups"],
        "bindings": bindings,
        "hidden": hidden,
    }


def lod_levels_for_export(mesh_objects, candidates, selected_levels=None,
                          all_levels=False):
    """Resolve checkbox state to discovered levels, never filename guesses."""
    discovered = set()
    for obj in mesh_objects:
        level = mesh_lod_level(obj)
        if level is not None:
            discovered.update(discover_mesh_lods(obj, candidates))
    if all_levels:
        return sorted(level for level in discovered if 0 <= level <= 4)
    return sorted(set(selected_levels or ()) & discovered)

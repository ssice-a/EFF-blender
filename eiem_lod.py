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


def _lod_levels(value):
    return {int(match.group(1)) for match in LOD_TOKEN_RE.finditer(str(value or ""))}


def _single_lod_level(obj, values):
    levels = set()
    for value in values:
        levels.update(_lod_levels(value))
    if len(levels) > 1:
        raise ValueError("Mesh %s has inconsistent LOD metadata: %s" %
                         (obj.name, ", ".join(str(value) for value in sorted(levels))))
    return next(iter(levels), None)


def _render_lod_level(obj):
    # The Render section is the concrete binding on the prefab. Its asset can
    # point at an independently named Mesh resource whose LOD token differs.
    for value in (obj.get("eiem_render_section", ""),
                  obj.get("eiem_render_asset", "")):
        level = _single_lod_level(obj, (value,))
        if level is not None:
            return level
    return None


def _mesh_resource_lod_level(obj):
    for value in (
            obj.data.get("eiem_target_asset", ""),
            obj.data.get("eiem_target_path", ""),
            obj.data.get("eiem_asset", ""),
            obj.data.get("eiem_source", ""),
            obj.data.get("eiem_section", "")):
        level = _single_lod_level(obj, (value,))
        if level is not None:
            return level
    return None


def _mesh_lod_is_independent_of_render(obj):
    render_level = _render_lod_level(obj)
    resource_level = _mesh_resource_lod_level(obj)
    return (render_level is not None and resource_level is not None
            and render_level != resource_level)


def mesh_lod_level(obj):
    """Return the target Render LOD, falling back to the Mesh resource LOD."""
    render_level = _render_lod_level(obj)
    return render_level if render_level is not None else _mesh_resource_lod_level(obj)


def mesh_export_target(obj):
    """Return (source Mesh path, source Mesh asset, bound Render LOD)."""
    path = str(obj.data.get("eiem_target_path", "") or
               obj.data.get("eiem_source", ""))
    asset = str(obj.data.get("eiem_target_asset", "") or
                obj.data.get("eiem_asset", ""))
    level = mesh_lod_level(obj)
    if level is None:
        level = 0
    return path, asset, level


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
    assets = (obj.get("eiem_render_asset", ""),
              obj.data.get("eiem_target_asset", ""),
              obj.data.get("eiem_asset", ""))
    for asset in assets:
        asset = str(asset or "")
        if asset.strip() and LOD_TOKEN_RE.search(asset):
            return (package, "asset", LOD_TOKEN_RE.sub("{lod}", asset.lower()))
    sources = (obj.data.get("eiem_target_path", ""), obj.data.get("eiem_source", ""))
    for source in sources:
        source = str(source or "")
        if source.strip() and LOD_TOKEN_RE.search(source):
            return (package, "source", LOD_TOKEN_RE.sub("{lod}", source.replace("\\", "/").lower()))
    asset = next((str(value) for value in assets if str(value or "").strip()), "")
    return (package, "asset", asset.lower())


def _mesh_source_path(obj):
    return str(obj.data.get("eiem_target_path", "") or
               obj.data.get("eiem_source", "")).replace("\\", "/").strip().casefold()


def discover_mesh_lods(obj, candidates):
    """Discover LODs actually imported for the same resource family."""
    if _mesh_lod_is_independent_of_render(obj):
        level = mesh_lod_level(obj)
        return {level} if level is not None else set()
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
    matching = [candidate for candidate in candidates
                if candidate.type == "MESH"
                and candidate.data.get("eiem_section")
                and mesh_lod_family(candidate) == mesh_lod_family(obj)
                and mesh_lod_level(candidate) == target_level]
    source_path = _mesh_source_path(obj)
    observed = next((candidate for candidate in matching
                     if source_path and _mesh_source_path(candidate) == source_path),
                    matching[0] if matching else None)
    object_overrides = {}
    data_overrides = {}
    metadata = observed or obj
    for key in ("eiem_render_asset", "eiem_render_section"):
        current = metadata.get(key, "")
        if str(current).strip():
            object_overrides[key] = (current if observed else
                                     _replace_lod(current, target_level))

    resource_level = _mesh_resource_lod_level(obj)
    follows_render_lod = (resource_level is None or resource_level == source_level)
    if follows_render_lod:
        for key in ("eiem_section", "eiem_source", "eiem_asset",
                    "eiem_target_path", "eiem_target_asset"):
            current = metadata.data.get(key, "")
            if str(current).strip():
                data_overrides[key] = (current if observed else
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

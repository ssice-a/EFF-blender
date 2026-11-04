"""Verify Format 3 Mod skin weights import when the package has no Skeleton sections."""
import importlib.util
import json
from pathlib import Path
import sys
import types

import bpy

addon_dir, mod_root = map(Path, sys.argv[sys.argv.index('--') + 1:])
package = types.ModuleType('eiem_format3_skin_test')
package.__path__ = [str(addon_dir)]
sys.modules[package.__name__] = package
spec = importlib.util.spec_from_file_location(
    package.__name__, addon_dir / '__init__.py',
    submodule_search_locations=[str(addon_dir)])
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()

before = {
    'objects': set(bpy.data.objects),
    'meshes': set(bpy.data.meshes),
    'materials': set(bpy.data.materials),
    'images': set(bpy.data.images),
    'collections': set(bpy.data.collections),
    'armatures': set(bpy.data.armatures),
}

try:
    root = mod_root.resolve()
    values = addon.eiem_blender_addon.parse_ini(root)
    expected_sections = {section for section in values.sections()
                         if section.lower().startswith('mesh') and values.get(section, 'path', fallback='')}
    imported = addon.eiem_blender_addon.import_package(
        root, clean=False, include_physics=False)
    objects = [obj for obj in set(bpy.data.objects) - before['objects']
               if obj.type == 'MESH' and
               obj.get('eiem_author_package') == str(root)]
    assert expected_sections and imported == len(objects) == len(expected_sections), (imported, len(objects))
    assert {obj.data.get('eiem_section', '') for obj in objects} == expected_sections

    for obj in objects:
        section = obj.data.get('eiem_section', '')
        mesh_path = values[section]['path']
        payload = addon.eiem_blender_addon.format_io.read_format3_mesh_resource(
            root / mesh_path)
        assert payload['skin'], section
        paths = payload['bone_paths']
        assert len(paths) == len(payload['bindposes']), section
        group_paths = json.loads(obj.get('eiem_vertex_group_bone_paths_json', '{}'))
        assert group_paths, section
        group_indices = {group.index: group_paths[group.name]
                         for group in obj.vertex_groups}

        expected = {}
        for vertex_index, (weights, indices) in enumerate(payload['skin']):
            for weight, palette_index in zip(weights, indices):
                if weight > 0:
                    path = paths[palette_index]
                    expected[(vertex_index, path)] = (
                        expected.get((vertex_index, path), 0.0) + float(weight))

        actual = {}
        for vertex in obj.data.vertices:
            for assignment in vertex.groups:
                path = group_indices[assignment.group]
                actual[(vertex.index, path)] = float(assignment.weight)
        assert set(actual) == set(expected), section
        for key, weight in expected.items():
            assert abs(actual[key] - weight) < 1e-6, (section, key, actual[key], weight)

    print('EFF_FORMAT3_IMPORT_SKIN_OK', imported,
          sum(len(obj.data.vertices) for obj in objects),
          sum(len(obj.vertex_groups) for obj in objects))
finally:
    for obj in set(bpy.data.objects) - before['objects']:
        bpy.data.objects.remove(obj, do_unlink=True)
    for collection in set(bpy.data.collections) - before['collections']:
        if collection.users == 0:
            bpy.data.collections.remove(collection)
    for name, datablocks in (
            ('meshes', bpy.data.meshes), ('materials', bpy.data.materials),
            ('images', bpy.data.images), ('armatures', bpy.data.armatures)):
        for block in set(datablocks) - before[name]:
            if block.users == 0:
                datablocks.remove(block)
    addon.unregister()

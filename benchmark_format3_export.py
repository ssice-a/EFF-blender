"""Measure one complete cold Format 3 export without touching the source scene.

Run Blender in background mode with: ADDON_DIR BLEND EFFSOURCE MOD_DIR REPORT.
MOD_DIR must not exist; this records a first-write export, not an update path.
"""
import importlib
import hashlib
import json
from pathlib import Path
import sys
import time

import bpy


arguments = sys.argv[sys.argv.index("--") + 1:]
warm_repeat = len(arguments) == 6 and arguments[-1] == "--warm-repeat"
if len(arguments) not in (5, 6) or (len(arguments) == 6 and not warm_repeat):
    raise ValueError("Expected ADDON_DIR BLEND EFFSOURCE MOD_DIR REPORT [--warm-repeat]")
addon_dir, blend, source, destination, report_path = map(Path, arguments[:5])
addon_dir, blend, source, destination, report_path = map(
    lambda path: path.resolve(),
    (addon_dir, blend, source, destination, report_path))
if destination.exists():
    raise ValueError("Cold Format 3 export destination already exists: " + str(destination))
if not (source / "source" / "manifest.json").is_file():
    raise ValueError("EFFSOURCE baseline is missing source/manifest.json: " + str(source))

sys.path.insert(0, str(addon_dir.parent))
package = importlib.import_module(addon_dir.name)
package.register()
try:
    bpy.ops.wm.open_mainfile(filepath=str(blend), load_ui=False, use_scripts=False)
    bpy.context.scene.eiem_source_baseline = str(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    native_export = package.eiem_blender_addon.native_export
    texture_profiles = []
    current_run = "cold"

    def instrument(module, name, category, describe):
        original = getattr(module, name)

        def measured(*args, **kwargs):
            started = time.perf_counter()
            result = None
            try:
                result = original(*args, **kwargs)
                return result
            finally:
                row = dict(run=current_run, category=category,
                           milliseconds=(time.perf_counter() - started) * 1000)
                try:
                    row.update(describe(args, kwargs, result))
                except Exception:
                    pass
                texture_profiles.append(row)

        setattr(module, name, measured)
        return module, name, original

    restore = [
        instrument(native_export, "texture_bitmap", "decode", lambda args, kwargs, result: dict(
            path=str(args[0]), cached=bool(args[1]) if len(args) > 1 else False,
            dimensions=list(result.size) if result is not None else None)),
        instrument(native_export.texture_compression, "rgba_pixels_top_down", "pixel-readback",
                   lambda args, kwargs, result: dict(
                       dimensions=list(args[0].size), outputBytes=len(result) if result is not None else 0)),
        instrument(native_export.texture_compression, "compress_rgba", "texconv",
                   lambda args, kwargs, result: dict(
                       dimensions=[args[1], args[2]], sourceFormat=args[4], colorSpace=args[5],
                       mipmaps=bool(args[3]), outputBytes=len(result["pixels"]) if result is not None else 0)),
        instrument(native_export, "_texture_image_signature", "image-signature",
                   lambda args, kwargs, result: dict(
                       image=getattr(args[1], "name", ""),
                       path=str(getattr(args[1], "filepath", "")),
                       section=str(args[1].get("eiem_section", "")),
                       packed=bool(getattr(args[1], "packed_file", None)))),
        instrument(native_export, "_update_texture_image_paths", "path-update",
                   lambda args, kwargs, result: dict(
                       entries=[dict(
                           texture=key, relative=value.get("dds_relative"),
                           images=[dict(name=getattr(image, "name", ""),
                                        path=str(getattr(image, "filepath", "")),
                                        section=str(image.get("eiem_section", "")))
                                   for image in value.get("images", ())],
                           exists=Path(args[0], value.get("dds_relative", "")).is_file())
                           for key, value in args[1].items()])),
        instrument(package.eiem_blender_addon, "write_texture", "blender-image-write",
                   lambda args, kwargs, result: dict(
                       image=getattr(args[1], "name", ""), path=str(args[0]),
                       outputBytes=Path(args[0]).stat().st_size if Path(args[0]).is_file() else 0)),
    ]
    started = time.perf_counter()
    stats = package.eiem_blender_addon.export_package(
        destination, source_baseline=str(source))
    elapsed = time.perf_counter() - started
    files = [path for path in destination.rglob("*") if path.is_file()]
    warm_result = None
    if warm_repeat:
        expected = {
            path.relative_to(destination).as_posix(): hashlib.sha256(path.read_bytes()).digest()
            for path in files
        }
        current_run = "warm"
        warm_started = time.perf_counter()
        warm_stats = package.eiem_blender_addon.export_package(
            destination, source_baseline=str(source))
        warm_elapsed = time.perf_counter() - warm_started
        actual = {
            path.relative_to(destination).as_posix(): hashlib.sha256(path.read_bytes()).digest()
            for path in destination.rglob("*") if path.is_file()
        }
        warm_result = dict(seconds=warm_elapsed, export=warm_stats,
                           byteIdenticalToCold=actual == expected,
                           changedFiles=sorted(name for name in expected.keys() | actual.keys()
                                               if expected.get(name) != actual.get(name)))
        files = [path for path in destination.rglob("*") if path.is_file()]
    for module, name, original in reversed(restore):
        setattr(module, name, original)
    report = dict(
        format=3,
        cold=True,
        seconds=elapsed,
        files=len(files),
        outputBytes=sum(path.stat().st_size for path in files),
        fileCounts={
            kind: sum(path.suffix == ".effres" and path.parent.name == kind for path in files)
            for kind in ("meshes", "materials", "textures", "physics", "effects")
        },
        export=stats,
        warmRepeat=warm_result,
        textureProfile=texture_profiles,
        source=str(source),
        destination=str(destination),
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print("EFF_FORMAT3_COLD_EXPORT " + json.dumps(report, ensure_ascii=False), flush=True)
finally:
    package.unregister()

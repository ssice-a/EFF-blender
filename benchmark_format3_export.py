"""Measure one complete cold Format 3 export without touching the source scene.

Run Blender in background mode with: ADDON_DIR BLEND EFFSOURCE MOD_DIR REPORT.
MOD_DIR must not exist; this records a first-write export, not an update path.
"""
import importlib
import json
from pathlib import Path
import sys
import time

import bpy


addon_dir, blend, source, destination, report_path = map(
    Path, sys.argv[sys.argv.index("--") + 1:])
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
    started = time.perf_counter()
    stats = package.eiem_blender_addon.export_package(
        destination, source_baseline=str(source))
    elapsed = time.perf_counter() - started
    files = [path for path in destination.rglob("*") if path.is_file()]
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
        source=str(source),
        destination=str(destination),
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print("EFF_FORMAT3_COLD_EXPORT " + json.dumps(report, ensure_ascii=False), flush=True)
finally:
    package.unregister()

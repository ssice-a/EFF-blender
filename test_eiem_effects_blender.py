"""Compatibility entrypoint for the object-owned effect regression.

blender -b --factory-startup --python-exit-code 1 --python THIS -- ADDON SOURCES OUTPUT
OUTPUT may be a directory (legacy invocation) or a .blend path.
"""
import runpy
import sys
from pathlib import Path

args = sys.argv[sys.argv.index('--') + 1:]
if len(args) != 3:
    raise SystemExit('Expected ADDON_DIRECTORY SOURCE_DIRECTORY OUTPUT')
output = Path(args[2])
if output.suffix.lower() != '.blend':
    sys.argv[-1] = str(output / 'effects-object-editor-tests.blend')
runpy.run_path(str(Path(__file__).with_name('test_eiem_effect_objects_blender.py')), run_name='__main__')

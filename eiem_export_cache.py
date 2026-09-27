"""Session-scoped binary resource cache for the EIEM Blender exporter."""

import hashlib
import shutil
import tempfile
from pathlib import Path


class ResourceCache:
    """Materialize unchanged resource files from a session-local cache.

    The cache is keyed by the destination Mod directory, but its files live in
    the operating system temp directory and never enter the exported package.
    Callers own the resource token and decide when an entry is invalid.
    """

    def __init__(self, destination):
        self.destination = Path(destination).resolve()
        digest = hashlib.sha256(
            str(self.destination).casefold().encode("utf-8")).hexdigest()
        self.root = Path(tempfile.gettempdir()) / "EIEM-Blender-Export-Cache" / digest
        self.files = self.root / "files"
        self.entries = {}
        self.metadata = {}
        self.active_paths = set()
        self.hits = 0
        self.misses = 0

    def begin_export(self):
        """Start a package sync and forget the previous resource manifest."""
        self.active_paths.clear()

    def materialize(self, relative_path, token, staging_path, producer):
        relative = str(relative_path).replace("\\", "/")
        self.active_paths.add(relative)
        cache_path = self.files / (
            hashlib.sha256(relative.encode("utf-8")).hexdigest() + ".bin")
        if self.entries.get(relative) == token and cache_path.is_file():
            destination_path = self.destination / relative
            # A cache hit does not need to be copied twice.  The existing
            # package file is already the desired bytes; only materialize the
            # cached file when the destination was removed or is incomplete.
            destination_complete = False
            if destination_path.is_file():
                try:
                    destination_complete = (
                        destination_path.stat().st_size == cache_path.stat().st_size)
                except OSError:
                    destination_complete = False
            if not destination_complete:
                staging_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(cache_path, staging_path)
            self.hits += 1
            return True
        staging_path.parent.mkdir(parents=True, exist_ok=True)
        producer(staging_path)
        self.files.mkdir(parents=True, exist_ok=True)
        shutil.copy2(staging_path, cache_path)
        self.entries[relative] = token
        self.misses += 1
        return False

    def prune_destination(self):
        """Remove stale files from generated resource directories.

        ``active_paths`` is populated by ``materialize`` during the current
        export.  Keeping this operation here makes the incremental sync and
        the cache's resource manifest one unit, while leaving ``mod.ini`` and
        user files outside the generated resource directories untouched.
        """
        generated_dirs = {"meshes", "materials", "textures", "skeletons", "physics"}
        for directory in generated_dirs:
            base = self.destination / directory
            if not base.is_dir():
                continue
            for path in sorted(base.rglob("*"), reverse=True):
                if path.is_file():
                    relative = path.relative_to(self.destination).as_posix()
                    if relative not in self.active_paths:
                        path.unlink()
                elif path.is_dir():
                    try:
                        path.rmdir()
                    except OSError:
                        pass
            # ``Path.rglob`` does not yield its root.  Remove the generated
            # top-level directory too when the current export no longer owns
            # any resource in it.
            try:
                base.rmdir()
            except OSError:
                pass

    def prune_cache(self):
        """Drop cached binaries for resources removed from this package."""
        active_cache_names = {
            hashlib.sha256(path.encode("utf-8")).hexdigest() + ".bin"
            for path in self.active_paths
        }
        if self.files.is_dir():
            for path in self.files.glob("*.bin"):
                if path.name not in active_cache_names:
                    try:
                        path.unlink()
                    except OSError:
                        pass
        self.entries = {
            relative: token for relative, token in self.entries.items()
            if relative in self.active_paths
        }
        self.metadata = {
            relative: value for relative, value in self.metadata.items()
            if relative in self.active_paths
        }

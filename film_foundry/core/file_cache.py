"""Small revision-based file caches; no content hashes or platform/UI state."""
from collections import OrderedDict
from copy import deepcopy
import os
from pathlib import Path
from threading import RLock


def file_revision(path):
    path = Path(path).resolve()
    stat = path.stat()
    return (os.path.normcase(str(path)), stat.st_mtime_ns, stat.st_size,
            stat.st_ctime_ns, stat.st_ino)


class FileRevisionCache:
    """Bounded LRU; failed or concurrently replaced reads are never cached.

    Values are private, and callers receive editable independent copies.
    Namespace separates loaders that interpret the same file differently.
    """
    def __init__(self, capacity=128):
        self.capacity = capacity
        self._values = OrderedDict()
        self._lock = RLock()

    def clear(self):
        with self._lock:
            self._values.clear()

    def get(self, path, loader, *, namespace=None):
        with self._lock:
            for _ in range(2):
                revision = file_revision(path)
                key = (namespace, revision[0])
                found = self._values.get(key)
                if found is not None and found[0] == revision:
                    self._values.move_to_end(key)
                    return deepcopy(found[1])
                value = loader(Path(revision[0]))
                if file_revision(path) != revision:
                    continue
                self._values[key] = (revision, value)
                self._values.move_to_end(key)
                while len(self._values) > self.capacity:
                    self._values.popitem(last=False)
                return deepcopy(value)
        raise OSError(f"File changed while reading: {path}")

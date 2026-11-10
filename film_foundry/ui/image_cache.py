"""Shared, byte-bounded decoded previews, independent of catalogue identities."""
from collections import OrderedDict
from concurrent.futures import Future
from threading import RLock

from film_foundry.core.file_cache import file_revision
from film_foundry.ui.qt import QtGui


class ImagePreviewCache:
    def __init__(self, max_bytes=64 * 1024 * 1024, max_entries=512):
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self._images = OrderedDict()
        self._pending = {}
        self._bytes = 0
        self._lock = RLock()

    @staticmethod
    def key(path, width=192, height=128):
        try:
            return (file_revision(path), int(width), int(height))
        except OSError:
            return None

    def clear(self):
        with self._lock:
            self._images.clear()
            self._bytes = 0

    def statistics(self):
        with self._lock:
            return {'bytes':self._bytes,'entries':len(self._images),'limit':self.max_bytes}

    def peek(self, key):
        with self._lock:
            if key not in self._images:
                return None
            self._images.move_to_end(key)
            return QtGui.QImage(self._images[key])

    def read(self, path, width, height, decode):
        key = self.key(path, width, height)
        if key is None:
            return QtGui.QImage()
        with self._lock:
            cached = self.peek(key)
            if cached is not None:
                return cached
            future = self._pending.get(key)
            owner = future is None
            if owner:
                future = self._pending[key] = Future()
        if not owner:
            return QtGui.QImage(future.result())
        try:
            image = decode(path, width, height)
            with self._lock:
                if self.key(path, width, height) == key and image.sizeInBytes() <= self.max_bytes:
                    # Retain only the current revision at this size.
                    for old in list(self._images):
                        if old[0][0] == key[0][0] and old[1:] == key[1:]:
                            self._bytes -= self._images.pop(old).sizeInBytes()
                    self._images[key] = QtGui.QImage(image)
                    self._bytes += image.sizeInBytes()
                    while self._bytes > self.max_bytes or len(self._images) > self.max_entries:
                        _, expired = self._images.popitem(last=False)
                        self._bytes -= expired.sizeInBytes()
                future.set_result(image)
            return QtGui.QImage(image)
        except BaseException as exc:
            future.set_exception(exc)
            raise
        finally:
            with self._lock:
                self._pending.pop(key, None)


preview_images = ImagePreviewCache()

"""Bounded, ordered raw-DEFLATE blocks inside the standard ZIP writer.

Z_FULL_FLUSH terminates a byte-aligned block and resets its dictionary, so
independently compressed blocks can be concatenated before one final block.
ZIP headers, sizes, CRC and ZIP64 remain owned by Python's zipfile writer.
See https://zlib.net/manual.html (deflate / Z_FULL_FLUSH).
"""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import os
from threading import Lock
import zlib

from .execution_topology import reference_execution_enabled

BLOCK_BYTES = 4 * 1024 * 1024
MIN_ARRAY_BYTES = 32 * 1024 * 1024
_lease = Lock()


class CompressionFailure(RuntimeError):
    """Optional compressor failed; retry the whole atomic archive serially."""


def _block(data, level, strategy=None):
    strategy = _strategy(data, level) if strategy is None else strategy
    compressor = zlib.compressobj(level, zlib.DEFLATED, -15, 8, strategy)
    return compressor.compress(data) + compressor.flush(zlib.Z_FULL_FLUSH)


def _strategy(data, level):
    """Avoid costly LZ matching when sampled byte statistics compress better.

    No byte shuffle or custom container: both choices are ordinary DEFLATE.
    Sampling only selects transport; a poor estimate can affect size, never
    decoded values. Structured/constant blocks retain dictionary compression.
    """
    if (level != 1 or len(data) < 256*1024
            or os.environ.get('FILM_FOUNDRY_ADAPTIVE_DEFLATE') == '0'):
        return zlib.Z_DEFAULT_STRATEGY
    span = 32768
    sample = b''.join(data[start:start+span] for start in
                     (0, (len(data)-span)//2, len(data)-span))
    def size(strategy):
        codec = zlib.compressobj(level, zlib.DEFLATED, -15, 8, strategy)
        return len(codec.compress(sample)) + len(codec.flush())
    normal = size(zlib.Z_DEFAULT_STRATEGY)
    if normal > len(sample)*.65 and size(zlib.Z_HUFFMAN_ONLY) <= normal*.995:
        return zlib.Z_HUFFMAN_ONLY
    return zlib.Z_DEFAULT_STRATEGY


class ParallelDeflate:
    def __init__(self, pool, level, workers=2):
        self.pool, self.level = pool, level
        self.limit = workers
        self.pending = deque()
        self.failed = False
        self.strategy = None

    def _take(self):
        try:
            return self.pending.popleft().result()
        except Exception as exc:
            self.failed = True
            for future in self.pending:
                future.cancel()
            self.pending.clear()
            raise CompressionFailure('Parallel NPZ compression failed') from exc

    def compress(self, data):
        output = []
        view = memoryview(data).cast('B')
        # One representative sample per array instead of repeating two trial
        # encodes for every 4 MiB block. This selects standard ZIP transport
        # only; heterogeneous arrays may compress less but never lose values.
        if self.strategy is None and len(view) >= 256*1024:
            self.strategy = _strategy(view, self.level)
        for start in range(0, len(view), BLOCK_BYTES):
            # Input ownership and queued results stay bounded by the budget.
            if len(self.pending) == self.limit:
                output.append(self._take())
            try:
                self.pending.append(self.pool.submit(_block, bytes(view[start:start+BLOCK_BYTES]), self.level, self.strategy))
            except Exception as exc:
                self.failed = True
                raise CompressionFailure('Unable to schedule NPZ compression') from exc
        return b''.join(output)

    def flush(self):
        if self.failed:
            return b''  # An earlier exception will discard this temporary ZIP.
        output = [self._take() for _ in range(len(self.pending))]
        output.append(zlib.compressobj(self.level, zlib.DEFLATED, -15).flush())
        return b''.join(output)


@contextmanager
def compression_pool(enabled, workers=2):
    workers = max(1, min(int(workers), 4, os.cpu_count() or 1))
    acquired = False
    if (enabled and os.environ.get('FILM_FOUNDRY_PARALLEL_NPZ') != '0'
            and not reference_execution_enabled() and workers > 1):
        acquired = _lease.acquire(blocking=False)
    if not acquired:
        yield None
        return
    try:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='npz-deflate') as pool:
            yield pool
    finally:
        _lease.release()


def attach_compressor(member, pool, level, workers=2):
    # This optional hook is deliberately local to a fresh writer. Unsupported
    # zipfile implementations retain the normal serial compressor; no globals
    # or ZIP internals other than the compressor object are replaced.
    if pool is not None and getattr(member, '_file_size', None) == 0 and getattr(member, '_compressor', None) is not None:
        member._compressor = ParallelDeflate(pool, level, max(1, min(int(workers), 4, os.cpu_count() or 1)))

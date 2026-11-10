"""Bounded overlap of an immutable archive with its derived image exports.

The scope must end inside the caller's file transaction. It drains the writer
on both success and failure, so rollback can never race a late publication.
"""
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import copy_context
import threading

_lease = threading.Lock()


class _ExportSubmission:
    def __init__(self, pool, pending, limit):
        self.pool, self.pending, self.limit = pool, pending, limit

    def __call__(self, function, *args, **kwargs):
        # The archive plus a bounded number of independent image groups. Never enqueue
        # an unbounded batch, and fall back synchronously if no worker exists.
        if self.pool is None or len(self.pending) >= self.limit:
            return function(*args, **kwargs)
        try:
            future = self.pool.submit(copy_context().run, function, *args, **kwargs)
        except RuntimeError:
            return function(*args, **kwargs)
        self.pending.append(future)
        return future

    def image(self, function, *args, **kwargs):
        result = self(function, *args, **kwargs)
        if isinstance(result, Future):
            return result
        future = Future()
        future.set_result(result)
        return future


@contextmanager
def archive_writer(processing, *, overlap_images=False, image_jobs=1):
    from .execution_topology import reference_execution_enabled
    enabled = (processing.memory_budget_mb is None
               and processing.native_thread_limit != 1
               and not reference_execution_enabled())
    from .runtime_resources import resolve_native_thread_limit
    from .material_parallel import thread_count
    limit = 1 + min(2, max(0, int(image_jobs))) if (overlap_images and thread_count() >= 4
                  and resolve_native_thread_limit(processing.native_thread_limit) >= 4) else 1
    acquired = enabled and _lease.acquire(blocking=False)
    try:
        pool = ThreadPoolExecutor(max_workers=limit, thread_name_prefix='medium-export') if acquired else None
    except Exception:
        if acquired:
            _lease.release()
        pool = None
    pending = []

    try:
        yield _ExportSubmission(pool, pending, limit)
        for future in pending:
            future.result()
    finally:
        # Do not replace an image-write exception with a secondary archive
        # error. Normal completion above still propagates archive failures.
        if pool is not None:
            try:
                pool.shutdown(wait=True)
            finally:
                _lease.release()

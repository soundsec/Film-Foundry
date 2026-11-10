"""CPU row work for independent materials, with one process-wide parallel lease."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from contextlib import contextmanager
from collections import deque
from threading import Lock
import os
from .execution_topology import reference_execution_enabled

_lease=Lock()
CHUNK_PIXELS=524288
PARALLEL_MIN_PIXELS=1048576


def configure(*,backend=None,threads=None):
    """Explicit process runtime policy; never serialized into material recipes."""
    if backend is not None and backend not in ('cpu','cuda','opencl','auto'):raise ValueError('Unknown compute backend')
    if threads is not None and (type(threads) is not int or threads<0):raise ValueError('Material threads must be nonnegative')
    if backend is not None:os.environ['FILM_FOUNDRY_COMPUTE']=backend
    if threads is not None:os.environ['FILM_FOUNDRY_MATERIAL_THREADS']=str(threads)


def thread_count():
    try:requested=int(os.environ.get('FILM_FOUNDRY_MATERIAL_THREADS','0'))
    except ValueError:requested=0
    available=max(1,os.cpu_count() or 1)
    return min(available,requested) if requested>0 else min(4,available)


def run_rows(height,width,work,*,chunk_pixels=CHUNK_PIXELS,workers=None):
    """work(start, stop) owns disjoint output rows; input arrays stay read-only.

    Concurrent windows/batches do not each create another full worker budget.
    The reference topology and workers=1 retain the serial execution path.
    """
    rows=max(1,chunk_pixels//width)
    spans=[(start,min(start+rows,height)) for start in range(0,height,rows)]
    limit=thread_count() if workers is None else max(1,min(int(workers),os.cpu_count() or 1))
    enabled=limit>1 and len(spans)>1 and height*width>=PARALLEL_MIN_PIXELS and not reference_execution_enabled()
    acquired=enabled and _lease.acquire(blocking=False)
    if not acquired:
        for start,stop in spans:work(start,stop)
        return
    try:
        with ThreadPoolExecutor(max_workers=min(limit,len(spans)),thread_name_prefix='material-cpu') as pool:
            futures=[pool.submit(copy_context().run,work,start,stop) for start,stop in spans]
            for future in futures:future.result()
    finally:_lease.release()


@contextmanager
def ordered_results(items, work, *, workers=1):
    """Bound in-flight private results and consume them in input order.

    Shares the material worker lease. On any producer/consumer failure, drain
    active work before leaving the caller's transaction. Numerical reductions
    belong to the ordered consumer, never to worker completion callbacks.
    """
    items=iter(items)
    limit=min(max(1,int(workers)),thread_count())
    acquired=limit>1 and not reference_execution_enabled() and _lease.acquire(blocking=False)
    if not acquired:
        yield 1, (work(item) for item in items)
        return
    pool=None;pending=deque()
    try:
        try:pool=ThreadPoolExecutor(max_workers=limit,thread_name_prefix='film-pool')
        except (RuntimeError,OSError):
            yield 1, (work(item) for item in items)
            return
        def submit_next():
            try:item=next(items)
            except StopIteration:return
            pending.append(pool.submit(copy_context().run,work,item))
        for _ in range(limit):submit_next()
        def consume():
            while pending:
                value=pending.popleft().result()
                submit_next()
                yield value
        yield limit,consume()
    finally:
        try:
            for future in pending:future.cancel()
            if pool is not None:pool.shutdown(wait=True,cancel_futures=True)
        finally:_lease.release()

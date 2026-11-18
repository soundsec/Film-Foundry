"""Optional read-only observations scoped to one formation or scan request."""
from contextlib import contextmanager
from contextvars import ContextVar

observer = ContextVar('formation_observer', default=None)
exposure_row = ContextVar('exposure_row', default=0)


def begin_exposure(shape):
    sink = observer.get()
    if sink is not None:
        sink.begin_exposure(shape[:2])


def observe_tile_rows(work):
    if observer.get() is None:
        return work
    def wrapped(span):
        token = exposure_row.set(span[0])
        try:
            return work(span)
        finally:
            exposure_row.reset(token)
    return wrapped


@contextmanager
def observe(observer_for_request):
    token = observer.set(observer_for_request)
    try:
        yield
    finally:
        observer.reset(token)

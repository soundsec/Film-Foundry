"""Optional work checkpoints inherited by numerical row workers."""
from contextlib import contextmanager
from contextvars import ContextVar

_check = ContextVar('computation_cancel_check', default=None)


@contextmanager
def cancellation_scope(check):
    token = _check.set(check)
    try:
        yield
    finally:
        _check.reset(token)


def checkpoint():
    check = _check.get()
    if check is not None:
        check()

"""Disposable material-master jobs, separate from the published medium contract."""
from __future__ import annotations

from contextlib import contextmanager
import errno
import os
from pathlib import Path
import re
import shutil
import threading
import time
from uuid import uuid4

from film_foundry.app.control import WorkflowControl

REGISTRY = '.ff-material-jobs'
OWNER = b'film_foundry_material_job_v1\n'
RESERVE_BYTES = 16 * 1024 * 1024
_JOB_NAME = re.compile(r'job-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z')
_mutex = threading.RLock()
_active: set[Path] = set()


def check_space(path: Path) -> None:
    """Best-effort low-water check; never substitute reduced material output."""
    if shutil.disk_usage(path).free < RESERVE_BYTES:
        raise OSError(errno.ENOSPC, 'Material master stopped: less than 16 MiB free space.', str(path))


def _try_lock(stream) -> bool:
    stream.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.lockf(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB, 1)
        return True
    except OSError as exc:
        if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
            return False
        raise


def _unlock(stream) -> None:
    stream.seek(0)
    if os.name == 'nt':
        import msvcrt
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.lockf(stream.fileno(), fcntl.LOCK_UN, 1)


def _plain(path: Path) -> bool:
    # resolve() alone would hide a junction/symlink before checking its ownership.
    return not path.is_symlink() and not bool(getattr(path.lstat(), 'st_file_attributes', 0) & 0x400)


@contextmanager
def _guard(parent: Path, control: WorkflowControl):
    with _mutex:
        registry = parent / REGISTRY
        registry.mkdir(exist_ok=True)
        if not _plain(registry) or not registry.is_dir():
            raise ValueError('Invalid material job registry.')
        lock_path = registry / 'guard.lock'
        if lock_path.exists() and not _plain(lock_path):
            raise ValueError('Invalid material job guard.')
        # Never unlink this stable lock inode, including when the registry is empty.
        with lock_path.open('a+b') as stream:
            while not _try_lock(stream):
                control.check_cancel()
                time.sleep(.025)
            try:
                control.check_cancel()
                yield registry
            finally:
                _unlock(stream)


def _recover(registry: Path) -> int:
    removed = 0
    for job in registry.iterdir():
        if not _JOB_NAME.fullmatch(job.name) or job in _active or not _plain(job) or not job.is_dir():
            continue
        owner, lease = job / 'owner', job / 'lease.lock'
        if not owner.is_file() or not lease.is_file() or not _plain(owner) or not _plain(lease):
            continue
        with owner.open('rb') as stream:
            if stream.read(len(OWNER) + 1) != OWNER:
                continue
        with lease.open('r+b') as stream:
            if not _try_lock(stream):
                continue
            _unlock(stream)
        # Registry guard excludes new workers and other collectors. No active
        # worker can acquire this UUID again. rmtree does not follow junctions.
        shutil.rmtree(job)
        removed += 1
    return removed


def recover_master_jobs(parent: Path, control: WorkflowControl | None = None) -> int:
    """Reclaim only marked, unlocked jobs immediately inside this output parent."""
    parent = Path(parent).resolve()
    if not parent.is_dir():
        return 0
    with _guard(parent, control or WorkflowControl()) as registry:
        return _recover(registry)


@contextmanager
def master_workspace(parent: Path, control: WorkflowControl):
    parent = Path(parent).resolve()
    parent.mkdir(parents=True, exist_ok=True)
    job = lease = None
    try:
        with _guard(parent, control) as registry:
            _recover(registry)
            check_space(parent)
            job = registry / ('job-' + str(uuid4()))
            job.mkdir()
            lease = (job / 'lease.lock').open('x+b')
            if not _try_lock(lease):
                raise RuntimeError('Cannot acquire new material job lease.')
            _active.add(job)
            with (job / 'owner').open('xb') as stream:
                stream.write(OWNER)
                stream.flush()
                os.fsync(stream.fileno())
            payload = job / 'payload'
            payload.mkdir()
        yield payload
    finally:
        if job is not None:
            # Cancellation must not prevent cleanup. Close the lease under the
            # registry guard, since Windows cannot delete an open locked file.
            with _guard(parent, WorkflowControl()):
                if lease is not None:
                    lease.close()
                _active.discard(job)
                if job.exists():
                    if job.parent != parent / REGISTRY or not _JOB_NAME.fullmatch(job.name) or not _plain(job):
                        raise RuntimeError('Invalid disposable material job.')
                    shutil.rmtree(job)

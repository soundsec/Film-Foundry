"""Transaction ownership/failure checks; no long clip or disk-filling workload."""
import errno
import os
from pathlib import Path
import subprocess
import sys

import pytest

from film_foundry.app.control import WorkflowControl
from film_foundry.app import master_workspace as storage
from film_foundry.app.movie_master import expose_movie_frames, read_master
from film_foundry.tests.test_movie_master import frames, recipe


def test_live_process_retained_and_killed_job_reclaimed(tmp_path):
    script = """
import sys, time
from pathlib import Path
from film_foundry.app.master_workspace import master_workspace
from film_foundry.app.control import WorkflowControl
with master_workspace(Path(sys.argv[1]),WorkflowControl()) as payload:
    (payload/'partial').write_text('incomplete')
    print(payload,flush=True)
    time.sleep(60)
"""
    child = subprocess.Popen([sys.executable,'-c',script,str(tmp_path)],stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE,text=True)
    try:
        payload = Path(child.stdout.readline().strip())
        assert payload.is_dir(), child.stderr.read() if child.poll() is not None else 'child failed to start'
        assert storage.recover_master_jobs(tmp_path) == 0
        with storage.master_workspace(tmp_path,WorkflowControl()) as second:
            assert second != payload and (payload/'partial').is_file()
        child.kill(); child.wait(timeout=10)
        assert payload.is_dir()
        assert storage.recover_master_jobs(tmp_path) == 1
        assert not payload.exists()
    finally:
        if child.poll() is None: child.kill(); child.wait(timeout=10)
        child.stdout.close();child.stderr.close()


def test_recovery_ignores_unknown_and_same_process_live_jobs(tmp_path):
    unknown = tmp_path/storage.REGISTRY/'job-00000000-0000-0000-0000-000000000000'
    unknown.mkdir(parents=True);(unknown/'precious').write_text('unknown')
    old = tmp_path/'.ff-master-old';old.mkdir();(old/'precious').write_text('old')
    complete = expose_movie_frames(frames(),tmp_path/'complete',recipe())
    def directory_link(link, target):
        if os.name == 'nt':
            quoted = lambda path: "'" + str(path).replace("'", "''") + "'"
            subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',
                'New-Item -ItemType Junction -Path '+quoted(link)+' -Target '+quoted(target)+' | Out-Null'],
                check=True,capture_output=True,text=True)
        else:
            link.symlink_to(target,target_is_directory=True)
    escaped = tmp_path/storage.REGISTRY/'job-11111111-1111-1111-1111-111111111111'
    protected = tmp_path/'protected';protected.mkdir()
    (protected/'owner').write_bytes(storage.OWNER)
    (protected/'lease.lock').touch()
    (protected/'precious').write_text('outside task')
    directory_link(escaped,protected)
    with storage.master_workspace(tmp_path,WorkflowControl()) as live:
        directory_link(live/'outside',complete)
        assert storage.recover_master_jobs(tmp_path) == 0
        assert live.is_dir() and unknown.is_dir() and old.is_dir()
    assert read_master(complete)[1]['stage'] == 'exposed_latent'
    assert (protected/'precious').read_text() == 'outside task'


def test_low_space_before_and_after_partial_frame_never_publishes(tmp_path,monkeypatch):
    source = expose_movie_frames(frames(),tmp_path/'source',recipe())
    _,document = read_master(source)
    before = [(source/f['path']).read_bytes() for f in document['frames']]
    from collections import namedtuple
    Usage = namedtuple('Usage','total used free')
    free = storage.RESERVE_BYTES - 1
    monkeypatch.setattr(storage.shutil,'disk_usage',lambda _:Usage(1<<30,0,free))
    with pytest.raises(OSError) as exc:
        expose_movie_frames(frames(),tmp_path/'empty',recipe())
    assert exc.value.errno == errno.ENOSPC and not (tmp_path/'empty').exists()
    free = 1<<30
    def become_full():
        nonlocal free
        yield next(frames())
        free = 0
        yield list(frames())[1]
    with pytest.raises(OSError):
        expose_movie_frames(become_full(),tmp_path/'partial',recipe())
    assert not (tmp_path/'partial').exists()
    assert [(source/f['path']).read_bytes() for f in document['frames']] == before
    assert [p.name for p in (tmp_path/storage.REGISTRY).iterdir()] == ['guard.lock']

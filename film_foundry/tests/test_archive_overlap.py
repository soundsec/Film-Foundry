import threading
from types import SimpleNamespace
import numpy as np
import pytest
from film_foundry.core.atomic_io import atomic_path_set,atomic_savez
from film_foundry.core.export_jobs import archive_writer


def test_pending_writer_finishes_before_bundle_rollback(tmp_path, monkeypatch):
    monkeypatch.setenv('FILM_FOUNDRY_MATERIAL_THREADS','4')
    monkeypatch.setattr('os.cpu_count',lambda:4)
    target=tmp_path/'archive.npz';target.write_bytes(b'previous')
    companion=tmp_path/'image.bin';companion.write_bytes(b'previous image')
    entered=threading.Event();release=threading.Event();finished=threading.Event()
    image_entered=threading.Event();image_finished=threading.Event()
    preview=tmp_path/'preview.bin';preview.write_bytes(b'previous preview')
    preview_entered=threading.Event();preview_finished=threading.Event()
    def write():
        entered.set();assert release.wait(5)
        try:atomic_savez(target,values=np.arange(30))
        finally:finished.set()
    def write_image():
        image_entered.set();assert release.wait(5)
        try:companion.write_bytes(b'new image')
        finally:image_finished.set()
    def write_preview():
        preview_entered.set();assert release.wait(5)
        try:preview.write_bytes(b'new preview')
        finally:preview_finished.set()
    with pytest.raises(RuntimeError,match='preview failed'):
        with atomic_path_set((target,companion,preview)):
            with archive_writer(SimpleNamespace(memory_budget_mb=None,native_thread_limit=4),overlap_images=True,image_jobs=2) as submit:
                submit(write);assert entered.wait(5)
                submit.image(write_image);assert image_entered.wait(5)
                submit.image(write_preview);assert preview_entered.wait(5)
                release.set();raise RuntimeError('preview failed')
    assert finished.is_set() and image_finished.is_set()
    assert target.read_bytes()==b'previous'
    assert companion.read_bytes()==b'previous image'
    assert preview_finished.is_set() and preview.read_bytes()==b'previous preview'


def test_archive_failure_is_not_reported_as_success_and_serial_fallback(tmp_path):
    target=tmp_path/'archive.npz';target.write_bytes(b'previous')
    def fail():raise OSError('archive failed')
    with pytest.raises(OSError,match='archive failed'):
        with atomic_path_set((target,)):
            with archive_writer(SimpleNamespace(memory_budget_mb=None,native_thread_limit=4)) as submit:submit(fail)
    assert target.read_bytes()==b'previous'
    owner=threading.get_ident();calls=[]
    for settings in (SimpleNamespace(memory_budget_mb=1024,native_thread_limit=4),SimpleNamespace(memory_budget_mb=None,native_thread_limit=1)):
        with archive_writer(settings,overlap_images=True) as submit:
            submit(lambda:calls.append(threading.get_ident()))
            assert submit.image(threading.get_ident).result()==owner
    assert calls==[owner,owner]


def test_image_error_propagates_and_writer_lease_is_reusable(monkeypatch):
    monkeypatch.setenv('FILM_FOUNDRY_MATERIAL_THREADS','4')
    monkeypatch.setattr('os.cpu_count',lambda:4)
    settings=SimpleNamespace(memory_budget_mb=None,native_thread_limit=4)
    def fail():raise OSError('TIFF failed')
    with pytest.raises(OSError,match='TIFF failed'):
        with archive_writer(settings,overlap_images=True) as submit:
            submit(lambda:None);submit.image(fail).result()
    with archive_writer(settings,overlap_images=True) as submit:
        assert submit.image(lambda:{'saved':True}).result()=={'saved':True}

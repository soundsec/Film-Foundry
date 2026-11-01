from fractions import Fraction
from pathlib import Path
import copy
import json

import numpy as np
import pytest

from film_foundry.app.control import CancelFlag, WorkflowControl, WorkflowCancelled
from film_foundry.app.movie_ingest import VideoFrameInput
from film_foundry.app.movie_master import (expose_movie_frames,import_video_master,read_master,
    develop_movie_master,observe_master_frame)
from film_foundry.contracts.movie_master import normalize_master
from film_foundry.core.latent_io import load_exposed_latent
from film_foundry.core.negative_io import load_developed_medium_for_scan
from film_foundry.model.config import DarkroomConfig


def recipe():
    config = DarkroomConfig(enable_mtf=False,enable_halation=False,enable_grain=False,random_seed=42)
    return config


def frames():
    for pts in (80,120,200):
        yield VideoFrameInput(np.full((13,17,3),.4,np.float32),pts,1,1000,40)


def test_material_timeline_repeated_development_frozen_material_and_readonly_observation(tmp_path):
    config = recipe()
    latent_path = expose_movie_frames(frames(),tmp_path/'latent',config)
    root,source = read_master(latent_path)
    before = [(root/item['path']).read_bytes() for item in source['frames']]
    first_state,first_meta = load_exposed_latent(root/source['frames'][0]['path'])
    assert first_meta['consumed'] is False and first_meta['frame_identity']['frame_index'] == 1
    assert first_state.developability.shape == (13,17,3)
    # A later request cannot replace the exposed stock with a new material.
    changed = recipe(); changed.film.film_base_density_rgb = (.9,.9,.9)
    changed.chemistry.time_min = 2
    a = develop_movie_master(root,tmp_path/'short',changed)
    changed.chemistry.time_min = 12
    b = develop_movie_master(root,tmp_path/'long',changed)
    _,a_doc = read_master(a); _,b_doc = read_master(b)
    assert a_doc['recipe']['film'] == source['recipe']['film']
    assert a_doc['source_master_id'] == source['master_id']
    assert a_doc['roll_id'] == source['roll_id'] == b_doc['roll_id']
    assert [f['pts'] for f in a_doc['frames']] == [80,120,200]
    aa = load_developed_medium_for_scan(a/a_doc['frames'][0]['path'])
    bb = load_developed_medium_for_scan(b/b_doc['frames'][0]['path'])
    assert not np.array_equal(aa.optical_density_rgb,bb.optical_density_rgb)
    optical_file = a/a_doc['frames'][0]['path']; optical_before = optical_file.read_bytes()
    observed = observe_master_frame(a,1)
    curve = recipe(); curve.scanner.tone_curve = ((0,.1),(1,.7))
    changed_scan = observe_master_frame(a,1,curve)
    assert not np.array_equal(observed.output_srgb,changed_scan.output_srgb)
    assert optical_file.read_bytes() == optical_before
    assert [(root/item['path']).read_bytes() for item in source['frames']] == before
    assert not any(root.rglob('*.mp4'))


def test_sequence_contract_rejects_traversal_duplicate_time_and_stage_mix(tmp_path):
    root = expose_movie_frames(frames(),tmp_path/'latent',recipe())
    _,document = read_master(root)
    for mutate in (
        lambda d: d['frames'][0].update(path='../other.darkroom_latent.npz'),
        lambda d: d['frames'][1].update(pts=80),
        lambda d: d.update(stage='captured_source'),
        lambda d: d['frames'][0].update(time_base_den=0),
        lambda d: d['frames'][0].update(path='frames/x.darkroom_negative.npz'),
    ):
        invalid = copy.deepcopy(document); mutate(invalid)
        with pytest.raises(ValueError): normalize_master(invalid)
    with pytest.raises(FileExistsError): expose_movie_frames(frames(),root,recipe())
    with pytest.raises(ValueError): observe_master_frame(root,1)


def test_cancelled_master_never_commits_or_changes_source(tmp_path):
    flag = CancelFlag()
    def cancelled():
        yield next(frames())
        flag.cancel()
        yield list(frames())[1]
    with pytest.raises(WorkflowCancelled):
        expose_movie_frames(cancelled(),tmp_path/'cancelled',recipe(),
            control=WorkflowControl(cancel_requested=flag.requested))
    assert not (tmp_path/'cancelled').exists()
    assert not list(tmp_path.glob('.ff-master-*'))
    source = expose_movie_frames(frames(),tmp_path/'source',recipe())
    root,document = read_master(source)
    before = [(root/f['path']).read_bytes() for f in document['frames']]
    flag = CancelFlag()
    stages = []
    def stop_on_second(event):
        stages.append(event.stage)
        if len(stages) == 2: flag.cancel()
    with pytest.raises(WorkflowCancelled):
        develop_movie_master(source,tmp_path/'cancelled-development',recipe(),
            control=WorkflowControl(cancel_requested=flag.requested,on_progress=stop_on_second))
    assert not (tmp_path/'cancelled-development').exists()
    assert [(root/f['path']).read_bytes() for f in document['frames']] == before
    def broken():
        yield next(frames())
        raise RuntimeError('decode failed')
    with pytest.raises(RuntimeError): expose_movie_frames(broken(),tmp_path/'broken',recipe())
    assert read_master(source)[1]['stage'] == 'exposed_latent'
    assert not list(tmp_path.glob('.ff-master-*'))


def test_real_vfr_video_import_preserves_pts_without_encoding(tmp_path):
    av = pytest.importorskip('av')
    path = tmp_path/'input.mp4'
    with av.open(str(path),'w') as container:
        stream = container.add_stream('libx264',rate=25)
        stream.width,stream.height,stream.pix_fmt = 32,24,'yuv420p'
        stream.time_base = stream.codec_context.time_base = Fraction(1,1000)
        stream.codec_context.color_primaries=1;stream.codec_context.color_trc=13;stream.codec_context.colorspace=1
        stream.options={'bf':'0','tune':'zerolatency'}
        for pts in (80,120,200):
            frame = av.VideoFrame.from_ndarray(np.full((24,32,3),100,np.uint8),format='rgb24')
            frame.pts,frame.time_base = pts,Fraction(1,1000)
            for packet in stream.encode(frame): container.mux(packet)
        for packet in stream.encode(): container.mux(packet)
    original = path.read_bytes()
    with av.open(str(path)) as container:
        expected = [(Fraction(f.pts)*f.time_base,f.duration) for f in container.decode(video=0)]
    root = import_video_master(path,tmp_path/'master',recipe())
    _,document = read_master(root)
    actual = [(Fraction(f['pts']*f['time_base_num'],f['time_base_den']),f['duration']) for f in document['frames']]
    assert actual == expected
    assert path.read_bytes() == original
    assert len(list((root/'frames').iterdir())) == 3
    assert all(p.suffix == '.npz' for p in (root/'frames').iterdir())
    from film_foundry.app.cli import main
    assert main(['movie-develop',str(root),str(tmp_path/'cli-optical'),'--seed','42']) == 0
    assert main(['movie-observe',str(tmp_path/'cli-optical'),str(tmp_path/'frame.png'),'--frame','2']) == 0
    assert (tmp_path/'frame.png').is_file()


def test_shared_mobile_manifest_and_desktop_window_dispatch(tmp_path,monkeypatch):
    import os
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from film_foundry.ui.qt import _application
    from film_foundry.ui.movie_master_dialog import MovieMasterDialog
    from film_foundry.app import movie_master
    from threading import Event
    import time
    fixture=Path(__file__).resolve().parents[2]/'android/interchange/src/test/resources/movie_master_v1_sample.json'
    assert normalize_master(json.loads(fixture.read_text()))['frames'][-1]['duration'] is None
    app=_application()
    started,release=Event(),Event()
    def controlled(source,output,config,*,control):
        started.set();assert release.wait(5);control.check_cancel()
    monkeypatch.setattr(movie_master,'import_video_master',controlled)
    dialog=MovieMasterDialog(recipe())
    dialog.source.setText('input.mp4');dialog.output.setText(str(tmp_path/'master'))
    dialog.run();assert started.wait(5)
    dialog.reject();release.set()
    deadline=time.monotonic()+5
    while dialog.running and time.monotonic() < deadline:
        app.processEvents();time.sleep(.005)
    assert not dialog.running
    assert '取消' in dialog.status.text() or 'Cancelled' in dialog.status.text()
    dialog.reject();dialog.deleteLater();app.processEvents()

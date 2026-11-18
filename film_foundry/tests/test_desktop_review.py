"""Second desktop review regression boundaries, using small deterministic inputs."""
from dataclasses import asdict
from pathlib import Path
from threading import Event, Thread
import numpy as np

from film_foundry.tests.test_desktop_usability import desktop, photos, wait


def test_insurance_generations_and_dismiss(desktop, tmp_path):
    owner = desktop
    store = owner.last_work.store
    store.generations = 5
    for number in range(7):
        store.write({'number': number})
    assert [r['work']['number'] for r in store.records()] == [6, 0]
    from film_foundry.core.atomic_io import atomic_write_json
    earlier=store.read()
    earlier['previous'][0]['saved_at']='2000-01-01T00:00:00+00:00'
    atomic_write_json(store.path,earlier)
    store.write({'number':7})
    assert [r['work']['number'] for r in store.records()]==[7,6,0]
    owner.last_work.unclean = True
    owner.open_library()
    owner._open_catalog_source(photos(tmp_path)[0],'film')
    owner.last_work.capture()
    wait(lambda:'film' in store.read()['work'])
    assert store.read(store.archives()[0])['work']=={'number':7}


def test_main_probe_and_matrix(desktop,tmp_path):
    from film_foundry.ui.trial_comparison import TrialComparison
    from film_foundry.ui.comparison_view import ComparisonView
    owner=desktop
    owner._open_catalog_source(photos(tmp_path)[0],'film')
    owner.film_probe.action.setChecked(True)
    wait(lambda:owner.film_probe.data is not None and not owner._preview_running)
    data=owner.film_probe.data
    assert data.count==data.expected_pixels and data.bins.sum(axis=1).tolist()==[data.count]*len(data.bins)
    assert np.isfinite(data.curves).all()
    height,width=data.density.shape[:2]
    np.testing.assert_array_equal(data.density_at(1,2,width,height),data.density[2,1])
    np.testing.assert_array_equal(data.exposure_at(1,2,width,height),data.log_exposure[2,1])
    patch=data.density[0:5,0:5].mean(axis=(0,1))
    np.testing.assert_allclose(data.density_at(2,2,width,height,5),patch)
    owner.film_probe.pins=[(.5,.5)]
    owner.film_probe.aperture.setCurrentIndex(1)
    assert 'D(RGB)' in owner.film_probe.pinned.text()
    owner.film_probe.action.setChecked(False)
    assert owner._preview_session._profile is None
    before=asdict(owner._config())
    dialog=owner._open_test_strip()
    dialog.matrix.setChecked(True);dialog.spacing.setCurrentIndex(dialog.spacing.findData('list'))
    dialog.values.setText('-1,0');dialog.times.setText('5,10');dialog.generate()
    wait(lambda:not dialog.busy)
    assert len(dialog.frames)==4,dialog.status.text()
    assert (dialog.grid.rowCount(),dialog.grid.columnCount())==(2,2)
    assert {dialog.second.itemData(i).path for i in range(dialog.second.count())} >= {
        ('chemistry','concentration'),('chemistry','temperature_c'),('look','print_contrast')}
    assert [v for v,c in dialog.variants]==[(-1.,5.),(0.,5.),(-1.,10.),(0.,10.)]
    linked=TrialComparison([(str(i),ComparisonView.pixmap(p)) for i,p in dialog.frames.items()],2,dialog)
    linked.show();linked.zoom=2;linked.sync()
    assert len(linked.views)==4
    from film_foundry.ui.qt import QtCore
    linked.set_region(QtCore.QRectF(.25,.25,.5,.5))
    for view in linked.views:
        assert view.item.pixmap().width()==view.picture.width()//2
        assert view.item.pixmap().height()==view.picture.height()//2
    linked.fit()
    assert all(v.item.pixmap().size()==v.picture.size() for v in linked.views)
    linked.close();dialog.reject()
    assert asdict(owner._config())==before


def test_budget_priority_and_cancelled_waiter():
    from film_foundry.app.preview_budget import PreviewBudget, Priority
    from film_foundry.app.control import WorkflowControl, WorkflowCancelled
    from film_foundry.core.computation_control import checkpoint
    budget=PreviewBudget();order=[];stop=Event()
    def low():
        try:
            budget.run(Priority.THUMBNAIL,lambda:order.append('bad'),check=WorkflowControl(cancel_requested=stop.is_set).check_cancel)
        except WorkflowCancelled:pass
    threads=[Thread(target=low),Thread(target=lambda:budget.run(Priority.STRIP,order.append,'strip')),
             Thread(target=lambda:budget.run(Priority.INTERACTIVE,order.append,'main'))]
    import time
    with budget.slot(Priority.INTERACTIVE):
        for th in threads:th.start()
        end=time.monotonic()+4
        while len(budget.waiting)!=3 and time.monotonic()<end:time.sleep(.01)
        assert len(budget.waiting)==3
        stop.set()
    for th in threads:th.join(4);assert not th.is_alive()
    assert order==['main','strip']
    from film_foundry.core.computation_control import cancellation_scope
    import pytest
    with cancellation_scope(WorkflowControl(cancel_requested=stop.is_set).check_cancel):
        with pytest.raises(WorkflowCancelled):checkpoint()


def test_cancelled_formation_and_probe_preserve_frame(tmp_path):
    from copy import deepcopy
    import pytest
    from film_foundry.app.control import WorkflowCancelled
    from film_foundry.app.preview_probe import PreviewProbe
    from film_foundry.app.preview_session import PreviewSession
    from film_foundry.core.computation_control import cancellation_scope
    from film_foundry.core.formation_observer import observe
    from film_foundry.model.config import DarkroomConfig

    path = Path(photos(tmp_path)[0])
    config = DarkroomConfig()
    config.seed_strategy = 'fixed'
    config.random_seed = 31
    session = PreviewSession()
    _, original, _ = session.render(path, config, long_edge=64)
    probe = PreviewProbe()
    with observe(probe):
        _, metered, _ = session.render(path, config, long_edge=64)
    np.testing.assert_array_equal(original, metered)
    assert probe.count == probe.expected_pixels and probe.log_exposure.shape[:2] == (16, 24)
    changed = deepcopy(config)
    changed.look.exposure_ev = .7
    calls = []
    def stop():
        calls.append(1)
        if len(calls) == 2:
            raise WorkflowCancelled()
    with cancellation_scope(stop), pytest.raises(WorkflowCancelled):
        session.render(path, changed, long_edge=64)
    retry = PreviewProbe()
    with observe(retry):
        _, recovered, _ = session.render(path, config, long_edge=64)
    np.testing.assert_array_equal(original, recovered)
    np.testing.assert_array_equal(probe.log_exposure, retry.log_exposure)


def test_stage_sources_are_reused(desktop, tmp_path, monkeypatch):
    from film_foundry.app import preview_session
    from film_foundry.core import latent_io
    from film_foundry.core.engine import expose_image_to_latent

    source = photos(tmp_path)[0]
    photo_reads, latent_reads = [], []
    def tracked(reader, calls):
        def load(path, *args, **kwargs):
            calls.append(path)
            return reader(path, *args, **kwargs)
        return load

    monkeypatch.setattr(preview_session, 'load_formation_image', tracked(preview_session.load_formation_image, photo_reads))
    owner = desktop
    owner._open_catalog_source(source, 'film')
    for endpoint in ('expose', 'develop', 'full'):
        owner.pipeline_mode.set(endpoint)
        owner._start_preview()
        wait(lambda:not owner._preview_running)
        assert owner.result_view.result is not None
    assert len(photo_reads)==1
    cfg = owner._config()
    latent = expose_image_to_latent(np.full((16,24,3), .35, np.float32), cfg)
    path = tmp_path/'test.darkroom_latent.npz'
    latent_io.save_exposed_latent(path,latent,identity=latent_io.latent_identity_from_film(cfg.film))
    monkeypatch.setattr(latent_io,'load_exposed_latent',tracked(latent_io.load_exposed_latent,latent_reads))
    cache = owner._preview_session
    copy_one = cache.latent_copy(path, 12)
    original = copy_one.halide.copy()
    copy_one.halide.fill(0)
    np.testing.assert_array_equal(cache.latent_copy(path,12).halide, original)
    assert len(latent_reads)==1


def test_factory_example_skips_formation_only_for_matching_version(tmp_path,monkeypatch):
    from film_foundry.app.control import WorkflowControl
    from film_foundry.app import selection_preview
    from film_foundry.app.preview_session import PreviewSession

    monkeypatch.setenv('FILM_FOUNDRY_UI_SETTINGS',str(tmp_path/'ui.json'))
    def unexpected(*args,**kwargs):
        raise AssertionError('formation requested')
    monkeypatch.setattr(PreviewSession,'render_target',unexpected)
    session=selection_preview.SelectionPreviewSession()
    record={'family':'film','key':'clear_modern_negative'}
    pixels=session.render(record,False,WorkflowControl())
    assert pixels.dtype==np.uint8 and pixels.shape==(240,240,3)
    monkeypatch.setattr(selection_preview,'__version__','version-changed')
    import pytest
    with pytest.raises(AssertionError,match='formation requested'):
        session.render(record,False,WorkflowControl())

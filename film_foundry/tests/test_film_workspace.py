"""Only portable film work, path identity and the changed restoration boundary."""
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import shutil
from threading import Event

import numpy as np
from PIL import Image
import pytest

from film_foundry.app import film_workspace as store
from film_foundry.app.api import WorkflowRequest, WorkflowResult, run_request
from film_foundry.app.source_seed import source_seed_override, offset_for
from film_foundry.app.workflow import resolved_seed_for
from film_foundry.model.config import DarkroomConfig
from film_foundry.model.session import session_payload
from film_foundry.tests.test_unified_workspace import panel, wait


def source(root, name='source.png', value=127):
    root.mkdir(parents=True, exist_ok=True); path = root / name
    pixels = np.full((19, 27, 3), value, np.uint8)
    pixels[:, 8:16, 1] = 205
    Image.fromarray(pixels).save(path)
    return path


def config():
    c = DarkroomConfig(); c.seed_strategy = 'path'; c.random_seed = 41
    c.chemistry.incident_identity_seed = 123
    c.enable_mtf = False; c.enable_halation = False
    c.output.save_scanner_raw = False; c.output.export_transparent_plate = False
    c.output.export_layer_pack = False; c.output.export_plate_set = False
    c.debug_output = False; c.comparison_grid = False
    return c


def payload(c=None, mode='full'):
    return session_payload(config=c or config(), pipeline_mode=mode,
        input_path='old-input', negative_path='old-medium', output_path='old-output',
        presets={'film': {'source': 'direct', 'path': 'missing-preset.json'}})


def test_moved_selection_keeps_bytes_order_config_and_separate_identities(tmp_path):
    paths = [source(tmp_path / 'a'), source(tmp_path / 'b', value=81)]
    work = store.snapshot_film_workspace(payload(), paths, active=1, layout='studio')
    package = tmp_path / 'original.fffilm'; store.save_film_workspace(package, work)
    destination = tmp_path / 'moved.fffilm'; shutil.move(package, destination)
    restored = store.load_film_workspace(destination)
    assert restored.active == 1 and restored.layout == 'studio'
    assert DarkroomConfig.from_dict(restored.session['config']) == DarkroomConfig.from_dict(work.session['config'])
    assert restored.session['presets'] == {}
    for old, new in zip(paths, restored.sources):
        assert old.read_bytes() == new.read_bytes()
        binding = offset_for(new, restored.session['path_seed_offsets'])
        for base in (0, 41, -3):
            c = config(); c.random_seed = base
            assert resolved_seed_for(old, c) == resolved_seed_for(new, c, path_seed_offset=binding)
            assert c.seed_strategy == 'path' and c.random_seed == base
        for strategy in ('fixed', 'random'):
            c.seed_strategy = strategy
            assert source_seed_override(c, binding) is None
        old.unlink()
    assert Path(restored.session['paths']['output']).parent == destination.parent
    assert not restored.session['ui_options']['scan_output_near_source']
    store.save_film_workspace(destination, restored)  # sources inside old target
    again = store.load_film_workspace(destination)
    assert again.source_names == restored.source_names
    assert [p.name for p in again.sources] == [p.name for p in restored.sources]


def test_copy_and_commit_failure_keep_previous_and_refuse_foreign_files(tmp_path, monkeypatch):
    work = store.snapshot_film_workspace(payload(), [source(tmp_path)])
    path = tmp_path / 'safe.fffilm'; store.save_film_workspace(path, work)
    before = (path / 'workspace.json').read_bytes()
    with monkeypatch.context() as patch:
        def fail(*args, **kwargs): raise OSError('copy failed')
        patch.setattr(store.shutil, 'copy2', fail)
        with pytest.raises(OSError, match='copy failed'): store.save_film_workspace(path, work)
    from film_foundry.core import atomic_io
    replace = atomic_io.os.replace
    with monkeypatch.context() as patch:
        def fail_commit(source, target):
            if Path(source).name.startswith('.ff-stage-') and target == path: raise OSError('publish failed')
            return replace(source, target)
        patch.setattr(atomic_io.os, 'replace', fail_commit)
        with pytest.raises(OSError, match='publish failed'): store.save_film_workspace(path, work)
    assert (path / 'workspace.json').read_bytes() == before
    assert not list(tmp_path.glob('.ff-*'))
    foreign = path / 'keep.txt'; foreign.write_text('keep')
    with pytest.raises(ValueError, match='unrelated'): store.save_film_workspace(path, work)
    assert foreign.read_text() == 'keep'
    foreign.unlink()
    malformed = json.loads(before); malformed['sources'][0]['file'] = '../outside.png'
    (path / 'workspace.json').write_text(json.dumps(malformed), encoding='utf-8')
    with pytest.raises(ValueError, match='filename'): store.load_film_workspace(path)


def test_moved_photo_and_latent_form_same_medium_and_scan_is_readonly(tmp_path):
    from film_foundry.core.negative_io import load_developed_negative_npz
    c = config(); original = source(tmp_path)
    baseline = run_request(WorkflowRequest('develop', original, tmp_path / 'original', c))
    package = tmp_path / 'photo.fffilm'
    store.save_film_workspace(package, store.snapshot_film_workspace(payload(c, 'develop'), [original]))
    moved = store.load_film_workspace(package); copied = moved.sources[0]
    result = run_request(WorkflowRequest('develop', copied, tmp_path / 'moved', c,
        path_seed_offset=offset_for(copied, moved.session['path_seed_offsets'])))
    a = load_developed_negative_npz(baseline.medium_path); b = load_developed_negative_npz(result.medium_path)
    np.testing.assert_array_equal(a.optical_density_rgb, b.optical_density_rgb)
    latent = tmp_path / 'captured.darkroom_latent.npz'
    run_request(WorkflowRequest('expose', original, latent, c))
    store.save_film_workspace(tmp_path / 'latent.fffilm', store.snapshot_film_workspace(payload(c, 'develop_latent'), [latent]))
    restored = store.load_film_workspace(tmp_path / 'latent.fffilm'); moved_latent = restored.sources[0]
    previous = moved_latent.read_bytes()
    a = run_request(WorkflowRequest('develop_latent', latent, tmp_path / 'latent-before', c))
    b = run_request(WorkflowRequest('develop_latent', moved_latent, tmp_path / 'latent-after', c,
        path_seed_offset=offset_for(moved_latent, restored.session['path_seed_offsets'])))
    np.testing.assert_array_equal(load_developed_negative_npz(a.medium_path).optical_density_rgb,
        load_developed_negative_npz(b.medium_path).optical_density_rgb)
    assert previous == moved_latent.read_bytes()
    store.save_film_workspace(tmp_path / 'scan.fffilm', store.snapshot_film_workspace(payload(c, 'scan'), [b.medium_path]))
    observation = store.load_film_workspace(tmp_path / 'scan.fffilm'); medium = observation.sources[0]
    previous = medium.read_bytes()
    before = run_request(WorkflowRequest('scan', b.medium_path, tmp_path, c)).extra['scanned']
    after = run_request(WorkflowRequest('scan', medium, tmp_path, c)).extra['scanned']
    np.testing.assert_array_equal(before.output_srgb, after.output_srgb)
    assert previous == medium.read_bytes()


def test_scanner_raw_carries_required_sidecar(tmp_path):
    raw = source(tmp_path, name='scan.scanner_raw.tiff')
    sidecar = raw.with_suffix('.tiff.json'); sidecar.write_text('{"border_width": 2}')
    path = tmp_path / 'raw.fffilm'
    store.save_film_workspace(path, store.snapshot_film_workspace(payload(mode='scan'), [raw]))
    restored = store.load_film_workspace(path)
    assert restored.sources[0].with_suffix('.tiff.json').read_bytes() == sidecar.read_bytes()


def test_batch_resume_retains_each_binding(tmp_path, monkeypatch):
    from film_foundry.app import workspace_jobs, replay_batch
    originals = [source(tmp_path / 'a'), source(tmp_path / 'b')]
    path = tmp_path / 'batch.fffilm'
    store.save_film_workspace(path, store.snapshot_film_workspace(payload(), originals))
    restored = store.load_film_workspace(path); captured = []; roots = []
    def fail(request):
        captured.append((str(request.input_path), request.path_seed_offset)); raise OSError('intentional item failure')
    monkeypatch.setattr(workspace_jobs, 'run_request', fail)
    failures = workspace_jobs.process_photos(restored.sources, tmp_path / 'runs', config(),
        path_seed_offsets=restored.session['path_seed_offsets'], on_run=roots.append)
    assert len(failures) == 2 and captured[0][1] != captured[1][1]
    plan = replay_batch.load_resume_plan(roots[0] / 'film_foundry_run.json')
    first = captured[:]; captured.clear()
    monkeypatch.setattr(replay_batch, 'run_request', fail)
    result = replay_batch.run_resume_batch(plan)
    assert len(result.failures) == 2 and captured == first


def test_desktop_background_restore_missing_presets_and_rollback(panel, tmp_path, monkeypatch):
    from film_foundry.ui.qt import QtWidgets
    monkeypatch.setattr(QtWidgets.QMessageBox, 'warning', lambda *args: None)
    paths = [source(tmp_path / 'a'), source(tmp_path / 'b', value=90)]
    panel._open_catalog_batch(list(map(str, paths)), 'film')
    panel._focus_selected_photo(str(paths[1]))
    panel.set_workspace_layout('studio', persist=False)
    panel.seed_strategy.set('path'); panel.random_seed.set('17')
    files = panel._film_workspace_files; path = tmp_path / 'ui.fffilm'
    before = panel._session_payload()['config']
    gate = Event(); real_save = store.save_film_workspace
    import film_foundry.ui.film_workspace_io as ui_files
    def held(*args, **kwargs): gate.wait(4); return real_save(*args, **kwargs)
    monkeypatch.setattr(ui_files, 'save_film_workspace', held)
    try:
        assert files.save_to(path) and panel._workspace_io_busy
        assert not panel._workspace_files.open_from(tmp_path / 'not-real.ffworkspace')
    finally:
        gate.set(); wait(lambda: not files.busy)
    assert files.last_error is None
    for p in paths: p.unlink()
    panel.film_preset.set('missing-film'); panel.develop_preset.set('missing-bath'); panel.scanner_preset.set('missing-scan')
    assert files.open_from(path); wait(lambda: not files.busy)
    assert files.last_error is None
    assert panel._workspace_layout == 'studio' and len(panel._selected_photos) == 2
    assert panel.input_path.get() == panel._selected_photos[1]
    assert panel._session_payload()['config'] == before
    # Saving after loading uses the complete recipe, despite missing preset files.
    assert files.save_to(path); wait(lambda: not files.busy); assert files.last_error is None
    stable = panel._session_payload(); selection = list(panel._selected_photos)
    setter = panel.set_workspace_layout; once = [True]
    def broken(mode, **kwargs):
        if once:
            once.pop(); raise ValueError('UI restoration failure')
        setter(mode, **kwargs)
    monkeypatch.setattr(panel, 'set_workspace_layout', broken)
    assert files.open_from(path); wait(lambda: not files.busy)
    assert 'UI restoration failure' in files.last_error
    assert panel._session_payload()['config'] == stable['config']
    assert panel._session_payload()['paths'] == stable['paths'] and panel._selected_photos == selection
    assert not panel._workspace_io_busy and panel.workspace_host.isEnabled()


def test_full_and_preview_binding_do_not_rewrite_effect_recipe(tmp_path):
    from film_foundry.app.preview_session import PreviewSession
    from film_foundry.contracts.effect_intent import effects_for_model
    from film_foundry.core.negative_io import load_developed_negative_npz
    c = config(); c.chemistry.incident_identity_seed = None
    c.chemistry.effect_intent = {'model': 'process_effect_intent_v3', 'enabled': True,
        'amounts': {key: .4 if key == 'uneven_development' else 0. for key in effects_for_model('process_effect_intent_v3')}}
    original = source(tmp_path); package = tmp_path / 'effects.fffilm'
    store.save_film_workspace(package, store.snapshot_film_workspace(payload(c), [original]))
    work = store.load_film_workspace(package); moved = work.sources[0]
    binding = offset_for(moved, work.session['path_seed_offsets']); before = asdict(c)
    a = run_request(WorkflowRequest('full', original, tmp_path / 'a.png', c))
    b = run_request(WorkflowRequest('full', moved, tmp_path / 'b.png', c, path_seed_offset=binding))
    np.testing.assert_array_equal(load_developed_negative_npz(a.medium_path).optical_density_rgb,
        load_developed_negative_npz(b.medium_path).optical_density_rgb)
    preview = PreviewSession()
    expected = preview.render(original, c, long_edge=48)[1]
    actual = preview.render(moved, c, long_edge=48, path_seed_offset=binding)[1]
    np.testing.assert_array_equal(expected, actual)
    assert preview.render(moved, c, long_edge=48, path_seed_offset=binding)[2]
    assert not preview.render(moved, c, long_edge=48, path_seed_offset=(binding + 1) % 2**32)[2]
    assert asdict(c) == before


def test_cli_package_execution_and_cancelled_plan_can_resume(tmp_path, monkeypatch, capsys):
    from film_foundry.app.cli import main
    from film_foundry.model.session import save_session, load_session
    from film_foundry.app import replay_batch
    from film_foundry.app.control import WorkflowCancelled
    paths = [source(tmp_path / 'a'), source(tmp_path / 'b')]
    session = tmp_path / 'session.json'; save_session(session, payload(mode='expose'))
    package = tmp_path / 'cli.fffilm'
    assert main(['film-work', 'save', '--session', str(session), '--sources', *map(str, paths), '--output', str(package)]) == 0
    capsys.readouterr()
    assert main(['film-work', 'show', str(package)]) == 0
    assert json.loads(capsys.readouterr().out)['sources'] == ['source.png', 'source.png']
    with monkeypatch.context() as patch:
        def cancelled(request): raise WorkflowCancelled('stop before first item')
        patch.setattr(replay_batch, 'run_request', cancelled)
        assert main(['film-work', 'run', str(package), '--output', str(tmp_path / 'results')]) == 1
    report = json.loads(capsys.readouterr().out)
    manifest = Path(report['manifest']); saved = load_session(manifest)
    assert len(saved['batch']['items']) == 2 and saved['batch']['items'][1]['status'] == 'pending'
    assert main(['film-work', 'resume', str(manifest)]) == 0
    assert json.loads(capsys.readouterr().out)['completed'] == 2
    assert main(['film-work', 'resume', str(manifest)]) == 0
    assert json.loads(capsys.readouterr().out)['reused'] == 2
    # The ordinary --session route carries the same binding too.
    work = store.load_film_workspace(package); standalone = tmp_path / 'relocated.json'
    save_session(standalone, work.session); captured = []
    import film_foundry.app.cli as cli
    def spy(request): captured.append(request); return WorkflowResult('expose', request.output_path)
    monkeypatch.setattr(cli, '_run_request', spy)
    assert main(['expose', str(work.sources[0]), str(tmp_path / 'unused'), '--session', str(standalone)]) == 0
    assert captured[0].path_seed_offset == offset_for(work.sources[0], work.session['path_seed_offsets'])

import copy

import numpy as np
import pytest
from PIL import Image

from film_foundry.app.api import WorkflowError, WorkflowRequest, run_request
from film_foundry.app import cli, workflow
from film_foundry.app.control import CancelFlag, WorkflowControl
from film_foundry.app.resume import find_complete_latent_development
from film_foundry.core import medium_export
from film_foundry.core.latent_io import load_exposed_latent
from film_foundry.model.config import DarkroomConfig


@pytest.fixture
def exposed(tmp_path):
    cfg = DarkroomConfig(enable_grain=False, enable_mtf=False, enable_halation=False)
    cfg.seed_strategy = 'fixed'
    cfg.random_seed = 31
    cfg.output.save_scanner_raw = False
    cfg.output.export_transparent_plate = False
    cfg.save_sidecar = True
    image = tmp_path/'source.png'
    Image.fromarray(np.linspace(15, 230, 6*8*3).astype(np.uint8).reshape(6, 8, 3)).save(image)
    latent = tmp_path/'source.darkroom_latent.npz'
    run_request(WorkflowRequest('expose', image, latent, cfg, frame_identity={'roll_id': 'retention', 'frame_index': 2}))
    return latent, cfg


def test_changed_bath_creates_an_independent_result_from_the_same_archive(exposed, tmp_path):
    latent, cfg = exposed
    original = latent.read_bytes()
    a = run_request(WorkflowRequest('develop_latent', latent, tmp_path/'negative', cfg))
    negative_bytes = a.output_path.read_bytes()
    changed = copy.deepcopy(cfg)
    changed.chemistry.program_key = 'color_reversal'
    b = run_request(WorkflowRequest('develop_latent', latent, tmp_path/'reversal', changed))
    assert a.polarity == 'negative' and b.polarity == 'positive'
    assert a.output_path.read_bytes() == negative_bytes
    assert a.extra['frame_identity'] == b.extra['frame_identity'] == {'roll_id': 'retention', 'frame_index': 2}
    assert latent.read_bytes() == original
    assert not load_exposed_latent(latent)[1]['consumed']


def test_export_failure_restores_prior_bundle_and_preserves_latent_for_retry(exposed, tmp_path, monkeypatch):
    latent, cfg = exposed
    target = tmp_path/'bundle'/'frame.darkroom_negative.npz'
    run_request(WorkflowRequest('develop_latent', latent, target, cfg))
    previous = {path.name: path.read_bytes() for path in target.parent.iterdir()}
    original = latent.read_bytes()
    def failed_preview(*args, **kwargs):
        # NPZ may still be compressing. The enclosing transaction must wait
        # for it before restoring the previous bundle and reporting failure.
        raise PermissionError('simulated preview write failure')
    with monkeypatch.context() as patch:
        patch.setattr(medium_export, 'save_image', failed_preview)
        with pytest.raises(WorkflowError) as caught:
            run_request(WorkflowRequest('develop_latent', latent, target, cfg))
        assert caught.value.code == 'io_permission'
    assert {path.name: path.read_bytes() for path in target.parent.iterdir()} == previous
    assert latent.read_bytes() == original
    retried = run_request(WorkflowRequest('develop_latent', latent, target, cfg))
    assert retried.output_path == target and latent.read_bytes() == original
    assert not list(target.parent.glob('.ff-*'))


def test_cancel_at_save_preserves_prior_bundle_and_source(exposed, tmp_path):
    latent, cfg = exposed
    target = tmp_path/'bundle'/'frame.darkroom_negative.npz'
    run_request(WorkflowRequest('develop_latent', latent, target, cfg))
    previous = {path.name: path.read_bytes() for path in target.parent.iterdir()}
    original = latent.read_bytes()
    flag = CancelFlag()
    control = WorkflowControl(cancel_requested=flag.requested,
        on_progress=lambda event: flag.cancel() if event.stage == 'saving' else None)
    with pytest.raises(WorkflowError) as caught:
        run_request(WorkflowRequest('develop_latent', latent, target, cfg, control=control))
    assert caught.value.code == 'cancelled'
    assert {path.name: path.read_bytes() for path in target.parent.iterdir()} == previous
    assert latent.read_bytes() == original


def test_source_overlap_uses_existing_export_guard_without_touching_archive(exposed):
    latent, cfg = exposed
    original = latent.read_bytes()
    with pytest.raises(WorkflowError, match='must not overwrite') as caught:
        run_request(WorkflowRequest('develop_latent', latent, latent, cfg))
    assert caught.value.code == 'invalid_request'
    assert latent.read_bytes() == original
    assert not load_exposed_latent(latent)[1]['consumed']


def test_cli_resume_reuses_actual_positive_output_and_explicit_run_still_reprocesses(exposed, tmp_path, monkeypatch, capsys):
    latent, cfg = exposed
    cfg.chemistry.program_key = 'color_reversal'
    root = tmp_path/'results'
    result = run_request(WorkflowRequest('develop_latent', latent, root, cfg))
    assert result.polarity == 'positive'
    # Current material naming suggests negative; its incomplete output must not
    # hide the complete positive result formed by the selected bath.
    wrong = workflow.planned_developed_path(latent, root, image_polarity='negative')
    wrong.write_bytes(b'truncated')
    assert find_complete_latent_development(latent, wrong, wrong) is None
    previous = result.output_path.read_bytes()
    previous_time = result.output_path.stat().st_mtime_ns
    calls = []
    real = cli._run_request
    def request(value):
        calls.append(value)
        return real(value)
    monkeypatch.setattr(cli, '_run_request', request)
    args = cli.build_parser().parse_args(['develop_latent', str(latent), str(root), '--resume', '--no-scanner-raw', '--no-transparent-plate'])
    assert cli._run_develop_latent(args, cfg) == 1
    assert calls == []
    assert result.output_path.read_bytes() == previous and result.output_path.stat().st_mtime_ns == previous_time
    assert str(result.output_path) in capsys.readouterr().out
    args.resume = False
    assert cli._run_develop_latent(args, cfg) == 1
    assert len(calls) == 1

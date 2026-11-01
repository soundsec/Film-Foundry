"""Headless batch coordination for exposed-latent workflow stages."""

import copy
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from time import perf_counter

from film_foundry.app.api import WorkflowRequest, run_request
from film_foundry.app.workflow import create_run_directory, iter_latent_files, iter_images, batch_item_record, finalize_batch_run
from film_foundry.app.control import WorkflowCancelled
from film_foundry.app.frame_batch import frame_identity_for_item
from film_foundry.app.incident_batch import prepare_incident_config
from film_foundry.core.io_utils import assert_unique_output_stems
from film_foundry.model.session import save_session,session_payload,SESSION_KIND,SESSION_VERSION


@dataclass(frozen=True)
class MaterialBatchResult:
    run_root: Path
    medium_root: Path
    completed: int
    failures: list
    cancelled: bool
    record_error: Exception | None


def run_material_batch(command, source, output_root, config, *, scaled_override=False, settings=None, control=None):
    if command not in {"expose", "develop_latent"}:
        raise ValueError("material batch requires expose or develop_latent")
    if control is not None:
        control.check_cancel()
    inputs = iter_images(Path(source)) if command == "expose" else iter_latent_files(Path(source))
    if not inputs:
        raise ValueError("No input images" if command == "expose" else "No undeveloped latent NPZ files")
    assert_unique_output_stems(inputs, command)
    first_identity=frame_identity_for_item(settings,0,len(inputs)) or {}
    config=prepare_incident_config(config,roll_id=first_identity.get("roll_id"),source=source)
    run_root, run_id = create_run_directory(Path(output_root), command)
    medium_root = run_root / ("latent" if command == "expose" else "negative")
    medium_root.mkdir()
    payload = session_payload(config=config,pipeline_mode=command,
        input_path=str(source) if command == "expose" else "",
        negative_path=str(source) if command == "develop_latent" else "",
        output_path=str(output_root),presets={})
    payload.update(copy.deepcopy(settings or {}))
    payload["kind"]=SESSION_KIND;payload["version"]=SESSION_VERSION
    payload["pipeline_mode"]=command
    options=dict(payload.get("ui_options") or {})
    options["run_as_preview"]=bool(scaled_override)
    payload["ui_options"]=options
    payload["config"]=asdict(config)
    payload["run"] = {"id": run_id, "directory": str(run_root), "actual_output": str(medium_root), "status": "running"}
    payload["run"]["inputs"] = [str(path) for path in inputs]
    manifest = run_root / "film_foundry_run.json"
    save_session(manifest, payload)
    start_time = datetime.now().isoformat(timespec="seconds")
    started = perf_counter()
    completed = 0
    failures = []
    items = []
    cancelled = False
    for item_index, path in enumerate(inputs):
        item_start = perf_counter()
        result_path = None
        try:
            if control is not None:
                control.check_cancel()
            result = run_request(WorkflowRequest(command, path, medium_root, config,
                scaled_override=scaled_override, control=control,
                frame_identity=frame_identity_for_item(settings, item_index, len(inputs))))
            result_path = result.output_path
            completed += 1
        except Exception as exc:
            failures.append((path, exc))
            items.append(batch_item_record(path, result_path, perf_counter()-item_start, exc))
            if getattr(exc, "code", None) == "cancelled" or isinstance(exc, WorkflowCancelled):
                cancelled = True
                break
        else:
            items.append(batch_item_record(path, result_path, perf_counter()-item_start))
    record_error = None
    try:
        finalize_batch_run(payload, operation=command, started_at=start_time,
            elapsed_seconds=perf_counter()-started, items=items)
        if cancelled:
            payload["batch"]["status"] = payload["run"]["status"] = "cancelled"
        save_session(manifest, payload)
    except Exception as exc:
        record_error = exc
    return MaterialBatchResult(run_root, medium_root, completed, failures, cancelled, record_error)

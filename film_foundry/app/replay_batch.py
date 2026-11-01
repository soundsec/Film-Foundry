"""Resume desktop runs through the headless API and complete artifacts.

The recorded recipe and destinations are authoritative. No partially formed
medium is checkpointed; artifact checks reuse the CLI's resume definitions.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import perf_counter

from film_foundry.app.api import WORKFLOW_COMMANDS, WorkflowRequest, run_request
from film_foundry.app.control import WorkflowCancelled
from film_foundry.app.frame_batch import frame_identity_for_item
from film_foundry.app.resume import complete_image_file, complete_latent_archive, complete_developed_archive, find_complete_latent_development
from film_foundry.app.workflow import (
    iter_images, iter_latent_files, iter_developed_medium_files, output_path_for,
    latent_path_for, planned_developed_path, batch_item_record, finalize_batch_run,
)
from film_foundry.core.io_utils import assert_unique_output_stems, save_image_bundle
from film_foundry.core.media import developed_medium_contract
from film_foundry.core.sidecar import final_positive_sidecar
from film_foundry.model.config import DarkroomConfig
from film_foundry.model.session import load_session, save_session


@dataclass(frozen=True)
class ResumeItem:
    input_path: Path
    output_path: Path


@dataclass(frozen=True)
class ResumePlan:
    manifest: Path
    command: str
    config: DarkroomConfig
    settings: dict
    output_root: Path
    items: tuple[ResumeItem, ...]
    scaled: bool


@dataclass(frozen=True)
class ResumeResult:
    completed: int
    reused: int
    failures: list
    cancelled: bool
    record_error: Exception | None


def load_resume_plan(path) -> ResumePlan:
    manifest = Path(path)
    payload = load_session(manifest)
    run = payload.get("run")
    if not isinstance(run, dict) or not run.get("actual_output"):
        raise ValueError("Select a batch run record, not a recipe or saved session")
    batch = payload.get("batch") or {}
    command = batch.get("operation", payload.get("pipeline_mode"))
    if command not in WORKFLOW_COMMANDS:
        raise ValueError(f"Unsupported batch operation: {command!r}")
    if not isinstance(payload.get("config"), dict):
        raise ValueError("The batch record has no recipe")
    config = DarkroomConfig.from_dict(payload["config"])
    paths = payload.get("paths", {})
    root = run.get("developed_medium") if command == "develop" else None
    output_root = Path(root or run["actual_output"])
    recorded = {str(item["input"]): item.get("output") for item in batch.get("items", [])}
    if "inputs" in run:
        inputs = run["inputs"]
        if not isinstance(inputs, list) or not all(isinstance(item, str) and item for item in inputs):
            raise ValueError("Invalid frozen batch input list")
        inputs = [Path(item) for item in inputs]
    else:
        # Older manifests only froze completed/failed items. Discover remaining
        # inputs as the CLI does, keeping the recorded prefix in original order.
        source = paths.get("input") if command in {"full", "develop", "expose"} else paths.get("developed_medium", paths.get("negative"))
        if not source:
            raise ValueError("The batch record has no input location")
        iterator = iter_images if command in {"full", "develop", "expose"} else iter_latent_files if command == "develop_latent" else iter_developed_medium_files
        inputs = [Path(item) for item in recorded]
        inputs += [item for item in iterator(Path(source)) if str(item) not in recorded]
    if not inputs:
        raise ValueError("No batch inputs were found")
    assert_unique_output_stems(inputs, command)
    if command in {"develop", "expose", "develop_latent"}:
        frame_identity_for_item(payload, 0, len(inputs))
    polarity = developed_medium_contract(config)["image_polarity"] if command in {"develop", "develop_latent"} else None
    items = []
    for source in inputs:
        known = recorded.get(str(source))
        if known:
            destination = Path(known)
        elif command in {"full", "scan"}:
            destination = output_path_for(source, output_root, config.output.format,
                run_suffix=run.get("id") if command == "scan" else None)
        elif command == "expose":
            destination = latent_path_for(source, output_root)
        else:
            destination = planned_developed_path(source, output_root, image_polarity=polarity)
        items.append(ResumeItem(source, destination))
    scaled = bool(payload.get("ui_options", {}).get("run_as_preview", config.processing.execution_mode == "scaled_fast"))
    return ResumePlan(manifest, command, config, payload, output_root, tuple(items), scaled)


def _complete_output(plan, item):
    if plan.command in {"full", "scan"}:
        return item.output_path if complete_image_file(item.output_path) else None
    if plan.command == "expose":
        return item.output_path if complete_latent_archive(item.output_path) else None
    if plan.command == "develop_latent":
        return find_complete_latent_development(item.input_path, plan.output_root, item.output_path)
    if complete_developed_archive(item.output_path):
        return item.output_path
    return None


def run_resume_batch(plan: ResumePlan, *, control=None) -> ResumeResult:
    if control is not None:
        control.check_cancel()
    payload = copy.deepcopy(plan.settings)
    payload["run"]["status"] = "running"
    payload["run"]["inputs"] = [str(item.input_path) for item in plan.items]
    save_session(plan.manifest, payload)
    started_at = datetime.now().isoformat(timespec="seconds")
    started = perf_counter()
    completed = reused = 0
    failures, records = [], []
    cancelled = False
    for index, item in enumerate(plan.items):
        item_started = perf_counter()
        destination = item.output_path
        try:
            if control is not None:
                control.check_cancel()
            complete = _complete_output(plan, item)
            if complete is not None:
                record = batch_item_record(item.input_path, complete, perf_counter()-item_started)
                record["reused"] = True
                records.append(record)
                reused += 1
                continue
            identity = frame_identity_for_item(payload, index, len(plan.items)) if plan.command in {"expose", "develop", "develop_latent"} else None
            request_output = plan.output_root if plan.command in {"develop", "develop_latent"} else destination
            result = run_request(WorkflowRequest(plan.command, item.input_path, request_output,
                plan.config, scaled_override=plan.scaled, frame_identity=identity, control=control))
            destination = result.output_path or destination
            if plan.command == "scan":
                scanned = result.extra["scanned"]
                source = result.source_path or item.input_path
                sidecar = final_positive_sidecar(negative_path=item.input_path,
                    scan_source=result.source_kind, scan_source_path=source,
                    output_path=destination, config=plan.config, scanned=scanned) if plan.config.save_sidecar else None
                if control is not None:
                    control.check_cancel()
                save_image_bundle(scanned.output_srgb, destination, plan.config.output,
                    sidecar, protected_paths=(item.input_path, source))
            completed += 1
        except Exception as exc:
            failures.append((item.input_path, exc))
            records.append(batch_item_record(item.input_path, destination, perf_counter()-item_started, exc))
            if getattr(exc, "code", None) == "cancelled" or isinstance(exc, WorkflowCancelled):
                cancelled = True
                break
        else:
            records.append(batch_item_record(item.input_path, destination, perf_counter()-item_started))
    finalize_batch_run(payload, operation=plan.command, started_at=started_at,
        elapsed_seconds=perf_counter()-started, items=records)
    payload["batch"]["reused"] = reused
    if cancelled:
        payload["run"]["status"] = payload["batch"]["status"] = "cancelled"
    record_error = None
    try:
        save_session(plan.manifest, payload)
    except Exception as exc:
        record_error = exc
    return ResumeResult(completed, reused, failures, cancelled, record_error)

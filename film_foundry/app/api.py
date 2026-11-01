"""Headless workflow request, result, and error types.

These wrap the existing application-layer functions. They do not own formation
formulas, scanner numerics, or UI strings. Leak/occlusion combinations and
scanner color live on DarkroomConfig and are therefore callable here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from film_foundry.app import workflow as _workflow
from film_foundry.app.incident_batch import prepare_incident_config
from film_foundry.app.control import (
    WorkflowCancelled,
    WorkflowControl,
)
from film_foundry.core.frame_identity import (
    FRAME_IDENTITY_KEY,
    frame_identity_from_mapping,
    normalize_frame_identity,
)
from film_foundry.core.states import DevelopedNegative
from film_foundry.model.config import DarkroomConfig


WORKFLOW_COMMANDS = ("full", "develop", "expose", "develop_latent", "scan")
WORKFLOW_ERROR_CODES = (
    "unsupported_command",
    "input_not_found",
    "io_permission",
    "io_error",
    "invalid_request",
    "runtime_failure",
    "cancelled",
)


class WorkflowError(ValueError):
    """Classified application-layer failure; code is stable, message is not."""

    def __init__(self, code: str, message: str, *, path: Path | None = None) -> None:
        super().__init__(message)
        self.code = str(code)
        self.path = path


def _workflow_error_from_exception(exc: BaseException, *, path: Path) -> WorkflowError:
    if isinstance(exc, WorkflowError):
        return exc
    if isinstance(exc, FileNotFoundError):
        return WorkflowError("input_not_found", str(exc), path=path)
    if isinstance(exc, PermissionError):
        return WorkflowError("io_permission", str(exc), path=path)
    if isinstance(exc, OSError):
        if not path.exists():
            return WorkflowError("input_not_found", str(exc), path=path)
        return WorkflowError("io_error", str(exc), path=path)
    if isinstance(exc, ValueError):
        return WorkflowError("invalid_request", str(exc), path=path)
    if isinstance(exc, RuntimeError):
        return WorkflowError("runtime_failure", str(exc), path=path)
    if isinstance(exc, WorkflowCancelled):
        return WorkflowError("cancelled", str(exc), path=path)
    return WorkflowError("runtime_failure", str(exc), path=path)


@dataclass(frozen=True)
class WorkflowRequest:
    command: str
    input_path: Path
    output_path: Path
    config: DarkroomConfig
    scaled_override: bool = False
    retain_stages: bool = False
    frame_identity: dict[str, Any] | None = None
    control: WorkflowControl | None = None


@dataclass(frozen=True)
class WorkflowResult:
    command: str
    output_path: Path | None
    medium_path: Path | None = None
    source_kind: str | None = None
    source_path: Path | None = None
    polarity: str | None = None
    extra: dict[str, object] = field(default_factory=dict)


def _report(control: WorkflowControl | None, command: str, stage: str) -> None:
    if control is None:
        return
    control.report(command, stage)


def run_request(request: WorkflowRequest) -> WorkflowResult:
    """Execute one frozen workflow request through the application layer."""
    command = str(request.command).strip().lower()
    input_path = Path(request.input_path)
    if command not in WORKFLOW_COMMANDS:
        raise WorkflowError(
            "unsupported_command",
            f"Unsupported workflow command: {request.command!r}",
            path=input_path,
        )
    try:
        if request.control is not None:
            request.control.check_cancel()
        _report(request.control, command, "starting")
        result = _execute_request(command, request)
        _report(request.control, command, "completed")
        return result
    except WorkflowError:
        raise
    except WorkflowCancelled as exc:
        raise WorkflowError("cancelled", str(exc), path=input_path) from exc
    except (OSError, ValueError, RuntimeError) as exc:
        raise _workflow_error_from_exception(exc, path=input_path) from exc


def _execute_request(command: str, request: WorkflowRequest) -> WorkflowResult:
    input_path = Path(request.input_path)
    output_path = Path(request.output_path)
    config = request.config
    if command != "scan" and request.frame_identity and request.frame_identity.get("roll_id"):
        config=prepare_incident_config(config,roll_id=request.frame_identity["roll_id"])
    if command == "full":
        written = _workflow.process_image_file(
            input_path,
            output_path,
            config,
            scaled_override=bool(request.scaled_override),
            control=request.control,
        )
        return WorkflowResult(command=command, output_path=Path(written))
    if command == "develop":
        medium, developed_path, paths = _workflow.develop_image_file(
            input_path,
            output_path,
            config,
            scaled_override=bool(request.scaled_override),
            frame_identity=request.frame_identity,
            control=request.control,
        )
        polarity = None
        if isinstance(medium, DevelopedNegative):
            polarity = str(medium.image_polarity)
        return WorkflowResult(
            command=command,
            output_path=Path(developed_path),
            medium_path=Path(developed_path),
            polarity=polarity,
            extra={
                "paths": paths,
                "medium": medium,
                FRAME_IDENTITY_KEY: frame_identity_from_mapping(
                    getattr(medium, "metadata", None)
                ),
            },
        )
    if command == "expose":
        written = _workflow.expose_image_file(
            input_path,
            output_path,
            config,
            scaled_override=bool(request.scaled_override),
            frame_identity=request.frame_identity,
            control=request.control,
        )
        return WorkflowResult(
            command=command,
            output_path=Path(written),
            extra={
                "stage": "exposed_latent",
                FRAME_IDENTITY_KEY: normalize_frame_identity(request.frame_identity),
            },
        )
    if command == "develop_latent":
        medium, developed_path, paths = _workflow.develop_latent_file(
            input_path,
            output_path,
            config,
            scaled_override=bool(request.scaled_override),
            frame_identity=request.frame_identity,
            control=request.control,
        )
        polarity = None
        if isinstance(medium, DevelopedNegative):
            polarity = str(medium.image_polarity)
        return WorkflowResult(
            command=command,
            output_path=Path(developed_path),
            medium_path=Path(developed_path),
            polarity=polarity,
            extra={
                "paths": paths,
                "medium": medium,
                FRAME_IDENTITY_KEY: frame_identity_from_mapping(
                    getattr(medium, "metadata", None)
                ),
            },
        )
    scanned, source_kind, source_path, preview = _workflow.observe_medium_file(
        input_path,
        config,
        retain_stages=bool(request.retain_stages),
        control=request.control,
    )
    return WorkflowResult(
        command=command,
        output_path=None,
        source_kind=str(source_kind),
        source_path=Path(source_path),
        polarity=str(getattr(scanned, "input_polarity", "") or "") or None,
        extra={
            "scanned": scanned,
            "preview": preview,
            FRAME_IDENTITY_KEY: frame_identity_from_mapping(
                getattr(scanned, "metadata", None)
            ),
        },
    )

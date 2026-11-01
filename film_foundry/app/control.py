"""Platform-agnostic workflow progress and cooperative cancel.

This is headless request control, not a medium JSON field and not a UI
callback type. Frontends may marshal reports onto their own threads.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import Event
from typing import Callable

from film_foundry.contracts.data_kernel import WORKFLOW_PROGRESS_STAGES


class WorkflowCancelled(Exception):
    """Cooperative cancel. Mapped to WorkflowError(code='cancelled')."""


@dataclass(frozen=True)
class WorkflowProgress:
    command: str
    stage: str
    fraction: float | None = None


class CancelFlag:
    """Thread-safe cancel latch for GUI/CLI callers."""

    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    def requested(self) -> bool:
        return self._event.is_set()


class WorkflowControl:
    """Optional cancel check and progress sink for one frozen request."""

    def __init__(
        self,
        *,
        cancel_requested: Callable[[], bool] | None = None,
        on_progress: Callable[[WorkflowProgress], None] | None = None,
    ) -> None:
        self._cancel_requested = cancel_requested
        self._on_progress = on_progress

    def check_cancel(self) -> None:
        if self._cancel_requested is not None and bool(self._cancel_requested()):
            raise WorkflowCancelled("workflow cancelled")

    def report(
        self,
        command: str,
        stage: str,
        fraction: float | None = None,
    ) -> None:
        if stage not in WORKFLOW_PROGRESS_STAGES:
            raise ValueError(f"Unsupported workflow progress stage: {stage!r}")
        if fraction is not None:
            if isinstance(fraction, bool) or not isinstance(fraction, (int, float)):
                raise ValueError("workflow progress fraction 必须是 0 到 1 的数字。")
            value = float(fraction)
            if value < 0.0 or value > 1.0:
                raise ValueError("workflow progress fraction 必须在 0 到 1 之间。")
            fraction = value
        self.check_cancel()
        if self._on_progress is not None:
            self._on_progress(
                WorkflowProgress(command=str(command), stage=str(stage), fraction=fraction)
            )
        self.check_cancel()

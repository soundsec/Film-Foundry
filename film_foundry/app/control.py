"""Platform-agnostic workflow progress and cooperative cancel.

This is headless request control, not a medium JSON field and not a UI
callback type. Frontends may marshal reports onto their own threads.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import Event
from typing import Callable

from film_foundry.contracts.data_kernel import WORKFLOW_PROGRESS_STAGES


_FILE_STAGES = {
    'full': ('loading', 'developing', 'scanning', 'saving'),
    'develop': ('loading', 'developing', 'saving'),
    'develop_latent': ('loading', 'developing', 'saving'),
    'expose': ('loading', 'exposing', 'saving'),
    'reexpose': ('loading', 'exposing', 'saving'),
    'scan': ('loading', 'scanning', 'saving'),
}


class WorkflowCancelled(Exception):
    """Cooperative cancel. Mapped to WorkflowError(code='cancelled')."""


@dataclass(frozen=True)
class WorkflowProgress:
    command: str
    stage: str
    fraction: float | None = None

    @property
    def step_position(self) -> str:
        """Stage position for a single-file export. Fractions belong to that stage."""
        stages = _FILE_STAGES.get(self.command, ())
        if self.stage in stages:
            return f'{stages.index(self.stage) + 1}/{len(stages)}'
        return ''

    def terminal_line(self) -> str:
        fields = [f'[{self.command}:{self.stage}]', self.step_position]
        if self.fraction is not None:
            fields.append(f'{self.fraction:.0%}')
        return ' '.join(filter(None, fields))


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

    def before_export(self) -> WorkflowControl:
        """Forward rendering progress; the enclosing file writer reports completion."""
        def forward(progress):
            if self._on_progress is not None and progress.stage != 'completed':
                self._on_progress(progress)
        return WorkflowControl(cancel_requested=self._cancel_requested, on_progress=forward)

    def output_saved(self, command: str) -> None:
        """A cancellation arriving during the final write applies to the next item."""
        if self._on_progress is not None:
            self._on_progress(WorkflowProgress(command, 'completed'))

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

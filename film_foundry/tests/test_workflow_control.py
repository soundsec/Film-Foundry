from __future__ import annotations

from pathlib import Path
import ast

import pytest

from film_foundry.app.control import (
    CancelFlag,
    WorkflowCancelled,
    WorkflowControl,
    WorkflowProgress,
)
from film_foundry.contracts import data_kernel


def test_control_module_does_not_import_ui_core_or_numpy():
    import film_foundry.app.control as control

    source = Path(control.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    forbidden = (
        "film_foundry.ui",
        "film_foundry.core",
        "numpy",
        "cv2",
        "PySide6",
    )
    assert not any(
        name == prefix or name.startswith(prefix + ".")
        for name in imported
        for prefix in forbidden
    )


def test_progress_catalog_matches_control_object():
    assert tuple(WorkflowProgress.__dataclass_fields__) == data_kernel.WORKFLOW_PROGRESS_FIELDS
    control = WorkflowControl()
    with pytest.raises(ValueError, match="Unsupported workflow progress stage"):
        control.report("expose", "halfway")
    with pytest.raises(ValueError, match="0 到 1"):
        control.report("expose", "loading", fraction=1.5)
    with pytest.raises(ValueError, match="0 到 1"):
        control.report("expose", "loading", fraction=True)


def test_cancel_flag_stops_before_progress_callback():
    flag = CancelFlag()
    seen: list[str] = []
    control = WorkflowControl(
        cancel_requested=flag.requested,
        on_progress=lambda progress: seen.append(progress.stage),
    )
    flag.cancel()
    with pytest.raises(WorkflowCancelled):
        control.report("expose", "starting")
    assert seen == []


def test_progress_callback_may_request_cancel():
    flag = CancelFlag()
    seen: list[str] = []

    def on_progress(progress: WorkflowProgress) -> None:
        seen.append(progress.stage)
        if progress.stage == "saving":
            flag.cancel()

    control = WorkflowControl(
        cancel_requested=flag.requested,
        on_progress=on_progress,
    )
    control.report("expose", "loading")
    with pytest.raises(WorkflowCancelled):
        control.report("expose", "saving")
    assert seen == ["loading", "saving"]

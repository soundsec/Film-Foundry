from pathlib import Path

import pytest

from half_frame_darkroom.app.cli import _ensure_batch_output_target


def test_batch_file_output_is_rejected():
    with pytest.raises(ValueError, match="must be a folder"):
        _ensure_batch_output_target([Path("a.jpg"), Path("b.jpg")], Path("out.jpg"), "Final image")


def test_single_file_output_is_allowed():
    _ensure_batch_output_target([Path("a.jpg")], Path("out.jpg"), "Final image")


def test_batch_folder_output_is_allowed():
    _ensure_batch_output_target([Path("a.jpg"), Path("b.jpg")], Path("outputs"), "Final image")

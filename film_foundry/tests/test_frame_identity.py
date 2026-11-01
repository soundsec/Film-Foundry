from __future__ import annotations

import pytest

from film_foundry.contracts import data_kernel
from film_foundry.core.frame_identity import (
    FRAME_IDENTITY_FIELDS,
    FRAME_IDENTITY_KEY,
    attach_frame_identity,
    normalize_frame_identity,
)


def test_catalog_names_match_core_and_are_not_material_fields():
    assert data_kernel.FRAME_IDENTITY_OBJECT_KEY == FRAME_IDENTITY_KEY
    assert data_kernel.FRAME_IDENTITY_FIELDS == FRAME_IDENTITY_FIELDS
    assert FRAME_IDENTITY_KEY not in data_kernel.MEDIUM_IDENTITY_FIELDS
    for name in FRAME_IDENTITY_FIELDS:
        assert name not in data_kernel.MEDIUM_IDENTITY_FIELDS


@pytest.mark.parametrize(
    "value, match",
    (
        ({"frame_id": "A1"}, "roll_id"),
        ({"roll_id": "R"}, "frame_id 或 frame_index"),
        ({"roll_id": "R", "frame_index": 0}, "从 1 起"),
        ({"roll_id": "R", "frame_index": True}, "必须是整数"),
        ({"roll_id": "R", "frame_id": "A1", "extra": 1}, "未编目"),
        ("R", "必须是对象"),
    ),
)
def test_normalize_rejects_invalid_identity(value, match):
    with pytest.raises(ValueError, match=match):
        normalize_frame_identity(value)


def test_normalize_accepts_partial_frame_fields_and_omits_empty():
    assert normalize_frame_identity({"roll_id": " R ", "frame_id": " A1 "}) == {
        "roll_id": "R",
        "frame_id": "A1",
    }
    assert normalize_frame_identity({"roll_id": "R", "frame_index": 3}) == {
        "roll_id": "R",
        "frame_index": 3,
    }
    assert normalize_frame_identity(None) is None
    assert normalize_frame_identity({}) is None


def test_attach_omits_absent_identity():
    payload = {"frame_identity": {"roll_id": "old", "frame_index": 1}}
    attach_frame_identity(payload, None)
    assert "frame_identity" not in payload

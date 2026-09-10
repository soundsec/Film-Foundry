import json

import pytest

from film_foundry.tools.audit_phenomena import (
    PHENOMENON_KEYS,
    main,
    run_phenomenon_audit,
)


def test_phenomenon_catalog_covers_formation_process_grain_and_scan_boundaries():
    assert len(PHENOMENON_KEYS) == len(set(PHENOMENON_KEYS))
    assert {
        "emulsion_mtf",
        "emulsion_highlight_spread",
        "halation",
        "light_leak",
        "light_piping",
        "latent_state",
        "uneven_development",
        "development_adjacency",
        "color_negative_process",
        "push_pull_process",
        "material_degradation",
        "cross_process",
        "bleach_bypass_process",
        "reversal_mask_bleach",
        "bw_negative_process",
        "bw_reversal_process",
        "color_reversal_process",
        "density_accidents",
        "dye_grain",
        "silver_grain",
        "negative_scan",
        "negative_channel_compensation",
        "positive_scan",
        "scan_normalization",
    } == set(PHENOMENON_KEYS)


def test_selected_phenomena_are_deterministic_isolated_and_report_costs():
    selected = (
        "halation",
        "light_leak",
        "density_accidents",
        "dye_grain",
        "negative_scan",
    )
    report = run_phenomenon_audit(
        width=31,
        height=19,
        repeats=2,
        seed=73,
        phenomena=selected,
    )

    assert report["source_arrays_unchanged"] is True
    assert report["runtime"]["selected"] == list(selected)
    assert set(report["phenomena"]) == set(selected)
    for result in report["phenomena"].values():
        assert result["determinism"]["stable_across_repeats"] is True
        assert len(result["determinism"]["sha256"]) == 64
        assert result["elapsed_seconds"]["min"] >= 0.0
        assert result["python_numpy_traced_peak_mib"]["min"] >= 0.0
        assert result["arrays"]
        for array in result["arrays"].values():
            assert array["bytes"] > 0
            assert array["minimum"] <= array["maximum"]


def test_every_phenomenon_case_executes_on_a_small_native_frame():
    report = run_phenomenon_audit(
        width=29,
        height=17,
        repeats=1,
        seed=91,
    )

    assert tuple(report["runtime"]["selected"]) == PHENOMENON_KEYS
    assert set(report["phenomena"]) == set(PHENOMENON_KEYS)
    assert all(
        result["determinism"]["stable_across_repeats"]
        for result in report["phenomena"].values()
    )


def test_uneven_development_audit_exposes_directional_grid_diagnostic():
    report = run_phenomenon_audit(
        width=160,
        height=128,
        repeats=1,
        seed=4,
        phenomena=("uneven_development",),
    )

    arrays = report["phenomena"]["uneven_development"]["arrays"]
    assert arrays["axis_gradient_energy_yx"]["shape"] == [2]
    assert arrays["axis_energy_balance"]["shape"] == [1]
    assert 0.0 <= arrays["axis_energy_balance"]["maximum"] < 0.75


def test_phenomenon_audit_rejects_empty_unknown_and_invalid_runs():
    with pytest.raises(ValueError, match="at least one"):
        run_phenomenon_audit(width=8, height=6, repeats=1, phenomena=())
    with pytest.raises(ValueError, match="duplicates"):
        run_phenomenon_audit(
            width=8,
            height=6,
            repeats=1,
            phenomena=("halation", "halation"),
        )
    with pytest.raises(ValueError, match="Unknown"):
        run_phenomenon_audit(width=8, height=6, repeats=1, phenomena=("unknown",))
    with pytest.raises(ValueError, match="repeats"):
        run_phenomenon_audit(width=8, height=6, repeats=0)
    with pytest.raises(ValueError, match="width and height"):
        run_phenomenon_audit(width=0, height=6, repeats=1)


def test_phenomenon_audit_cli_lists_and_writes_atomic_json(tmp_path, capsys):
    assert main(["--list"]) == 0
    listing = capsys.readouterr().out
    assert "dye_grain" in listing
    assert "negative_scan" in listing

    destination = tmp_path / "phenomena.json"
    assert main(
        [
            "--width",
            "18",
            "--height",
            "12",
            "--repeats",
            "1",
            "--seed",
            "11",
            "--phenomenon",
            "emulsion_mtf",
            "--json-output",
            str(destination),
        ]
    ) == 0
    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert payload["runtime"]["selected"] == ["emulsion_mtf"]
    assert payload["source_arrays_unchanged"] is True
    assert not list(tmp_path.glob(".ff-*"))
    assert '"emulsion_mtf"' in capsys.readouterr().out

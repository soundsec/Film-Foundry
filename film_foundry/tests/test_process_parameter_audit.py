import json

import pytest

from film_foundry.tools.audit_process_parameters import (
    PARAMETER_COST_KEYS,
    main,
    run_process_parameter_audit,
)


def test_parameter_cost_catalog_covers_scalar_retained_and_spatial_controls():
    assert len(PARAMETER_COST_KEYS) == len(set(PARAMETER_COST_KEYS))
    assert {
        "baseline",
        "time_long",
        "push_two_stops",
        "developer_exhaustion",
        "fixer_exhaustion",
        "silver_retention",
        "partial_first_development",
        "strong_mask_bleach",
        "color_reversal_baseline",
        "color_reversal_partial_first_development",
        "color_reversal_partial_second_development",
        "color_reversal_partial_activation",
        "color_reversal_partial_first_silver_removal",
        "light_leak",
        "chemical_stain",
        "chemical_stain_exhausted_fix",
        "uneven_development",
        "uneven_development_stressed",
        "silver_plating",
        "reticulation",
        "dye_damage",
        "silver_plating_monobath",
        "developer_high_acutance",
        "development_adjacency",
        "development_adjacency_fine",
        "development_adjacency_broad",
        "process_variation",
        "process_variation_with_accidents",
        "combined_accidents",
        "occlusion_dust",
        "occlusion_hair",
        "imprint_laser_dot",
        "imprint_lab_edge",
    }.issubset(PARAMETER_COST_KEYS)


def test_parameter_cost_audit_reports_actual_branch_activation_and_baseline():
    report = run_process_parameter_audit(
        width=43,
        height=31,
        repeats=1,
        seed=73,
        cases=("baseline", "light_leak", "uneven_development", "silver_plating"),
    )

    assert report["source_image_unchanged"] is True
    assert report["runtime"]["quality_mode"] == "high"
    assert report["parameters"]["baseline"]["active_branches"]["accident_maps"] == []
    assert report["parameters"]["light_leak"]["active_branches"]["has_light_leak_map"] is True
    assert "uneven_development" in report["parameters"]["uneven_development"]["active_branches"]["accident_maps"]
    assert "silver_plating" in report["parameters"]["silver_plating"]["active_branches"]["accident_maps"]
    for result in report["parameters"].values():
        assert result["elapsed_seconds"]["median"] >= 0.0
        assert result["python_numpy_traced_peak_mib"]["median"] >= 0.0
        assert result["relative_to_baseline"]["time_ratio"] >= 0.0
        assert result["determinism"]["stable_across_repeats"] is True
        assert "grain_factor" in result["active_branches"]["effective_cost_drivers"]
        assert "grain_radius_factor" in result["active_branches"]["effective_cost_drivers"]
        assert "grain_clump_factor" in result["active_branches"]["effective_cost_drivers"]


def test_parameter_cost_audit_places_reversal_completions_in_reversal_topology():
    report = run_process_parameter_audit(
        width=37,
        height=29,
        repeats=1,
        seed=79,
        cases=(
            "color_reversal_baseline",
            "color_reversal_partial_second_development",
        ),
    )

    reference = report["parameters"]["color_reversal_baseline"]
    partial = report["parameters"]["color_reversal_partial_second_development"]
    assert reference["active_branches"]["process_program"] == "color_reversal"
    assert partial["active_branches"]["process_program"] == "color_reversal"
    assert (
        reference["determinism"]["hashes"]["optical_density_rgb"]
        != partial["determinism"]["hashes"]["optical_density_rgb"]
    )


def test_parameter_cost_audit_validates_inputs_and_writes_json(tmp_path, capsys):
    with pytest.raises(ValueError, match="positive"):
        run_process_parameter_audit(width=0, height=8)
    with pytest.raises(ValueError, match="at least one"):
        run_process_parameter_audit(width=8, height=8, cases=())
    with pytest.raises(ValueError, match="duplicates"):
        run_process_parameter_audit(width=8, height=8, cases=("baseline", "baseline"))
    with pytest.raises(ValueError, match="Unknown"):
        run_process_parameter_audit(width=8, height=8, cases=("unknown",))

    destination = tmp_path / "parameter-costs.json"
    assert main(
        [
            "--width",
            "19",
            "--height",
            "13",
            "--case",
            "time_long",
            "--json-output",
            str(destination),
        ]
    ) == 0
    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert payload["runtime"]["selected"] == ["time_long"]
    assert payload["source_image_unchanged"] is True
    assert "baseline" not in payload["parameters"]
    assert '"time_long"' in capsys.readouterr().out

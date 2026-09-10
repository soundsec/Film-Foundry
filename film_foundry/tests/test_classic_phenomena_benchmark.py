import numpy as np

from film_foundry.tools.benchmark_classic_phenomena import (
    build_classic_phenomena_targets,
    run_classic_phenomena_benchmark,
    write_classic_phenomena_bundle,
)
from film_foundry.core.atomic_io import strict_json_load


def test_classic_targets_are_immutable_and_cover_density_and_highlight_geometries():
    targets = build_classic_phenomena_targets(360, 240)

    assert targets.density_layers.shape == (240, 360, 3)
    assert targets.highlight_exposure.shape == (240, 360, 3)
    assert targets.density_layers.dtype == np.float32
    assert targets.highlight_exposure.dtype == np.float32
    assert targets.density_layers.flags.writeable is False
    assert targets.highlight_exposure.flags.writeable is False
    assert {
        "neutral_d0.12",
        "neutral_d0.68",
        "neutral_d1.68",
        "cyan_layer",
        "magenta_layer",
        "yellow_layer",
    }.issubset(targets.density_regions)
    assert {
        "white_point_core",
        "red_point_core",
        "cyan_point_core",
        "horizontal_line",
        "vertical_line",
        "hard_edge_area",
        "broad_highlight_area",
    }.issubset(targets.highlight_regions)
    assert float(np.max(targets.highlight_exposure)) > 1.0


def test_classic_benchmark_reports_formation_roles_and_compute_separately():
    report, artifacts = run_classic_phenomena_benchmark(
        width=360,
        height=240,
        repeats=1,
        seed=20260811,
    )

    assert report["runtime"]["scanner_used"] is False
    assert report["runtime"]["source_arrays_unchanged"] is True
    assert report["formation_contract"] == {
        "dye_grain_stage": "material_layer_density",
        "emulsion_spread_stage": "pre_latent_exposure",
        "halation_stage": "pre_latent_exposure_support_or_backing_return",
        "full_grain_model": "multiscale_layer_density_v1",
        "reduced_grain_model": "reduced_effective_scale_cloud_v2",
        "full_halation_source_model": "scattered_peak_area_v1",
        "reduced_halation_source_model": "direct_peak_area_v1",
    }
    assert report["directional_checks"]
    assert all(report["directional_checks"].values())
    assert set(report["compute_performance"]) == {
        "dye_grain_full",
        "dye_grain_reduced",
        "emulsion_highlight_spread",
        "halation_return",
        "halation_return_reduced",
    }
    for value in report["compute_performance"].values():
        assert value["elapsed_seconds"]["median"] > 0.0
        assert value["python_numpy_traced_peak_mib"]["maximum"] > 0.0
    radii = report["phenomenon_characterization"][
        "highlight_spread_and_halation"
    ]["isolated_white_point"]
    assert radii["emulsion_energy_radius_90_px"] < radii[
        "halation_energy_radius_90_px"
    ]
    assert {
        "30_grain_comparison_left_target_full_reduced.png",
        "31_highlight_comparison_left_target_spread_halation.png",
    }.issubset(artifacts)


def test_classic_benchmark_writes_one_complete_visual_bundle(tmp_path):
    report, artifacts = run_classic_phenomena_benchmark(
        width=300,
        height=200,
        repeats=1,
        seed=17,
        work_long_edge=240,
    )
    output_dir = write_classic_phenomena_bundle(
        tmp_path / "classic-targets",
        report,
        artifacts,
    )

    assert output_dir.is_dir()
    assert (output_dir / "report.json").is_file()
    assert {path.name for path in output_dir.glob("*.png")} == set(artifacts)
    stored = strict_json_load(output_dir / "report.json")
    assert stored["image"] == {"width": 300, "height": 200, "pixels": 60_000}
    assert stored["runtime"]["work_long_edge"] == 240
    assert all(stored["directional_checks"].values())

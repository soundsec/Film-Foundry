import json
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from film_foundry.tools.benchmark_pipeline import (
    _apply_spatial_profile,
    _system_cpu_busy_percent,
    main,
    run_benchmark,
    run_production_benchmark,
)
from film_foundry.core.execution_topology import REFERENCE_TOPOLOGY_VERSION
from film_foundry.model.config import DarkroomConfig
from film_foundry.tools.run_preset_curve_preview import _save_curve_image
from film_foundry.core.random_fields import fill_standard_normal_float32


def test_pipeline_benchmark_can_explicitly_verify_determinism(tmp_path):
    result = run_benchmark(
        width=24,
        height=16,
        repeats=2,
        quality_mode="draft",
        seed=17,
        verify_determinism=True,
    )

    assert result["image"] == {"width": 24, "height": 16, "pixels": 384}
    assert result["determinism"]["stable_across_repeats"] is True
    assert result["determinism"]["hash_policy"] == "all_repeats"
    assert result["determinism"]["hashed_repetitions"] == 2
    assert len(result["determinism"]["density_grain_sha256"]) == 64
    assert len(result["determinism"]["scan_output_sha256"]) == 64
    assert result["develop_seconds"]["min"] >= 0.0
    assert result["scan_seconds"]["min"] >= 0.0
    assert result["runtime"]["execution_mode"] == "quality"
    assert result["runtime"]["effective_work_long_edge"] == {
        "halation": 1200,
        "emulsion_highlight_spread": 1400,
        "grain_and_accidents": 1200,
        "development_adjacency": 1200,
    }
    assert result["runtime"]["native_thread_limit"] == 4
    assert result["runtime"]["opencv_threads_effective"] <= 4
    assert not list(tmp_path.iterdir())


def test_pipeline_benchmark_json_output_is_valid_and_atomic(tmp_path, capsys):
    output = tmp_path / "benchmark.json"

    assert main([
        "--width", "20",
        "--height", "12",
        "--repeats", "1",
        "--quality", "draft",
        "--seed", "9",
        "--json-output", str(output),
    ]) == 0

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["image"]["pixels"] == 240
    assert payload["runtime"]["seed"] == 9
    assert not list(tmp_path.glob(".ff-*"))
    assert payload["determinism"]["hash_policy"] == "first_repeat"
    assert payload["determinism"]["hashed_repetitions"] == 1
    assert payload["determinism"]["stable_across_repeats"] is None
    assert '"stable_across_repeats": null' in capsys.readouterr().out


def test_production_benchmark_uses_output_only_contract():
    result = run_production_benchmark(
        width=20,
        height=12,
        repeats=2,
        seed=11,
        verify_determinism=True,
    )

    assert result["schema_version"] == 2
    assert result["execution_path"] == "production_output_only"
    assert result["runtime"]["retain_cold_history"] is False
    assert result["runtime"]["retain_layer_masters"] is False
    assert result["runtime"]["execution_mode"] == "quality"
    assert result["runtime"]["effective_work_long_edge"] == {
        "halation": 1800,
        "emulsion_highlight_spread": 2200,
        "grain_and_accidents": 1800,
        "development_adjacency": 1800,
    }
    assert result["determinism"]["stable_across_repeats"] is True
    assert result["determinism"]["hash_policy"] == "all_repeats"
    assert result["determinism"]["hashed_repetitions"] == 2
    assert len(result["determinism"]["scan_output_sha256"]) == 64
    assert result["python_numpy_traced_peak_mib"]["min"] >= 0.0
    assert result["develop_seconds"]["min"] >= 0.0
    assert result["scan_seconds"]["min"] >= 0.0
    assert set(result["stage_memory"]) == {"after_develop", "after_scan"}
    for stage in result["stage_memory"].values():
        assert stage["elapsed_seconds"]["min"] >= 0.0
        assert stage["python_numpy_current_mib"]["min"] >= 0.0
        assert stage["python_numpy_peak_mib"]["min"] >= 0.0


def test_production_benchmark_hashes_only_first_repeat_by_default(monkeypatch):
    from film_foundry.tools import benchmark_pipeline

    calls = 0
    original_digest = benchmark_pipeline._array_digest

    def counted_digest(array):
        nonlocal calls
        calls += 1
        return original_digest(array)

    monkeypatch.setattr(benchmark_pipeline, "_array_digest", counted_digest)
    result = run_production_benchmark(width=20, height=12, repeats=3, seed=11)

    assert calls == 1
    assert result["determinism"] == {
        "scan_output_sha256": result["determinism"]["scan_output_sha256"],
        "hash_policy": "first_repeat",
        "hashed_repetitions": 1,
        "stable_across_repeats": None,
    }


def test_benchmark_reports_the_effective_reduced_plan():
    result = run_production_benchmark(
        width=20,
        height=12,
        repeats=1,
        quality_mode="high",
        fast_mode=True,
        seed=13,
    )

    assert result["runtime"]["execution_mode"] == "reduced_fast"
    assert result["runtime"]["effective_work_long_edge"] == {
        "halation": 1600,
        "emulsion_highlight_spread": 1400,
        "grain_and_accidents": 1600,
        "development_adjacency": 1200,
    }


@pytest.mark.parametrize(
    ("program_key", "accident_profile"),
    (
        ("bw_negative", "none"),
        ("color_negative", "none"),
        ("color_negative_bleach_bypass", "none"),
        ("bw_reversal", "none"),
        ("color_reversal", "none"),
        ("color_negative", "combined"),
    ),
)
def test_reference_topology_benchmark_is_an_exact_same_version_fallback(
    program_key,
    accident_profile,
):
    # Very small OpenCV kernels can alternate by a few float32 ULPs even with
    # one worker, independently of execution topology (48x32, and the BW
    # reversal path at 96x64). 128x96 exercises the same production branches
    # with a stable exact kernel on the supported runtime. Warm each topology
    # independently, then require byte-for-byte equality; no product tolerance
    # is widened.
    audit_width, audit_height = 128, 96
    run_production_benchmark(
        width=audit_width,
        height=audit_height,
        repeats=1,
        seed=29,
        program_key=program_key,
        accident_profile=accident_profile,
        native_thread_limit=1,
    )
    run_production_benchmark(
        width=audit_width,
        height=audit_height,
        repeats=1,
        seed=29,
        program_key=program_key,
        accident_profile=accident_profile,
        reference_topology=True,
        native_thread_limit=1,
    )
    optimized = run_production_benchmark(
        width=audit_width,
        height=audit_height,
        repeats=1,
        seed=29,
        program_key=program_key,
        accident_profile=accident_profile,
        native_thread_limit=1,
    )
    reference = run_production_benchmark(
        width=audit_width,
        height=audit_height,
        repeats=1,
        seed=29,
        program_key=program_key,
        accident_profile=accident_profile,
        reference_topology=True,
        native_thread_limit=1,
    )

    assert optimized["runtime"]["execution_topology"] == "optimized"
    assert reference["runtime"]["execution_topology"] == "reference"
    assert (
            reference["runtime"]["reference_topology_version"]
            == optimized["runtime"]["reference_topology_version"]
            == REFERENCE_TOPOLOGY_VERSION
        )
    assert (
        reference["determinism"]["scan_output_sha256"]
        == optimized["determinism"]["scan_output_sha256"]
    )


def test_system_cpu_busy_percent_uses_endpoint_deltas():
    assert _system_cpu_busy_percent((100, 1000), (140, 1200)) == pytest.approx(80.0)
    assert _system_cpu_busy_percent(None, (140, 1200)) is None
    assert _system_cpu_busy_percent((100, 1000), (100, 1000)) is None


def test_bounded_random_fill_matches_legacy_stream_and_rng_state():
    legacy_rng = np.random.default_rng(101)
    bounded_rng = np.random.default_rng(101)
    expected = legacy_rng.standard_normal(47).astype("float32")
    actual = np.empty(47, dtype="float32")

    fill_standard_normal_float32(
        bounded_rng,
        actual,
        max_temp_values=11,
    )

    assert np.array_equal(actual, expected)
    assert bounded_rng.bit_generator.state == legacy_rng.bit_generator.state


def test_benchmark_bleach_bypass_name_selects_an_actual_partial_bleach():
    ordinary = run_production_benchmark(
        width=48,
        height=32,
        repeats=1,
        seed=19,
        program_key="color_negative",
    )
    bypass = run_production_benchmark(
        width=48,
        height=32,
        repeats=1,
        seed=19,
        program_key="color_negative_bleach_bypass",
    )

    assert (
        bypass["determinism"]["scan_output_sha256"]
        != ordinary["determinism"]["scan_output_sha256"]
    )


def test_production_benchmark_accident_profile_uses_same_output_only_contract():
    ordinary = run_production_benchmark(width=48, height=32, repeats=1, seed=23)
    accidents = run_production_benchmark(
        width=48,
        height=32,
        repeats=1,
        seed=23,
        accident_profile="combined",
    )

    assert accidents["runtime"]["accident_profile"] == "combined"
    assert ordinary["runtime"]["accident_profile"] == "none"
    assert (
        accidents["determinism"]["scan_output_sha256"]
        != ordinary["determinism"]["scan_output_sha256"]
    )


def test_production_benchmark_spatial_profiles_are_explicit_and_stage_specific():
    results = {
        profile: run_production_benchmark(
            width=96,
            height=64,
            repeats=1,
            seed=31,
            spatial_profile=profile,
        )
        for profile in ("clean", "emulsion_spread", "layer_halation", "combined")
    }

    assert all(
        result["runtime"]["spatial_profile"] == profile
        for profile, result in results.items()
    )
    assert (
        results["clean"]["determinism"]["scan_output_sha256"]
        != results["emulsion_spread"]["determinism"]["scan_output_sha256"]
    )

    config = DarkroomConfig()
    _apply_spatial_profile(config, "combined")
    assert config.enable_halation is True
    assert config.film.halation_return_model == "layer_selective"
    assert config.film.emulsion_highlight_spread_strength > 0.0


def test_curve_preview_preserves_previous_file_when_encoding_fails(tmp_path, monkeypatch):
    output = tmp_path / "curve.png"
    output.write_bytes(b"previous-valid-preview")
    image = Image.new("RGB", (8, 6), (120, 100, 80))

    def fail_save(_self, path, *_args, **_kwargs):
        Path(path).write_bytes(b"partial-preview")
        raise OSError("simulated encoder failure")

    monkeypatch.setattr(Image.Image, "save", fail_save)
    with pytest.raises(OSError, match="simulated encoder failure"):
        _save_curve_image(image, output)

    assert output.read_bytes() == b"previous-valid-preview"
    assert not list(tmp_path.glob(".ff-*"))

import pytest
import numpy as np

from film_foundry.core import engine
from film_foundry.core.resource_planning import (
    dimensions_for_long_edge,
    enforce_memory_budget,
    estimate_pipeline_memory,
    estimate_scan_memory,
    estimated_untiled_material_peak_mib,
    exact_material_tiling_required,
    warn_outside_comfort_zone,
)
from film_foundry.model.config import DarkroomConfig


def test_dimension_planning_matches_long_edge_resize_contract():
    assert dimensions_for_long_edge(6000, 4000, None) == (6000, 4000)
    assert dimensions_for_long_edge(6000, 4000, 3000) == (3000, 2000)
    assert dimensions_for_long_edge(1200, 800, 1600) == (1200, 800)


def test_memory_estimate_scales_with_pixels_and_retention_contract():
    production_24mp = estimate_pipeline_memory(6000, 4000)
    production_100mp = estimate_pipeline_memory(10000, 10000)
    plate_capable = estimate_pipeline_memory(
        6000, 4000, retain_development_stages=True
    )
    diagnostic = estimate_pipeline_memory(6000, 4000, diagnostic=True)

    assert production_24mp.retention_policy == "production_mixed_precision_cold_history"
    assert production_24mp.retained_state_bytes == 6000 * 4000 * 3 * (3 * 4 + 3 * 2)
    assert production_100mp.planning_peak_bytes > production_24mp.planning_peak_bytes * 4
    assert plate_capable.retained_state_bytes > production_24mp.retained_state_bytes
    assert diagnostic.retained_state_bytes > plate_capable.retained_state_bytes
    assert diagnostic.planning_peak_bytes > plate_capable.planning_peak_bytes


def test_long_edge_reduces_processing_estimate_but_still_accounts_for_full_decode():
    native = estimate_pipeline_memory(10000, 10000)
    reduced = estimate_pipeline_memory(10000, 10000, long_edge=2000)

    assert reduced.work_width == reduced.work_height == 2000
    assert reduced.planning_peak_bytes < native.planning_peak_bytes
    # The current loader decodes the complete source before resize, so the
    # estimate must not pretend that render_long_edge eliminates decode cost.
    assert reduced.planning_peak_bytes >= 10000 * 10000 * 3 * 4 * 4


def test_30mp_comfort_zone_is_advisory_and_decoder_reduction_is_explicit():
    comfort = estimate_pipeline_memory(6000, 5000)
    large = estimate_pipeline_memory(8000, 5000)
    scaled_jpeg = estimate_pipeline_memory(
        10000,
        8000,
        long_edge=1600,
        decoder_reduced=True,
    )

    assert comfort.support_tier == "comfort"
    assert large.support_tier == "best_effort_large_frame"
    assert scaled_jpeg.decoder_reduced is True
    assert scaled_jpeg.planning_peak_bytes < estimate_pipeline_memory(
        10000, 8000, long_edge=1600
    ).planning_peak_bytes
    with pytest.warns(RuntimeWarning, match="best-effort large-frame support"):
        warn_outside_comfort_zone(large)


def test_budget_policy_warns_or_errors_without_changing_estimate():
    estimate = estimate_pipeline_memory(6000, 4000)

    with pytest.warns(RuntimeWarning, match="will not silently resize"):
        enforce_memory_budget(estimate, 64.0, "warn")
    with pytest.raises(MemoryError, match="will not silently resize"):
        enforce_memory_budget(estimate, 64.0, "error")
    enforce_memory_budget(estimate, 64.0, "allow")


def test_memory_budget_can_only_advance_the_exact_material_schedule():
    width, height = 1000, 1000
    trigger_mib = estimated_untiled_material_peak_mib(width, height)

    assert not exact_material_tiling_required(
        width,
        height,
        threshold_megapixels=8.0,
        memory_budget_mb=trigger_mib + 1.0,
    )
    assert exact_material_tiling_required(
        width,
        height,
        threshold_megapixels=8.0,
        memory_budget_mb=trigger_mib - 1.0,
    )
    assert exact_material_tiling_required(
        4000,
        2000,
        threshold_megapixels=8.0,
        memory_budget_mb=None,
    )


def test_process_file_error_budget_stops_before_pixel_decode(tmp_path, monkeypatch):
    input_path = tmp_path / "large.png"
    output_path = tmp_path / "result.png"
    config = DarkroomConfig()
    config.processing.memory_budget_mb = 64.0
    config.processing.memory_budget_policy = "error"

    monkeypatch.setattr(engine, "probe_image_dimensions", lambda _path: (6000, 4000))

    def fail_if_decoded(_path, **_kwargs):
        raise AssertionError("memory preflight must run before load_image")

    monkeypatch.setattr(engine, "load_image_with_color", fail_if_decoded)
    with pytest.raises(MemoryError, match="Estimated pipeline peak"):
        engine.process_file(input_path, output_path, config)


@pytest.mark.parametrize("policy", ("silent", "", "resize"))
def test_invalid_memory_policy_is_rejected(policy):
    estimate = estimate_pipeline_memory(32, 24)
    with pytest.raises(ValueError, match="memory_budget_policy"):
        enforce_memory_budget(estimate, 1.0, policy)


def test_scan_preflight_counts_selected_spectral_fields_without_loading_pixels(tmp_path, monkeypatch):
    from film_foundry.core.negative_io import probe_scan_archive_memory
    from film_foundry.app import workflow

    path = tmp_path / "medium.npz"
    np.savez_compressed(path, optical_density_rgb=np.zeros((4, 6, 3), np.float32),
        spectral_component_amounts=np.zeros((4, 6, 9), np.float32),
        spectral_post_process_density_rgb=np.zeros((4, 6, 3), np.float32),
        density_cmy=np.zeros((4, 6, 3), np.float32))
    # np.load is lazy, but the preflight must not invoke it at all.
    monkeypatch.setattr(np, "load", lambda *a, **k: pytest.fail("header probe loaded arrays"))
    assert probe_scan_archive_memory(path) == (6, 4, 24 * 15 * 4, 24 * (15 + 18) * 4)
    monkeypatch.setattr(workflow, "load_developed_medium", lambda *a: pytest.fail("decoded before budget rejection"))
    config = DarkroomConfig()
    config.processing.memory_budget_mb = 0.001
    config.processing.memory_budget_policy = "error"
    with pytest.raises(MemoryError, match="will not silently resize"):
        workflow.observe_medium_file(path, config)


def test_scan_preview_estimate_preserves_full_source_decode_and_24mp_dimensions():
    resident = 6000 * 4000 * 15 * 4
    estimate = estimate_scan_memory(6000, 4000, resident_bytes=resident,
        decode_peak_bytes=resident * 2, preview_long_edge=1000)
    assert (estimate.source_width, estimate.source_height) == (6000, 4000)
    assert (estimate.work_width, estimate.work_height) == (1000, 667)
    assert estimate.planning_peak_bytes >= resident * 2


def test_scan_header_preflight_rejects_truncated_payload_and_keeps_legacy_fallback(tmp_path):
    import zipfile
    from io import BytesIO
    from film_foundry.core.negative_io import probe_scan_archive_memory

    path = tmp_path / "bad.npz"
    header = BytesIO()
    np.lib.format.write_array_header_1_0(header, dict(descr="<f4", fortran_order=False, shape=(4000, 6000, 3)))
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("optical_density_rgb.npy", header.getvalue())
    with pytest.raises(ValueError, match="truncated"):
        probe_scan_archive_memory(path)
    legacy = tmp_path / "legacy.npz"
    np.savez(legacy, negative=np.array({"old": True}, dtype=object))
    assert probe_scan_archive_memory(legacy) is None

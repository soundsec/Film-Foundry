from dataclasses import asdict

import numpy as np
import pytest

from film_foundry.core.engine import develop_negative, scan_negative
from film_foundry.core.film_border import (
    FILM_BORDER_PIPELINE_STATUS,
    apply_edge_imprint_to_linear,
    resolve_film_border_layout,
    validate_film_border_config,
)
from film_foundry.model.config import DarkroomConfig, FilmBorderConfig


def test_disabled_film_border_resolves_to_none():
    layout = resolve_film_border_layout(FilmBorderConfig(), (48, 64, 3))
    assert layout is None


def test_film_border_layout_places_sprockets_outside_the_image_gate():
    config = FilmBorderConfig(
        enabled=True,
        format_key="135",
        sprocket_rows="both",
        imprint_style="laser_dot",
        imprint_side="bottom",
        imprint_text="A12",
        imprint_time="2026-08-27 01:52",
    )
    layout = resolve_film_border_layout(config, (40, 60, 3))
    assert layout is not None
    assert layout.pipeline_status == FILM_BORDER_PIPELINE_STATUS
    assert layout.canvas_size[0] == 60
    assert layout.canvas_size[1] > 40
    image_x, image_y, image_w, image_h = layout.image_rect
    assert (image_x, image_w, image_h) == (0, 60, 40)
    assert image_y > 0
    assert len(layout.sprocket_holes) == 16
    for hole in layout.sprocket_holes:
        x0, y0, x1, y1 = hole.normalized_rect
        centre_y = 0.5 * (y0 + y1)
        gate_top = image_y / layout.canvas_size[1]
        gate_bottom = (image_y + image_h) / layout.canvas_size[1]
        assert centre_y < gate_top or centre_y > gate_bottom
        assert 0.0 <= x0 < x1 <= 1.0
    assert layout.imprint is not None
    assert layout.imprint.style == "laser_dot"
    assert layout.imprint.text == "A12"
    assert layout.imprint.origin_xy[1] > gate_bottom


def test_film_border_preserves_image_gate_and_expands_formed_medium():
    config = DarkroomConfig()
    config.film_border.enabled = True
    config.film_border.format_key = "half_frame"
    config.film_border.sprocket_rows = "top"
    config.film_border.imprint_style = "none"
    config.film_border.imprint_text = "LAB"
    restored = DarkroomConfig.from_dict(asdict(config))
    assert restored.film_border.format_key == "half_frame"
    assert restored.film_border.sprocket_rows == "top"
    assert restored.film_border.imprint_style == "none"

    image = np.linspace(0.1, 0.8, 24 * 32 * 3, dtype=np.float32).reshape(24, 32, 3)
    clean = DarkroomConfig()
    clean.enable_grain = False
    clean.enable_halation = False
    bordered = DarkroomConfig.from_dict(asdict(restored))
    bordered.enable_grain = False
    bordered.enable_halation = False
    medium_clean = develop_negative(image, clean, rng=np.random.default_rng(5))
    medium_border = develop_negative(image, bordered, rng=np.random.default_rng(5))
    layout = resolve_film_border_layout(bordered.film_border, image.shape)
    assert layout is not None
    assert layout.pipeline_status == "formed_rebate_reference_v1"
    rows = layout.image_rect[1]
    assert np.array_equal(medium_clean.optical_density_rgb, medium_border.optical_density_rgb[rows:-rows])


def test_edge_imprint_adds_bounded_bright_linear_exposure_without_mutating_input():
    image = np.full((48, 96, 3), 0.12, dtype=np.float32)
    original = image.copy()
    flashed, mask = apply_edge_imprint_to_linear(
        image,
        FilmBorderConfig(
            imprint_style="lab_edge",
            imprint_side="bottom",
            imprint_text="LAB",
            imprint_time="2026-08-27",
        ),
    )
    assert np.array_equal(image, original)
    assert mask is not None
    assert 1.0 < float(np.max(flashed)) < 10.0
    assert float(np.mean(flashed[-10:])) > float(np.mean(flashed[:10]))
    empty, empty_mask = apply_edge_imprint_to_linear(
        image,
        FilmBorderConfig(imprint_style="lab_edge", imprint_text="", imprint_time=""),
    )
    assert empty_mask is None
    assert np.array_equal(empty, original)


def test_edge_imprint_styles_have_distinct_spectra_and_do_not_need_sprocket_enable():
    image = np.zeros((40, 80, 3), dtype=np.float32)
    laser, laser_mask = apply_edge_imprint_to_linear(
        image,
        FilmBorderConfig(
            enabled=False,
            imprint_style="laser_dot",
            imprint_side="top",
            imprint_text="A12",
        ),
    )
    amber, amber_mask = apply_edge_imprint_to_linear(
        image,
        FilmBorderConfig(
            enabled=False,
            imprint_style="optical_amber",
            imprint_side="top",
            imprint_text="A12",
        ),
    )
    lab, lab_mask = apply_edge_imprint_to_linear(
        image,
        FilmBorderConfig(
            enabled=False,
            imprint_style="lab_edge",
            imprint_side="top",
            imprint_text="A12",
        ),
    )
    assert laser_mask is not None and amber_mask is not None and lab_mask is not None
    laser_delta = laser[laser_mask > 0.05].mean(axis=0)
    amber_delta = amber[amber_mask > 0.05].mean(axis=0)
    lab_delta = lab[lab_mask > 0.05].mean(axis=0)
    assert float(laser_delta[0]) > float(laser_delta[1])
    assert float(laser_delta[0]) > float(laser_delta[2])
    assert float(amber_delta[0]) > float(amber_delta[2])
    assert float(lab_delta[1]) > float(lab_delta[0])
    assert not np.allclose(laser_mask, lab_mask, atol=0.02)


def test_edge_imprint_enters_formation_and_scan_is_read_only():
    image = np.full((36, 64, 3), 0.34, dtype=np.float32)
    original = image.copy()
    clean = DarkroomConfig()
    clean.enable_grain = False
    clean.enable_halation = False
    clean.enable_mtf = False
    clean.scanner.scan_normalize = False
    marked = DarkroomConfig.from_dict(asdict(clean))
    marked.film_border.imprint_style = "optical_amber"
    marked.film_border.imprint_side = "bottom"
    marked.film_border.imprint_text = "FF"
    marked.film_border.imprint_time = "12:00"

    medium_clean = develop_negative(image, clean, rng=np.random.default_rng(8))
    medium_marked = develop_negative(image, marked, rng=np.random.default_rng(8))
    optical_before_scan = medium_marked.optical_density_rgb.copy()

    assert np.array_equal(image, original)
    assert medium_marked.metadata["has_edge_imprint_map"] is True
    assert medium_marked.metadata["imprint_style"] == "optical_amber"
    assert medium_marked.metadata["accident_stages"]["edge_imprint"] == "pre_latent_exposure"
    assert np.array_equal(medium_clean.linear_input, medium_marked.linear_input)
    assert float(np.mean(medium_marked.density_cmy[-8:])) > float(
        np.mean(medium_clean.density_cmy[-8:])
    )
    scan_negative(medium_marked)
    assert np.array_equal(medium_marked.optical_density_rgb, optical_before_scan)


def test_film_border_validation_rejects_unknown_keys():
    with pytest.raises(ValueError, match="format_key"):
        validate_film_border_config(FilmBorderConfig(format_key="super8"))
    with pytest.raises(ValueError, match="imprint_style"):
        validate_film_border_config(FilmBorderConfig(imprint_style="rgb_watermark"))


def test_factory_label_is_outside_sprockets_and_120_has_no_holes_even_with_override():
    for side in ("top", "bottom"):
        config = FilmBorderConfig(enabled=True, imprint_style="laser_dot", imprint_side=side, imprint_text="TEST")
        layout = resolve_film_border_layout(config, (240, 360, 3))
        marks_y = layout.imprint.origin_xy[1]
        selected = [h.normalized_rect for h in layout.sprocket_holes if (h.normalized_rect[1] < .5) == (side == "top")]
        assert all(marks_y < h[1] if side == "top" else marks_y > h[3] for h in selected)
    assert not resolve_film_border_layout(FilmBorderConfig(enabled=True, format_key="120", sprocket_count=8), (300, 300, 3)).sprocket_holes


def test_stencil_code_uses_bounded_row_major_bits_and_survives_recipe_sharing():
    from film_foundry.contracts.edge_marks import EdgeMarkPattern
    from film_foundry.contracts.config_share import encode_config_share, decode_config_share
    pattern = EdgeMarkPattern.decode("FFEM1:3x2:a8")
    assert pattern.encode() == "FFEM1:3x2:A8"
    assert [[pattern.ink(x, y) for x in range(3)] for y in range(2)] == [[True, False, True], [False, True, False]]
    config = FilmBorderConfig(factory_pattern_top=pattern.encode(), factory_pattern_bottom="FFEM1:3x2:54", factory_pattern_linked=False, factory_flip_top=True)
    restored = DarkroomConfig.from_dict(decode_config_share(encode_config_share({"film_border": asdict(config)})))
    assert restored.film_border == config
    for invalid in ("FFEM1:3x2:A9", "FFEM1:129x1:00", "FFEM1:3x2:A", "not-a-code"):
        with pytest.raises(ValueError):
            EdgeMarkPattern.decode(invalid)


def test_stencil_slots_are_outside_holes_and_independent_from_camera_imprint():
    config = FilmBorderConfig(enabled=True, factory_pattern_top="FFEM1:3x2:A8", factory_pattern_bottom="FFEM1:3x2:54", factory_pattern_linked=False, factory_flip_top=True)
    layout = resolve_film_border_layout(config, (240, 360, 3))
    top, bottom = layout.factory_edges
    assert (top.pattern_code, bottom.pattern_code) == ("FFEM1:3x2:A8", "FFEM1:3x2:54")
    assert top.flip and not bottom.flip and layout.imprint is None
    assert all(top.normalized_rect[3] <= hole.normalized_rect[1] for hole in layout.sprocket_holes if hole.normalized_rect[1] < .5)
    assert all(bottom.normalized_rect[1] >= hole.normalized_rect[3] for hole in layout.sprocket_holes if hole.normalized_rect[1] > .5)
    config.factory_pattern_linked = True
    assert resolve_film_border_layout(config, (240, 360, 3)).factory_edges[1].pattern_code == top.pattern_code

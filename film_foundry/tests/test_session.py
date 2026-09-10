import argparse
from dataclasses import asdict
import json
from pathlib import Path

import pytest

from film_foundry.app.cli import _apply_common_config
from film_foundry.model.config import DarkroomConfig
from film_foundry.model.session import config_from_session, load_session, preset_reference, save_session, session_payload


def _base_cli_args(**overrides):
    values = dict(
        session=None,
        preset=None,
        seed=None,
        seed_strategy=None,
        fast=False,
        quality_mode=None,
        halation_work_long_edge=None,
        grain_work_long_edge=None,
        format=None,
        bit_depth=None,
        quality=None,
        anti_banding=None,
        render_long_edge=None,
        preview_long_edge=None,
        exposure_ev=None,
        material_degradation=None,
        negative_contrast=None,
        print_contrast=None,
        print_exposure_ev=None,
        saturation=None,
        dye_selectivity=None,
        halation=None,
        halation_sensitivity=None,
        grain=None,
        grain_size=None,
        developer_type=None,
        fixer_type=None,
        frame_size=None,
        develop_time=None,
        concentration=None,
        agitation=None,
        process_mode=None,
        compensation=None,
        push=None,
        temperature=None,
        exhaustion=None,
        fixer_exhaustion=None,
        silver_retention=None,
        silver_plating=None,
        light_leak=None,
        chemical_stain=None,
        uneven_development=None,
        process_variation=None,
        bw=False,
        no_mtf=False,
        no_halation=False,
        no_grain=False,
        no_subtractive=False,
        no_scan_normalize=False,
        scan_normalize_strength=None,
        debug_output=False,
        comparison_grid=False,
        no_sidecar=False,
    )
    values.update(overrides)
    return argparse.Namespace(**values)


def test_preset_reference_records_user_and_builtin_sources(tmp_path: Path):
    user_dir = tmp_path / "user" / "film"
    builtin_dir = tmp_path / "builtin" / "film"
    user_dir.mkdir(parents=True)
    builtin_dir.mkdir(parents=True)
    (user_dir / "custom.json").write_text("{}", encoding="utf-8")
    (builtin_dir / "clear.json").write_text("{}", encoding="utf-8")

    user_ref = preset_reference("custom", "film", user_dir, builtin_dir)
    builtin_ref = preset_reference("clear", "film", user_dir, builtin_dir)

    assert user_ref["source"] == "user"
    assert user_ref["source_label"] == "用户预设"
    assert user_ref["path"].endswith("custom.json")
    assert builtin_ref["source"] == "builtin"
    assert builtin_ref["source_label"] == "官方预设"
    assert builtin_ref["path"].endswith("clear.json")


def test_session_roundtrip_restores_runtime_config(tmp_path: Path):
    config = DarkroomConfig()
    config.look.halation_sensitivity = 0.65
    config.chemistry.chemical_stain = 0.4
    config.chemistry.silver_plating = 0.35
    config.film.material_degradation = 0.55
    config.film.auxiliary_layer_amount = 0.8
    config.film.auxiliary_layer_density_rgb = (0.16, 0.11, 0.07)
    session_path = tmp_path / "session.json"
    payload = session_payload(
        config=config,
        pipeline_mode="develop",
        input_path="input_images",
        negative_path="outputs/negatives",
        output_path="outputs",
        presets={
            "film": {"kind": "film", "name": "clear_modern_negative", "source": "builtin", "source_label": "官方预设", "path": "film.json"},
            "develop": {"kind": "develop", "name": "standard_color_negative", "source": "builtin", "source_label": "官方预设", "path": "develop.json"},
            "scanner": {"kind": "scanner", "name": "neutral_scan", "source": "builtin", "source_label": "官方预设", "path": "scanner.json"},
        },
    )

    save_session(session_path, payload)
    restored = config_from_session(session_path)

    assert restored.look.halation_sensitivity == 0.65
    assert restored.chemistry.chemical_stain == 0.4
    assert restored.chemistry.silver_plating == 0.35
    assert restored.film.material_degradation == 0.55
    assert restored.film.auxiliary_layer_amount == 0.8
    assert restored.film.auxiliary_layer_density_rgb == (0.16, 0.11, 0.07)


def test_cli_can_override_material_degradation_and_surface_plating():
    args = _base_cli_args(material_degradation=0.65, silver_plating=0.45)
    config = _apply_common_config(args)

    assert config.film.material_degradation == 0.65
    assert config.chemistry.silver_plating == 0.45


def test_cli_session_config_can_be_overridden(tmp_path: Path):
    config = DarkroomConfig()
    config.look.halation_sensitivity = 0.25
    session_path = tmp_path / "session.json"
    save_session(
        session_path,
        session_payload(
            config=config,
            pipeline_mode="full",
            input_path="input_images",
            negative_path="outputs/negatives",
            output_path="outputs",
            presets={},
        ),
    )
    args = _base_cli_args(session=session_path, halation_sensitivity=0.8)

    restored = _apply_common_config(args)

    assert restored.look.halation_sensitivity == 0.8


def test_cli_full_preset_overrides_session_base(tmp_path: Path):
    session_config = DarkroomConfig()
    session_config.look.halation_sensitivity = 0.25
    session_path = tmp_path / "session.json"
    save_session(
        session_path,
        session_payload(
            config=session_config,
            pipeline_mode="full",
            input_path="input_images",
            negative_path="outputs/negatives",
            output_path="outputs",
            presets={},
        ),
    )
    preset_config = DarkroomConfig()
    preset_config.look.halation_sensitivity = -0.5
    preset_path = tmp_path / "full_config.json"
    preset_path.write_text(json.dumps(asdict(preset_config), ensure_ascii=False), encoding="utf-8")
    args = _base_cli_args(session=session_path, preset=str(preset_path))

    restored = _apply_common_config(args)

    assert restored.look.halation_sensitivity == -0.5


def test_session_save_rejects_nan_without_replacing_previous_valid_file(tmp_path: Path):
    path = tmp_path / "session.json"
    valid = {"kind": "FilmFoundrySession", "version": 1, "config": {}}
    save_session(path, valid)
    previous = path.read_bytes()

    invalid = {"kind": "FilmFoundrySession", "version": 1, "config": {"bad": float("nan")}}
    with pytest.raises(ValueError, match="Out of range"):
        save_session(path, invalid)

    assert path.read_bytes() == previous


@pytest.mark.parametrize(
    "text",
    (
        '{"kind":"FilmFoundrySession","config":{"value":NaN}}',
        '{"kind":"FilmFoundrySession","kind":"FilmFoundrySession","config":{}}',
    ),
)
def test_session_load_rejects_nonstandard_numbers_and_duplicate_keys(tmp_path: Path, text: str):
    path = tmp_path / "invalid_session.json"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(ValueError, match="Non-standard|Duplicate"):
        load_session(path)


def test_config_loader_requires_object_root_and_unique_finite_json(tmp_path: Path):
    root_array = tmp_path / "array.json"
    root_array.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="root must be an object"):
        DarkroomConfig.from_json(root_array)

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"film":{},"film":{}}', encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
        DarkroomConfig.from_json(duplicate)

    bad_section = tmp_path / "bad_section.json"
    bad_section.write_text('{"film":[]}', encoding="utf-8")
    with pytest.raises(ValueError, match="section 'film'"):
        DarkroomConfig.from_json(bad_section)


def test_session_loader_rejects_unknown_version(tmp_path: Path):
    path = tmp_path / "future_session.json"
    path.write_text(
        json.dumps({"kind": "FilmFoundrySession", "version": 999, "config": {}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="session version"):
        load_session(path)


def test_session_loader_accepts_legacy_project_name(tmp_path: Path):
    path = tmp_path / "legacy_project.json"
    path.write_text(
        json.dumps(
            {
                "kind": "FilmFoundrySession",
                "version": 1,
                "project": "Film Foundry / Electronic Negative Factory",
                "config": {},
            }
        ),
        encoding="utf-8",
    )
    payload = load_session(path)
    assert payload["project"] == "Film Foundry / Electronic Negative Factory"

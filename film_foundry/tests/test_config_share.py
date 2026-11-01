from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from film_foundry.app import workflow
from film_foundry.app.cli import _apply_common_config, build_parser, main
from film_foundry.contracts.config_share import decode_config_share, encode_config_share
from film_foundry.contracts.data_kernel import CONFIG_SHARE_PREFIX
from film_foundry.model.config import DarkroomConfig


def _custom_recipe() -> DarkroomConfig:
    config = DarkroomConfig()
    config.film.name = "Pocket Custom"
    config.film.contrast = 1.37
    config.look.exposure_ev = -0.4
    config.chemistry.time_min = 9.5
    config.scanner.scan_saturation = 1.22
    config.look.print_contrast = 1.11
    config.enable_grain = False
    return config


def test_config_share_round_trips_parameter_values_without_preset_files():
    original = _custom_recipe()
    code = workflow.encode_recipe_share(original)
    assert code.startswith(CONFIG_SHARE_PREFIX)
    assert "/" not in code[len(CONFIG_SHARE_PREFIX) :]
    assert "clear_modern_negative" not in code
    restored = workflow.config_from_share_code(
        f"\ufeff {code[:20]} {code[20:]} \n"
    )
    assert restored.film.name == "Pocket Custom"
    assert restored.film.contrast == pytest.approx(1.37)
    assert restored.look.exposure_ev == pytest.approx(-0.4)
    assert restored.chemistry.time_min == pytest.approx(9.5)
    assert restored.scanner.scan_saturation == pytest.approx(1.22)
    assert restored.look.print_contrast == pytest.approx(1.11)
    assert restored.enable_grain is False
    assert asdict(restored.output) == asdict(DarkroomConfig().output)
    assert asdict(restored.processing) == asdict(DarkroomConfig().processing)


def test_config_share_accepts_chemistry_alias():
    code = encode_config_share({"chemistry": {"time_min": 7.25}, "film": {"contrast": 0.9}})
    payload = decode_config_share(code)
    assert "chemistry" not in payload
    assert payload["develop"]["time_min"] == pytest.approx(7.25)
    restored = DarkroomConfig.from_dict(payload)
    assert restored.chemistry.time_min == pytest.approx(7.25)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"film": "clear_modern_negative", "develop": "standard_color_negative", "scanner": "neutral_scan"},
        {"unknown": 1},
        {"film": {"contrast": float("nan")}},
    ],
)
def test_config_share_rejects_names_unknown_fields_and_nan(kwargs):
    with pytest.raises(ValueError):
        encode_config_share(kwargs)


@pytest.mark.parametrize(
    "text",
    [
        "FFC1:clear_modern_negative/standard_color_negative/neutral_scan",
        "FFC2:clear_modern_negative/standard_color_negative/neutral_scan",
        "FFC2:",
        "ffc2:abc",
        "clear_modern_negative/standard_color_negative/neutral_scan",
    ],
)
def test_config_share_rejects_preset_name_and_malformed_text(text):
    with pytest.raises(ValueError):
        decode_config_share(text)


def test_config_from_share_code_does_not_need_local_json(tmp_path):
    source = tmp_path / "only_on_this_machine.json"
    payload = {
        "film": {"name": "Transient", "contrast": 1.19},
        "look": {"exposure_ev": 0.3},
    }
    source.write_text(json.dumps(payload), encoding="utf-8")
    original = DarkroomConfig.from_json(source)
    code = workflow.encode_recipe_share(original)
    source.unlink()
    restored = workflow.config_from_share_code(code)
    assert restored.film.name == "Transient"
    assert restored.film.contrast == pytest.approx(1.19)
    assert restored.look.exposure_ev == pytest.approx(0.3)


def test_cli_config_share_applies_embedded_values():
    code = workflow.encode_recipe_share(_custom_recipe())
    args = build_parser().parse_args(["full", "in.png", "out.png", "--config-share", code])
    config = _apply_common_config(args)
    assert config.film.contrast == pytest.approx(1.37)
    assert config.look.exposure_ev == pytest.approx(-0.4)
    assert config.scanner.scan_saturation == pytest.approx(1.22)


def test_cli_config_share_numeric_flag_still_overrides():
    code = workflow.encode_recipe_share(_custom_recipe())
    args = build_parser().parse_args(
        [
            "full",
            "in.png",
            "out.png",
            "--config-share",
            code,
            "--exposure-ev",
            "0.8",
        ]
    )
    config = _apply_common_config(args)
    assert config.look.exposure_ev == pytest.approx(0.8)
    assert config.film.contrast == pytest.approx(1.37)


def test_cli_scan_config_share_applies_only_scanner():
    code = workflow.encode_recipe_share(_custom_recipe())
    args = build_parser().parse_args(["scan", "in.npz", "out.png", "--config-share", code])
    config = _apply_common_config(args)
    default = DarkroomConfig()
    assert asdict(config.film) == asdict(default.film)
    assert asdict(config.chemistry) == asdict(default.chemistry)
    assert config.scanner.scan_saturation == pytest.approx(1.22)
    assert config.look.print_contrast == pytest.approx(1.11)


def test_cli_config_share_rejects_preset_files(tmp_path):
    code = workflow.encode_recipe_share(_custom_recipe())
    args = build_parser().parse_args(
        [
            "full",
            "in.png",
            "out.png",
            "--config-share",
            code,
            "--film-preset",
            "clear_modern_negative",
        ]
    )
    with pytest.raises(ValueError, match="already carries parameter values"):
        _apply_common_config(args)

    session = tmp_path / "session.json"
    session.write_text("{}", encoding="utf-8")
    args = build_parser().parse_args(
        ["full", "in.png", "out.png", "--config-share", code, "--session", str(session)]
    )
    with pytest.raises(ValueError, match="cannot be combined"):
        _apply_common_config(args)


def test_cli_share_config_encode_decode_custom_json(tmp_path, capsys):
    source = tmp_path / "custom.json"
    source.write_text(
        json.dumps({"film": {"contrast": 1.42}, "look": {"exposure_ev": -0.15}}),
        encoding="utf-8",
    )
    rc = main(["share-config", "encode", str(source)])
    assert rc == 0
    code = capsys.readouterr().out.strip()
    assert code.startswith(CONFIG_SHARE_PREFIX)
    assert "clear_modern_negative" not in code

    rc = main(["share-config", "decode", code])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    restored = DarkroomConfig.from_dict(payload)
    assert restored.film.contrast == pytest.approx(1.42)
    assert restored.look.exposure_ev == pytest.approx(-0.15)


def test_cli_share_config_encode_rejects_share_token_as_file(capsys):
    rc = main(["share-config", "encode", "FFC2:abc"])
    assert rc == 1
    assert "share-config decode" in capsys.readouterr().err


def test_android_ffc2_fixture_matches_desktop_codec():
    fixture = Path(__file__).resolve().parent / "fixtures" / "interchange" / "ffc2_python_sample.txt"
    payload = {
        "film": {"name": "Pocket Custom", "contrast": 1.37},
        "look": {"exposure_ev": -0.4},
    }
    assert fixture.read_text(encoding="utf-8").strip() == encode_config_share(payload)


def test_share_rejects_oversized_input_and_encode_output():
    with pytest.raises(ValueError, match="too large"):
        decode_config_share("FFC2:" + "A" * 524289)
    with pytest.raises(ValueError, match="too large"):
        encode_config_share({"film": {"name": "x" * 262145}})


def test_phone_factory_strip_config_can_be_shared_and_retained():
    values = {"film_border": {"enabled": True, "format_key": "135", "sprocket_rows": "both", "factory_marks": True, "stock_label": "TEST FILM"}}
    decoded = decode_config_share(encode_config_share(values))
    config = DarkroomConfig.from_dict(decoded)
    assert config.film_border.factory_marks
    assert config.film_border.stock_label == "TEST FILM"

"""GUI-facing labels for headless workflow catalogs and option keys.

The application layer returns preset keys, polarity, and untranslated names.
This module is the only place those values are turned into display strings.
"""

from __future__ import annotations

from pathlib import Path

from film_foundry.app import workflow as _workflow
from film_foundry.core.accidents import LIGHT_LEAK_CONFIG_TEMPLATE_KEYS
from film_foundry.core.film_border import (
    FILM_BORDER_IMPRINT_SIDES,
    FILM_BORDER_IMPRINT_STYLES,
)
from film_foundry.ui.i18n import tr, current_language


def seed_strategy_labels() -> dict[str, str]:
    return {
        key: tr(f"seed.strategy.{key}")
        for key in _workflow.SEED_STRATEGY_KEYS
    }


def seed_strategy_label(key: str) -> str:
    normalized = str(key).strip().lower()
    return seed_strategy_labels().get(normalized, seed_strategy_labels()["random"])


def seed_strategy_key_from_label(label: str) -> str:
    return _key_from_labels(
        seed_strategy_labels(),
        label,
        default="random",
        allowed=_workflow.SEED_STRATEGY_KEYS,
    )


def light_leak_template_labels() -> dict[str, str]:
    return {
        key: tr(f"light_leak.template.{key}")
        for key in LIGHT_LEAK_CONFIG_TEMPLATE_KEYS
    }


def light_leak_template_label(key: str) -> str:
    labels = light_leak_template_labels()
    normalized = str(key).strip().lower()
    return labels.get(normalized, labels["auto"])


def light_leak_template_key_from_label(label: str) -> str:
    return _key_from_labels(
        light_leak_template_labels(),
        label,
        default="auto",
        allowed=LIGHT_LEAK_CONFIG_TEMPLATE_KEYS,
    )


def imprint_style_labels() -> dict[str, str]:
    return {key: tr(f"film_border.imprint.{key}") for key in FILM_BORDER_IMPRINT_STYLES}


def imprint_style_label(key: str) -> str:
    labels = imprint_style_labels()
    normalized = str(key).strip().lower()
    return labels.get(normalized, labels["none"])


def imprint_style_key_from_label(label: str) -> str:
    return _key_from_labels(
        imprint_style_labels(),
        label,
        default="none",
        allowed=FILM_BORDER_IMPRINT_STYLES,
    )


def imprint_side_labels() -> dict[str, str]:
    return {key: tr(f"film_border.side.{key}") for key in FILM_BORDER_IMPRINT_SIDES}


def imprint_side_label(key: str) -> str:
    labels = imprint_side_labels()
    normalized = str(key).strip().lower()
    return labels.get(normalized, labels["bottom"])


def imprint_side_key_from_label(label: str) -> str:
    return _key_from_labels(
        imprint_side_labels(),
        label,
        default="bottom",
        allowed=FILM_BORDER_IMPRINT_SIDES,
    )


def material_polarity_label(polarity: str) -> str:
    return tr(f"material.{polarity}")


def material_color_label(color: str) -> str:
    return tr(f"material.{color}")


def localized_preset_name(kind: str, key: str, fallback: str) -> str:
    """Translate built-in preset names without replacing user-defined names."""
    path = Path(key)
    user_dirs = {
        "film": _workflow.USER_FILM_PRESET_DIR,
        "develop": _workflow.USER_DEVELOP_PRESET_DIR,
        "scanner": _workflow.USER_SCANNER_PRESET_DIR,
    }
    builtin_dirs = {
        "film": _workflow.FILM_PRESET_DIR,
        "develop": _workflow.DEVELOP_PRESET_DIR,
        "scanner": _workflow.SCANNER_PRESET_DIR,
    }
    is_builtin = (
        not path.exists()
        and not (user_dirs[kind] / f"{key}.json").exists()
        and (builtin_dirs[kind] / f"{key}.json").exists()
    )
    from film_foundry.app.seasonal_collection import collection_ids
    is_builtin = is_builtin or (key in collection_ids(kind) and not path.exists() and not (user_dirs[kind]/f"{key}.json").exists())
    translation_key = f"preset.{kind}.{Path(key).stem}"
    if is_builtin:
        from film_foundry.app.seasonal_collection import localized_name
        seasonal = localized_name(kind, key, current_language())
        if seasonal:
            return seasonal
    translated = tr(translation_key)
    return translated if is_builtin and translated != translation_key else fallback


def film_preset_record(key: str) -> dict[str, str] | None:
    record = _workflow.film_preset_record(key)
    if record is None:
        return None
    display_name = localized_preset_name("film", key, record["name"])
    labeled = dict(record)
    labeled["label"] = (
        f"{material_color_label(record['color'])} · "
        f"{material_polarity_label(record['polarity'])} · "
        f"{display_name}"
    )
    return labeled


def film_preset_catalog(polarity: str | None = None, *, include_variants: bool = True) -> list[dict[str, str]]:
    records = [
        labeled
        for record in _workflow.film_preset_catalog(polarity, include_variants=include_variants)
        if (labeled := film_preset_record(record["key"])) is not None
    ]
    return sorted(
        records,
        key=lambda record: (record["color"], record["name"].casefold(), record["key"].casefold()),
    )


def develop_preset_record(key: str) -> dict[str, str] | None:
    record = _workflow.develop_preset_record(key)
    if record is None:
        return None
    labeled = dict(record)
    labeled["label"] = localized_preset_name("develop", key, record["name"])
    return labeled


def develop_preset_catalog(*, include_variants: bool = False) -> list[dict[str, str]]:
    return [
        labeled
        for key in _workflow.develop_preset_names(include_variants=include_variants)
        if (labeled := develop_preset_record(key)) is not None
    ]


def scanner_preset_record(key: str) -> dict[str, str] | None:
    record = _workflow.scanner_preset_record(key)
    if record is None:
        return None
    labeled = dict(record)
    labeled["label"] = localized_preset_name("scanner", key, record["name"])
    return labeled


def scanner_preset_catalog(polarity: str | None = None, *, include_variants: bool = True) -> list[dict[str, str]]:
    records = [
        labeled
        for record in _workflow.scanner_preset_catalog(polarity, include_variants=include_variants)
        if (labeled := scanner_preset_record(record["key"])) is not None
    ]
    return records


def scanner_capture_summary(metadata: dict) -> str:
    """Return a compact, localized scanner-raw headroom diagnostic."""
    saturation = float(metadata.get("scanner_saturation_fraction", 0.0)) * 100.0
    floor = float(metadata.get("scanner_floor_fraction", 0.0)) * 100.0
    parts = [
        f"{tr('scan.diagnostic.saturation')} {saturation:.1f}%",
        f"{tr('scan.diagnostic.floor')} {floor:.1f}%",
    ]
    base_channels = metadata.get("base_saturated_channels")
    if isinstance(base_channels, (list, tuple)) and any(bool(value) for value in base_channels):
        clipped = "".join(
            channel for channel, value in zip("RGB", base_channels) if bool(value)
        )
        parts.append(f"{tr('scan.diagnostic.base_clip')} {clipped}")
    return " · ".join(parts)


def preset_source_text(value: str, user_dir: Path, builtin_dir: Path) -> str:
    path = Path(value)
    if path.exists():
        return tr("preset.source.direct")
    if (user_dir / f"{value}.json").exists():
        return tr("preset.source.user")
    from film_foundry.app.seasonal_collection import collection_ids
    if value in collection_ids(builtin_dir.name):
        return tr("preset.source.builtin")
    if (builtin_dir / f"{value}.json").exists():
        if builtin_dir == _workflow.SCANNER_PRESET_DIR and value in {
            "reference_soft_tone_scan", "reference_deep_tone_scan",
        }:
            return tr("preset.source.reference_tone")
        return tr("preset.source.builtin")
    return tr("preset.source.missing")


def _key_from_labels(labels: dict[str, str], label: str, *, default: str, allowed: tuple[str, ...] | None = None) -> str:
    requested = str(label)
    for key, localized in labels.items():
        if requested == localized:
            return key
    normalized = requested.strip().lower()
    if allowed is not None and normalized in allowed:
        return normalized
    return default

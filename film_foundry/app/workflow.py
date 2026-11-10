"""Headless main-console workflow helpers.

Path planning, preset catalogs, batch records, and medium-file observation live
here so CLI, the IDE script, and the Qt panel share one application layer.
Display strings and preview mosaics stay in the UI layer; CLI/IDE keep their
own output naming and scan third-value contracts.
"""

from __future__ import annotations

import copy
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from time import perf_counter

from film_foundry.core.source_image import load_formation_image
from film_foundry.core.engine import expose_linear_to_latent
import numpy as np

from film_foundry.app.control import WorkflowControl
from film_foundry.core.engine import (
    apply_optical_observation_snapshot,
    configure_scan_interpretation,
    develop_negative,
    develop_negative_from_latent,
    expose_image_to_latent,
    process_array,
    process_file,
    scan_medium_output,
    scan_medium_direct,
    scan_scanner_raw_direct,
    save_developed_medium_at_path,
    seed_from_path,
)
from film_foundry.core.electronic_negative import (
    load_linear_rgb_tiff,
    split_scanner_raw_border,
)
from film_foundry.core.execution import (
    processing_long_edge,
    resolve_execution_axes,
)
from film_foundry.core.io_utils import (
    SUPPORTED_EXTENSIONS,
    assert_unique_output_stems,
    iter_images,
    load_image,
    load_image_with_color,
    output_target_is_file,
    save_image_bundle,
    scan_output_stem,
)
from film_foundry.core.negative_io import load_developed_medium_for_scan
from film_foundry.core.latent_io import (
    LATENT_SUFFIX,
    latent_identity_from_film,
    load_exposed_latent,
    save_exposed_latent,
)
from film_foundry.core.frame_identity import (
    attach_frame_identity,
    frame_identity_from_mapping,
    normalize_frame_identity,
)
from film_foundry.core.preview import (
    negative_visual_preview,
    optical_density_visual_preview,
    resize_to_long_edge,
)
from film_foundry.core.sidecar import (
    final_positive_sidecar,
    load_scanner_raw_sidecar,
    scanner_raw_border_width_from_sidecar,
    scanner_raw_optical_observation_from_sidecar,
    transmission_raw_source_kind,
)
from film_foundry.core.states import DevelopedNegative
from film_foundry.contracts.config_share import decode_config_share, encode_config_share
from film_foundry.contracts.data_kernel import CONFIG_SHARE_FIELDS
from film_foundry.core.atomic_io import atomic_write_json
from film_foundry.model.config import SCAN_LOOK_FIELDS, DarkroomConfig, merge_config_presets
from film_foundry.model.session import (
    load_session,
    preset_reference as _session_preset_reference,
    preset_value_from_reference,
    save_session,
    session_payload,
)
from film_foundry.app.paths import app_root, resource_root
from film_foundry.app.preset_catalog import CORE_DEVELOP_PRESETS, CORE_FILM_PRESETS, CORE_SCANNER_PRESETS
from film_foundry.app.seasonal_collection import collection_ids, visible_builtin_names

PROJECT_ROOT = app_root()
RESOURCE_ROOT = resource_root()
APP_TITLE = "Film Foundry - Imaging Medium Workflow"
PRESET_DIR = RESOURCE_ROOT / "film_foundry" / "presets"
FILM_PRESET_DIR = PRESET_DIR / "film"
DEVELOP_PRESET_DIR = PRESET_DIR / "develop"
SCANNER_PRESET_DIR = PRESET_DIR / "scanner"
USER_PRESET_DIR = PROJECT_ROOT / "user_presets"
USER_FILM_PRESET_DIR = USER_PRESET_DIR / "film"
USER_DEVELOP_PRESET_DIR = USER_PRESET_DIR / "develop"
USER_SCANNER_PRESET_DIR = USER_PRESET_DIR / "scanner"
INTERNAL_PRESET_PREFIXES = ("diagnostic_", "accident_", "experimental_")
MAIN_GUI_BUILTIN_DEVELOP_PRESETS = CORE_DEVELOP_PRESETS
DEFAULT_INPUT_DIR = PROJECT_ROOT / "input_images"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs"
DEFAULT_NEGATIVE_DIR = DEFAULT_OUTPUT_DIR / "negatives"
PREVIEW_PROCESS_LONG_EDGE = 900
PREVIEW_DISPLAY_LONG_EDGE = 520
NEGATIVE_SUFFIX = ".darkroom_negative.npz"
POSITIVE_SUFFIX = ".darkroom_positive.npz"
SEED_STRATEGY_KEYS = ("random", "fixed", "path")


def preset_reference(value, kind: str, user_dir: Path, builtin_dir: Path) -> dict:
    reference=_session_preset_reference(value,kind,user_dir,builtin_dir)
    if reference["source"]=="missing" and str(value) in collection_ids(kind):
        from film_foundry.app.seasonal_collection import preset_path
        reference.update(source="builtin",source_label="官方预设",path=str(preset_path(kind,str(value)).resolve()))
    return reference


def _workflow_stage(
    control: WorkflowControl | None,
    command: str,
    stage: str,
) -> None:
    if control is None:
        return
    control.report(command, stage)


def ensure_user_dirs() -> None:
    DEFAULT_INPUT_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_NEGATIVE_DIR.mkdir(parents=True, exist_ok=True)
    USER_FILM_PRESET_DIR.mkdir(parents=True, exist_ok=True)
    USER_DEVELOP_PRESET_DIR.mkdir(parents=True, exist_ok=True)
    USER_SCANNER_PRESET_DIR.mkdir(parents=True, exist_ok=True)


def safe_preset_stem(name: str) -> str:
    """Filesystem-safe user preset name. Empty if nothing usable remains."""
    stem: list[str] = []
    for char in name.strip().lower():
        if char.isalnum() or char in {"_", "-"}:
            stem.append(char)
        elif char.isspace():
            stem.append("_")
    return "".join(stem).strip("_-")


def film_preset_payload(config: DarkroomConfig) -> dict:
    return {
        "mode": str(config.mode),
        "medium": str(config.medium),
        "film": asdict(config.film),
    }


def scanner_preset_payload(config: DarkroomConfig) -> dict:
    look = {field_name: getattr(config.look, field_name) for field_name in SCAN_LOOK_FIELDS}
    return {
        "scanner": asdict(config.scanner),
        "look": look,
        "enable_subtractive": bool(config.enable_subtractive),
    }


def save_user_film_preset(
    name: str,
    config: DarkroomConfig,
    *,
    overwrite: bool = False,
) -> Path:
    """Write the film section of a runtime config as a user material preset."""
    ensure_user_dirs()
    stem = safe_preset_stem(name)
    if not stem:
        raise ValueError("film preset filename cannot be empty.")
    path = USER_FILM_PRESET_DIR / f"{stem}.json"
    if path.exists() and not overwrite:
        raise FileExistsError(str(path))
    atomic_write_json(path, film_preset_payload(config))
    return path


def save_user_scanner_preset(
    name: str,
    config: DarkroomConfig,
    *,
    overwrite: bool = False,
) -> Path:
    """Write the scanner/observation section as a user scan preset."""
    ensure_user_dirs()
    stem = safe_preset_stem(name)
    if not stem:
        raise ValueError("scanner preset filename cannot be empty.")
    path = USER_SCANNER_PRESET_DIR / f"{stem}.json"
    if path.exists() and not overwrite:
        raise FileExistsError(str(path))
    atomic_write_json(path, scanner_preset_payload(config))
    return path


def output_path_for(
    input_path: Path,
    output_root: Path,
    output_format: str,
    *,
    run_suffix: str | None = None,
) -> Path:
    if output_target_is_file(output_root):
        return output_root
    suffix = "." + output_format.lower().lstrip(".")
    stem = f"{scan_output_stem(input_path)}_darkroom"
    if run_suffix:
        stem += f"_{run_suffix}"
    return output_root / f"{stem}{suffix}"


def run_timestamp(now: datetime | None = None) -> str:
    """Return a filesystem-safe local run timestamp."""
    return (now or datetime.now()).strftime("%Y-%m-%d_%H-%M-%S")


def create_run_directory(
    output_root: Path,
    mode: str,
    *,
    timestamp: str | None = None,
) -> tuple[Path, str]:
    """Atomically reserve a non-overwriting directory for one GUI run."""
    output_root = Path(output_root)
    if output_target_is_file(output_root):
        raise ValueError(f"运行输出位置必须是文件夹：{output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    base_id = f"{timestamp or run_timestamp()}_{mode}"
    for index in range(1, 10_000):
        run_id = base_id if index == 1 else f"{base_id}_{index:02d}"
        candidate = output_root / run_id
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        return candidate, run_id
    raise FileExistsError(f"无法创建唯一运行目录：{output_root / base_id}")


def default_scan_output_root(developed_source: Path) -> Path:
    """Place scan-only results one directory above the selected source folder."""
    source = Path(developed_source)
    if source.suffix.lower() in {".npz", ".tif", ".tiff"}:
        containing_folder = source.parent
        if containing_folder.name.lower() in {"negative", "negatives"}:
            return containing_folder.parent
        return containing_folder
    return source.parent


def unique_scan_run_id(
    output_root: Path,
    *,
    timestamp: str | None = None,
) -> str:
    """Choose a scan generation id without replacing a previous run manifest."""
    base_id = f"{timestamp or run_timestamp()}_scan"
    for index in range(1, 10_000):
        run_id = base_id if index == 1 else f"{base_id}_{index:02d}"
        if not (Path(output_root) / f"film_foundry_{run_id}.json").exists():
            return run_id
    raise FileExistsError(f"无法创建唯一扫描运行标识：{output_root / base_id}")


def batch_item_record(
    input_path: Path,
    output_path: Path | None,
    elapsed_seconds: float,
    error: BaseException | None = None,
) -> dict[str, object]:
    """Build one lightweight batch record without inspecting output files."""
    record: dict[str, object] = {
        "input": str(input_path),
        "output": str(output_path) if output_path is not None else None,
        "status": "failed" if error is not None else "completed",
        "elapsed_seconds": round(max(float(elapsed_seconds), 0.0), 6),
    }
    if error is not None:
        record["error"] = {
            "type": type(error).__name__,
            "message": str(error),
        }
    return record


def finalize_batch_run(
    payload: dict,
    *,
    operation: str,
    started_at: str,
    elapsed_seconds: float,
    items: list[dict[str, object]],
) -> None:
    """Attach one in-memory batch summary before the final manifest write."""
    completed = sum(item.get("status") == "completed" for item in items)
    failed = len(items) - completed
    status = "completed" if failed == 0 else ("failed" if completed == 0 else "completed_with_errors")
    finished_at = datetime.now().isoformat(timespec="seconds")
    payload["batch"] = {
        "version": 1,
        "operation": str(operation),
        "status": status,
        "started_at": str(started_at),
        "finished_at": finished_at,
        "elapsed_seconds": round(max(float(elapsed_seconds), 0.0), 6),
        "completed": int(completed),
        "failed": int(failed),
        "items": items,
        "recording_strategy": "memory_then_single_atomic_write",
    }
    run_record = payload.get("run")
    if isinstance(run_record, dict):
        run_record["status"] = status


def save_batch_run_record(
    path: Path,
    payload: dict,
    *,
    operation: str,
    started_at: str,
    started_counter: float,
    items: list[dict[str, object]],
) -> Exception | None:
    """Finalize and atomically save a batch record without failing image work."""
    finalize_batch_run(
        payload,
        operation=operation,
        started_at=started_at,
        elapsed_seconds=perf_counter() - float(started_counter),
        items=items,
    )
    try:
        save_session(path, payload)
    except Exception as exc:  # Recording must not invalidate completed outputs.
        return exc
    return None


def planned_developed_path(
    input_path: Path,
    output_root: Path,
    *,
    image_polarity: str,
) -> Path:
    """Destination for one developed archive before the medium exists."""
    if output_root.suffix.lower() == ".npz":
        return output_root
    suffix = POSITIVE_SUFFIX if str(image_polarity).lower() == "positive" else NEGATIVE_SUFFIX
    return output_root / f"{input_path.stem}{suffix}"


def developed_path_for(
    input_path: Path,
    output_root: Path,
    medium: DevelopedNegative,
) -> Path:
    return planned_developed_path(
        input_path,
        output_root,
        image_polarity=str(medium.image_polarity),
    )


def scanner_raw_path_for_negative(negative_path: Path) -> Path:
    return negative_path.with_suffix(".scanner_raw.tiff")


def transmission_raw_path_for_medium(medium_path: Path) -> Path:
    if medium_path.name.lower().endswith(POSITIVE_SUFFIX):
        return medium_path.with_suffix(".light_table_raw.tiff")
    return scanner_raw_path_for_negative(medium_path)


def is_scanner_raw_tiff(path: Path) -> bool:
    return path.suffix.lower() in {".tif", ".tiff"} and any(
        token in path.stem.lower() for token in (".scanner_raw", ".light_table_raw")
    )


def iter_developed_medium_files(path: Path) -> list[Path]:
    if path.is_file() and (path.suffix.lower() == ".npz" or path.suffix.lower() in SUPPORTED_EXTENSIONS):
        return [path]
    if path.is_dir():
        npz_paths = sorted(path.glob(f"*{NEGATIVE_SUFFIX}"))
        npz_paths += sorted(path.glob(f"*{POSITIVE_SUFFIX}"))
        raw_paths = sorted(item for item in path.glob("*.tif*") if is_scanner_raw_tiff(item))
        npz_raw_paths = {transmission_raw_path_for_medium(item).resolve() for item in npz_paths}
        return npz_paths + [item for item in raw_paths if item.resolve() not in npz_raw_paths]
    return []


# Backward-compatible name for sessions/tests created before positive masters.
iter_negative_files = iter_developed_medium_files


def scale_dye_selectivity(matrix, selectivity: float):
    """调节染料吸收矩阵的光谱选择性，而不是直接在 RGB 上拉饱和度。"""
    selectivity = max(0.0, float(selectivity))
    rows = []
    for row in matrix:
        neutral = sum(float(v) for v in row) / 3.0
        rows.append(tuple(max(0.0, neutral + (float(v) - neutral) * selectivity) for v in row))
    return tuple(rows)


def density_preview(density_cmy: np.ndarray, config: DarkroomConfig) -> np.ndarray:
    d_min = np.asarray(config.film.density_min, dtype=np.float32)
    d_max = np.asarray(config.film.density_max, dtype=np.float32)
    return np.clip((density_cmy - d_min) / np.maximum(d_max - d_min, 1e-6), 0.0, 1.0)


def gray_to_rgb(values: np.ndarray) -> np.ndarray:
    values = np.clip(np.asarray(values, dtype=np.float32), 0.0, 1.0)
    return np.repeat(values[..., None], 3, axis=-1)


def resolved_seed_for(path: Path, config: DarkroomConfig, *, path_seed_offset=None) -> int | None:
    from film_foundry.app.source_seed import source_seed_override
    override = source_seed_override(config, path_seed_offset)
    if override is not None:
        return override
    strategy = str(config.seed_strategy).lower()
    if strategy == "fixed":
        return 0 if config.random_seed is None else int(config.random_seed)
    if strategy == "path":
        return seed_from_path(path, 0 if config.random_seed is None else int(config.random_seed))
    return None


def rng_and_resolved_seed_for_develop(
    path: Path,
    config: DarkroomConfig,
    *, path_seed_offset=None,
) -> tuple[np.random.Generator, int]:
    seed = resolved_seed_for(path, config, path_seed_offset=path_seed_offset)
    if seed is None:
        seed = int(np.random.SeedSequence().generate_state(1, dtype=np.uint32)[0])
    return np.random.default_rng(seed), seed


def rng_for_develop(path: Path, config: DarkroomConfig, *, path_seed_offset=None) -> np.random.Generator:
    rng, _ = rng_and_resolved_seed_for_develop(path, config, path_seed_offset=path_seed_offset)
    return rng


def prepare_develop_runtime_config(
    config: DarkroomConfig,
    *,
    scaled_override: bool = False,
) -> tuple[DarkroomConfig, object]:
    """Freeze execution axes onto a private runtime copy for one develop job."""
    execution_axes = resolve_execution_axes(config, scaled_override=scaled_override)
    runtime_config = copy.deepcopy(config)
    runtime_config.processing.execution_mode = (
        "scaled_fast"
        if execution_axes.scaling_enabled
        else ("reduced_fast" if execution_axes.lower_order_enabled else "quality")
    )
    runtime_config.fast_mode = execution_axes.lower_order_enabled
    return runtime_config, execution_axes


def develop_image_file(
    input_path: Path,
    developed_root: Path,
    config: DarkroomConfig,
    *,
    scaled_override: bool = False,
    frame_identity: dict | None = None,
    control: WorkflowControl | None = None,
    path_seed_offset: int | None = None,
) -> tuple[DevelopedNegative, Path, dict[str, str]]:
    """Develop one input image and write the polarity-aware medium bundle."""
    identity = normalize_frame_identity(frame_identity)
    runtime_config, execution_axes = prepare_develop_runtime_config(
        config,
        scaled_override=scaled_override,
    )
    long_edge = processing_long_edge(config, scaled_override=scaled_override)
    _workflow_stage(control, "develop", "loading")
    if execution_axes.scaling_enabled:
        image, input_color = load_formation_image(
            input_path,
            decode_long_edge=long_edge,
            input_icc_policy=runtime_config.input_icc_policy,
        )
    else:
        image, input_color = load_formation_image(
            input_path,
            input_icc_policy=runtime_config.input_icc_policy,
        )
    image = resize_to_long_edge(image, long_edge)
    develop_rng, resolved_seed = rng_and_resolved_seed_for_develop(input_path, runtime_config, path_seed_offset=path_seed_offset)
    _workflow_stage(control, "develop", "developing")
    medium = develop_negative(image, runtime_config, rng=develop_rng, input_encoding=input_color["encoding"])
    medium.metadata["input_color"] = input_color
    attach_frame_identity(medium.metadata, identity)
    del image
    developed_path = developed_path_for(input_path, developed_root, medium)
    _workflow_stage(control, "develop", "saving")
    paths = save_developed_medium_at_path(
        input_path,
        developed_path,
        medium,
        runtime_config,
        resolved_seed=resolved_seed,
    )
    return medium, developed_path, paths


def latent_path_for(input_path: Path, output_root: Path) -> Path:
    if output_root.suffix.lower() == ".npz":
        return output_root
    return output_root / f"{input_path.stem}{LATENT_SUFFIX}"


def iter_latent_files(path: Path) -> list[Path]:
    if path.is_file() and path.name.lower().endswith(LATENT_SUFFIX):
        return [path]
    if path.is_dir():
        return sorted(path.glob(f"*{LATENT_SUFFIX}"))
    return []


def expose_image_file(
    input_path: Path,
    latent_root: Path,
    config: DarkroomConfig,
    *,
    scaled_override: bool = False,
    frame_identity: dict | None = None,
    control: WorkflowControl | None = None,
    path_seed_offset: int | None = None,
) -> Path:
    """Expose one input image to an undeveloped latent archive."""
    identity_overlay = normalize_frame_identity(frame_identity)
    runtime_config, execution_axes = prepare_develop_runtime_config(
        config,
        scaled_override=scaled_override,
    )
    long_edge = processing_long_edge(config, scaled_override=scaled_override)
    _workflow_stage(control, "expose", "loading")
    if execution_axes.scaling_enabled:
        image, _input_color = load_formation_image(
            input_path,
            decode_long_edge=long_edge,
            input_icc_policy=runtime_config.input_icc_policy,
        )
    else:
        image, _input_color = load_formation_image(
            input_path,
            input_icc_policy=runtime_config.input_icc_policy,
        )
    image = resize_to_long_edge(image, long_edge)
    expose_rng, _resolved_seed = rng_and_resolved_seed_for_develop(
        input_path, runtime_config, path_seed_offset=path_seed_offset
    )
    _workflow_stage(control, "expose", "exposing")
    expose = expose_linear_to_latent if _input_color["encoding"] == "linear_rgb" else expose_image_to_latent
    state = expose(image, runtime_config, rng=expose_rng)
    del image
    latent_path = latent_path_for(input_path, latent_root)
    if latent_path.resolve() == Path(input_path).resolve():
        raise ValueError("Latent output path must not overwrite the source input file.")
    identity = latent_identity_from_film(runtime_config.film)
    identity["input_color"] = _input_color
    if runtime_config.chemistry.incident_identity_seed is not None:
        identity["capture_incident_identity"]={"model":"roll_batch_incident_identity_v1","seed":int(runtime_config.chemistry.incident_identity_seed)}
    attach_frame_identity(identity, identity_overlay)
    _workflow_stage(control, "expose", "saving")
    save_exposed_latent(latent_path, state, identity=identity)
    return latent_path


def develop_latent_file(
    input_path: Path,
    developed_root: Path,
    config: DarkroomConfig,
    *,
    scaled_override: bool = False,
    frame_identity: dict | None = None,
    control: WorkflowControl | None = None,
    path_seed_offset: int | None = None,
) -> tuple[DevelopedNegative, Path, dict[str, str]]:
    """Develop an owned in-memory working state; preserve the source archive.

    Output bundles are committed by the existing transactional exporter. Old
    archives explicitly marked consumed remain rejected by the latent reader.
    """
    override = normalize_frame_identity(frame_identity)
    runtime_config, _execution_axes = prepare_develop_runtime_config(
        config,
        scaled_override=scaled_override,
    )
    _workflow_stage(control, "develop_latent", "loading")
    state, metadata = load_exposed_latent(input_path)
    inherited = frame_identity_from_mapping(metadata)
    develop_rng, resolved_seed = rng_and_resolved_seed_for_develop(
        input_path, runtime_config, path_seed_offset=path_seed_offset
    )
    _workflow_stage(control, "develop_latent", "developing")
    medium = develop_negative_from_latent(state, runtime_config, rng=develop_rng)
    if isinstance(metadata.get("input_color"), dict):
        medium.metadata["input_color"] = copy.deepcopy(metadata["input_color"])
    from film_foundry.contracts.optical_cascade import CASCADE_KEY, validate_cascade
    if CASCADE_KEY in metadata:
        medium.metadata['film_process_model'][CASCADE_KEY] = validate_cascade(metadata[CASCADE_KEY])
    if isinstance(metadata.get("capture_incident_identity"),dict):
        medium.metadata["film_process_model"]["capture_incident_identity"]=copy.deepcopy(metadata["capture_incident_identity"])
    attach_frame_identity(
        medium.metadata,
        override if override is not None else inherited,
    )
    developed_path = developed_path_for(input_path, developed_root, medium)
    _workflow_stage(control, "develop_latent", "saving")
    paths = save_developed_medium_at_path(
        input_path,
        developed_path,
        medium,
        runtime_config,
        resolved_seed=resolved_seed,
    )
    return medium, developed_path, paths


def process_image_file(
    input_path: Path,
    output_path: Path,
    config: DarkroomConfig,
    *,
    scaled_override: bool = False,
    control: WorkflowControl | None = None,
    artifact_paths: dict[str,str] | None = None,
    path_seed_offset: int | None = None,
) -> Path:
    """Run the full develop-and-scan file pipeline through the application layer."""
    _workflow_stage(control, "full", "developing")
    from film_foundry.app.source_seed import source_seed_override
    seed = source_seed_override(config, path_seed_offset)
    seed_options = {'resolved_seed_override': seed} if seed is not None else {}
    return process_file(input_path, output_path, config, preview=scaled_override,artifact_paths=artifact_paths, **seed_options)


def is_internal_preset_name(name: str) -> bool:
    normalized = name.lower()
    return normalized.startswith(INTERNAL_PRESET_PREFIXES)


def public_builtin_preset_names(directory: Path) -> set[str]:
    return {
        path.stem
        for path in directory.glob("*.json")
        if not is_internal_preset_name(path.stem)
    }


def develop_preset_names(*, include_variants: bool = False) -> list[str]:
    # Keep the daily GUI concise. Specialized B&W recipes remain available in
    # the process editor and CLI, while user presets are always shown. The one
    # experimental clear-base reversal recipe is intentionally exposed because
    # it represents a distinct, user-selectable color-negative reversal path.
    names = {
        path.stem
        for path in DEVELOP_PRESET_DIR.glob("*.json")
        if path.stem in CORE_DEVELOP_PRESETS
        or (include_variants and not is_internal_preset_name(path.stem))
    }
    names.update(path.stem for path in USER_DEVELOP_PRESET_DIR.glob("*.json"))
    return sorted(names)


def develop_preset_path(name: str) -> Path:
    path = Path(name)
    if path.exists():
        return path
    user_path = USER_DEVELOP_PRESET_DIR / f"{name}.json"
    if user_path.exists():
        return user_path
    return DEVELOP_PRESET_DIR / f"{name}.json"


def film_preset_names(*, include_variants: bool = True) -> list[str]:
    names = visible_builtin_names(public_builtin_preset_names(FILM_PRESET_DIR), "film")
    if not include_variants:
        names.intersection_update(CORE_FILM_PRESETS | collection_ids("film"))
    names.update(path.stem for path in USER_FILM_PRESET_DIR.glob("*.json"))
    return sorted(names)


def film_preset_path(name: str) -> Path:
    path = Path(name)
    if path.exists():
        return path
    user_path = USER_FILM_PRESET_DIR / f"{name}.json"
    if user_path.exists():
        return user_path
    from film_foundry.app.seasonal_collection import preset_path
    optional=preset_path("film",name)
    if optional is not None:
        return optional
    return FILM_PRESET_DIR / f"{name}.json"


def material_polarity_category(config: DarkroomConfig) -> str:
    """Return the material's native result category used by the develop UI."""
    polarity = str(config.film.image_polarity).strip().lower()
    process = str(config.film.medium_process).strip().lower()
    return "positive" if polarity == "positive" or process in {"positive", "reversal", "slide"} else "negative"


def material_color_category(config: DarkroomConfig) -> str:
    color_process = str(config.film.color_process).strip().lower()
    return "monochrome" if color_process in {"bw", "black_white", "black-and-white", "monochrome"} else "color"


from film_foundry.core.file_cache import FileRevisionCache
_preset_summary_cache = FileRevisionCache(256)


def _preset_record(kind: str, key: str, path: Path) -> dict[str, str] | None:
    def describe(target):
        config = DarkroomConfig.from_json(target)
        if kind == 'film':
            return {'name': str(config.film.name).strip() or target.stem,
                    'polarity': material_polarity_category(config), 'color': material_color_category(config)}
        if kind == 'develop':
            return {'name': str(config.chemistry.developer_name).strip() or target.stem}
        return {'name': target.stem.replace('_', ' ').title(), 'polarity': scanner_preset_polarity(config)}
    try:
        record = _preset_summary_cache.get(path, describe, namespace=kind)
    except (OSError, ValueError, TypeError, KeyError):
        return None
    # Materialized hidden resources have numeric filenames, not display names.
    if kind == 'scanner':record['name'] = Path(key).stem.replace('_', ' ').title()
    return {'key': key, **record}


def film_preset_record(key: str) -> dict[str, str] | None:
    return _preset_record('film', key, film_preset_path(key))


def film_preset_catalog(polarity: str | None = None, *, include_variants: bool = True) -> list[dict[str, str]]:
    records = [record for key in film_preset_names(include_variants=include_variants) if (record := film_preset_record(key)) is not None]
    if polarity is not None:
        records = [record for record in records if record["polarity"] == polarity]
    return sorted(records, key=lambda record: (record["color"], record["name"].casefold(), record["key"].casefold()))


def develop_preset_record(key: str) -> dict[str, str] | None:
    return _preset_record('develop', key, develop_preset_path(key))


def develop_preset_catalog(*, include_variants: bool = False) -> list[dict[str, str]]:
    return [record for key in develop_preset_names(include_variants=include_variants) if (record := develop_preset_record(key)) is not None]


def scanner_preset_polarity(config: DarkroomConfig) -> str:
    scanner = config.scanner
    return "positive" if str(scanner.interpreter_key).lower() == "positive_transparency_scan" or str(scanner.input_polarity).lower() == "positive" else "negative"


def scanner_preset_record(key: str) -> dict[str, str] | None:
    return _preset_record('scanner', key, scanner_preset_path(key))


def scanner_preset_catalog(polarity: str | None = None, *, include_variants: bool = True) -> list[dict[str, str]]:
    records = [record for key in scanner_preset_names(include_variants=include_variants) if (record := scanner_preset_record(key)) is not None]
    if polarity is not None:
        records = [record for record in records if record["polarity"] == polarity]
    return records


def scanner_preset_names(*, include_variants: bool = True) -> list[str]:
    names = visible_builtin_names(public_builtin_preset_names(SCANNER_PRESET_DIR), "scanner")
    if not include_variants:
        names.intersection_update(CORE_SCANNER_PRESETS | collection_ids("scanner"))
    names.update(path.stem for path in USER_SCANNER_PRESET_DIR.glob("*.json"))
    return sorted(names)


def scanner_preset_path(name: str) -> Path:
    path = Path(name)
    if path.exists():
        return path
    user_path = USER_SCANNER_PRESET_DIR / f"{name}.json"
    if user_path.exists():
        return user_path
    from film_foundry.app.seasonal_collection import preset_path
    optional=preset_path("scanner",name)
    if optional is not None:
        return optional
    return SCANNER_PRESET_DIR / f"{name}.json"


_SHARE_PAYLOAD_SOURCES = {
    "film": "film",
    "exposure": "exposure",
    "develop": "chemistry",
    "scanner": "scanner",
    "look": "look",
    "film_border": "film_border",
    "mode": "mode",
    "medium": "medium",
    "enable_mtf": "enable_mtf",
    "enable_halation": "enable_halation",
    "enable_grain": "enable_grain",
    "enable_subtractive": "enable_subtractive",
}


def recipe_share_payload(config: DarkroomConfig) -> dict[str, object]:
    """Recipe fields carried by a share code. Runtime/output axes stay local."""
    if config.process_control_mode=='principle':
        from film_foundry.core.process_controls import condition_driven_config
        config=condition_driven_config(config)
    data = asdict(config)
    payload: dict[str, object] = {}
    for field in CONFIG_SHARE_FIELDS:
        # Older frontends reject unknown top-level sections. Do not introduce
        # a capture-clock extension into recipes that do not use it.
        if field == 'exposure' and config.exposure.duration_seconds is None:
            continue
        source = _SHARE_PAYLOAD_SOURCES[field]
        payload[field] = data[source]
    return payload


def encode_recipe_share(config: DarkroomConfig) -> str:
    if config.chemistry.light_leak_mask_path:
        raise ValueError('FFC2 does not embed external leak-mask images. Use a built-in mask or share the configuration and mask files separately.')
    return encode_config_share(recipe_share_payload(config))


def config_from_share_code(text: str) -> DarkroomConfig:
    """Rebuild a recipe from parameter values in the share code."""
    return DarkroomConfig.from_dict(decode_config_share(text))


def recommended_standard_develop_preset(config: DarkroomConfig) -> str:
    positive = material_polarity_category(config) == "positive"
    monochrome = material_color_category(config) == "monochrome"
    if positive:
        return "standard_bw_reversal" if monochrome else "standard_color_reversal"
    return "standard_bw_negative" if monochrome else "standard_color_negative"


def save_developed_medium(
    negative: DevelopedNegative,
    path: Path,
    input_path: Path,
    config: DarkroomConfig,
    save_sidecar: bool,
    *,
    resolved_seed: int | None = None,
) -> None:
    """Save through the shared polarity-aware medium exporter."""
    config.save_sidecar = bool(save_sidecar)
    save_developed_medium_at_path(
        input_path,
        path,
        negative,
        config,
        resolved_seed=(
            resolved_seed_for(input_path, config)
            if resolved_seed is None
            else int(resolved_seed)
        ),
    )


def load_developed_medium(path: Path) -> DevelopedNegative:
    return load_developed_medium_for_scan(path)


# Compatibility aliases for the older electronic-negative-only GUI API.
save_negative = save_developed_medium
load_negative = load_developed_medium


def _observe_transmission_raw(
    path: Path,
    config: DarkroomConfig,
    interpretation: str,
    preview_long_edge: int | None = None,
):
    raw_sidecar = load_scanner_raw_sidecar(path)
    apply_optical_observation_snapshot(
        config,
        scanner_raw_optical_observation_from_sidecar(raw_sidecar),
    )
    scanner_raw = load_linear_rgb_tiff(path)
    source_kind = transmission_raw_source_kind(path, raw_sidecar)
    border_width = scanner_raw_border_width_from_sidecar(raw_sidecar, scanner_raw.shape)
    if border_width is not None:
        inner, border_samples = split_scanner_raw_border(
            scanner_raw,
            border_width_px=border_width,
        )
    elif source_kind == "light_table_raw_tiff":
        inner, border_samples = scanner_raw, None
    else:
        inner, border_samples = split_scanner_raw_border(
            scanner_raw,
            border_percent=config.output.scanner_raw_border_percent,
            border_min_px=config.output.scanner_raw_border_min_px,
        )
    if preview_long_edge is not None:
        inner = resize_to_long_edge(inner, preview_long_edge)
        scanner_raw = resize_to_long_edge(scanner_raw, preview_long_edge)
    scanned = scan_scanner_raw_direct(
        inner,
        config,
        interpretation,
        base_samples=border_samples,
        source_path=path,
        raw_source_kind=source_kind,
    )
    return scanned, source_kind, path, scanner_raw


def observe_medium_file(
    path: Path,
    config: DarkroomConfig,
    *,
    retain_stages: bool = False,
    control: WorkflowControl | None = None,
    preview_long_edge: int | None = None,
):
    """Observe developed NPZ, transmission raw or an ordinary captured image.

    Returns ``(scanned, source_kind, source_path, preview)``. CLI uses
    ``source_path`` for sidecars and overwrite protection; the GUI inspector
    uses ``preview`` (raw pixels or a density visual).
    ``retain_stages=True`` requests Inspector stages for NPZ observations;
    the default keeps only the final output. Transmission raw retains its
    existing full-stage result contract in either mode.
    An explicit preview_long_edge observes an approximate private reduced
    copy. It never rewrites the archive or changes ordinary/full-size calls.
    """
    _workflow_stage(control, "scan", "loading")
    config = copy.deepcopy(config)
    interpretation = str(config.scanner.interpretation_mode or "auto")
    if is_scanner_raw_tiff(path):
        if config.scanner.spectral_observation_profile is not None:
            raise ValueError("Spectral observation requires a material NPZ, not an already captured RGB TIFF.")
        _workflow_stage(control, "scan", "scanning")
        return _observe_transmission_raw(path, config, interpretation, preview_long_edge)
    if path.suffix.lower() in SUPPORTED_EXTENSIONS:
        from film_foundry.core.color import srgb_to_linear, linear_to_srgb
        pixels, input_color = load_formation_image(path, decode_long_edge=preview_long_edge,
                                                   input_icc_policy=config.input_icc_policy)
        linear = pixels if input_color['encoding']=='linear_rgb' else srgb_to_linear(pixels)
        if interpretation in ('auto',''):
            interpretation='direct';config.scanner.remove_base_mask=False
        _workflow_stage(control, 'scan', 'scanning')
        scanned=scan_scanner_raw_direct(linear,config,interpretation,source_path=path,raw_source_kind='image_rgb')
        scanned.metadata['input_color']=input_color
        preview=linear_to_srgb(pixels) if input_color['encoding']=='linear_rgb' else pixels
        return scanned,'image_rgb',path,preview
    scanner_raw_path = transmission_raw_path_for_medium(path)
    if scanner_raw_path.exists() and config.scanner.spectral_observation_profile is None:
        _workflow_stage(control, "scan", "scanning")
        return _observe_transmission_raw(scanner_raw_path, config, interpretation, preview_long_edge)
    if path.suffix.lower() != ".npz":
        raise ValueError(
            f"不支持的扫描来源：{path}。请选择介质 .npz 或受支持的图像文件，不要选择 sidecar .json。"
        )
    if config.processing.memory_budget_mb is not None:
        from film_foundry.core.negative_io import probe_scan_archive_memory
        from film_foundry.core.resource_planning import estimate_scan_memory, enforce_memory_budget
        layout = probe_scan_archive_memory(path)
        if layout is not None:
            width, height, resident, decode_peak = layout
            estimate = estimate_scan_memory(
                width, height, resident_bytes=resident, decode_peak_bytes=decode_peak,
                preview_long_edge=preview_long_edge, retain_stages=retain_stages,
            )
            enforce_memory_budget(estimate, config.processing.memory_budget_mb, config.processing.memory_budget_policy)
    negative = load_developed_medium(path)
    if preview_long_edge is not None:
        from film_foundry.app.preview_inputs import preview_medium
        negative = preview_medium(negative, preview_long_edge)
    scan = scan_medium_direct if retain_stages else scan_medium_output
    _workflow_stage(control, "scan", "scanning")
    scanned = scan(negative, config, interpretation)
    runtime_config = scanned.metadata.get("runtime_config")
    preview_film = runtime_config.film if isinstance(runtime_config, DarkroomConfig) else config.film
    if negative.optical_density_rgb is not None:
        preview = optical_density_visual_preview(negative.optical_density_rgb)
    else:
        preview = negative_visual_preview(negative.density_grain, preview_film)
    return scanned, "density_npz", path, preview


def scan_from_file(path: Path, config: DarkroomConfig, *, retain_stages: bool = False, preview_long_edge: int | None = None):
    options = {} if preview_long_edge is None else {"preview_long_edge": preview_long_edge}
    scanned, source_kind, _source_path, preview = observe_medium_file(path, config, retain_stages=retain_stages, **options)
    return scanned, source_kind, preview


import numpy as np
import pytest

from film_foundry.core.media import config_development_process
from film_foundry.core.media_registry import (
    get_develop_pipeline,
    get_media_pipeline,
    get_scan_pipeline,
    registered_media_processes,
    registered_media_toolkits,
)
from film_foundry.model.config import DarkroomConfig


def test_registry_lists_only_current_and_experimental_media_without_engine_dependency():
    processes = registered_media_processes()
    assert "negative" in processes
    assert "slide" in processes
    assert "daguerreotype" not in processes
    assert "instant" not in processes


def test_registry_exposes_toolkit_metadata():
    toolkits = {toolkit.key: toolkit for toolkit in registered_media_toolkits()}
    assert toolkits["negative"].status == "stable"
    assert not hasattr(toolkits["negative"], "material_editor_script")
    assert toolkits["negative"].default_interpreter == "negative_scan"
    assert toolkits["negative"].compatible_interpreters == ("negative_scan",)
    assert toolkits["slide"].status == "experimental"
    assert toolkits["slide"].default_interpreter == "positive_transparency_scan"
    assert "instant" not in toolkits


def test_negative_pipeline_is_registered_and_usable():
    from film_foundry.core.engine import develop_negative

    config = DarkroomConfig()
    pipeline = get_media_pipeline(config)
    assert pipeline.key in {"negative", "color_negative", "bw_negative", "film_negative"}
    assert "negative" in registered_media_processes()

    image = np.full((8, 8, 3), 0.5, dtype=np.float32)
    negative = develop_negative(image, config)
    assert negative.density_grain.shape == image.shape
    assert negative.metadata["medium_process"] == "negative"
    assert negative.image_polarity == "negative"
    assert negative.view_mode == "transmissive"
    assert "negative_scan" in negative.compatible_interpreters


def test_future_medium_process_fails_explicitly():
    from film_foundry.core.engine import develop_negative

    config = DarkroomConfig()
    config.film.medium_process = "instant"
    image = np.full((8, 8, 3), 0.5, dtype=np.float32)

    with pytest.raises(NotImplementedError, match="not implemented"):
        develop_negative(image, config)


def test_slide_pipeline_is_registered_as_experimental_after_engine_import():
    import film_foundry.core.engine  # noqa: F401

    toolkits = {toolkit.key: toolkit for toolkit in registered_media_toolkits()}
    assert toolkits["slide"].status == "experimental"
    assert toolkits["slide"].compatible_interpreters == ("positive_transparency_scan",)
    assert not hasattr(toolkits["slide"], "material_editor_script")
    assert not hasattr(toolkits["slide"], "develop_editor_script")


def test_explicit_process_program_controls_development_route_independently_of_material():
    from film_foundry.core.engine import develop_negative

    image = np.full((5, 6, 3), 0.45, dtype=np.float32)
    negative_material_reversal_process = DarkroomConfig()
    negative_material_reversal_process.chemistry.program_key = "color_reversal"

    assert config_development_process(negative_material_reversal_process) == "reversal"
    assert get_develop_pipeline(negative_material_reversal_process).key == "reversal"
    positive = develop_negative(image, negative_material_reversal_process)
    assert positive.image_polarity == "positive"
    assert positive.compatible_interpreters == ("positive_transparency_scan",)
    assert get_scan_pipeline(positive).key == "reversal"

    slide_material_negative_process = DarkroomConfig()
    slide_material_negative_process.film.medium_process = "slide"
    slide_material_negative_process.film.image_polarity = "positive"
    slide_material_negative_process.chemistry.program_key = "color_negative"

    assert config_development_process(slide_material_negative_process) == "negative"
    negative = develop_negative(image, slide_material_negative_process)
    assert negative.image_polarity == "negative"
    assert negative.compatible_interpreters == ("negative_scan",)


def test_scan_auto_selects_final_medium_interpreter_and_rejects_explicit_mismatch():
    from film_foundry.core.engine import develop_negative, scan_negative

    image = np.full((5, 6, 3), 0.45, dtype=np.float32)
    config = DarkroomConfig()
    config.chemistry.program_key = "color_reversal"
    medium = develop_negative(image, config)

    automatic = scan_negative(medium)
    assert automatic.interpreter_key == "positive_transparency_scan"

    incompatible = DarkroomConfig()
    incompatible.scanner.interpreter_key = "negative_scan"
    incompatible.scanner.target_medium_process = "negative"
    incompatible.scanner.input_polarity = "negative"
    with pytest.raises(ValueError, match="incompatible"):
        scan_negative(medium, incompatible)


def test_frontend_editor_routes_live_outside_core_registry():
    from film_foundry.tools.media_editor_routes import editor_routes_for

    negative = editor_routes_for("negative")
    assert negative["material"] == "film_foundry.tools.run_film_material_editor"
    assert negative["develop"] == "film_foundry.tools.run_develop_process_editor"
    assert negative["scanner"] == "film_foundry.tools.run_scanner_render_editor"
    assert editor_routes_for("color_negative") == negative
    slide = editor_routes_for("slide")
    assert slide["scanner"] == "film_foundry.tools.run_positive_scanner_editor"
    assert editor_routes_for("reversal")["scanner"] == slide["scanner"]


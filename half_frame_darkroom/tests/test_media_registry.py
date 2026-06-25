import numpy as np
import pytest

from half_frame_darkroom.core.media_registry import get_media_pipeline, registered_media_processes
from half_frame_darkroom.model.config import DarkroomConfig


def test_registry_lists_placeholder_media_without_engine_dependency():
    processes = registered_media_processes()
    assert "negative" in processes
    assert "slide" in processes
    assert "daguerreotype" in processes


def test_negative_pipeline_is_registered_and_usable():
    from half_frame_darkroom.core.engine import develop_negative

    config = DarkroomConfig()
    pipeline = get_media_pipeline(config)
    assert pipeline.key in {"negative", "color_negative", "bw_negative", "film_negative"}
    assert "negative" in registered_media_processes()

    image = np.full((8, 8, 3), 0.5, dtype=np.float32)
    negative = develop_negative(image, config)
    assert negative.density_grain.shape == image.shape
    assert negative.metadata["medium_process"] == "negative"


def test_future_medium_process_fails_explicitly():
    from half_frame_darkroom.core.engine import develop_negative

    config = DarkroomConfig()
    config.film.medium_process = "slide"
    image = np.full((8, 8, 3), 0.5, dtype=np.float32)

    with pytest.raises(NotImplementedError, match="not implemented"):
        develop_negative(image, config)

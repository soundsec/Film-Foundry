import cv2
import numpy as np
from PIL import Image
import pytest

from half_frame_darkroom.core.color import ensure_rgb_float
from half_frame_darkroom.core.engine import process_file
from half_frame_darkroom.core.io_utils import iter_images, load_image
from half_frame_darkroom.model.config import DarkroomConfig


def test_grayscale_array_becomes_rgb():
    gray = np.array([[0, 128], [255, 64]], dtype=np.uint8)

    rgb = ensure_rgb_float(gray)

    assert rgb.shape == (2, 2, 3)
    assert np.allclose(rgb[..., 0], rgb[..., 1])
    assert np.allclose(rgb[..., 1], rgb[..., 2])


def test_alpha_array_is_composited_over_white():
    rgba = np.zeros((1, 2, 4), dtype=np.uint8)
    rgba[0, 0] = (0, 0, 0, 0)
    rgba[0, 1] = (0, 0, 0, 255)

    rgb = ensure_rgb_float(rgba)

    assert np.allclose(rgb[0, 0], 1.0)
    assert np.allclose(rgb[0, 1], 0.0)


def test_hyperspectral_like_array_is_rejected():
    image = np.zeros((2, 2, 8), dtype=np.float32)

    with pytest.raises(ValueError, match="Unsupported channel count"):
        ensure_rgb_float(image)


def test_load_16bit_tiff_preserves_range(tmp_path):
    path = tmp_path / "input.tiff"
    rgb = np.zeros((2, 2, 3), dtype=np.uint16)
    rgb[..., 0] = 65535
    rgb[..., 1] = 32768
    bgr = rgb[..., ::-1]
    assert cv2.imwrite(str(path), bgr)

    image = load_image(path)

    assert image.shape == (2, 2, 3)
    assert np.isclose(image[..., 0].max(), 1.0)
    assert np.isclose(image[..., 1].max(), 32768 / 65535, atol=1e-4)


def test_raw_dng_input_is_rejected_with_clear_message(tmp_path):
    path = tmp_path / "input.dng"
    path.write_bytes(b"not really a dng")

    with pytest.raises(ValueError, match="RAW/DNG input is not supported yet"):
        load_image(path)


def test_iter_images_ignores_unsupported_single_file(tmp_path):
    path = tmp_path / "input.dng"
    path.write_bytes(b"not really a dng")

    assert iter_images(path) == []


def test_process_file_with_chinese_paths_exports_negative_materials(tmp_path):
    input_path = tmp_path / "中文输入.png"
    output_path = tmp_path / "中文输出.png"
    image = np.full((12, 12, 3), 160, dtype=np.uint8)
    image[3:7, 4:9] = (235, 230, 220)
    Image.fromarray(image, mode="RGB").save(input_path)

    config = DarkroomConfig()
    config.random_seed = 1
    config.seed_strategy = "fixed"
    config.output.format = "png"
    config.output.save_scanner_raw = True
    config.output.export_transparent_plate = True
    config.output.export_plate_set = True

    result_path = process_file(input_path, output_path, config)

    negative_path = output_path.with_suffix(".darkroom_negative.npz")
    transparent_dir = output_path.with_suffix(".darkroom_negative_transparent_plate")
    plate_dir = output_path.with_suffix(".darkroom_negative_plate_set")
    assert result_path.exists()
    assert negative_path.exists()
    assert output_path.with_suffix(".negative_visual.png").exists()
    assert output_path.with_suffix(".scanner_raw.tiff").exists()
    assert transparent_dir.joinpath("negative_transparent_16bit.tiff").exists()
    assert plate_dir.joinpath("halation_layer_linear.png").exists()

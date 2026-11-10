import numpy as np
from PIL import Image


def test_fast_png_keeps_rgb_rgba_gray_and_metadata(tmp_path):
    from film_foundry.core.execution_topology import execution_topology
    from film_foundry.core.io_utils import save_image
    from film_foundry.core.electronic_negative import _save_gray_png, _save_rgba_png
    from film_foundry.model.config import OutputConfig
    rng = np.random.default_rng(19)
    rgba = rng.random((97, 131, 4), dtype=np.float32)
    output = OutputConfig(bit_depth=8, anti_banding_strength=0, watermark_metadata=True)
    decoded = {}
    for topology in ('reference', 'optimized'):
        with execution_topology(topology):
            for mode in ('RGB', 'RGBA', 'L'):
                path = tmp_path / f'{topology}-{mode}.png'
                if mode == 'RGB':
                    save_image(rgba[..., :3], path, output)
                elif mode == 'RGBA':
                    _save_rgba_png(rgba, path)
                else:
                    _save_gray_png(rgba[..., 0], path)
                with Image.open(path) as img:
                    assert img.mode == mode
                    pixels, metadata = np.array(img), dict(img.info)
                if topology == 'reference':
                    decoded[mode] = pixels, metadata
                else:
                    np.testing.assert_array_equal(pixels, decoded[mode][0])
                    assert metadata == decoded[mode][1]
    assert 'Software' in decoded['RGB'][1] and 'Comment' in decoded['RGB'][1]

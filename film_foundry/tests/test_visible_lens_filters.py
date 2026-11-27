from copy import deepcopy

import numpy as np
from PIL import Image

from film_foundry.app.diagnostic_targets import diagnostic_target
from film_foundry.app.separations import CHANNELS, export_separations, prepare_separations
from film_foundry.app.selection_preview import SelectionPreviewSession
from film_foundry.app.workflow import config_from_share_code, encode_recipe_share
from film_foundry.contracts.lens_filters import lens_filter
from film_foundry.core.engine import expose_linear_to_latent, develop_negative_from_latent
from film_foundry.core.latent_io import load_exposed_latent, save_exposed_latent


def test_filtered_capture_and_registered_plate_roundtrip(tmp_path):
    cfg = SelectionPreviewSession().film_config({'key': 'monochrome_fine_negative'})
    cfg.exposure.lens_filter = lens_filter('red', True)
    assert config_from_share_code(encode_recipe_share(cfg)).exposure == cfg.exposure
    chart = diagnostic_target('color24', long_edge=96)
    path = tmp_path / 'source.png'
    Image.fromarray(np.rint(chart * 255).astype('uint8')).save(path)
    state = expose_linear_to_latent(chart, cfg, np.random.default_rng(3))
    latent = tmp_path / 'red.darkroom_latent.npz'
    save_exposed_latent(latent, state, identity={})
    draft = deepcopy(cfg)
    draft.exposure.lens_filter = lens_filter('blue')
    restored, _ = load_exposed_latent(latent)
    medium = develop_negative_from_latent(restored, draft, np.random.default_rng(3))
    assert medium.metadata['film_process_model']['lens_filter'] == cfg.exposure.lens_filter

    options = dict(seed=43, compensate=True)
    direct = prepare_separations([path], cfg, **options)
    offsets, gains = [(1, 0), (0, 0), (0, 1)], [0., -.2, .4]
    destination = export_separations(tmp_path / 'plates', [path], cfg, offsets=offsets, gains=gains, **options)
    paths = [destination / (band + '.darkroom_negative.npz') for band in CHANNELS]
    readback = prepare_separations(paths, draft, **options)
    np.testing.assert_array_equal(direct.compose(offsets, gains), readback.compose(offsets, gains))
    assert (destination / 'tricolour.png').is_file()

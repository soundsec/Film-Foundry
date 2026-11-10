"""Factory exposure is frozen capture data, developed through the stock/baths."""
import copy
from dataclasses import asdict
from itertools import groupby

import numpy as np

from film_foundry.contracts.edge_barcode import code128c_pattern, code128c_symbols
from film_foundry.contracts.edge_marks import factory_edge_pattern, factory_layout_pattern
from film_foundry.core.engine import develop_negative, expose_image_to_latent, develop_negative_from_latent
from film_foundry.core.film_border import resolve_film_border_layout
from film_foundry.core.formed_film_border import _strip_masks
from film_foundry.core.latent_io import save_exposed_latent, load_exposed_latent, latent_identity_from_film
from film_foundry.model.config import DarkroomConfig


def test_numeric_identifier_and_legacy_layout_compatibility():
    # Independent Code 128 C vector: 01 23 00 12, weighted checksum 200 % 103.
    assert code128c_symbols('01230012') == (105, 1, 23, 0, 12, 97, 106)
    pattern = code128c_pattern(123, 12)
    row = [pattern.ink(x, 4) for x in range(pattern.width)]
    runs = [sum(1 for _ in cells) for _, cells in groupby(row)]
    assert runs == [10] + [int(c) for c in '2112322221223121312122221122324111132331112'] + [10]
    assert all([pattern.ink(x, y) for x in range(pattern.width)] == row for y in range(pattern.height))
    for layout in ('legacy', 'text_code', 'combined', 'text_only'):
        for side in ('top', 'bottom'):
            assert factory_edge_pattern('FF 400',123,12,layout,'legacy',side) == factory_layout_pattern('FF 400',123,layout,side)


def test_factory_dose_and_baths_form_density_without_changing_image_identity(tmp_path):
    cfg = DarkroomConfig(enable_mtf=False, enable_halation=False, enable_grain=True)
    cfg.seed_strategy='fixed';cfg.random_seed=27
    cfg.film_border.enabled = True
    cfg.film_border.factory_layout = 'text_code'
    cfg.film_border.factory_barcode = 'code128c'
    cfg.film_border.factory_batch = 123
    cfg.film_border.frame_number = 12
    cfg.film_border.stock_label = 'FF 400'
    cfg.film_border.factory_mark_type = 'laser'
    cfg.film_border.factory_mark_spread = .65
    cfg.film_border.factory_exposure_variation = .18
    cfg.film_border.factory_exposure_ev = -2
    source = np.linspace(.03,.8,80*600*3,dtype=np.float32).reshape(80,600,3)
    latent = expose_image_to_latent(source,cfg)
    path = tmp_path/'frame.darkroom_latent.npz'
    save_exposed_latent(path,latent,identity=latent_identity_from_film(cfg.film))
    loaded,_ = load_exposed_latent(path)
    cfg.film_border.factory_exposure_ev = 0
    a = develop_negative_from_latent(loaded.copy(),cfg)
    b = develop_negative_from_latent(expose_image_to_latent(source,cfg),cfg)
    rows = a.metadata['formed_film_rebate']['rebate_rows']
    assert a.metadata['formed_film_rebate']['factory_exposure_ev'] == -2
    assert a.metadata['formed_film_rebate']['barcode']['payload'] == '01230012'
    np.testing.assert_array_equal(a.optical_density_rgb[rows:-rows],b.optical_density_rgb[rows:-rows])
    layout = resolve_film_border_layout(cfg.film_border,source.shape)
    dose,holes = _strip_masks(cfg.film_border,layout,'FF 400')
    marked=(dose[:rows]>.7)&(holes[:rows]==0)
    assert marked.any()
    assert b.optical_density_rgb[:rows][marked].mean() > a.optical_density_rgb[:rows][marked].mean()+.01
    under = copy.deepcopy(cfg);under.chemistry.time_min = cfg.chemistry.time_min*.2
    c = develop_negative(source,under)
    assert np.mean(abs(c.optical_density_rgb[:rows][marked]-b.optical_density_rgb[:rows][marked]))>.01
    old = asdict(cfg)
    for name in ('factory_barcode','factory_exposure_ev','factory_exposure_variation'):old['film_border'].pop(name)
    restored = DarkroomConfig.from_dict(old)
    assert restored.film_border.factory_barcode=='legacy'
    assert restored.film_border.factory_exposure_ev==restored.film_border.factory_exposure_variation==0


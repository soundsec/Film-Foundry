"""Independent lamp settings enter formed material and survive the desktop route."""
from copy import deepcopy
from dataclasses import asdict,replace
import numpy as np
import pytest
from film_foundry.model.config import FilmBorderConfig,DarkroomConfig
from film_foundry.core.film_border import apply_edge_imprint_to_linear,validate_film_border_config
from film_foundry.core.engine import develop_negative,expose_image_to_latent,develop_negative_from_latent,scan_medium_output
from film_foundry.tests.test_unified_workspace import panel


def test_imprint_colour_and_ev_are_exposure_and_preserve_legacy_defaults():
    image=np.zeros((80,160,3),np.float32)
    cfg=FilmBorderConfig(imprint_style='optical_amber',imprint_text='FF',imprint_font='seven_segment')
    original,mask=apply_edge_imprint_to_linear(image,cfg)
    np.testing.assert_array_equal(original,mask[...,None]*(np.array([1.,.55,.12],np.float32)*4.5))
    red=replace(cfg,imprint_light_color_rgb=(1.,0.,0.))
    a,_=apply_edge_imprint_to_linear(image,red)
    b,_=apply_edge_imprint_to_linear(image,replace(red,imprint_exposure_ev=1.))
    np.testing.assert_array_equal(b,a*2)
    assert a[...,0].max()>0 and not a[...,1:].any() and not image.any()
    off,_=apply_edge_imprint_to_linear(image,replace(red,imprint_style='none'))
    np.testing.assert_array_equal(off,image)
    for key,value in [('factory_light_color_rgb',(1.,float('nan'),0.)),('imprint_light_color_rgb',(2.,0.,0.)),('imprint_exposure_ev',5.)]:
        with pytest.raises(ValueError):validate_film_border_config(replace(cfg,**{key:value}))


def test_factory_colour_is_formed_frozen_and_does_not_recolour_the_image():
    image=np.full((64,120,3),.3,np.float32)
    cfg=DarkroomConfig(enable_grain=False,enable_mtf=False,enable_halation=False)
    cfg.film_border=FilmBorderConfig(enabled=True,stock_label='FF',factory_layout='text_only',factory_light_color_rgb=(1.,.02,.01),factory_exposure_ev=-1.)
    cfg.random_seed=19
    first=develop_negative(image,cfg,rng=np.random.default_rng(19))
    rows=first.metadata['formed_film_rebate']['rebate_rows']
    blue=deepcopy(cfg);blue.film_border.factory_light_color_rgb=(.01,.02,1.)
    second=develop_negative(image,blue,rng=np.random.default_rng(19))
    np.testing.assert_array_equal(first.optical_density_rgb[rows:-rows],second.optical_density_rgb[rows:-rows])
    assert np.max(np.abs(first.optical_density_rgb[:rows]-second.optical_density_rgb[:rows]))>.01
    latent=expose_image_to_latent(image,cfg,rng=np.random.default_rng(19))
    formed=develop_negative_from_latent(latent.copy(),blue,rng=np.random.default_rng(19))
    np.testing.assert_array_equal(formed.optical_density_rgb,first.optical_density_rgb)
    before=formed.optical_density_rgb.copy();scan_medium_output(formed,blue)
    np.testing.assert_array_equal(formed.optical_density_rgb,before)
    restored=DarkroomConfig.from_dict(asdict(cfg))
    assert validate_film_border_config(restored.film_border).factory_light_color_rgb==(1.,.02,.01)


def test_desktop_applies_and_resets_each_lamp_independently(panel):
    from film_foundry.ui.qt import _application
    panel.show_workbench();panel.imprint_text.set('FF');panel._edit_imprint()
    app=_application()
    def dialog(name):return next(w for w in app.topLevelWidgets() if w.__class__.__name__==name and w.isVisible())
    imprint=dialog('ImprintDialog');imprint.light_color.set_value((.02,1.,.1));imprint.exposure.setValue(-1.5);imprint.accept()
    cfg=panel._config().film_border
    assert cfg.imprint_light_color_rgb==(.02,1.,.1) and cfg.imprint_exposure_ev==-1.5
    panel._edit_film_border();edge=dialog('FilmBorderDialog')
    edge.enabled.setChecked(True);edge.light_color.set_value((1.,.02,.01));edge.exposure.setValue(1.25);edge.accept()
    cfg=panel._config().film_border
    assert cfg.factory_light_color_rgb==(1.,.02,.01) and cfg.factory_exposure_ev==1.25
    assert cfg.imprint_light_color_rgb==(.02,1.,.1) and cfg.imprint_exposure_ev==-1.5
    panel._edit_imprint();imprint=dialog('ImprintDialog');imprint.light_color.set_value((0.,0.,1.));imprint.exposure.setValue(3.)
    imprint.reset();assert imprint.value().imprint_light_color_rgb==(.02,1.,.1) and imprint.value().imprint_exposure_ev==-1.5
    imprint.reject()
    panel.control_resets.reset_key('imprint.placement')
    cfg=panel._config().film_border
    assert cfg.imprint_light_color_rgb is None and cfg.imprint_exposure_ev==0
    assert cfg.factory_light_color_rgb==(1.,.02,.01)

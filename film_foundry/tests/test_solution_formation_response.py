"""Explicit developer response follows actual products, with old models intact."""
from copy import deepcopy
from dataclasses import replace
import numpy as np
import pytest
from film_foundry.contracts.bath_solution import MODEL,MODEL_V3,validate_solution
from film_foundry.core.film_process.recipe import program_from_develop_recipe
from film_foundry.model.config import DarkroomConfig


def solution(**response):
    return validate_solution({'model':MODEL_V3,'action':'develop_color','formation_response':response},'develop_color')


def test_response_scales_new_product_moments_and_fog_not_existing_mass():
    from film_foundry.tests.test_bath_formation_history import run
    from film_foundry.tests.test_custom_process_steps import _latent
    from film_foundry.core.film_process.formation_history import response_from_totals
    row={'action':'develop_color','time_min':3.,'solution':solution()}
    baseline=run([row]);modified=run([{**row,'solution':solution(granularity_factor=.4,grain_radius_factor=1.8,grain_clumping_factor=1.5)}])
    np.testing.assert_array_equal(baseline.state.dye,modified.state.dye)
    np.testing.assert_array_equal(baseline.state.halide,modified.state.halide)
    a=response_from_totals(baseline.state.totals(),'dye');b=response_from_totals(modified.state.totals(),'dye')
    for key,factor in (('grain_factor',.4),('grain_radius_factor',1.8),('grain_clump_factor',1.5)):
        assert b[key]==pytest.approx(a[key]*factor,rel=1e-6)
    old={k:v for k,v in row['solution'].items() if k!='formation_response'};old['model']=MODEL
    reference=run([{**row,'solution':old}])
    for key,value in baseline.state.formation_history.items():np.testing.assert_array_equal(value,reference.state.formation_history[key])
    dark=_latent();dark.developability[:]=0
    fogless=run([{**row,'solution':solution(chemical_fog_factor=0)}],state=dark)
    np.testing.assert_array_equal(fogless.state.halide,dark.halide)
    assert not fogless.state.dye.any()
    recipe=replace(DarkroomConfig().chemistry,process_steps=(row,),formation_history_model='legacy_global_v1')
    with pytest.raises(ValueError,match='bath-product'):program_from_develop_recipe(recipe)
    for response in ({'granularity_factor':-1},{'chemical_fog_factor':True},{'grain_radius_factor':float('nan')},{'invented_factor':1}):
        with pytest.raises(ValueError):solution(**response)


def test_coupled_response_and_checkpoint_preserve_program_and_products(tmp_path):
    from film_foundry.tests.test_coupled_bath import coupled
    from film_foundry.tests.test_film_process_work import source,same_medium
    from film_foundry.app.film_process_work import start_work,continue_work
    from film_foundry.core.negative_io import load_developed_negative_npz
    from film_foundry.core.engine import develop_negative
    c=DarkroomConfig(enable_mtf=False,enable_halation=False,random_seed=19,seed_strategy='fixed')
    c.chemistry.process_variation=0.;old=coupled('develop_silver')
    new={**old,'model':MODEL_V3,'formation_response':{'granularity_factor':.5}}
    c.chemistry.process_steps=({'action':'develop_silver','solution':new},)
    old_c=deepcopy(c);old_c.chemistry.process_steps=({'action':'develop_silver','solution':old},)
    a=program_from_develop_recipe(c.chemistry);b=program_from_develop_recipe(old_c.chemistry)
    assert len(a.steps)==len(b.steps)>1
    for left,right in zip(a.steps,b.steps):
        assert left.strength==right.strength and left.reaction_window==right.reaction_window
        if left.formation_response:assert left.formation_response.grain_factor==right.formation_response.grain_factor*.5
    latent=source(tmp_path,c)
    initial=tmp_path/'start.ffprocess.npz';start_work(latent,initial,c)
    mid=tmp_path/'mid.ffprocess.npz';continue_work(initial,mid,stop_after=3)
    final=continue_work(mid,tmp_path/'final.npz');direct=continue_work(initial,tmp_path/'direct.npz')
    same_medium(load_developed_negative_npz(final.output_path),load_developed_negative_npz(direct.output_path))


def test_real_editors_search_versions_and_non_destructive_bath_edit(tmp_path,monkeypatch):
    from film_foundry.ui.qt import _application,QtWidgets
    from film_foundry.ui.bath_solution_dialog import BathSolutionDialog
    from film_foundry.ui.roll_process_dialog import RollProcessDialog
    from film_foundry.tools.run_film_material_editor import FilmMaterialEditor
    from film_foundry.tools.run_develop_process_editor import DevelopProcessEditor
    from film_foundry.app.workflow import encode_recipe_share,config_from_share_code
    for key in ('FILM_FOUNDRY_UI_SETTINGS','FILM_FOUNDRY_CAMERA_SETTINGS','FILM_FOUNDRY_CATALOG'):monkeypatch.setenv(key,str(tmp_path/(key+'.json')))
    app=_application();c=DarkroomConfig();c.chemistry.temperature_c=45.;c.chemistry.push_stops=2.;c.look.grain_multiplier=.7
    c.chemistry.process_steps=({'action':'develop_color','strength':.6,'solution':solution(granularity_factor=.7)},
                              {'action':'destroy_dye','strength':.1,'dye_destruction_basis':'explicit'})
    objects=[]
    try:
        dialog=BathSolutionDialog('develop_color',c.chemistry.process_steps[0]['solution']);objects.append(dialog);dialog.apply()
        assert dialog.value==c.chemistry.process_steps[0]['solution']
        legacy=BathSolutionDialog('develop_color',None,formation_available=False);objects.append(legacy)
        legacy.enabled.setChecked(True)
        assert not legacy.formation_enabled.isEnabled()
        baths=RollProcessDialog(c,principle_only=False);objects.append(baths);baths.apply()
        assert baths.result()==QtWidgets.QDialog.DialogCode.Accepted
        assert baths.config.look.grain_multiplier==.7 and baths.config.chemistry.push_stops==2.
        assert baths.config.chemistry.process_steps[0]['strength']==.6 and baths.config.chemistry.process_steps[1]['dye_destruction_basis']=='explicit'
        editor=DevelopProcessEditor(config=c);material=FilmMaterialEditor(config=c)
        try:
            assert editor._config_from_controls().chemistry.temperature_c==45.
            assert editor.preview_table.topLevelItem(1).childCount()==len(program_from_develop_recipe(c.chemistry).steps)
            for page,query in ((editor,'reticulation_strength'),(material,'granularity_sigma')):
                page.parameter_search.setText(query)
                assert page.parameter_matches.count()==1
            assert material._config_from_controls().film.granularity_sigma==c.film.granularity_sigma
        finally:editor.root.destroy();material.root.destroy()
        shared=config_from_share_code(encode_recipe_share(c))
        assert shared.chemistry.process_steps[0]['solution']==c.chemistry.process_steps[0]['solution']
    finally:
        for dialog in objects:dialog.close()
        app.processEvents()

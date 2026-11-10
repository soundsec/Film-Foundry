from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import numpy as np
import pytest

from film_foundry.model.config import DarkroomConfig
from film_foundry.core.process_controls import condition_driven_config
from film_foundry.tests.test_electronic_roll import roll


def test_principle_controls_act_on_frozen_material_not_target_effects(roll):
    from film_foundry.core.engine import develop_negative_from_latent
    from film_foundry.core.latent_io import load_exposed_latent
    session,cfg,source,_=roll
    latent=load_exposed_latent(source)[0]
    cfg.process_control_mode='principle'
    cfg.chemistry.chemical_stain=.9;cfg.chemistry.reticulation_strength=.8;cfg.chemistry.uneven_development=.7
    before=asdict(cfg);clean=condition_driven_config(cfg)
    first=develop_negative_from_latent(latent,cfg,rng=np.random.default_rng(4))
    baseline=develop_negative_from_latent(load_exposed_latent(source)[0],clean,rng=np.random.default_rng(4))
    np.testing.assert_array_equal(first.density_grain,baseline.density_grain)
    assert asdict(cfg)==before
    cold=deepcopy(clean);cold.chemistry.temperature_c=5;cold.chemistry.time_min=1;cold.chemistry.agitation_time_s=0
    weak=develop_negative_from_latent(load_exposed_latent(source)[0],cold,rng=np.random.default_rng(4))
    assert not np.allclose(weak.density_grain,first.density_grain)
    assert first.metadata['process_control']['mode']=='principle'
    assert first.metadata['chemical_stain']==0 and first.metadata['reticulation_strength']==0
    assert DarkroomConfig.from_dict(asdict(cfg)).process_control_mode=='principle'
    from film_foundry.app.workflow import encode_recipe_share,config_from_share_code
    assert config_from_share_code(encode_recipe_share(cfg)).chemistry.chemical_stain==0


def test_custom_bath_policy_keeps_material_compatibility_and_rejects_unknowns():
    from film_foundry.core.film_process.recipe import program_from_develop_recipe
    cfg=DarkroomConfig();cfg.chemistry.process_steps=({'action':'develop_color','temperature_c':15,'strength':.1},
        {'action':'fix_halide','fixer_type':'rapid','fixer_exhaustion':.6})
    cfg.look.exposure_ev=2;cfg.look.print_contrast=1.3
    resolved=condition_driven_config(cfg)
    assert resolved.look.exposure_ev==2 and resolved.look.print_contrast==1.3
    assert 'strength' not in resolved.chemistry.process_steps[0]
    assert asdict(condition_driven_config(resolved))==asdict(resolved)
    assert resolved.chemistry.temperature_c==15 and resolved.chemistry.fixer_exhaustion==.6
    program=program_from_develop_recipe(resolved.chemistry)
    damage=[s for s in program.steps if s.action.value=='destroy_dye']
    assert len(damage)==1 and damage[0].dye_destruction_basis=='compatibility'
    cfg.chemistry.process_steps=({'action':'develop_silver'},{'action':'activate_remaining_halide'},
        {'action':'develop_color'},{'action':'bleach_silver'},{'action':'fix_halide'})
    reversal=program_from_develop_recipe(condition_driven_config(cfg).chemistry)
    assert reversal.output_polarity=='positive'
    from film_foundry.tests.test_custom_process_steps import _material,_latent
    from film_foundry.core.film_process.operators import CompatibilityProfile,apply_process_program
    stable=apply_process_program(_material(),_latent(),program,CompatibilityProfile())
    damaged=apply_process_program(_material(),_latent(),program,CompatibilityProfile(dye_stability=.6))
    assert np.sum(damaged.state.dye)<np.sum(stable.state.dye)
    cfg.chemistry.developer_type='made up'
    with pytest.raises(ValueError,match='Unknown developer'):condition_driven_config(cfg)
    cfg.chemistry.developer_type='standard';cfg.chemistry.process_steps=({'action':'destroy_dye'},)
    with pytest.raises(ValueError,match='dye-damage'):condition_driven_config(cfg)


def test_roll_strips_and_light_table_preserve_medium(roll,tmp_path):
    from film_foundry.app.roll_presentation import roll_segments,RollLightTable
    session,cfg,_,_=roll
    assert roll_segments(session.data)==[]
    session.develop(cfg,tmp_path/'out')
    assert [len(s) for s in roll_segments(session.data)]==[6,2]
    assert [f['frame_index'] for s in roll_segments(session.data,4) for f in s]==list(range(1,9))
    assert roll_segments(session.data)[0][0]['unexposed']
    path=Path(session.data['frames']['2']['medium']);stamp=path.stat().st_mtime_ns
    viewer=RollLightTable();low=viewer.observe(str(path),-1);high=viewer.observe(str(path),1)
    assert np.mean(high)>np.mean(low) and path.stat().st_mtime_ns==stamp
    assert session.data['phase']=='developed' and not session.data['frames']['2'].get('cover')


def test_desktop_modes_bath_editor_and_strip_observer(roll,tmp_path,monkeypatch):
    from film_foundry.ui.qt import _application,QtWidgets
    from film_foundry.tools.run_desktop_gui import DesktopPanel
    from film_foundry.ui.roll_process_dialog import RollProcessDialog
    from film_foundry.ui.roll_strips import RollLightTableDialog
    from time import monotonic
    for name in ('UI','CAMERA','CATALOG'):
        key='FILM_FOUNDRY_'+('CATALOG' if name=='CATALOG' else name+'_SETTINGS')
        monkeypatch.setenv(key,str(tmp_path/(name+'.json')))
    app=_application();panel=DesktopPanel();panel.auto_preview.set(False)
    windows=[]
    try:
        assert panel._config().process_control_mode=='normal'
        panel.expert_mode.set(True);panel._update_expert_visibility()
        panel.chemical_stain.set(.8)
        expected=panel._config();assert expected.process_control_mode=='expected'
        panel.expert_operation.setCurrentIndex(1)
        principle=panel._config();assert principle.chemistry.chemical_stain==0
        assert panel.develop_additional_groups[0].qt.isHidden()
        panel.expert_operation.setCurrentIndex(0);assert panel._config().chemistry.chemical_stain==expected.chemistry.chemical_stain
        dialog=RollProcessDialog(principle);windows.append(dialog)
        from film_foundry.ui.roll_process_dialog import FIELDS
        dialog.custom.setChecked(True);dialog.table.cellWidget(0,len(FIELDS)+1).setCurrentText('fine_grain')
        dialog.table.item(0,1).setText('2');dialog.table.item(0,5).setText('10')
        dialog.apply();assert dialog.result()==QtWidgets.QDialog.DialogCode.Accepted
        assert dialog.config.chemistry.process_steps[0]['agitation_time_s']==10
        assert dialog.config.chemistry.process_steps[0]['developer_type']=='fine_grain'
        session,cfg,_,_=roll;session.develop(cfg,tmp_path/'out')
        panel.open_roll_workspace();panel.roll_view.storage=session.path.parent.parent;panel.roll_view.session=session;panel.roll_view.refresh()
        assert panel.roll_view.display.currentWidget() is panel.roll_view.strips
        assert panel.roll_view.lamp.isEnabled() and not panel.roll_view.settings.isEnabled()
        assert len(panel.roll_view.strips.buttons)==8
        lamp=RollLightTableDialog(session.data,2);windows.append(lamp);lamp.show()
        end=monotonic()+6
        while lamp.image.source is None and monotonic()<end:app.processEvents()
        assert lamp.image.source is not None, lamp.status.text()
        assert not session.data['frames']['2'].get('cover')
    finally:
        for window in windows:window.close()
        panel.root.destroy();app.processEvents()

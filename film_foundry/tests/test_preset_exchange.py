"""Share only selected recipe assets; retained processes stay self-contained."""
from copy import deepcopy
from dataclasses import asdict
import numpy as np
import pytest

from film_foundry.app import preset_exchange as exchange
from film_foundry.app.workflow import encode_recipe_share
from film_foundry.contracts.config_share import encode_config_share,decode_config_share
from film_foundry.contracts.bath_solution import MODEL_V3,validate_solution
from film_foundry.model.config import DarkroomConfig
from film_foundry.tests.test_desktop_usability import desktop


def recipe(mode='expected'):
    from film_foundry.tests.test_bath_incidents import fixer_config
    cfg=fixer_config();cfg.process_control_mode=mode
    cfg.film.name='My material';cfg.film.contrast=1.3;cfg.scanner.scan_saturation=1.23
    cfg.chemistry.formation_history_model='bath_products_local_v2'
    solution=validate_solution({'model':MODEL_V3,'action':'develop_color','rate_per_minute':.38,
        'formation_response':{'grain_radius_factor':1.35,'chemical_fog_factor':.6}},'develop_color')
    cfg.chemistry.process_steps=({'action':'develop_color','label':'Custom colour bath','time_min':3.1,'solution':solution},
        {'action':'bleach_silver','time_min':1.4},{'action':'fix_halide','time_min':2.})
    cfg.enable_grain=True;cfg.enable_halation=False;cfg.enable_mtf=False
    cfg.scanner.invert_transmission=True;cfg.look.print_exposure_ev=.25
    return cfg


@pytest.mark.parametrize('mode',['expected','principle'])
def test_complex_process_roundtrip_keeps_formed_result_and_local_runtime(tmp_path,mode):
    from film_foundry.core.engine import develop_negative
    cfg=recipe(mode);items=exchange.current_items(cfg)
    restored_items=exchange.read_items(exchange.encode_items(items))
    target=DarkroomConfig();target.random_seed=cfg.random_seed;target.seed_strategy=cfg.seed_strategy
    target.output.format='tiff';target.processing.quality_mode=cfg.processing.quality_mode
    target.output.quality=42
    selected=[item for item in restored_items if item['kind']!='solution']
    restored=exchange.apply_items(target,selected)
    assert restored.output.format=='tiff' and restored.output.quality==42
    assert restored.scanner.scan_saturation==1.23
    assert restored.chemistry.process_events==DarkroomConfig.from_dict(asdict(cfg)).chemistry.process_events
    # Real formation checks the reference path, embedded drug and ordering,
    # rather than treating successful JSON decoding as numerical equivalence.
    scene=np.linspace(.02,.85,24*36*3,dtype=np.float32).reshape(24,36,3)
    a=develop_negative(scene,cfg);b=develop_negative(scene,restored)
    np.testing.assert_array_equal(a.optical_density_rgb,b.optical_density_rgb)
    flow=next(item for item in restored_items if item['kind']=='develop')
    paths=exchange.save_items([flow],root=tmp_path)
    assert not (tmp_path/'solution').exists()
    reloaded=exchange.read_preset(paths[0],'develop')
    replay=exchange.apply_items(target,[next(i for i in selected if i['kind']=='film'),reloaded])
    assert replay.chemistry.process_steps==restored.chemistry.process_steps
    assert replay.chemistry.process_events==restored.chemistry.process_events


def test_old_full_and_sparse_codes_selective_import_and_no_global_export(tmp_path):
    cfg=recipe();target=DarkroomConfig();target.film.contrast=.8;target.scanner.scan_saturation=.7
    target.look.print_exposure_ev=-1;target.look.exposure_ev=2;target.processing.quality_mode='draft'
    before=asdict(target)
    items=exchange.read_items(encode_recipe_share(cfg))
    scan=next(item for item in items if item['kind']=='scanner')
    result=exchange.apply_items(target,[scan]);after=asdict(result)
    assert after['film']==before['film'] and after['chemistry']==before['chemistry']
    assert after['processing']==before['processing'] and after['output']==before['output']
    assert result.look.exposure_ev==2 and result.look.print_exposure_ev==.25
    sparse=exchange.read_items(encode_config_share({'scanner':{'scan_saturation':1.7}}))
    result=exchange.apply_items(target,sparse)
    assert result.scanner.scan_saturation==1.7 and result.look.print_exposure_ev==-1
    historical=exchange.split_recipe({'develop':{'time_min':7},'mode':'bw'})
    assert [i['kind'] for i in historical]==['develop']
    assert historical[0]['payload']['develop']['program_key']=='bw_negative'
    target.exposure.duration_seconds=100
    capture=next(i for i in exchange.current_items(DarkroomConfig()) if i['kind']=='capture')
    assert exchange.apply_items(target,[capture]).exposure.duration_seconds is None
    material=next(item for item in exchange.current_items(cfg) if item['kind']=='film')
    assert set(decode_config_share(exchange.legacy_code([material])))=={'film','mode','medium'}
    with pytest.raises(ValueError,match='collection'):decode_config_share(exchange.encode_items([material]))
    with pytest.raises(ValueError):exchange.apply_items(target,[scan,scan])
    paths=exchange.save_items([scan,scan],root=tmp_path)
    assert paths[0]!=paths[1] and all(path.exists() for path in paths)
    assert not (tmp_path/'film').exists()


def test_auto_process_is_frozen_without_replacing_receivers_material():
    cfg=DarkroomConfig();cfg.mode='bw';cfg.film.medium_process='reversal'
    flow=next(item for item in exchange.current_items(cfg) if item['kind']=='develop')
    assert flow['payload']['develop']['program_key']=='bw_reversal'
    receiver=DarkroomConfig();result=exchange.apply_items(receiver,[flow])
    assert asdict(result.film)==asdict(receiver.film) and result.mode==receiver.mode
    assert result.chemistry.program_key=='bw_reversal'


def test_receive_selection_cancel_frozen_stage_and_solution_reuse(desktop,tmp_path,monkeypatch):
    from film_foundry.ui.preset_exchange import ReceivePresetsDialog,SharePresetsDialog
    from film_foundry.ui.bath_solution_dialog import BathSolutionDialog
    from film_foundry.ui.qt import _application
    monkeypatch.setattr(exchange,'library_root',lambda:tmp_path/'presets')
    panel=desktop;items=exchange.current_items(recipe());before=asdict(panel._config())
    dialog=ReceivePresetsDialog(items,panel.root.qt,on_apply=panel._apply_shared_presets,can_apply=panel._can_apply_preset)
    dialog.reject();assert asdict(panel._config())==before and not (tmp_path/'presets').exists()
    dialog=ReceivePresetsDialog(items,panel.root.qt,on_apply=panel._apply_shared_presets,can_apply=panel._can_apply_preset)
    for row,item in enumerate(items):
        dialog.keep_checks[row].setChecked(item['kind']=='solution')
        dialog.apply_checks[row].setChecked(item['kind']=='scanner')
    dialog.commit();assert dialog.result()==dialog.DialogCode.Accepted
    assert panel._config().scanner.scan_saturation==1.23
    assert asdict(panel._config().film)==before['film']
    records=[r for r in exchange.library_records() if not r['builtin']]
    assert len(records)==1 and records[0]['kind']=='solution'
    bath=BathSolutionDialog('develop_color',None,panel.root.qt)
    bath.library.setCurrentIndex(1);bath.load_preset();bath.apply()
    assert bath.value==records[0]['payload']
    panel.pipeline_mode.set('scan');panel._update_mode_visibility()
    frozen=ReceivePresetsDialog(items,panel.root.qt,on_apply=panel._apply_shared_presets,can_apply=panel._can_apply_preset)
    assert all(not frozen.apply_checks[i].isEnabled() for i,item in enumerate(items) if item['kind']!='scanner')
    share=SharePresetsDialog(items);assert {i['kind'] for i in exchange.read_items(share.code.toPlainText())}=={'film','develop','scanner'}
    manager=panel.open_preset_library();_application().processEvents()
    assert panel.workspace_host.currentWidget() is panel.editor_host
    assert manager is panel.open_preset_library()
    for widget in (dialog,bath,frozen,share):widget.close()


def test_process_shortcut_and_step_inspector_keep_values(desktop):
    from film_foundry.ui.qt import _application
    panel=desktop;panel._edit_current_process();_application().processEvents()
    dialog=panel.find_configuration_dialog('current-process');assert dialog is not None
    old=deepcopy(dialog.config.chemistry)
    dialog.common_conditions_toggle.setChecked(True)
    dialog.table.setCurrentCell(1,0)
    assert not dialog.common_conditions_toggle.isChecked()
    assert dialog.sequence_view.advanced_toggle.isCheckable()
    dialog.reject();assert panel._config().chemistry.process_steps==old.process_steps


def test_saved_process_reloads_from_picker_and_cli_composition(desktop,tmp_path):
    from film_foundry.model.config import merge_config_presets
    cfg=recipe('principle');cfg.enable_grain=False
    flow=next(item for item in exchange.current_items(cfg) if item['kind']=='develop')
    path=exchange.save_items([flow],root=tmp_path)[0]
    restored=DarkroomConfig.from_json(path)
    combined=merge_config_presets(DarkroomConfig(),develop_config=restored)
    assert not combined.enable_grain and combined.process_control_mode=='principle'
    panel=desktop;panel.develop_preset.set(str(path))
    actual=panel._config()
    assert not actual.enable_grain and actual.process_control_mode=='principle'
    assert actual.chemistry.process_steps==restored.chemistry.process_steps
    assert actual.chemistry.process_events==restored.chemistry.process_events
    before=asdict(actual)
    panel._preset_library_changed()
    assert asdict(panel._config())==before


def test_external_image_dependency_is_not_silently_shared():
    cfg=recipe();cfg.chemistry.light_leak_mask_path='private/mask.png'
    items=exchange.current_items(cfg)
    with pytest.raises(ValueError,match='FFC2'):exchange.encode_items(items)
    with pytest.raises(ValueError,match='FFC2'):encode_recipe_share(cfg)
    material=[i for i in items if i['kind']=='film']
    assert exchange.read_items(exchange.encode_items(material))[0]['kind']=='film'

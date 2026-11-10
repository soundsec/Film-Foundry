from copy import deepcopy
from types import SimpleNamespace
import numpy as np


def test_nested_editors_apply_to_parent_draft_and_cancel_outer(tmp_path,monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM','offscreen')
    for key in ('FILM_FOUNDRY_UI_SETTINGS','FILM_FOUNDRY_CAMERA_SETTINGS','FILM_FOUNDRY_CATALOG'):
        monkeypatch.setenv(key,str(tmp_path/(key+'.json')))
    from film_foundry.ui.qt import _application,QtWidgets,QtCore
    from film_foundry.ui.workspace_host import WorkspaceHost
    from film_foundry.tools.run_develop_process_editor import DevelopProcessEditor
    from film_foundry.model.config import DarkroomConfig
    app=_application();window=QtWidgets.QWidget();layout=QtWidgets.QVBoxLayout(window)
    workspace=WorkspaceHost();layout.addWidget(workspace)
    editor=DevelopProcessEditor(config=DarkroomConfig())
    workspace.add_page(editor.root.qt,'Chemistry',closable=False)
    other=QtWidgets.QWidget();workspace.add_page(other,'Other')
    window.show();workspace.setCurrentWidget(editor.root.qt);app.processEvents()
    original=deepcopy(editor._config_from_controls().chemistry)
    monkeypatch.setattr(QtWidgets.QDialog,'exec',lambda *_: (_ for _ in ()).throw(AssertionError('modal editor')))
    try:
        editor._edit_baths();host=editor.root.qt._inline_editor_host
        process=host.frames[-1][1];process.table.setCurrentCell(0,0)
        process.edit_native_bath();bath=host.frames[-1][1]
        bath.controls['time_min'].setValue(6)
        bath.edit_solution();solution=host.frames[-1][1]
        assert len(host.frames)==3
        assert all(dialog.isWindow() and dialog.windowModality()==QtCore.Qt.WindowModality.WindowModal for _,dialog in host.frames)
        solution.enabled.setChecked(True);solution.controls['rate_per_minute'].setValue(.7);solution.apply()
        assert bath.solution['rate_per_minute']==.7
        workspace.setCurrentWidget(other);workspace.setCurrentWidget(editor.root.qt)
        assert host.frames[-1][1] is bath and bath.controls['time_min'].value()==6
        bath.apply();assert len(host.frames)==1
        assert process.config.chemistry.bath_overrides[0]['time_min']==6
        process.reject();assert not host.frames
        assert editor._config_from_controls().chemistry==original
        editor._edit_baths();process=host.frames[-1][1]
        process.controls['temperature_c'].setValue(24);process.apply()
        assert editor._config_from_controls().chemistry.temperature_c==24
        assert not host.frames
    finally:
        editor.root.destroy();window.close();app.processEvents()


def test_loading_material_preset_draws_once(tmp_path,monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM','offscreen')
    for key in ('FILM_FOUNDRY_UI_SETTINGS','FILM_FOUNDRY_CAMERA_SETTINGS','FILM_FOUNDRY_CATALOG'):
        monkeypatch.setenv(key,str(tmp_path/(key+'.json')))
    from film_foundry.ui.qt import _application
    from film_foundry.tools.run_film_material_editor import FilmMaterialEditor
    from film_foundry.model.config import DarkroomConfig
    _application();config=DarkroomConfig();editor=FilmMaterialEditor(config=config)
    count=[];original=editor._draw_hd_curve
    monkeypatch.setattr(editor,'_draw_hd_curve',lambda c:(count.append(c),original(c))[1])
    try:
        config.film.hd_gamma=(.61,.67,.72);config.film.silver_grain_strength=.13
        editor._set_controls_from_config(config)
        assert len(count)==1
        assert editor._config_from_controls().film.hd_gamma==config.film.hd_gamma
        assert editor._config_from_controls().film.silver_grain_strength==.13
    finally:editor.root.destroy()


def test_blocked_moments_equal_reference_with_strides_and_aliases():
    from film_foundry.core.film_process.formation_history import add_product,BathFormationResponse,NAMES
    from film_foundry.core.execution_topology import execution_topology
    rng=np.random.default_rng(48);amount=rng.random((257,513,3),dtype=np.float32);fog=amount*.07
    response=BathFormationResponse(.01,1.234,.876,.645)
    for source in (amount,amount[:,::2]):
        history={'silver_'+key:rng.random(source.shape,dtype=np.float32) for key in NAMES}
        old=SimpleNamespace(formation_history=deepcopy(history),formation_created={'silver_fog':0.})
        new=deepcopy(old)
        with execution_topology('reference'):add_product(old,'silver',source,fog[:,:source.shape[1]],response)
        add_product(new,'silver',source,fog[:,:source.shape[1]],response)
        for key in history:np.testing.assert_array_equal(old.formation_history[key],new.formation_history[key])
        assert old.formation_created==new.formation_created
    for topology in ('reference','optimized'):
        values=amount.copy();state=SimpleNamespace(formation_history={'silver_grain_factor':values},formation_created={})
        with execution_topology(topology):add_product(state,'silver',values,None,response)
        if topology=='reference':expected=values
        else:np.testing.assert_array_equal(values,expected)


def test_shared_rate_cache_is_per_step_and_unknown_providers_are_not_cached(monkeypatch):
    from film_foundry.core.process_operations import BathContactRate
    from film_foundry.core.spatial_fields import StepDevelopmentRateField
    from film_foundry.core.execution_topology import execution_topology
    from film_foundry.model.config import DarkroomConfig
    c=DarkroomConfig();shape=(3,5)
    base=StepDevelopmentRateField(full_shape=shape,first_step_label='first',common_rate=np.full(shape,.9,np.float32),first_step_rate=np.full(shape,.8,np.float32))
    provider=BathContactRate(c,shape,base,prepare_surface=False,prepare_inhibitor=False)
    count=[];resolve=StepDevelopmentRateField.rate_for_step
    monkeypatch.setattr(StepDevelopmentRateField,'rate_for_step',lambda self,*a:(count.append(a),resolve(self,*a))[1])
    step=provider.program.steps[0]
    provider.contact_for_step(0,step)
    signal=provider.rate_for_step('first',step.action.value);fog=provider.fog_rate_for_step('first',step.action.value)
    assert len(count)==1;np.testing.assert_array_equal(signal,fog)
    provider.contact_for_step(0,step);provider.rate_for_step('first',step.action.value);assert len(count)==2
    with execution_topology('reference'):
        provider.rate_for_step('first',step.action.value);provider.fog_rate_for_step('first',step.action.value)
    assert len(count)==4
    class Stateful:
        def rate_for_step(self,*_):count.append('stateful');return np.ones(shape,np.float32)*len(count)
    provider.base=Stateful()
    assert not np.array_equal(provider.rate_for_step('first','develop_color'),provider.fog_rate_for_step('first','develop_color'))


def test_run_destination_is_chosen_at_execution_and_cancel_is_inert(tmp_path,monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM','offscreen')
    for key in ('FILM_FOUNDRY_UI_SETTINGS','FILM_FOUNDRY_CAMERA_SETTINGS','FILM_FOUNDRY_CATALOG'):
        monkeypatch.setenv(key,str(tmp_path/(key+'.json')))
    from film_foundry.tools.run_desktop_gui import DesktopPanel
    from film_foundry.ui.preferences import _read
    from film_foundry.app.output_destination import default_output_directory
    _read.cache_clear();panel=DesktopPanel();panel.auto_preview.set(False)
    started=[];monkeypatch.setattr(panel,'_execute_process',lambda:started.append(panel.output_path.get()))
    try:
        panel._open_catalog_source(str(tmp_path/'source.jpg'),'film')
        previous=panel.output_path.get();panel._start_process()
        host=panel.film_workspace._inline_editor_host;dialog=host.frames[-1][1]
        assert dialog.path.text()==str(default_output_directory())
        dialog.path.setText(str(tmp_path/'not-created'));dialog.reject()
        assert not started and panel.output_path.get()==previous and not (tmp_path/'not-created').exists()
        panel._start_process();dialog=host.frames[-1][1]
        dialog.path.setText(str(tmp_path/'chosen'));dialog.apply()
        assert started==[str((tmp_path/'chosen').resolve())]
        assert not panel.scan_output_near_source.get()
        panel._start_process();dialog=host.frames[-1][1]
        panel.input_path.set(str(tmp_path/'other.jpg'));dialog.apply()
        assert len(started)==1
    finally:panel.root.destroy()


def test_material_batch_buttons_open_inline_destination_without_running(tmp_path,monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM','offscreen')
    from film_foundry.ui.material_batch_dialog import MaterialBatchDialog
    from film_foundry.ui.sheet_lab_window import SheetBatchWindow
    from film_foundry.ui.qt import _application
    app=_application()
    for window in (MaterialBatchDialog('integral',[str(tmp_path/'input.jpg')],load_source=False),
                   SheetBatchWindow('cyanotype',[str(tmp_path/'input.jpg')],load_source=False)):
        try:
            window.show();window.batch_button.click();app.processEvents()
            host=window.centralWidget()._inline_editor_host
            assert len(host.frames)==1 and not window.busy
            assert host.frames[-1][1].isWindow()
            host.frames[-1][1].reject();assert not host.frames
        finally:window.close()

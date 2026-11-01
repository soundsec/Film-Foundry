"""Desktop safeguards: task ownership, cancellation and accidental parameter edits."""
import os
from dataclasses import asdict
from pathlib import Path
from time import monotonic, sleep

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import numpy as np
import pytest
from PySide6.QtTest import QTest

from film_foundry.ui.qt import QtCore, QtGui, QtWidgets, ui


@pytest.fixture
def panel(tmp_path, monkeypatch):
    from film_foundry.ui import i18n
    from film_foundry.ui.preferences import _read
    from film_foundry.tools.run_desktop_gui import DesktopPanel
    previous=i18n.current_language()
    monkeypatch.setenv("FILM_FOUNDRY_UI_SETTINGS",str(tmp_path/"ui.json"))
    monkeypatch.setenv("FILM_FOUNDRY_CAMERA_SETTINGS",str(tmp_path/"camera.json"))
    _read.cache_clear();i18n.set_language("zh_CN")
    window=DesktopPanel();window.auto_preview.set(False);window._change_auto_preview()
    yield window
    if window.root.winfo_exists():window.root.destroy()
    QtWidgets.QApplication.processEvents()
    i18n.set_language(previous);_read.cache_clear()


def wait_for(condition):
    deadline=monotonic()+5
    while not condition() and monotonic()<deadline:
        QtWidgets.QApplication.processEvents();sleep(.01)
    assert condition()


def test_blank_active_source_and_output_never_start_a_worker(panel,monkeypatch,tmp_path):
    from film_foundry.tools import run_darkroom_gui as legacy
    import film_foundry.tools.run_desktop_gui as desktop
    def unexpected(*args,**kwargs):pytest.fail("Blank required path started a worker")
    monkeypatch.setattr(legacy,"Thread",unexpected);monkeypatch.setattr(desktop,"Thread",unexpected)
    panel.input_path.set("  ");panel.negative_path.set(str(tmp_path/"hidden.npz"))
    panel._start_process();assert not panel._processing
    panel._start_preview();assert not panel._preview_running
    assert "来源" in panel.status.get()
    panel.input_path.set(str(tmp_path/"frame.png"));panel.output_path.set("")
    panel._start_process();assert not panel._processing
    assert "输出" in panel.status.get()
    panel.pipeline_mode.set("scan");panel.scan_output_near_source.set(True)
    assert panel._required_paths()  # This mode computes its destination from the source.
    panel.negative_path.set("");assert not panel._required_paths()
    assert not list(tmp_path.glob("*run*"))


def test_wheel_over_inactive_fields_does_not_change_recipe_but_direct_input_works(panel):
    container=ui.Frame(panel.root);container.pack()
    selected=ui.TextValue(value="A");amount=ui.FloatValue(value=.5)
    choice=ui.Choice(container,textvariable=selected,values=("A","B"),state="readonly")
    slider=ui.Slider(container,variable=amount,from_=0,to=1)
    number=ui.NumberInput(container,textvariable=amount,from_=0,to=1,increment=.1)
    for control in (choice,slider,number):control.pack()
    panel.root.qt.show();panel.navigation.setFocus();QTest.qWait(10)
    def wheel(widget):
        position=widget.rect().center()
        event=QtGui.QWheelEvent(QtCore.QPointF(position),QtCore.QPointF(widget.mapToGlobal(position)),
            QtCore.QPoint(),QtCore.QPoint(0,120),QtCore.Qt.MouseButton.NoButton,
            QtCore.Qt.KeyboardModifier.NoModifier,QtCore.Qt.ScrollPhase.NoScrollPhase,False)
        QtWidgets.QApplication.sendEvent(widget,event)
        return event.isAccepted()
    for control in (choice,slider,number):
        assert not control.qt.hasFocus()
        assert not wheel(control.qt)
        assert amount.get()==.5 and selected.get()=="A"
    number.qt.setFocus();QTest.qWait(1)
    assert wheel(number.qt) and amount.get()==pytest.approx(.6)
    choice.qt.setFocus();QTest.keyClick(choice.qt,QtCore.Qt.Key.Key_Down)
    assert selected.get()=="B"
    assert not wheel(choice.qt) and selected.get()=="B"


def test_progress_locks_task_paths_and_opens_the_actual_output(panel,tmp_path,monkeypatch):
    from film_foundry.app.control import CancelFlag,WorkflowProgress
    from film_foundry.ui.i18n import language_label
    panel.root.qt.show();panel._processing=True;panel._cancel_flag=CancelFlag();panel._task_serial=1
    panel._workflow_control(panel._cancel_flag).report("full","developing",.25)
    wait_for(lambda:panel.activity_progress.value()==250)
    assert panel.activity_progress.maximum()==1000 and panel.activity_progress.isVisible()
    assert not panel.mode_choice.qt.isEnabled() and not panel.resume_button.qt.isEnabled()
    assert not panel.image_source_row[0].qt.isEnabled() and not panel.output_location_row[0].qt.isEnabled()
    assert not panel.preview_button.qt.isEnabled()
    panel._present_progress(0,WorkflowProgress("full","saving",1))
    assert panel.activity_progress.value()==250
    panel.language.set(language_label("en_US"));panel._change_language()
    assert "Developing" in panel.activity_label.qt.text()
    assert not panel.mode_choice.qt.isEnabled()
    assert "Developing" in panel.status.get()
    actual=tmp_path/"actual-run";actual.mkdir()
    panel._batch_status("full",1,[],actual)
    wait_for(lambda:panel._latest_output==actual)
    panel._finish_process()
    assert not panel.activity_progress.isVisible()
    assert panel.mode_choice.qt.isEnabled() and panel.resume_button.qt.isEnabled()
    assert panel.preview_button.qt.isEnabled() and panel.open_output_button.qt.isEnabled()
    opened=[];monkeypatch.setattr(QtGui.QDesktopServices,"openUrl",lambda url:opened.append(url.toLocalFile()) or True)
    panel.open_output_button.qt.click()
    assert [Path(path) for path in opened]==[actual.resolve()]
    actual.rmdir();panel.open_output_button.qt.click()
    assert len(opened)==1 and "missing" in panel.status.get()


def test_window_close_waits_for_cancelled_task_and_does_not_start_preview(panel,monkeypatch):
    from film_foundry.app.control import CancelFlag
    panel.root.qt.show();panel._processing=True;panel._cancel_flag=CancelFlag();panel._sync_activity_ui()
    flag=panel._cancel_flag
    monkeypatch.setattr(panel,"_confirm_cancel_close",lambda:False)
    assert not panel.root.qt.close()
    assert panel.root.winfo_exists() and not flag.requested()
    monkeypatch.setattr(panel,"_confirm_cancel_close",lambda:True)
    assert not panel.root.qt.close()
    assert flag.requested() and panel.root.winfo_exists() and not panel.process_button.qt.isEnabled()
    assert not panel.root.qt.close()  # A repeated close doesn't destroy the working window.
    monkeypatch.setattr(panel,"_start_preview",lambda *a,**kw:pytest.fail("Preview started during exit"))
    panel.auto_preview.set(True);panel._preview_pending=True
    panel._finish_process();wait_for(lambda:not panel.root.winfo_exists())


def test_preview_failure_stale_edits_and_language_rebuild_are_explicit(panel,tmp_path):
    from film_foundry.ui.i18n import language_label
    source=tmp_path/"invalid.png";source.write_text("invalid image",encoding="utf-8")
    panel.input_path.set(str(source));panel._start_preview()
    wait_for(lambda:not panel._preview_running)
    assert panel._preview_state_kind=="failed" and "失败" in panel.status.get()
    assert panel.preview_button.qt.isEnabled()
    pixels=np.full((4,6,3),.4,np.float32)
    panel._show_preview("test",pixels,pixels,"Source","Result")
    assert panel._preview_state_kind=="ready"
    panel.print_exposure.set(.5)
    assert panel._preview_state_kind=="stale"
    panel.language.set(language_label("en_US"));panel._change_language()
    assert "update the preview" in panel.preview_state_label.text()
    panel._preview_running=True;panel._preview_generation=10
    panel._finish_preview(9)
    assert panel._preview_running
    panel._finish_preview(10)
    assert not panel._preview_running and panel._preview_state_kind=="stale"


def test_resume_retains_original_recipe_and_result_folder(panel,tmp_path,monkeypatch):
    from film_foundry.tests.test_ui_capability_wiring import make_run
    from film_foundry.tools import run_darkroom_gui as legacy
    from film_foundry.model.session import load_session
    plan=make_run(tmp_path,"full");requests=[]
    class DeferredThread:
        def __init__(self,**request):requests.append(request)
        def start(self):pass
    monkeypatch.setattr(legacy,"Thread",DeferredThread)
    monkeypatch.setattr(legacy.file_dialog,"askopenfilename",lambda **kwargs:str(plan.manifest))
    panel.exposure_ev.set(2);panel.output_path.set(str(tmp_path/"unrelated"))
    panel._start_resume()
    assert panel._processing and not panel.mode_choice.qt.isEnabled()
    frozen,flag=requests[0]["args"]
    assert frozen.config.look.exposure_ev==plan.config.look.exposure_ev
    assert panel.exposure_ev.get()==2
    requests[0]["target"](frozen,flag)
    wait_for(lambda:not panel._processing and panel._latest_output is not None)
    assert load_session(plan.manifest)["batch"]["completed"]==2
    assert panel._latest_output==plan.output_root
    assert panel.resume_button.qt.isEnabled() and panel.open_output_button.qt.isEnabled()
    assert not (tmp_path/"unrelated").exists()


def test_folded_settings_keep_values_and_language_keeps_workspace_position(panel,monkeypatch):
    from film_foundry.ui.i18n import language_label
    panel.light_leak.set(.35);panel.exposure_occlusion_hair.set(True);panel.chemical_stain.set(.4)
    panel.curve_editors["tone_curve"].add_point(.4,.6)
    before=asdict(panel._config())
    assert "1" in panel.sections["film.gate"].header.text()
    assert "影调" in panel.sections["scan.curves"].header.text()
    for section in panel.sections.values():
        section.set_expanded(True);section.set_expanded(False)
    assert asdict(panel._config())==before
    panel.root.qt.resize(1280,800);panel.root.qt.show()
    panel.navigation.setCurrentRow(panel.page_index["film"])
    for key,section in panel.sections.items():
        if key.startswith("film."):section.set_expanded(True)
    panel.workspace_splitter.setSizes([180,500,580])
    QtWidgets.QApplication.processEvents()
    scroll=panel.stack.currentWidget().verticalScrollBar()
    scroll.setValue(min(150,scroll.maximum()));position=scroll.value();assert position>0
    sizes=panel.workspace_splitter.sizes();states=dict(panel._section_states)
    bindings=len(panel.chemical_stain._views)
    panel.language.set(language_label("en_US"));panel._change_language()
    wait_for(lambda:panel.stack.currentWidget().verticalScrollBar().value()==position)
    assert all(abs(a-b)<3 for a,b in zip(sizes,panel.workspace_splitter.sizes()))
    assert panel._section_states==states and asdict(panel._config())==before
    assert len(panel.chemical_stain._views)==bindings
    assert "Tone" in panel.sections["scan.curves"].header.text()
    assert panel.preview_button.master is panel.preview_actions
    assert panel.clear_preview_cache_button.master is panel.preview_actions
    opened=[];monkeypatch.setattr(panel,"_open_editor",lambda module:opened.append(module))
    for button in (panel.material_editor_link,panel.process_editor_link,panel.scanner_editor_link):button.qt.click()
    assert opened==["film_foundry.tools.run_film_material_editor","film_foundry.tools.run_develop_process_editor","film_foundry.tools.run_scanner_render_editor"]


def test_preset_browser_search_is_non_destructive_and_confirms_existing_selection_path(panel,monkeypatch):
    from film_foundry.ui.preset_picker import PresetPicker
    from film_foundry.tools import run_darkroom_gui as legacy
    combo=panel.scanner_preset_combo.qt
    labels=[combo.itemText(i) for i in range(combo.count())]
    target=next(i for i in range(combo.count()) if i!=combo.currentIndex())
    before=asdict(panel._config());original=panel.scanner_preset.get()
    def cancel(dialog):
        dialog.search.setText("no preset matches this query")
        assert not dialog.apply.isEnabled()
        assert dialog.selected_index is None
        dialog.reject();return dialog.result()
    monkeypatch.setattr(PresetPicker,"exec",cancel)
    panel._browse_presets(panel.scanner_preset_combo,"Scanner")
    assert panel.scanner_preset.get()==original and asdict(panel._config())==before
    def choose(dialog):
        dialog.search.setText(labels[target])
        assert dialog.apply.isEnabled()
        assert dialog.results.currentItem().text()==labels[target]
        assert asdict(panel._config())==before
        dialog._apply();return dialog.result()
    monkeypatch.setattr(PresetPicker,"exec",choose)
    panel._browse_presets(panel.scanner_preset_combo,"Scanner")
    key=panel._scanner_display_key_map[labels[target]]
    assert panel.scanner_preset.get()==key and key!=original
    expected=legacy.DarkroomConfig.from_json(legacy.scanner_preset_path(key))
    assert panel.print_exposure.get()==expected.look.print_exposure_ev
    assert panel.saturation.get()==expected.scanner.scan_saturation
    assert asdict(panel._config().film)==before["film"]
    assert asdict(panel._config().chemistry)==before["chemistry"]

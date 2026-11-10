import os
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
from threading import Event
from time import monotonic
import json
import numpy as np
from PIL import Image
import pytest
from film_foundry.ui.qt import _application,QtWidgets
from film_foundry.ui.material_lab_window import MaterialLabWindow
from film_foundry.app.material_lab_session import MaterialLabSession
from film_foundry.app import material_families as api
from film_foundry.core import silver_plate


@pytest.fixture(autouse=True)
def isolate_material_catalog(tmp_path,monkeypatch):
    monkeypatch.setenv('FILM_FOUNDRY_CATALOG',str(tmp_path/'catalog.json'))


def wait(window):
    app=_application();deadline=monotonic()+5
    while window.busy and monotonic()<deadline:app.processEvents()
    assert not window.busy,"Worker did not complete"
    assert window.last_error is None,window.last_error


@pytest.mark.parametrize("kind,steps",[
    ("peel",("expose","spread","advance","peel")),
    ("integral",("expose","eject","spread","advance","stabilize")),
    ("plate",("prepare","polish","sensitize","expose","develop","fix","gild","surface")),
])
def test_window_workflow_save_export_and_independent_observation(kind,steps,tmp_path,monkeypatch):
    source=tmp_path/'input.png';Image.fromarray(np.linspace(0,255,24*36*3,dtype=np.uint8).reshape(24,36,3)).save(source)
    window=MaterialLabWindow(kind);window.show()
    try:
        window.seed.setValue(4000000000)
        assert not window.conditions().include_border
        window._submit(lambda:window.session.open_image(source,0),"load");wait(window)
        assert window.viewer.source is not None
        setting="streaks" if kind=="plate" else "temperature_c"
        expected=.3 if kind=="plate" else 27
        window.process_controls[setting].setValue(expected)
        for layout in ('studio','compact','studio'):
            window.set_workspace_layout(layout)
            assert window.workspace_splitter.widget(1) is window.preview_surface
            assert window.process_controls[setting].value()==expected
            assert (window.config_tabs.indexOf(window.process_group)>=0)==(layout=='compact')
        if kind!="plate":
            window.material_controls["batch_variation"].setValue(.6)
            window.material_controls["batch_seed"].setValue(19)
        if kind=="peel":
            window.material_controls["donor_mode"].setCurrentIndex(1)
            assert window.material_controls["transfer"].currentData()=="silver_compound"
        for operation in steps:
            if operation=="spread":
                window.defect_controls["reagent_leak"].setValue(.5)
                window.defect_controls["spread_unevenness"].setValue(.6)
                if kind=="integral":
                    window.defect_controls["solar_warming"].setValue(8)
                    window.defect_controls["ambient_light"].setValue(4)
            assert window.operation_buttons[operation].isEnabled()
            assert window.process(operation)
            assert not window.file_bar.isEnabled()
            wait(window)
            if operation=="expose":
                window.leak_strength.setValue(.4)
                assert window.operation_buttons["light_leak"].isEnabled()
                window.process("light_leak");wait(window)
            if operation=="surface":
                window.defect_controls["scratches"].setValue(.5)
                window.process("weather");wait(window)
            if operation=="peel":
                assert window.operation_buttons["clear_negative"].isEnabled()
                window.process("clear_negative");wait(window)
                assert window.body_choice.currentIndex()==1
                assert window.session.group.bodies[1].usable_negative
        assert window.material_group.isEnabled()  # Frozen values remain browsable.
        assert all(not control.isEnabled() for control in window.material_controls.values())
        assert len(window.session.group.bodies)==(2 if kind=="peel" else 1)
        assert window.inspector.histogram.bins[0].sum()==24*36
        assert window.inspector.history.topLevelItemCount()==len(window.session.recipe()['steps'])+1
        window.inspector.history_button.click();_application().processEvents()
        assert window.inspector.history.isVisible()
        before={k:v.copy() for k,v in window.session.state.fields.items()}
        if kind=="peel":window.body_choice.setCurrentIndex(1)
        if kind=="plate":window.view_angle.setValue(-35)
        window.view_ev.setValue(.5);window.refresh_preview();wait(window)
        for k,v in before.items():np.testing.assert_array_equal(v,window.session.state.fields[k])
        archive=tmp_path/'products.ffmaterial.npz'
        monkeypatch.setattr(QtWidgets.QFileDialog,"getSaveFileName",lambda *a,**k:(str(archive),""))
        window.save_dialog("products");wait(window)
        restored=api.load(archive);assert len(restored.bodies)==len(window.session.group.bodies)
        png=tmp_path/'view.png'
        monkeypatch.setattr(QtWidgets.QFileDialog,"getSaveFileName",lambda *a,**k:(str(png),""))
        window.save_dialog("png");wait(window)
        with Image.open(png) as image:
            assert image.size==(36,24)
            expected_shape=window.session.preview(window.conditions(),max(0,window.body_choice.currentIndex())).shape
            assert image.size==expected_shape[1::-1]
            metadata=json.loads(image.info["FilmFoundryObservation"])
            assert metadata["view"]["exposure_ev"]==.5
            assert metadata["body_id"]==window.session.group.bodies[window.body_choice.currentIndex()].body_id
        if kind=="integral":
            before_border=window.session.state
            window.border_choice.setCurrentIndex(window.border_choice.findData("pod_short"))
            window.refresh_preview();wait(window)
            assert window.session.state is before_border
            assert (window.viewer.source.height(),window.viewer.source.width())==(26,42)
        state_path=tmp_path/'state.ffmaterial.npz';window.session.save(state_path)
        window.process_controls[setting].setValue(0)
        monkeypatch.setattr(QtWidgets.QFileDialog,"getOpenFileName",lambda *a,**k:(str(state_path),""))
        monkeypatch.setattr(window,"confirm_replace",lambda:True)
        window.load_dialog();wait(window)
        assert window.process_controls[setting].value()==expected
        assert window.leak_strength.value()==.4
        if kind=="plate":assert window.defect_controls["scratches"].value()==.5
        else:
            assert window.defect_controls["reagent_leak"].value()==.5
            assert window.material_controls["batch_seed"].value()==19
            if kind=="integral":assert window.defect_controls["solar_warming"].value()==8
        if kind!="plate":assert not window.process_controls["coverage"].isVisible()
    finally:window.close();_application().processEvents()


def test_plate_preparation_restore_attach_and_wrong_family_is_transactional(tmp_path):
    state=silver_plate.sensitize(silver_plate.polish(silver_plate.prepare((4,6),group_id="prepared")))
    archive=tmp_path/'plate.npz';api.save(archive,state)
    session=MaterialLabSession("plate");session.load(archive)
    assert "expose" not in session.operations
    source=tmp_path/'source.png';Image.new("RGB",(6,4),"red").save(source)
    session.open_image(source,0,preserve_preparation=True)
    assert session.state.group_id=="prepared" and "expose" in session.operations
    session.process("expose");assert session.state.stage=="exposed"
    other=MaterialLabSession("peel");other.open_image(source,0)
    previous=other.image
    with pytest.raises(ValueError):other.load(archive)
    assert other.image is previous and other.state is None


def test_windows_are_independent_and_close_waits_for_active_work():
    first=MaterialLabWindow("peel");second=MaterialLabWindow("peel")
    first.show();second.show();gate=Event()
    try:
        first.material_controls["reaction_seconds"].setValue(123)
        assert second.material_controls["reaction_seconds"].value()!=123
        first._submit(lambda:gate.wait(2),"working",render=False)
        first.close();assert first.isVisible() and first._closing
        gate.set();wait(first);assert not first.isVisible()
    finally:gate.set();first.close();second.close();_application().processEvents()


def test_batch_material_draft_survives_preview_sync(tmp_path):
    from film_foundry.ui.material_batch_dialog import MaterialBatchDialog
    path=tmp_path/'batch.png';Image.new('RGB',(12,8),'gray').save(path)
    window=MaterialBatchDialog('peel',[str(path)])
    try:
        wait(window);window.process('expose');wait(window)
        window.material_controls['reaction_seconds'].setValue(400)
        window._sync()
        assert window.material_controls['reaction_seconds'].isEnabled()
        assert window.material_controls['reaction_seconds'].value()==400
        assert window.session.state.material.reaction_seconds!=400
    finally:window.close();_application().processEvents()

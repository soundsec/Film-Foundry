"""Boundaries for native-scale inspection, material branches and the guided first work."""
from pathlib import Path
import numpy as np
import pytest
from PIL import Image

from film_foundry.app.material_lab_session import FAMILIES
from film_foundry.tests.test_desktop_usability import desktop, photos, wait


def test_native_region_matches_formal_file(tmp_path):
    from film_foundry.model.config import DarkroomConfig
    from film_foundry.app.film_test_strip import FilmStripSession
    from film_foundry.app.workflow import process_image_file
    from film_foundry.app.workspace_jobs import numbered_photo_config
    from film_foundry.contracts.scan_framing import pixel_bounds
    image=np.random.default_rng(11).integers(0,256,(78,94,3),dtype=np.uint8)
    path=tmp_path/'input.png';Image.fromarray(image).save(path)
    config=DarkroomConfig();config.seed_strategy='fixed';config.random_seed=34
    config.output.rotation_quarters=1;config.output.anti_banding_strength=0
    config.output.bit_depth=8;config.output.preview_long_edge=64
    config.scanner.crop_rect=(.06,.03,.92,.95);config.scanner.straighten_degrees=3.
    session=FilmStripSession(path,'full',config,long_edge=32)
    session.frame_settings=(2,.5)
    area=(.19,.21,.71,.76)
    from film_foundry.app.region_inspection import RegionInspection
    inspection=RegionInspection(session,scaled=True)
    pixels,bounds,shape=inspection.read(0,config,area)
    old=inspection.frame
    smaller,(left,top,right,bottom),_=inspection.read(0,config,(.3,.3,.6,.6))
    x,y=bounds[:2]
    np.testing.assert_array_equal(smaller,pixels[top-y:bottom-y,left-x:right-x])
    assert inspection.frame is old and old is not None
    output=tmp_path/'formal.png'
    process_image_file(path,output,numbered_photo_config(config,2,.5),scaled_override=True)
    encoded=np.asarray(Image.open(output).convert('RGB'))
    assert encoded.shape[:2]==shape and bounds==pixel_bounds(shape,area)
    l,t,r,b=bounds
    np.testing.assert_allclose(np.clip(pixels,0,1)*255,encoded[t:b,l:r],atol=1)
    assert config.exposure_ev==0
    # A scan may select its raw companion and sidecar even when opened by NPZ.
    from film_foundry.app.workflow import transmission_raw_path_for_medium
    medium=next(tmp_path.rglob('*.npz'))
    scan=FilmStripSession(medium,'scan',config)
    raw=transmission_raw_path_for_medium(medium)
    sidecar=raw.with_suffix(raw.suffix+'.json')
    with sidecar.open('a',encoding='utf-8') as stream:stream.write(' ')
    with pytest.raises(ValueError,match='Source changed'):scan.check_source()


@pytest.mark.parametrize('family',FAMILIES)
def test_branch_from_recorded_operation(family):
    from film_foundry.tests.test_material_recipes import formed
    original=formed(family);record=original.recipe();state=original.state
    index=len(record['steps'])//2
    fork=original.branch_at_step(index)
    assert fork.recipe()['steps']==record['steps'][:index]
    for step in record['steps'][index:]:
        fork.process(step['operation'],step['parameters'])
    for key,value in state.fields.items():
        np.testing.assert_array_equal(fork.state.fields[key],value)
    assert original.state is state and fork.state is not state
    assert original.branch_at_step().state is state


def test_gallery_tour_and_native_dialog(desktop,tmp_path):
    from film_foundry.ui.first_tour import open_tour
    from film_foundry.ui.formal_region import open_formal_region
    from film_foundry.ui.preferences import load_preferences
    panel=desktop;panel.open_library()
    assert not panel.library_view.tour_banner.isHidden()
    before=panel.edit_tools.capture()
    tour=open_tour(panel)
    for stage in (1,2,3,4):
        tour.next.click();wait(lambda:not tour.busy)
        assert tour.page==stage and tour.viewer.source is not None,tour.status.text()
        if stage==1:
            tour.value.setValue(1.0)
            tour.value.setValue(1.5)
            assert not tour.next.isEnabled()
            wait(lambda:tour.next.isEnabled() and not tour.busy)
            assert tour.config.exposure_ev==1.5
            assert ('expose',1.5,tour.config.chemistry.time_min,tour.config.look.print_exposure_ev) in tour.images
    tour.next.click()
    assert load_preferences()['tour_seen'] and panel.library_view.tour_banner.isHidden()
    assert panel.edit_tools.capture()==before
    panel._open_catalog_source(photos(tmp_path)[0],'film')
    native=open_formal_region(panel);wait(lambda:not native.busy)
    assert native.canvas.pixmap is not None,native.status.text()
    native.calculate.click();wait(lambda:not native.busy)
    assert native.view.source is not None and native.view.zoom_percent>100,native.status.text()
    assert native.zoom_label.text().endswith(f'{native.view.zoom_percent}%')
    fitted=native.view.zoom_percent
    native.view.zoom(1.2)
    assert native.view.zoom_percent>fitted
    native.native_button.click()
    assert native.view.zoom_percent==100 and native.zoom_label.text().endswith('100%')
    native.reject()


def test_checkpoint_branch_save_and_reopen(desktop,tmp_path):
    from film_foundry.ui.material_checkpoints import open_checkpoints
    from film_foundry.app.material_workspace import load_material_workspace
    from film_foundry.tests.test_material_recipes import formed
    panel=desktop
    from film_foundry.ui.material_lab_window import create_material_window
    page=create_material_window('wet');page.session=formed('wet')
    panel._mount_material_workspace(page,'wet');page._restore_controls();page._sync()
    state=page.session.state
    from film_foundry.ui.formal_region import open_formal_region
    region=open_formal_region(panel);wait(lambda:not region.busy)
    region.calculate.click();wait(lambda:not region.busy)
    assert region.view.source is not None and region.view.zoom_percent>100,region.status.text()
    assert page.session.state is state
    region.reject()
    picker=open_checkpoints(page);picker.resize(540,290);picker.steps.setCurrentRow(2)
    steps=picker.steps
    boxes=[steps.visualItemRect(steps.item(i)) for i in range(steps.count())]
    assert all(box.height()>=steps.fontMetrics().height() for box in boxes)
    assert all(a.bottom()<b.top() for a,b in zip(boxes,boxes[1:]))
    picker.branch.click()
    wait(lambda:not picker.busy)
    branch=panel.workspace_host.currentWidget()
    assert branch is not page,picker.status.text()
    wait(lambda:not branch.busy)
    assert page.session.state is state and len(branch.session.recipe()['steps'])==2
    picker=open_checkpoints(branch);target=tmp_path/'checkpoint.ffworkspace'
    picker.save_to(target);wait(lambda:not picker.busy)
    reloaded=load_material_workspace(target).documents[0].session
    for key,value in branch.session.state.fields.items():
        np.testing.assert_array_equal(reloaded.state.fields[key],value)
    picker.open_from(target);wait(lambda:not picker.busy)
    reopened=panel.workspace_host.currentWidget()
    assert reopened is not branch,picker.status.text()
    wait(lambda:not reopened.busy)
    assert reopened.session.operations==branch.session.operations


def test_material_trial_region_uses_working_pixels():
    from dataclasses import asdict
    from film_foundry.app.material_lab_session import MaterialLabSession
    from film_foundry.app.material_test_strip import MaterialStripSession
    from film_foundry.app.region_inspection import RegionInspection
    from film_foundry.core.material_family import ViewingConditions
    sheet=MaterialLabSession('paper')
    sheet.image=np.random.default_rng(7).random((43,71,3),dtype=np.float32)
    config=dict(operation=None,operations={},material={},seed=31,scan={'rotation_quarters':1},
                view=asdict(ViewingConditions()),body=0)
    session=MaterialStripSession(sheet,long_edge=24)
    assert max(session.render(config).shape[:2])==24
    result,bounds,shape=RegionInspection(session).read(0,config,(.1,.15,.8,.6))
    assert sheet.state is None
    sheet.run_standard(seed=config['seed'])
    sheet.scan_adjustments=config['scan']
    expected=sheet.preview()
    assert shape==expected.shape[:2] and max(shape)>24
    l,t,r,b=bounds
    np.testing.assert_array_equal(result,expected[t:b,l:r])


def test_linked_trial_transfers_selected_area(desktop,tmp_path,monkeypatch):
    from film_foundry.ui.trial_comparison import TrialComparison
    from film_foundry.ui.formal_region import FormalRegion
    from film_foundry.ui.qt import QtCore,QtWidgets
    desktop._open_catalog_source(photos(tmp_path)[0],'film')
    strip=desktop._open_test_strip()
    strip.matrix.setChecked(True)
    strip.spacing.setCurrentIndex(strip.spacing.findData('list'))
    strip.values.setText('-1,0');strip.times.setText('5,10');strip.generate()
    wait(lambda:not strip.busy)
    assert len(strip.frames)==4,strip.status.text()
    def inspect(linked):
        linked.set_region(QtCore.QRectF(.25,.25,.5,.5))
        linked.choices.button(3).click()
        native_action=next(b for b in linked.findChildren(QtWidgets.QPushButton) if b.text() in ('局部正式尺度…','Formal-scale region…'))
        native_action.click()
        dialog=linked.findChild(FormalRegion)
        assert dialog.choice.currentIndex()==3 and dialog.canvas.region==(.25,.25,.75,.75)
        for choice in (3,0):
            dialog.choice.setCurrentIndex(choice);dialog.calculate.click();wait(lambda:not dialog.busy)
            assert dialog.view.source is not None,dialog.status.text()
            if choice==3:
                assert dialog.view.zoom_percent>100
                dialog.native_button.click()
            assert dialog.view.zoom_percent==100
            assert dialog.inspection.index==choice and dialog.canvas.region==(.25,.25,.75,.75)
        dialog.reject();linked.close()
    monkeypatch.setattr(TrialComparison,'exec',inspect)
    strip.compare_all()
    strip.reject()

def test_curve_window_refreshes_current_recipe(desktop,monkeypatch):
    from film_foundry.ui import characteristic_curve
    from film_foundry.ui.qt import QtWidgets
    from film_foundry.model.config import DarkroomConfig

    recipe = DarkroomConfig()
    seen = []
    measure = characteristic_curve.measure_curve
    def record(config):
        seen.append(config.chemistry.time_min)
        return measure(config)
    monkeypatch.setattr(characteristic_curve, 'measure_curve', record)
    monkeypatch.setattr(desktop, '_config', lambda: recipe)
    parent = desktop.film_probe.dialog
    curve = characteristic_curve.show_curve(desktop,parent)
    assert not curve.isModal() and characteristic_curve.show_curve(desktop,parent) is curve
    wait(lambda:not curve.busy)
    before = recipe.chemistry.time_min
    recipe.chemistry.time_min = before+2
    curve.findChild(QtWidgets.QPushButton).click()
    wait(lambda:not curve.busy)
    assert seen==[before,before+2] and curve.findChild(characteristic_curve.CurvePlot).data is not None
    curve.close()
    assert parent._curve_dialog is None


def test_material_pin_tracks_bordered_rotated_result(desktop):
    from types import SimpleNamespace
    from film_foundry.tests.test_material_recipes import formed
    from film_foundry.core.material_family import ViewingConditions, observation_border
    from film_foundry.ui.comparison_view import ComparisonView
    from film_foundry.ui.material_probe import MaterialProbe
    from film_foundry.ui.qt import QtCore,QtGui,QtWidgets

    session=formed('integral')
    session.scan_adjustments['rotation_quarters']=1
    view=ViewingConditions(include_border=True,border_style='pod_short')
    body=session.group.bodies[0]
    h,w=next(iter(body.fields.values())).shape[:2]
    top,bottom,left,right=observation_border(body,(h,w),view)
    widget=ComparisonView()
    owner=SimpleNamespace(session=session,viewer=widget,kind='integral')
    panel=QtWidgets.QWidget()
    probe=MaterialProbe(owner,QtWidgets.QMenu(panel),QtWidgets.QVBoxLayout(panel))
    frame=QtGui.QPixmap(h+top+bottom,w+left+right)
    frame.fill(QtCore.Qt.GlobalColor.gray)
    widget.set_pixmap_pair(frame,frame)
    widget.set_rotation(3)
    widget.set_comparison_mode('side_by_side')
    probe.publish(0,view)
    probe.set_aperture(5)
    probe.pins=[(5.5/w,7.5/h)]
    probe.show_pins()
    assert '(5, 7)' in probe.readout.text()
    def render_marks():
        canvas=QtGui.QImage(150,80,QtGui.QImage.Format.Format_ARGB32)
        canvas.fill(QtCore.Qt.GlobalColor.transparent)
        painter=QtGui.QPainter(canvas)
        widget.probe_overlay(painter)
        painter.end()
        return canvas
    # The two rotations cancel. Only the result half carries the five-pixel ring.
    cx=widget.displayed_result().width()+left+5
    cy=top+7
    assert any(render_marks().pixelColor(x,cy).alpha() for x in range(cx+4,cx+8))
    widget.set_source_held(True)
    assert not render_marks().pixelColor(cx+5,cy).alpha()
    probe.clear()
    assert not probe.pins and probe.readout.isHidden()

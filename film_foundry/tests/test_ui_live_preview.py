"""Direct curve interaction, preview scheduling and explicit reduced copies."""
import copy
from dataclasses import asdict,fields,replace
from pathlib import Path
from threading import Event
from time import monotonic,sleep
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import numpy as np
import pytest
from PySide6.QtTest import QTest
from film_foundry.ui.qt import QtCore, QtGui, QtWidgets, _application


@pytest.fixture(autouse=True)
def isolated_ui(tmp_path, monkeypatch):
    from film_foundry.ui import i18n
    from film_foundry.ui.preferences import _read
    previous=i18n.current_language()
    monkeypatch.setenv("FILM_FOUNDRY_UI_SETTINGS", str(tmp_path/"ui.json"))
    _read.cache_clear();i18n.set_language("zh_CN")
    yield
    i18n.set_language(previous);_read.cache_clear()


def wait_for(condition, timeout=5):
    deadline=monotonic()+timeout
    while not condition() and monotonic()<deadline:
        QtWidgets.QApplication.processEvents();sleep(.01)
    assert condition()


def test_double_click_segment_adds_without_tone_jump_and_direct_delete():
    from film_foundry.ui.curve_editor import CurveEditor
    _application();curve=CurveEditor("color_curve_rgb");curve.resize(440,430);curve.show()
    try:
        curve.channel.setCurrentIndex(2);QTest.qWait(10)
        position=curve.canvas.position((.5,.5)).toPoint()+QtCore.QPoint(0,5)
        QTest.mouseDClick(curve.canvas,QtCore.Qt.MouseButton.LeftButton,pos=position)
        points=curve.read()[1]
        assert len(points)==3
        assert points[1][0] == pytest.approx(points[1][1],abs=1e-7)
        assert curve.read()[0] == ((0.,0.),(1.,1.))
        QTest.mouseDClick(curve.canvas,QtCore.Qt.MouseButton.LeftButton,pos=curve.canvas.position(points[1]).toPoint())
        assert len(curve.read()[1])==3
        QTest.keyClick(curve.canvas,QtCore.Qt.Key.Key_Delete)
        assert len(curve.read()[1])==2
        QTest.keyClick(curve.canvas,QtCore.Qt.Key.Key_Delete)
        assert len(curve.read()[1])==2
        QTest.mouseDClick(curve.canvas,QtCore.Qt.MouseButton.LeftButton,pos=position)
        QTest.mouseClick(curve.canvas,QtCore.Qt.MouseButton.RightButton,pos=curve.canvas.position(curve.current_points()[1]).toPoint())
        assert len(curve.read()[1])==2
        QTest.mouseDClick(curve.canvas,QtCore.Qt.MouseButton.LeftButton,pos=QtCore.QPoint(2,2))
        assert len(curve.read()[1])==2
    finally:curve.close()


def test_debounce_one_worker_latest_frame_and_manual_toggle(tmp_path,monkeypatch):
    from film_foundry.tools.run_desktop_gui import DesktopPanel
    from film_foundry.ui.preferences import load_preferences
    monkeypatch.setattr(DesktopPanel,"PREVIEW_DEBOUNCE_MS",40)
    panel=DesktopPanel();requests=[];shown=[];release=Event();finished=Event()
    source=tmp_path/"sample.png";source.touch()
    pixels=np.full((4,6,3),.5,np.float32)
    def render(request):
        requests.append(request)
        if len(requests)==1:release.wait(3)
        panel._post_preview(request.token,lambda:shown.append(request.config.look.print_exposure_ev))
        panel.root.after(0,lambda:panel._finish_preview(request.token))
        finished.set()
    monkeypatch.setattr(panel,"_render_preview",render)
    try:
        panel.input_path.set(str(source));wait_for(lambda:len(requests)==1)
        formal=asdict(panel._config())
        assert requests[0].config.processing.quality_mode == "draft"
        assert requests[0].config.processing.execution_mode == "reduced_fast"
        assert requests[0].config.seed_strategy == "path"
        assert requests[0].long_edge == 900
        assert asdict(panel._config()) == formal
        for value in (.2,.4,.6):panel.print_exposure.set(value)
        panel.curve_editors["tone_curve"].add_point(.5,.65)
        QTest.qWait(80);assert len(requests)==1
        release.set();wait_for(lambda:len(requests)==2 and not panel._preview_running)
        assert shown == [.6]
        assert requests[1].config.scanner.tone_curve is not None
        assert panel.preview_button.qt.isEnabled()
        panel.auto_preview.set(False);panel._change_auto_preview()
        assert load_preferences()["auto_preview"] is False
        panel.print_exposure.set(.8);QTest.qWait(80);assert len(requests)==2
        panel.preview_button.qt.click();wait_for(lambda:len(requests)==3 and not panel._preview_running)
        assert shown == [.6,.8]
    finally:
        release.set();panel.root.destroy()


def test_auto_preview_waits_for_formal_task_and_language_rebuild_keeps_one_binding(tmp_path,monkeypatch):
    from film_foundry.tools.run_desktop_gui import DesktopPanel
    from film_foundry.ui.i18n import language_label
    monkeypatch.setattr(DesktopPanel,"PREVIEW_DEBOUNCE_MS",30)
    panel=DesktopPanel();requests=[]
    source=tmp_path/"sample.png";source.touch()
    def render(request):
        requests.append(request)
        panel.root.after(0,lambda:panel._finish_preview(request.token))
    monkeypatch.setattr(panel,"_render_preview",render)
    try:
        panel._processing=True;panel.input_path.set(str(source));panel.exposure_ev.set(1)
        QTest.qWait(80);assert requests==[]
        panel._finish_process();wait_for(lambda:len(requests)==1 and not panel._preview_running)
        count=len(panel.exposure_ev._views)
        for language in ("en_US","zh_CN","en_US"):
            panel.language.set(language_label(language));panel._change_language()
            assert len(panel.exposure_ev._views)==count
        revision=panel._preview_revision
        panel.exposure_ev.set(2)
        assert panel._preview_revision==revision+1
        wait_for(lambda:len(requests)==2 and not panel._preview_running)
    finally:panel.root.destroy()


def test_closing_window_discards_late_frame_and_completion(tmp_path,monkeypatch):
    from film_foundry.tools.run_desktop_gui import DesktopPanel
    panel=DesktopPanel();release=Event();started=Event();done=Event();shown=[]
    source=tmp_path/"sample.png";source.touch()
    def render(request):
        started.set();release.wait(3)
        panel._post_preview(request.token,lambda:shown.append(True))
        panel.root.after(0,lambda:panel._finish_preview(request.token));done.set()
    monkeypatch.setattr(panel,"_render_preview",render)
    panel.input_path.set(str(source));panel._start_preview();assert started.wait(1)
    panel.root.destroy();release.set();assert done.wait(1);QTest.qWait(20)
    assert shown==[]


def test_real_auto_refresh_and_observation_cache_leave_formal_config_unchanged(tmp_path,monkeypatch):
    from PIL import Image
    from film_foundry.tools.run_desktop_gui import DesktopPanel
    monkeypatch.setattr(DesktopPanel,"PREVIEW_DEBOUNCE_MS",30)
    panel=DesktopPanel();source=tmp_path/"sample.png"
    Image.fromarray(np.linspace(20,220,48*72*3,dtype=np.uint8).reshape(48,72,3)).save(source)
    try:
        config=panel._config();config.fast_mode=False;config.processing.execution_mode="quality"
        config.processing.quality_mode="high";config.enable_grain=False;config.enable_mtf=False;config.enable_halation=False
        panel._apply_loaded_config(config);before=asdict(panel._config())
        panel.input_path.set(str(source));wait_for(lambda:panel.result_view.source is not None and not panel._preview_running)
        medium=panel._preview_session._medium;optical=medium.optical_density_rgb.copy()
        first=panel.result_view.source.toImage()
        panel.curve_editors["tone_curve"].add_point(.5,.8)
        wait_for(lambda:not panel._preview_running and not panel._preview_timer.isActive() and panel.result_view.source.toImage()!=first)
        assert panel._preview_session._medium is medium
        np.testing.assert_array_equal(medium.optical_density_rgb,optical)
        current=asdict(panel._config());current["scanner"]["tone_curve"]=before["scanner"]["tone_curve"]
        assert current==before
    finally:panel.root.destroy()


def test_preview_copies_keep_frozen_geometry_profiles_and_one_layer_shape():
    from film_foundry.app.preview_inputs import preview_latent,preview_medium
    from film_foundry.core.engine import expose_image_to_latent,develop_negative
    from film_foundry.model.config import DarkroomConfig
    config=DarkroomConfig(enable_grain=False,enable_mtf=False,enable_halation=False)
    config.mode="bw_negative";config.film.spatial_reference_short_edge_mm=24.
    image=np.full((16,24,3),.3,np.float32)
    latent=expose_image_to_latent(image,config)
    # Shared/phone latent sources can carry a single material layer, while
    # the desktop unified black-and-white exposure may keep three pools.
    latent=replace(latent,**{field.name:getattr(latent,field.name)[...,:1].copy() for field in fields(latent) if isinstance(getattr(latent,field.name),np.ndarray)})
    original=latent.halide.copy()
    small=preview_latent(latent,12)
    assert small.halide.shape == (8,12,1)
    assert small.material_geometry.full_shape == (8,12)
    assert small.material_geometry.frame_long_edge_mm == latent.material_geometry.frame_long_edge_mm
    assert small.spectral_exposure_profile is latent.spectral_exposure_profile
    np.testing.assert_array_equal(latent.halide,original)
    small.halide.fill(0);np.testing.assert_array_equal(latent.halide,original)
    medium=develop_negative(image,config);optical=medium.optical_density_rgb.copy()
    viewed=preview_medium(medium,12)
    assert viewed.frame_shape[:2] == (8,12)
    assert viewed.clear_base_optical_density_rgb == medium.clear_base_optical_density_rgb
    assert viewed.image_polarity == medium.image_polarity
    np.testing.assert_array_equal(medium.optical_density_rgb,optical)


@pytest.mark.parametrize("spectral",[False,True])
def test_bounded_file_observation_preserves_medium_and_spectral_amounts(tmp_path,spectral):
    from film_foundry.app.workflow import observe_medium_file
    from film_foundry.core.engine import develop_negative,save_developed_medium_at_path
    from film_foundry.core.negative_io import load_developed_medium_for_scan
    from film_foundry.tests.test_desktop_capabilities import spectral_config
    from film_foundry.model.config import DarkroomConfig
    config=spectral_config() if spectral else DarkroomConfig(enable_grain=False,enable_mtf=False,enable_halation=False)
    medium=develop_negative(np.full((16,24,3),.4,np.float32),config)
    config.output.save_scanner_raw=False;config.output.export_transparent_plate=False;config.save_sidecar=False
    path=tmp_path/"frame.darkroom_negative.npz";save_developed_medium_at_path(tmp_path/"source.png",path,medium,config)
    original=medium.optical_density_rgb.copy()
    amounts=medium.spectral_medium.component_amounts.copy() if spectral else None
    result,kind,_,view=observe_medium_file(path,config,retain_stages=True,preview_long_edge=12)
    assert kind == "density_npz" and result.output_srgb.shape[:2] == (8,12)
    assert view.shape[:2] == (8,12)
    restored=load_developed_medium_for_scan(path)
    np.testing.assert_array_equal(restored.optical_density_rgb,original)
    if spectral:np.testing.assert_array_equal(restored.spectral_medium.component_amounts,amounts)


def test_fast_latent_and_scan_preview_keep_source_readonly_and_show_large_result(tmp_path,monkeypatch):
    from film_foundry.tools.run_desktop_gui import DesktopPanel
    from film_foundry.core.engine import expose_image_to_latent,develop_negative,save_developed_medium_at_path
    from film_foundry.core.latent_io import save_exposed_latent,load_exposed_latent,latent_identity_from_film
    from film_foundry.model.config import DarkroomConfig
    monkeypatch.setattr(DesktopPanel,"PREVIEW_EDGE",12)
    panel=DesktopPanel();panel.auto_preview.set(False);panel._change_auto_preview()
    config=DarkroomConfig(enable_grain=False,enable_mtf=False,enable_halation=False)
    config.film.spatial_reference_short_edge_mm=24.
    image=np.full((16,24,3),.35,np.float32)
    latent=expose_image_to_latent(image,config)
    path=tmp_path/"frame.darkroom_latent.npz";save_exposed_latent(path,latent,identity=latent_identity_from_film(config.film))
    config.output.save_scanner_raw=False;config.output.export_transparent_plate=False;config.save_sidecar=False
    medium=develop_negative(image,config);negative=tmp_path/"frame.darkroom_negative.npz";save_developed_medium_at_path(tmp_path/"source.png",negative,medium,config)
    try:
        panel._apply_loaded_config(config);panel.pipeline_mode.set("develop_latent");panel.negative_path.set(str(path))
        panel._update_mode_visibility();panel._start_preview()
        wait_for(lambda:not panel._preview_running)
        assert panel.result_view.source is not None,panel.status.get()
        restored,metadata=load_exposed_latent(path)
        assert not metadata["consumed"]
        np.testing.assert_array_equal(restored.halide,latent.halide)
        panel.pipeline_mode.set("scan");panel.negative_path.set(str(negative));panel._update_mode_visibility();panel._start_preview()
        wait_for(lambda:not panel._preview_running)
        assert panel.result_view.source.width()==12,panel.status.get()
        assert panel.preview_tabs.isTabVisible(2)
        assert panel.inspector_view.source is not None
    finally:panel.root.destroy()


def test_transmission_raw_preview_reduces_after_border_split_and_preserves_file(tmp_path):
    from film_foundry.app.workflow import observe_medium_file
    from film_foundry.core.electronic_negative import save_linear_rgb_tiff,load_linear_rgb_tiff
    from film_foundry.model.config import DarkroomConfig
    image=np.full((16,24,3),.35,np.float32)
    image[:2]=.75;image[-2:]=.75;image[:,:2]=.75;image[:,-2:]=.75
    path=tmp_path/"frame.scanner_raw.tiff";save_linear_rgb_tiff(image,path)
    stored=load_linear_rgb_tiff(path)
    config=DarkroomConfig();config.output.scanner_raw_border_percent=.1;config.output.scanner_raw_border_min_px=1
    result,kind,_,view=observe_medium_file(path,config,preview_long_edge=12)
    assert kind == "scanner_raw_tiff"
    assert max(result.output_srgb.shape[:2])==12 and max(view.shape[:2])==12
    np.testing.assert_array_equal(load_linear_rgb_tiff(path),stored)


def test_chinese_labels_titles_and_curve_buttons_fit_at_large_font():
    from film_foundry.tools.run_desktop_gui import DesktopPanel
    app=_application();previous=app.font()
    for filename in ("C:/Windows/Fonts/msyh.ttc","C:/Windows/Fonts/segoeui.ttf"):
        if Path(filename).exists():QtGui.QFontDatabase.addApplicationFont(filename)
    app.setFont(QtGui.QFont("Microsoft YaHei",12))
    panel=DesktopPanel();panel.root.qt.resize(1100,850);panel.root.qt.show()
    try:
        panel.expert_mode.set(True);panel._update_expert_visibility()
        for section in panel.sections.values():section.set_expanded(True)
        for page in range(6):
            panel.navigation.setCurrentRow(page);QTest.qWait(20)
            for label in panel.pages[page].qt.findChildren(QtWidgets.QLabel):
                if label.isVisible() and label.wordWrap():
                    assert label.height() >= label.heightForWidth(label.width()), label.text()
        panel.navigation.setCurrentRow(panel.page_index["scan"]);QTest.qWait(20)
        for button in panel.curve_editors["tone_curve"].buttons.values():
            assert button.width() >= button.minimumSizeHint().width()
            assert button.height() >= button.fontMetrics().height()+8
        group=panel.film_source_group.qt
        option=QtWidgets.QStyleOptionGroupBox();group.initStyleOption(option)
        title=group.style().subControlRect(QtWidgets.QStyle.ComplexControl.CC_GroupBox,option,QtWidgets.QStyle.SubControl.SC_GroupBoxLabel,group)
        assert title.height() >= group.fontMetrics().height()
    finally:panel.root.destroy();app.setFont(previous)

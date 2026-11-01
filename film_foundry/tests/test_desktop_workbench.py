import copy
from dataclasses import asdict
import json
import os
from time import monotonic

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import numpy as np
import pytest

from film_foundry.model.config import DarkroomConfig, ScannerConfig, normalize_scanner_tone_curve
from film_foundry.core.scanner import apply_scanner_tone_curve


@pytest.fixture(autouse=True)
def isolated_camera_profile(tmp_path,monkeypatch):
    from film_foundry.ui.preferences import _read
    monkeypatch.setenv("FILM_FOUNDRY_CAMERA_SETTINGS",str(tmp_path/"desktop_camera.json"))
    monkeypatch.setenv("FILM_FOUNDRY_UI_SETTINGS",str(tmp_path/"desktop_ui.json"))
    _read.cache_clear()


def test_camera_identity_survives_batches_restart_and_separate_resets(tmp_path,monkeypatch):
    from film_foundry.tools.run_desktop_gui import DesktopPanel
    from film_foundry.app.camera_profile import CameraIdentity,CameraProfileStore
    store=CameraProfileStore();store.save(CameraIdentity(11,22))
    panel=DesktopPanel()
    try:
        first=panel._freeze_incident_config(panel._config())
        exported=panel._recipe_export_config()
        assert exported.chemistry.light_leak_identity_seed==11 and exported.chemistry.exposure_occlusion_identity_seed==22
        assert panel._config().chemistry.light_leak_identity_seed is None
        panel._new_incident_identity()
        batch=panel._freeze_incident_config(panel._config())
        assert first.chemistry.light_leak_identity_seed==batch.chemistry.light_leak_identity_seed==11
        assert first.chemistry.exposure_occlusion_identity_seed==batch.chemistry.exposure_occlusion_identity_seed==22
        monkeypatch.setattr("film_foundry.app.camera_profile.secrets.randbits",lambda _:33)
        panel._replace_camera_identity(gate=True)
        gate=panel._freeze_incident_config(panel._config())
        assert gate.chemistry.light_leak_identity_seed==11
        assert gate.chemistry.exposure_occlusion_identity_seed==33
        panel._replace_camera_identity(structure=True)
        body=panel._freeze_incident_config(panel._config())
        assert body.chemistry.light_leak_identity_seed==33 and body.chemistry.exposure_occlusion_identity_seed==33
    finally:panel.root.destroy()
    restored=DesktopPanel()
    try:
        recipe=restored._freeze_incident_config(restored._config())
        assert recipe.chemistry.light_leak_identity_seed==33 and recipe.chemistry.exposure_occlusion_identity_seed==33
        restored.pipeline_mode.set("develop_latent")
        cfg=restored._config();frozen=restored._freeze_incident_config(cfg)
        assert frozen.chemistry.light_leak_identity_seed is None
    finally:restored.root.destroy()


def test_tone_contract_json_share_and_cli(tmp_path):
    from film_foundry.app.config_assets import load_curve_file, load_recipe_file
    from film_foundry.app.workflow import encode_recipe_share
    from film_foundry.app.cli import build_parser, _apply_common_config
    config=DarkroomConfig();config.scanner.tone_curve=((0.,.05),(.4,.6),(1.,1.))
    config.scanner.color_curve_rgb=(((0.,0.),(.5,.45),(1.,1.)),)*3
    path=tmp_path/"recipe.json";path.write_text(json.dumps(asdict(config)),encoding="utf-8")
    assert load_recipe_file(path).scanner == config.scanner
    assert load_curve_file(path,"tone_curve") == config.scanner.tone_curve
    share=tmp_path/"recipe.ffc2";share.write_text(encode_recipe_share(config),encoding="utf-8")
    assert load_recipe_file(share).scanner == config.scanner
    curve=tmp_path/"tone.json";curve.write_text(json.dumps(config.scanner.tone_curve),encoding="utf-8")
    args=build_parser().parse_args(["scan","medium.npz",str(tmp_path),"--scanner-tone-curve",str(curve)])
    assert _apply_common_config(args).scanner.tone_curve == config.scanner.tone_curve


@pytest.mark.parametrize("curve",[[],[(.2,.2)],[(.5,0),(.5,1)],[(0,0),(1,float('nan'))],[(0,0),(1,float('inf'))],[(0,0),(1.1,1)]])
def test_tone_rejects_invalid_contract(curve):
    with pytest.raises(ValueError):normalize_scanner_tone_curve(curve)


def test_rgb_curve_nonfinite_values_cannot_enter_graph_or_scanner():
    from film_foundry.model.config import normalize_scanner_color_curve_rgb
    with pytest.raises(ValueError,match="finite"):
        normalize_scanner_color_curve_rgb((((0.,0.),(1.,float("nan"))),)*3)


def test_tone_identity_hue_ratios_black_and_rgb_order():
    image=np.array([[[.2,.3,.4],[0.,0.,0.],[.9,.2,.1]]],np.float32)
    image.setflags(write=False);before=image.copy()
    identity=ScannerConfig(tone_curve=((0,0),(.5,.5),(1,1)))
    np.testing.assert_array_equal(apply_scanner_tone_curve(image,identity),image)
    scanner=ScannerConfig(tone_curve=((0,.1),(1,1)))
    toned=apply_scanner_tone_curve(image,scanner)
    np.testing.assert_allclose(toned[0,0]/image[0,0],np.repeat(toned[0,0,0]/image[0,0,0],3),rtol=2e-7)
    np.testing.assert_array_equal(toned[0,1],np.full(3,.1,np.float32))
    np.testing.assert_array_equal(image,before)


@pytest.mark.parametrize("interpretation",["negative","positive"])
@pytest.mark.parametrize("subtractive,tile_rows",[(True,None),(True,3),(False,None)])
def test_tone_observation_changes_only_output_for_both_polarities(interpretation,subtractive,tile_rows):
    from film_foundry.core.engine import develop_negative, scan_medium_output, scan_medium_direct
    config=DarkroomConfig(enable_grain=False,enable_halation=False,enable_mtf=False)
    config.enable_subtractive=subtractive
    config.processing.scan_tile_rows=tile_rows or 512
    config.processing.scan_tile_threshold_megapixels=.000001 if tile_rows else 9999
    config.scanner.color_curve_rgb=(((0,0),(.4,.65),(1,1)),)*3
    config.seed_strategy="fixed";config.random_seed=17
    image=np.linspace(.03,.9,12*18*3,dtype=np.float32).reshape(12,18,3)
    medium=develop_negative(image,config)
    original=medium.optical_density_rgb.copy();pools=medium.density_cmy.copy()
    baseline=scan_medium_output(medium,config,interpretation)
    config.scanner.tone_curve=((0,.12),(.4,.62),(1,1))
    changed=scan_medium_output(medium,config,interpretation)
    assert not np.array_equal(baseline.output_srgb,changed.output_srgb)
    np.testing.assert_allclose(changed.output_srgb,apply_scanner_tone_curve(baseline.output_srgb,config.scanner),atol=2e-7)
    diagnostic=scan_medium_direct(medium,config,interpretation)
    np.testing.assert_array_equal(changed.output_srgb,diagnostic.output_srgb)
    config.scanner.tone_curve=None
    plain=scan_medium_direct(medium,config,interpretation)
    np.testing.assert_array_equal(plain.scanner_raw,diagnostic.scanner_raw)
    np.testing.assert_array_equal(original,medium.optical_density_rgb)
    np.testing.assert_array_equal(pools,medium.density_cmy)
    assert changed.metadata["scanner_color_response"]["tone"] == "piecewise_output_luma_v1"


def test_new_layout_preserves_legacy_config_and_hidden_banks_in_all_modes():
    from film_foundry.tools.run_desktop_gui import DesktopPanel
    from film_foundry.tools.run_darkroom_gui import DarkroomPanel
    old=DarkroomPanel();new=DesktopPanel()
    try:
        assert asdict(new._config()) == asdict(old._config())
        config=old._config();config.scanner.tone_curve=((0,0),(.5,.65),(1,1))
        config.scanner.device_response_rgb=(((0,.05),(1,1)),)*3
        config.chemistry.developer_name="Custom chemistry"
        config.film.dir_material_response_matrix=((.1,.2,0),(0,.1,0),(0,0,.1))
        config.scanner.print_color_bias=(.93,1.03,.99)
        old._apply_loaded_config(config);new._apply_loaded_config(config)
        for mode in ("full","develop","scan","expose","develop_latent"):
            old.pipeline_mode.set(mode);new.pipeline_mode.set(mode)
            old._update_mode_visibility();new._update_mode_visibility()
            assert asdict(new._config()) == asdict(old._config())
        assert not hasattr(new,"main_scroller")
        assert len(new.pages)==6
    finally:
        new.root.destroy();old.root.destroy()


def test_expert_language_and_source_lock_preserve_controls_and_curve_drafts():
    from film_foundry.tools.run_desktop_gui import DesktopPanel
    from film_foundry.ui.i18n import language_label,set_language
    new=DesktopPanel()
    try:
        tone=new.curve_editors["tone_curve"];tone.add_point(.3,.6)
        rgb=new.curve_editors["color_curve_rgb"];rgb.channel.setCurrentIndex(1);rgb.add_point(.25,.4)
        config=new._config();new.exposure_ev.set(4.7);new.development_adjacency_radius.set(.017)
        for expert in (True,False,True,False):new.expert_mode.set(expert);new._update_expert_visibility()
        assert new.exposure_ev.get()==4.7
        assert new.development_adjacency_radius.get()==.017
        assert new._config().scanner == config.scanner
        tone.enabled.setChecked(False);draft=copy.deepcopy(tone.points)
        new.language.set(language_label("en_US"));new._change_language()
        assert new.curve_editors["tone_curve"].points == draft
        assert new._config().scanner.tone_curve is None
        new.curve_editors["tone_curve"].enabled.setChecked(True)
        assert new._config().scanner.tone_curve == config.scanner.tone_curve
        assert new._config().scanner.color_curve_rgb == config.scanner.color_curve_rgb
        for mode in ("develop_latent","scan"):
            new.pipeline_mode.set(mode);new._update_mode_visibility()
            assert not new.film_source_group.qt.isEnabled()
            assert not new.film_effect_group.qt.isEnabled()
            assert not new.film_condition_group.qt.isEnabled()
            assert not new.capture_exposure_group.qt.isEnabled()
            before=new._config();new._apply_editor_bank("film",DarkroomConfig())
            assert new._config().film == before.film
        assert not new.develop_source_group.qt.isEnabled()
    finally:
        new.root.destroy();set_language("zh_CN")


def test_new_buttons_preserve_material_stages_and_frozen_latent_source(tmp_path):
    from PIL import Image
    from film_foundry.tools.run_desktop_gui import DesktopPanel
    from film_foundry.ui.qt import QtWidgets
    from film_foundry.core.latent_io import load_exposed_latent
    from film_foundry.core.negative_io import load_developed_medium_for_scan
    source=tmp_path/"input.png";Image.fromarray(np.full((12,18,3),128,np.uint8)).save(source)
    panel=DesktopPanel()
    try:
        config=panel._config();config.enable_grain=False;config.enable_halation=False;config.enable_mtf=False
        config.output.save_scanner_raw=False;config.output.export_transparent_plate=False;config.save_sidecar=False
        config.seed_strategy="fixed";config.random_seed=31;panel._apply_loaded_config(config)
        panel.input_path.set(str(source));panel.run_as_preview.set(False);panel.scan_output_near_source.set(False)
        def run(mode):
            panel.pipeline_mode.set(mode);panel._update_mode_visibility();panel.output_path.set(str(tmp_path/mode))
            panel.process_button.qt.click();deadline=monotonic()+10
            while panel._processing and monotonic()<deadline:QtWidgets.QApplication.processEvents()
            assert not panel._processing,panel.status.get()
            assert not panel._preview_pending
            assert not panel._preview_timer.isActive()
            manifest=next((tmp_path/mode).rglob("film_foundry*.json"))
            assert json.loads(manifest.read_text(encoding="utf-8"))["batch"]["failed"] == 0
            return tmp_path/mode
        developed=next(run("develop").rglob("*.darkroom_negative.npz"))
        latent=next(run("expose").rglob("*.darkroom_latent.npz"))
        assert not load_exposed_latent(latent)[1]["consumed"]
        panel.negative_path.set(str(latent));run("develop_latent")
        assert not panel.fields["halation"][0].qt.isEnabled()
        # A completed latent task leaves the delivered source consumed.
        with np.load(latent,allow_pickle=False) as archive:
            metadata=json.loads(str(archive["latent_medium_metadata"].item()))
        assert metadata["consumed"] is True
        medium=load_developed_medium_for_scan(developed);before=medium.optical_density_rgb.copy()
        panel.negative_path.set(str(developed));scans=run("scan")
        assert list(scans.rglob("*.jpg"))
        np.testing.assert_array_equal(before,load_developed_medium_for_scan(developed).optical_density_rgb)
    finally:panel.root.destroy()


def test_curve_mouse_edit_precision_channels_and_removal():
    from film_foundry.ui.curve_editor import CurveEditor
    from film_foundry.ui.qt import QtCore,QtWidgets,_application
    from PySide6.QtTest import QTest
    _application();curve=CurveEditor("color_curve_rgb");curve.resize(400,340);curve.show()
    try:
        curve.channel.setCurrentIndex(2);curve.add_point(.5,.5);QtWidgets.QApplication.processEvents()
        point=curve.canvas.position((.5,.5)).toPoint()
        QTest.mousePress(curve.canvas,QtCore.Qt.MouseButton.LeftButton,pos=point)
        QTest.mouseMove(curve.canvas,curve.canvas.position((.5,.75)).toPoint())
        QTest.mouseRelease(curve.canvas,QtCore.Qt.MouseButton.LeftButton,pos=point)
        assert curve.read()[1][1][1] > .7
        assert curve.read()[0] == ((0.,0.),(1.,1.))
        curve.y.setValue(.8);assert curve.read()[1][1] == (.5,.8)
        curve.remove_point();assert curve.read()[1] == ((0.,0.),(1.,1.))
        curve.channel.setCurrentIndex(0);curve.add_point(.5,.6)
        assert curve.read()[0] == curve.read()[1] == curve.read()[2]
    finally:curve.close()


def test_wrapped_options_and_english_narrow_window_remain_usable():
    from film_foundry.tools.run_desktop_gui import DesktopPanel
    from film_foundry.ui.qt import QtCore,QtGui,QtWidgets,_application
    from film_foundry.ui.i18n import set_language
    from PySide6.QtTest import QTest
    from pathlib import Path
    app=_application();previous_font=app.font()
    # Windows' offscreen platform does not enumerate its normal desktop fonts.
    for filename in ("C:/Windows/Fonts/msyh.ttc","C:/Windows/Fonts/segoeui.ttf"):
        if Path(filename).exists():QtGui.QFontDatabase.addApplicationFont(filename)
    app.setFont(QtGui.QFont("Microsoft YaHei",9))
    set_language("en_US");panel=DesktopPanel();panel.root.qt.resize(1024,768);panel.root.qt.show()
    try:
        panel.expert_mode.set(True);panel._update_expert_visibility()
        for section in panel.sections.values():section.set_expanded(True)
        for page in range(6):
            panel.navigation.setCurrentRow(page);QtWidgets.QApplication.processEvents()
            assert panel.stack.widget(page).horizontalScrollBar().maximum() == 0
        panel.navigation.setCurrentRow(panel.page_index["scan"]);QtWidgets.QApplication.processEvents()
        description=panel.scan_pipeline_label.qt
        assert description.height() >= description.heightForWidth(description.width())
        row=panel.fields["fast_mode"]
        panel.navigation.setCurrentRow(panel.page_index["compute"]);QtWidgets.QApplication.processEvents()
        caption=row.qt.findChild(QtWidgets.QLabel);before=panel.fast_mode.get()
        QTest.mouseClick(caption,QtCore.Qt.MouseButton.LeftButton)
        assert panel.fast_mode.get() is not before
        panel.export_layer_pack.set(True);panel._update_export_option_state()
        assert not panel.transparent_export_check.qt.isEnabled()
        panel.export_layer_pack.set(False);panel._update_export_option_state()
        assert panel.transparent_export_check.qt.isEnabled()
    finally:panel.root.destroy();set_language("zh_CN");app.setFont(previous_font)


def test_material_filters_narrow_the_list_without_replacing_the_loaded_film():
    from PySide6 import QtWidgets
    from film_foundry.tools.run_desktop_gui import DesktopPanel
    from film_foundry.ui.workflow_text import film_preset_record
    panel=DesktopPanel()
    try:
        key=panel.film_preset.get()
        loaded=film_preset_record(key)
        label=next(name for name,item in panel._color_filter_labels.items() if item=="monochrome")
        panel.material_color_display.set(label)
        panel._on_material_list_filter()
        assert panel.film_preset.get()==key
        shown={film_preset_record(item)["color"] for item in panel._film_display_key_map.values()}
        assert "monochrome" in shown
        assert shown <= {"monochrome", loaded["color"]}
        assert panel.process_button.qt.objectName()=="primaryAction"
        panel.root.qt.resize(1280,800);panel.root.qt.show();QtWidgets.QApplication.processEvents()
        caption=next(label for label in panel.root.qt.findChildren(QtWidgets.QLabel) if label.text()=="材料原生类型")
        assert caption.width()>=120
        assert caption.heightForWidth(caption.width())<=caption.fontMetrics().height()+6
        assert panel.navigation.minimumWidth()>=172
        from film_foundry.ui.qt import QtWidgets
        splitter=panel.root.qt.findChild(QtWidgets.QSplitter)
        assert splitter is not None and not splitter.childrenCollapsible()
    finally:
        panel.root.destroy()


def test_historical_script_entry_points_open_new_desktop(monkeypatch):
    import runpy
    from pathlib import Path
    from film_foundry.tools import run_desktop_gui
    calls=[];monkeypatch.setattr(run_desktop_gui,"main",lambda:calls.append("new"))
    for filename in ("run_darkroom_gui.py","run_main_console.py"):
        runpy.run_path(str(Path(run_desktop_gui.__file__).with_name(filename)),run_name="__main__")
    assert calls == ["new","new"]


def test_output_page_leads_and_smits_turns_on_without_a_file():
    from film_foundry.tools.run_desktop_gui import DesktopPanel
    from film_foundry.ui.i18n import current_language,set_language
    previous=current_language();set_language("zh_CN")
    panel=DesktopPanel()
    try:
        assert panel.page_index["output"]==0 and panel.page_index["scan"]==3
        assert panel.navigation.item(0).text()=="任务与输出"
        assert panel._profiles["spectral_exposure_profile"] is None
        panel._enable_smits_exposure()
        profile=panel._profiles["spectral_exposure_profile"]
        assert "smits" in profile.model and "ff_smits" not in panel._profile_labels["spectral_exposure_profile"].get()
        panel._use_material_sensitivity()
        assert panel._profiles["spectral_exposure_profile"] is None
    finally:panel.root.destroy();set_language(previous)


def test_task_sources_and_destination_follow_mode_without_replacing_hidden_paths(tmp_path):
    from pathlib import Path
    from film_foundry.tools.run_desktop_gui import DesktopPanel
    from film_foundry.app.workflow import default_scan_output_root
    from film_foundry.ui.i18n import tr
    panel=DesktopPanel();panel.auto_preview.set(False)
    try:
        image=str(tmp_path/"input.png");medium=str(tmp_path/"negative"/"frame.darkroom_negative.npz")
        target=str(tmp_path/"chosen-output")
        panel.input_path.set(image);panel.negative_path.set(medium);panel.output_path.set(target)
        for mode in ("full","expose","scan","develop_latent","develop"):
            panel.pipeline_mode.set(mode);panel._update_mode_visibility()
            frozen=mode in ("scan","develop_latent")
            assert panel.image_source_row[0].qt.isHidden() is frozen
            assert panel.medium_source_row[0].qt.isHidden() is not frozen
            assert (panel.input_path.get(),panel.negative_path.get(),panel.output_path.get())==(image,medium,target)
            assert panel.source_relative_toggle.qt.isHidden() is (mode!="scan")
            assert panel.develop_identity_group.qt.isEnabled() is (mode in ("full","develop","develop_latent"))
        panel.pipeline_mode.set("scan");panel.scan_output_near_source.set(True);panel._update_mode_visibility()
        assert not panel.output_location_row[0].qt.isEnabled()
        assert str(default_scan_output_root(Path(medium))) in panel.destination_note.qt.text()
        panel.scan_output_near_source.set(False)
        assert panel.output_location_row[0].qt.isEnabled() and panel.output_path.get()==target
        panel._set_process_button_mode("cancel")
        assert panel.process_button.qt.text()==tr("button.cancel")
        panel._set_process_button_mode("process")
        assert panel.process_button.qt.text()!=tr("button.process")
    finally:panel.root.destroy()


def test_preview_zoom_is_read_only_and_same_size_updates_keep_inspection_position():
    from film_foundry.ui.image_view import ImageView
    from film_foundry.ui.qt import _application,QtWidgets,QtCore
    from PySide6.QtTest import QTest
    _application();view=ImageView();view.resize(400,300);view.show()
    original=np.linspace(0,1,300*450*3,dtype=np.float32).reshape(300,450,3);before=original.copy()
    try:
        view.show_array(original);QtWidgets.QApplication.processEvents()
        view.actual_size();assert view.zoom_percent==100
        view.zoom(2);assert view.zoom_percent==200
        view.centerOn(170,120)
        center=view.mapToScene(view.viewport().rect().center())
        view.show_array(original*.5)
        assert view.zoom_percent==200
        assert (view.mapToScene(view.viewport().rect().center())-center).manhattanLength()<1.0
        np.testing.assert_array_equal(original,before)
        assert view.source.toImage().pixelColor(200,100).red()==int(original[100,200,0]*.5*255)
        QTest.mouseDClick(view.viewport(),QtCore.Qt.MouseButton.LeftButton,pos=view.viewport().rect().center())
        assert view._fit_mode and view.transform().m11()<1.0
    finally:view.close()


def test_default_launcher_and_real_workbench_preview_process(tmp_path,monkeypatch):
    from PIL import Image
    from film_foundry.tools.run_desktop_gui import DesktopPanel
    from film_foundry.ui.qt import QtWidgets
    import run_film_foundry_launcher as launcher
    assert launcher.TOOLS[0][3] == "film_foundry.tools.run_desktop_gui"
    calls=[];monkeypatch.setattr(DesktopPanel,"run",lambda self:calls.append(self))
    launcher.run_tool("main");calls.pop().root.destroy()
    source=tmp_path/"frame.png";Image.fromarray(np.linspace(0,255,32*48*3,dtype=np.uint8).reshape(32,48,3)).save(source)
    new=DesktopPanel()
    try:
        cfg=new._config();cfg.output.save_scanner_raw=False;cfg.output.export_transparent_plate=False;cfg.save_sidecar=False
        cfg.seed_strategy="fixed";cfg.random_seed=17;new._apply_loaded_config(cfg)
        new.input_path.set(str(source));new.output_path.set(str(tmp_path/"results"));new.run_as_preview.set(False)
        new.curve_editors["tone_curve"].add_point(.4,.6)
        new.preview_button.qt.click()
        def wait(predicate):
            deadline=monotonic()+10
            while monotonic()<deadline and not predicate():QtWidgets.QApplication.processEvents()
            assert predicate(),new.status.get()
        wait(lambda:not new._preview_running)
        assert new.result_view.source is not None
        new.process_button.qt.click();wait(lambda:not new._processing)
        manifests=list((tmp_path/"results").rglob("*run*.json"))
        assert manifests,new.status.get()
        manifest=json.loads(manifests[0].read_text(encoding="utf-8"))
        assert manifest["batch"]["status"] == "completed"
        assert list((tmp_path/"results").rglob("*.jpg"))
        assert new._latest_output==manifests[0].parent
        assert new.open_output_button.qt.isEnabled()
        assert new.mode_choice.qt.isEnabled() and new.resume_button.qt.isEnabled()
    finally:new.root.destroy()

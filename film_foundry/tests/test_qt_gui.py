import os
from threading import Thread
from time import monotonic


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _process_until(predicate, timeout=1.0):
    from PySide6 import QtWidgets

    app = QtWidgets.QApplication.instance()
    deadline = monotonic() + timeout
    while monotonic() < deadline and not predicate():
        app.processEvents()
    return predicate()


def test_qt_launcher_and_all_primary_tools_construct_without_event_loop():
    from PySide6 import QtWidgets
    import run_film_foundry_launcher as launcher
    from film_foundry.tools.run_darkroom_gui import DarkroomPanel
    from film_foundry.tools.run_develop_process_editor import DevelopProcessEditor
    from film_foundry.tools.run_film_material_editor import FilmMaterialEditor
    from film_foundry.tools.run_scanner_render_editor import ScannerRenderEditor

    windows = [
        launcher.FoundryLauncher(),
        DarkroomPanel(),
        FilmMaterialEditor(),
        DevelopProcessEditor(),
        ScannerRenderEditor(),
    ]
    try:
        assert all(isinstance(window.root.qt, QtWidgets.QWidget) for window in windows)
        assert windows[1]._config().__class__.__name__ == "DarkroomConfig"
        assert windows[2]._config_from_controls().__class__.__name__ == "DarkroomConfig"
        assert windows[3]._config_from_controls().__class__.__name__ == "DarkroomConfig"
        assert windows[4]._config_from_controls().__class__.__name__ == "DarkroomConfig"
    finally:
        for window in reversed(windows):
            window.root.destroy()


def test_qt_launcher_button_does_not_forward_qt_checked_state(monkeypatch):
    from PySide6 import QtCore, QtWidgets
    from PySide6.QtTest import QTest
    import run_film_foundry_launcher as launcher

    calls = []
    monkeypatch.setattr(
        launcher.subprocess,
        "Popen",
        lambda command, **kwargs: calls.append((command, kwargs)),
    )
    window = launcher.FoundryLauncher()
    try:
        open_button = window.root.qt.findChildren(QtWidgets.QPushButton)[0]
        QTest.mouseClick(open_button, QtCore.Qt.MouseButton.LeftButton)
        assert calls
        assert calls[0][0][-1] == "film_foundry.tools.run_darkroom_gui"
    finally:
        window.root.destroy()


def test_qt_main_dynamic_visibility_language_rebuild_and_exact_value_round_trip():
    from PySide6 import QtCore, QtWidgets
    from PySide6.QtTest import QTest
    from film_foundry.tools.run_darkroom_gui import DarkroomPanel

    window = DarkroomPanel()
    try:
        window.pipeline_mode.set("scan")
        window._update_mode_visibility()
        assert window.develop_frame.qt.isHidden()

        window.pipeline_mode.set("full")
        window._update_mode_visibility()
        assert not window.develop_frame.qt.isHidden()

        window.exposure_ev.set(1.2345)
        assert window._config().look.exposure_ev == 1.2345

        window.exposure_ev.set(-0.35)
        value_entry = next(
            entry
            for entry in window.develop_frame.qt.findChildren(QtWidgets.QLineEdit)
            if entry.text() == "-0.35"
        )
        value_entry.setFocus()
        value_entry.selectAll()
        QTest.keyClicks(value_entry, "1.2345")
        QTest.keyClick(value_entry, QtCore.Qt.Key.Key_Return)
        assert window.exposure_ev.get() == 1.2345

        slider_count = len(window.slider_specs)
        window._change_language()
        assert len(window.slider_specs) == slider_count
        assert len(window.exposure_ev._views) == 1
        QtWidgets.QApplication.processEvents()
        QtCore.QCoreApplication.sendPostedEvents(
            None, QtCore.QEvent.Type.DeferredDelete
        )
        window.exposure_ev.set(0.625)
        assert window._config().look.exposure_ev == 0.625
    finally:
        window.root.destroy()


def test_qt_scanner_can_rebuild_between_negative_and_positive_interpretation():
    from film_foundry.tools.editor_ui import local_text
    from film_foundry.tools.run_scanner_render_editor import ScannerRenderEditor

    window = ScannerRenderEditor()
    try:
        window.interpretation_display.set(local_text("正片解释", "Positive Interpretation"))
        window._on_interpretation_selected()
        assert window.interpretation == "positive"
        assert window.invert_transmission.get() is False
        assert window.remove_base_mask.get() is False
    finally:
        window.root.destroy()


def test_qt_after_marshals_worker_callbacks_to_the_gui_thread():
    from film_foundry.ui.qt import ui

    root = ui.Application()
    received = []
    try:
        worker = Thread(target=lambda: root.after(0, lambda: received.append("done")))
        worker.start()
        worker.join()
        assert _process_until(lambda: received == ["done"])
    finally:
        root.destroy()


def test_qt_preview_window_can_be_recreated_after_native_close():
    import numpy as np
    from PySide6 import QtCore, QtWidgets
    from film_foundry.tools.run_darkroom_gui import DarkroomPanel

    window = DarkroomPanel()
    try:
        image = np.full((12, 8, 3), 0.5, dtype=np.float32)
        window._show_preview("first.png", image, image, "Input", "Result")
        first_preview = window.preview_window
        assert first_preview is not None
        assert first_preview.winfo_exists()

        first_preview.qt.close()
        assert not first_preview.winfo_exists()

        QtWidgets.QApplication.processEvents()
        QtCore.QCoreApplication.sendPostedEvents(
            None, QtCore.QEvent.Type.DeferredDelete
        )
        window._show_preview("second.png", image, image, "Input", "Result")

        assert window.preview_window is not None
        assert window.preview_window is not first_preview
        assert window.preview_window.winfo_exists()
        assert window.preview_left_label is not None
        assert window.preview_right_label is not None
        assert window.preview_left_label.qt.pixmap() is not None
        assert window.preview_right_label.qt.pixmap() is not None
    finally:
        window.root.destroy()


def test_qt_preview_request_freezes_ui_state_before_worker_start(monkeypatch):
    from pathlib import Path
    from film_foundry.tools import run_darkroom_gui as gui

    started = []

    class DeferredThread:
        def __init__(self, *, target, args, daemon):
            self.target = target
            self.args = args
            self.daemon = daemon

        def start(self):
            started.append(self)

    monkeypatch.setattr(gui, "Thread", DeferredThread)
    window = gui.DarkroomPanel()
    try:
        monkeypatch.setattr(window, "_first_input_image", lambda: Path("input.png"))
        window.pipeline_mode.set("full")
        window.exposure_ev.set(0.75)
        window._start_preview()

        assert len(started) == 1
        request = started[0].args[0]
        assert request.mode == "full"
        assert request.source_path == Path("input.png")
        assert request.config.look.exposure_ev == 0.75
        assert window._preview_running
        assert "disabled" in window.preview_button.state()

        window.exposure_ev.set(-1.0)
        assert request.config.look.exposure_ev == 0.75
        window._start_preview()
        assert len(started) == 1

        window._finish_preview(request.token)
        assert not window._preview_running
        assert "disabled" not in window.preview_button.state()
    finally:
        window.root.destroy()


def test_qt_native_root_close_rejects_late_worker_callbacks():
    from PySide6 import QtWidgets
    from film_foundry.ui.qt import ui

    root = ui.Application()
    received = []
    root.qt.close()
    assert not root.winfo_exists()
    assert root.after(0, lambda: received.append("late")) == ""
    QtWidgets.QApplication.processEvents()
    assert received == []


def test_shared_qt_numeric_control_keeps_exact_value_and_one_callback():
    from film_foundry.ui.components import add_numeric_control
    from film_foundry.ui.qt import ui

    root = ui.Application()
    value = ui.FloatValue(value=0.0)
    changes = []
    try:
        frame = ui.Frame(root)
        frame.pack(fill="x")
        control = add_numeric_control(
            frame,
            label="Value",
            value=value,
            minimum=-2.0,
            maximum=2.0,
            row=0,
            increment=0.001,
            on_change=lambda: changes.append(value.get()),
        )
        control.number_input.qt.setValue(0.375)
        assert value.get() == 0.375
        assert changes == [0.375]
    finally:
        root.destroy()

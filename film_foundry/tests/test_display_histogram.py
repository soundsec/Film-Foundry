import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import numpy as np


def test_endpoint_peaks_define_common_channel_scale():
    from film_foundry.ui.histogram import histogram_plot
    bins=np.zeros((4,256),np.int64);bins[0,0]=100
    bins[1,0]=100;bins[2,120]=10;bins[3,255]=50
    heights,peak,group=histogram_plot(bins,[1,2,3],600)
    assert group==1 and peak==100
    np.testing.assert_allclose(heights.max(axis=1),[1,.1,.5])


def test_histogram_matches_display_pixels_and_does_not_modify_source():
    from film_foundry.ui.histogram import display_bins
    pixels = np.array([[[0, 0, 0], [255, 255, 255], [255, 0, 0],
                        [0, 255, 0], [0, 0, 255], [128, 128, 128]]], dtype=np.uint8)
    before = pixels.copy()
    bins = display_bins(pixels)
    np.testing.assert_array_equal(bins.sum(axis=1), [6]*4)
    for level in (0, 255, 54, 182, 18, 128):
        assert bins[0, level] == 1
    np.testing.assert_array_equal(bins[1:, 0], [3]*3)
    np.testing.assert_array_equal(bins[1:, 255], [2]*3)
    np.testing.assert_array_equal(pixels, before)


def test_rgb_overlap_and_modes_render_without_altering_counts():
    from film_foundry.ui.histogram import Histogram
    from film_foundry.ui.qt import _application, QtCore
    from PySide6.QtTest import QTest
    _application()
    widget = Histogram();widget.resize(300, 130)
    widget.set_pixels(np.full((4, 4, 3), 120, dtype=np.uint8))
    before = widget.bins.copy()
    widget.show()
    try:
        image = widget.grab().toImage()
        color = image.pixelColor(round(42+(120+2)/256*(widget.width()-54)), 60)
        assert color.red() == color.green() == color.blue() and color.red() > 100
        QTest.mouseClick(widget, QtCore.Qt.MouseButton.LeftButton)
        assert widget.brightness
        QTest.mouseClick(widget, QtCore.Qt.MouseButton.RightButton)
        assert widget.logarithmic
        widget.grab()
        np.testing.assert_array_equal(widget.bins, before)
    finally:
        widget.close()


def test_workbench_histogram_follows_current_tab(tmp_path, monkeypatch):
    from film_foundry.ui.preferences import _read
    monkeypatch.setenv("FILM_FOUNDRY_CAMERA_SETTINGS", str(tmp_path/"camera.json"))
    monkeypatch.setenv("FILM_FOUNDRY_UI_SETTINGS", str(tmp_path/"ui.json"))
    _read.cache_clear()
    from film_foundry.tools.run_desktop_gui import DesktopPanel
    panel = DesktopPanel()
    try:
        panel.result_view.show_array(np.ones((2, 3, 3), dtype=np.float32))
        panel.input_view.show_array(np.zeros((2, 3, 3), dtype=np.float32))
        assert panel.preview_histogram.bins[1, 255] == 6
        panel.preview_tabs.setCurrentWidget(panel.input_view)
        assert panel.preview_histogram.bins[1, 0] == 6
        panel.preview_tabs.setTabVisible(2, True)
        panel.preview_tabs.setCurrentWidget(panel.inspector_view)
        assert panel.preview_histogram.bins is None
    finally:
        panel.root.destroy()

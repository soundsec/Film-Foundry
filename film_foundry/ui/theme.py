"""Shared native desktop palettes; purely presentation, never image processing."""
from __future__ import annotations

from pathlib import Path
from PySide6 import QtGui, QtWidgets
from film_foundry.ui.color_schemes import Theme, THEMES, THEME_KEYS, custom_theme, text_on
from film_foundry.ui.preferences import load_preferences


_active_theme: str | None = None
_active_colors: Theme | None = None


def current_theme() -> str:
    return _active_theme or load_preferences()["theme"]


def saved_custom_colors():
    base = THEMES["slate"]
    return load_preferences().get("custom_colors", {"background": base.background, "accent": base.accent})


def _resolve(name):
    return custom_theme(**saved_custom_colors()) if name == "custom" else THEMES[name]


def colors() -> Theme:
    return _active_colors or _resolve(current_theme())


def stylesheet(t: Theme) -> str:
    assets = (Path(__file__).resolve().parents[1] / "assets").as_posix()
    accent_hover = QtGui.QColor(t.accent).lighter(110).name()
    return f"""
        QWidget {{background:{t.background}; color:{t.text};}}
        QWidget:disabled {{color:{t.disabled};}}
        QLabel {{background:transparent;}}
        QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox, QTextEdit, QPlainTextEdit {{
            background:{t.input}; color:{text_on(t.input)}; border:1px solid {t.border}; padding:3px 5px;
            selection-background-color:{t.accent}; selection-color:{text_on(t.accent)};
        }}
        QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus,
        QTextEdit:focus, QPlainTextEdit:focus {{border-color:{t.accent};}}
        QComboBox::drop-down, QSpinBox::up-button, QSpinBox::down-button,
        QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{border:0; width:15px;}}
        QComboBox::drop-down:hover, QSpinBox::up-button:hover, QSpinBox::down-button:hover,
        QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{background:{t.hover}; color:{text_on(t.hover)};}}
        QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
            image:url("{assets}/chevron_up.svg"); width:9px; height:6px;
        }}
        QComboBox::down-arrow, QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
            image:url("{assets}/chevron_down.svg"); width:9px; height:6px;
        }}
        QCheckBox::indicator:unchecked {{border:1px solid {t.muted}; background:{t.input}; color:{text_on(t.input)};}}
        QCheckBox::indicator:unchecked:disabled {{border-color:{t.border};}}
        QPushButton {{background:{t.button}; color:{text_on(t.button)}; border:1px solid {t.border}; padding:5px 9px;}}
        QToolButton, QWidget#commandBar QPushButton, QPushButton[toolbarAction="true"] {{
            background:transparent; color:{t.text}; border:1px solid transparent; padding:4px 6px;
        }}
        QPushButton:hover, QToolButton:hover, QWidget#commandBar QPushButton:hover,
        QPushButton[toolbarAction="true"]:hover {{background:{t.hover}; color:{text_on(t.hover)};}}
        QPushButton:pressed, QToolButton:pressed, QPushButton:checked, QToolButton:checked,
        QPushButton[toolbarAction="true"]:checked {{background:{t.selected}; color:{text_on(t.selected)}; border-color:{t.accent};}}
        QPushButton:focus, QToolButton:focus, QWidget#commandBar QPushButton:focus,
        QPushButton[toolbarAction="true"]:focus {{border-color:{t.accent};}}
        QPushButton:disabled, QToolButton:disabled {{background:{t.surface}; color:{t.disabled}; border:1px solid transparent;}}
        QWidget#commandBar QPushButton:disabled {{border-color:transparent; color:{t.disabled};}}
        QToolButton#stageEndpoint {{background:{t.surface}; color:{text_on(t.surface)}; padding:1px 3px;}}
        QToolButton#stageEndpoint::menu-button {{border:0; width:12px;}}
        QToolButton#stageEndpoint::menu-arrow {{image:url("{assets}/chevron_down.svg"); width:8px; height:5px;}}
        QToolButton#stageEndpoint:checked {{background:{t.selected}; color:{text_on(t.selected)}; border-color:{t.border};}}
        QToolButton#stageEndpoint:focus {{border-color:{t.accent};}}
        QToolButton#stageEndpoint:disabled {{color:{t.disabled};}}
        QToolButton::menu-indicator {{subcontrol-position:right bottom;}}
        QWidget#commandBar {{background:{t.surface}; color:{text_on(t.surface)}; border-bottom:1px solid {t.border};}}
        QWidget#commandBar QLabel, QWidget#commandBar QAbstractButton {{color:{text_on(t.surface)};}}
        QWidget#commandBar QToolButton:hover {{background:{t.hover}; color:{text_on(t.hover)};}}
        QGroupBox {{font-weight:600; border:0; border-top:1px solid {t.border};
                    margin-top:1em; padding-top:0.45em;}}
        QGroupBox[title=""] {{border:0; margin:0; padding:0;}}
        QGroupBox::title {{subcontrol-origin:margin; subcontrol-position:top left;
                          left:0; padding:0 6px 0 0; background:{t.background};}}
        QFrame#parameterSection {{border:0; border-top:1px solid {t.border};}}
        QToolButton#sectionToggle {{background:transparent; color:{t.text}; border:1px solid transparent;
                                   text-align:left; padding:5px 3px; font-weight:600;}}
        QToolButton#sectionToggle:hover {{background:{t.button}; color:{text_on(t.button)};}}
        QToolButton#sectionToggle:focus {{border-color:{t.accent};}}
        QListWidget, QTreeWidget {{background:{t.surface}; color:{text_on(t.surface)}; border:0;}}
        QAbstractItemView {{selection-background-color:{t.selected}; selection-color:{text_on(t.selected)};}}
        QListWidget::item {{padding:6px 8px;}}
        QListWidget#checkpointSteps::item {{padding:2px 8px;}}
        QTreeWidget::item {{padding:4px;}}
        QListWidget::item:hover, QTreeWidget::item:hover {{background:{t.hover}; color:{text_on(t.hover)};}}
        QListWidget::item:selected, QTreeWidget::item:selected {{background:{t.selected}; color:{text_on(t.selected)};}}
        QListWidget#workbenchNavigation::item {{border-left:2px solid transparent; padding:8px 10px;}}
        QListWidget#workbenchNavigation::item:selected {{border-left-color:{t.accent}; font-weight:600;}}
        QMenu {{background:{t.surface}; color:{text_on(t.surface)}; border:1px solid {t.border}; padding:2px;}}
        QMenu::item {{padding:6px 22px;}}
        QMenu::item:selected {{background:{t.selected}; color:{text_on(t.selected)};}}
        QMenu::item:disabled {{color:{t.disabled};}}
        QMenu::separator {{height:1px; background:{t.border}; margin:3px 5px;}}
        QLabel#materialPageTitle {{font-size:18px; font-weight:600; padding:0 4px;}}
        QLabel#pageTitle {{padding:2px 0;}}
        QLabel#sectionNote, QLabel#launcherNote {{color:{t.muted};}}
        QLabel#sectionTitle {{font-weight:600;}}
        QWidget#launcherCard {{border:0; border-top:1px solid {t.border};}}
        QPushButton#primaryAction {{background:{t.accent}; color:{text_on(t.accent)}; border:2px solid {t.accent};
                                 font-weight:600; padding:5px 14px;}}
        QPushButton#primaryAction:hover {{background:{accent_hover}; color:{text_on(accent_hover)};}}
        QPushButton#primaryAction:focus, QPushButton#primaryAction:pressed {{border-color:{t.text};}}
        QPushButton#primaryAction:disabled {{background:{t.surface}; color:{t.disabled}; border-color:{t.border};}}
        QPushButton#editorShortcut, QPushButton#aboutLink {{background:transparent;
                                border:1px solid transparent; text-align:left; padding:3px 1px;}}
        QPushButton#editorShortcut {{color:{t.text}; text-decoration:underline;}}
        QPushButton#aboutLink {{color:{t.text};}}
        QPushButton#editorShortcut:hover, QPushButton#aboutLink:hover {{background:{t.hover}; color:{text_on(t.hover)};}}
        QPushButton#editorShortcut:focus, QPushButton#aboutLink:focus {{border-color:{t.accent};}}
        QPushButton#editorShortcut:disabled, QPushButton#aboutLink:disabled {{color:{t.disabled};}}
        QSplitter::handle:horizontal {{width:9px; background:qlineargradient(x1:0,y1:0,x2:1,y2:0,
            stop:0.43 {t.background},stop:0.44 {t.border},stop:0.56 {t.border},stop:0.57 {t.background});}}
        QSplitter::handle:vertical {{height:9px; background:qlineargradient(x1:0,y1:0,x2:0,y2:1,
            stop:0.43 {t.background},stop:0.44 {t.border},stop:0.56 {t.border},stop:0.57 {t.background});}}
        QSplitter::handle:hover {{background:{t.hover};}}
        QComboBox QAbstractItemView {{background:{t.input}; color:{text_on(t.input)};}}
        QTabWidget::pane {{border:0; border-top:1px solid {t.border};}}
        QTabWidget#workspaceDocuments::pane {{border:0;}}
        QTabBar::tab, QToolButton#editorTab {{background:transparent; color:{t.text}; border:0;
                                            border-bottom:2px solid transparent; padding:5px 10px;}}
        QTabBar::tab:hover, QToolButton#editorTab:hover {{background:{t.hover}; color:{text_on(t.hover)};}}
        QTabBar::tab:selected, QToolButton#editorTab:checked {{background:{t.background}; border-bottom-color:{t.accent}; color:{t.text};}}
        QTabBar::tab:disabled, QToolButton#editorTab:disabled {{color:{t.disabled};}}
        QToolButton#editorTab:focus {{background:{t.selected}; color:{text_on(t.selected)};}}
        QToolBox::tab {{background:{t.surface}; color:{text_on(t.surface)}; border:0; border-bottom:1px solid {t.border};
                        padding:0 8px; min-height:26px; text-align:left;}}
        QToolBox::tab:selected {{color:{text_on(t.surface)}; border-bottom-color:{t.accent}; font-weight:600;}}
        QScrollArea {{border:0;}}
        QSlider::groove:horizontal {{height:2px; background:{t.border};}}
        QSlider::sub-page:horizontal {{background:{t.accent};}}
        QSlider::handle:horizontal {{background:{t.text}; border:1px solid {t.surface}; width:6px; margin:-5px 0;}}
        QSlider::handle:horizontal:hover, QSlider::handle:horizontal:focus {{background:{t.accent};}}
        QSlider::handle:horizontal:disabled, QSlider::sub-page:horizontal:disabled {{background:{t.disabled};}}
        QLabel#editorHeading {{font-weight:600; border-bottom:1px solid {t.border}; padding:5px 0;}}
        QLabel#sourcePreviewNote {{background:{t.surface}; color:{text_on(t.surface)}; border-left:2px solid {t.accent}; padding:3px 6px;}}
        QFrame#rollStrip {{background:{t.surface}; color:{text_on(t.surface)}; border:0; border-top:1px solid {t.border};}}
        QFrame#rollStrip QToolButton {{background:{t.input}; color:{text_on(t.input)}; border:2px solid transparent;}}
        QFrame#rollStrip QToolButton:hover {{background:{t.hover}; color:{text_on(t.hover)};}}
        QFrame#rollStrip QToolButton:checked, QFrame#rollStrip QToolButton:focus {{border-color:{t.accent};}}
        QTableWidget#bathSequence {{background:{t.surface}; color:{text_on(t.surface)}; border:0; gridline-color:{t.border};}}
        QTableWidget#bathSequence::item {{padding:5px;}}
        QHeaderView::section {{background:{t.surface}; color:{text_on(t.surface)}; padding:5px; border:0; border-bottom:1px solid {t.border};}}
        QProgressBar {{background:{t.input}; color:{text_on(t.input)}; border:1px solid {t.border}; text-align:center;}}
        QProgressBar::chunk {{background:{t.accent};}}
        QProgressBar#materialMetric::chunk {{background:{t.selected}; color:{text_on(t.selected)};}}
        QScrollBar {{background:{t.surface};}}
        QScrollBar:vertical {{width:10px; margin:0 2px;}}
        QScrollBar:horizontal {{height:10px; margin:2px 0;}}
        QScrollBar::handle {{background:{t.border};}}
        QScrollBar::handle:hover {{background:{t.muted};}}
        QScrollBar::handle:vertical {{min-height:24px;}}
        QScrollBar::handle:horizontal {{min-width:24px;}}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{height:0;}}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{width:0;}}
        QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled,
        QToolButton#sectionToggle:disabled, QWidget#commandBar QToolButton:disabled,
        QPushButton[toolbarAction="true"]:disabled {{color:{t.disabled};}}
        QToolTip {{background:{t.input}; color:{text_on(t.input)}; border:1px solid {t.border}; padding:4px;}}
    """


def apply_theme(name: str, app: QtWidgets.QApplication | None = None) -> None:
    global _active_theme, _active_colors
    if name not in THEME_KEYS:
        raise ValueError("Unsupported UI theme")
    if app is None:
        from film_foundry.ui.qt import _application
        app = _application()
    if not app.property("film_foundry_theme_initialized"):
        app.setStyle("Fusion")
    _active_theme = name
    _active_colors = t = _resolve(name)
    palette = QtGui.QPalette()
    roles = {"Window": t.background, "WindowText": t.text, "Base": t.input,
             "AlternateBase": t.surface, "Text": text_on(t.input), "Button": t.button,
             "ButtonText": text_on(t.button), "Highlight": t.selected, "HighlightedText": text_on(t.selected),
             "ToolTipBase": t.input, "ToolTipText": text_on(t.input), "PlaceholderText": text_on(t.input)}
    for name, color in roles.items():
        palette.setColor(getattr(QtGui.QPalette.ColorRole, name), QtGui.QColor(color))
    for role in ("Text", "ButtonText", "WindowText", "PlaceholderText"):
        palette.setColor(QtGui.QPalette.ColorGroup.Disabled, getattr(QtGui.QPalette.ColorRole, role), QtGui.QColor(t.disabled))
    app.setPalette(palette)
    app.setStyleSheet(stylesheet(t))
    app.setProperty("film_foundry_theme_initialized", True)
    # Native custom plots paint from these tokens; no data/model refresh.
    for window in app.topLevelWidgets():
        window.update()
        for child in window.findChildren(QtWidgets.QWidget):
            child.update()


def initialize_theme(app: QtWidgets.QApplication) -> None:
    if not app.property("film_foundry_theme_initialized"):
        apply_theme(current_theme(), app)

"""Shared native desktop palettes; purely presentation, never image processing."""
from __future__ import annotations

from dataclasses import dataclass
from PySide6 import QtGui, QtWidgets
from film_foundry.ui.preferences import load_preferences


@dataclass(frozen=True)
class Theme:
    background: str
    surface: str
    input: str
    text: str
    muted: str
    disabled: str
    border: str
    button: str
    hover: str
    accent: str
    selected: str
    canvas: str
    grid: str
    diagonal: str
    point: str
    red: str
    green: str
    blue: str
    tone: str


THEMES = {
    "dark": Theme("#222426", "#1b1d1f", "#17191b", "#eeeae3", "#b2ada5", "#85827d",
                  "#484a4d", "#35383b", "#454a4f", "#d0ae78", "#494234", "#151b23",
                  "#354050", "#718297", "#f0ede5", "#ef7878", "#73c89b", "#81aef0", "#e9c885"),
    "ivory": Theme("#eee8dc", "#e4ddcf", "#f5f0e6", "#302e29", "#655f54", "#8a8275",
                   "#b8ae9f", "#e1d8c8", "#d3c7b4", "#765735", "#ddd2bd", "#eee7db",
                   "#d0c6b7", "#928573", "#302e29", "#a83d40", "#35694a", "#355ea0", "#775a20"),
}

_active_theme: str | None = None


def current_theme() -> str:
    return _active_theme or load_preferences()["theme"]


def colors() -> Theme:
    return THEMES[current_theme()]


def stylesheet(t: Theme) -> str:
    accent_hover = QtGui.QColor(t.accent).lighter(112).name()
    return f"""
        QWidget {{background:{t.background}; color:{t.text};}}
        QWidget:disabled {{color:{t.disabled};}}
        QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox, QTextEdit {{
            background:{t.input}; border:1px solid {t.border}; border-radius:4px; padding:4px;
            selection-background-color:{t.accent}; selection-color:{t.input};
        }}
        QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus {{border:1px solid {t.accent};}}
        QPushButton {{background:{t.button}; border:1px solid {t.border}; border-radius:6px; padding:7px 10px;}}
        QPushButton:hover {{background:{t.hover};}}
        QPushButton:pressed, QPushButton:focus {{border-color:{t.accent};}}
        QPushButton:disabled {{background:{t.surface}; color:{t.disabled};}}
        QToolButton {{background:{t.button}; border:1px solid {t.border}; border-radius:5px; padding:6px 9px;}}
        QToolButton:hover {{background:{t.hover};}}
        QToolButton:checked, QPushButton:checked {{background:{t.selected}; border-color:{t.accent};}}
        QToolButton:disabled {{background:{t.surface}; color:{t.disabled};}}
        QToolButton::menu-indicator {{subcontrol-position:right bottom;}}
        QTreeWidget {{background:{t.surface}; border:1px solid {t.border}; border-radius:5px;}}
        QTreeWidget::item {{padding:5px;}}
        QTreeWidget::item:selected {{background:{t.selected};}}
        QHeaderView::section {{background:{t.surface}; color:{t.muted}; border:0; padding:6px;}}
        QMenu {{background:{t.surface}; border:1px solid {t.border}; padding:4px;}}
        QMenu::item {{padding:7px 22px;}}
        QMenu::item:selected {{background:{t.selected};}}
        QGroupBox {{font-weight:600; border:1px solid {t.border}; border-radius:6px; margin-top:1.5em; padding-top:0.6em;}}
        QGroupBox::title {{subcontrol-origin:margin; left:12px; padding:0 4px;}}
        QFrame#parameterSection {{border:1px solid {t.border}; border-radius:6px;}}
        QToolButton#sectionToggle {{background:{t.surface}; color:{t.text}; border:0; border-radius:6px; text-align:left; padding:10px 8px; font-weight:600;}}
        QToolButton#sectionToggle:hover {{background:{t.button};}}
        QToolButton#sectionToggle:focus {{border:1px solid {t.accent};}}
        QToolButton#sectionToggle:disabled {{color:{t.disabled};}}
        QListWidget {{border:0; background:{t.surface}; border-radius:6px;}}
        QListWidget::item {{padding:10px 12px; color:{t.text};}}
        QListWidget::item:selected {{background:{t.selected}; color:{t.text};}}
        QListWidget#workbenchNavigation::item {{margin:3px 5px; border-radius:6px; padding:11px 10px;}}
        QListWidget#workbenchNavigation::item:hover {{background:{t.button};}}
        QListWidget#workbenchNavigation::item:selected {{background:{t.selected}; color:{t.text}; font-weight:600;}}
        QLabel#pageTitle {{color:{t.text}; background:transparent; padding:2px 0;}}
        QLabel#sectionNote {{color:{t.muted}; background:transparent;}}
        QLabel#launcherNote {{color:{t.muted}; background:transparent;}}
        QWidget#launcherCard {{background:{t.surface}; border:1px solid {t.border}; border-radius:8px;}}
        QWidget#launcherCard QLabel {{background:transparent;}}
        QPushButton#primaryAction {{background:{t.accent}; color:{t.input}; font-weight:600; padding:8px 16px;}}
        QPushButton#primaryAction:hover {{background:{accent_hover}; color:{t.input};}}
        QPushButton#primaryAction:disabled {{background:{t.surface}; color:{t.disabled};}}
        QPushButton#editorShortcut {{background:transparent; color:{t.accent}; border:0; text-align:left; padding:5px 0;}}
        QPushButton#editorShortcut:hover {{background:{t.surface};}}
        QPushButton#editorShortcut:disabled {{color:{t.disabled};}}
        QPushButton#aboutLink {{background:transparent; color:{t.muted}; border:1px solid transparent; padding:2px 4px; font-weight:400;}}
        QPushButton#aboutLink:hover {{color:{t.text};}}
        QPushButton#aboutLink:focus {{border-color:{t.border};}}
        QSplitter::handle {{background:{t.background};}}
        QSplitter::handle:horizontal {{width:8px;}}
        QSplitter::handle:hover {{background:{t.border};}}
        QComboBox QAbstractItemView {{background:{t.input}; color:{t.text}; selection-background-color:{t.selected};}}
        QTabWidget::pane {{border:1px solid {t.border};}}
        QTabBar::tab {{background:{t.surface}; padding:6px 10px; border:1px solid {t.border};}}
        QTabBar::tab:selected {{background:{t.button};}}
        QScrollArea {{border:0;}}
        QSlider::groove:horizontal {{height:4px; border-radius:2px; background:{t.border};}}
        QSlider::sub-page:horizontal {{background:{t.accent}; border-radius:2px;}}
        QSlider::handle:horizontal {{background:{t.text}; border:2px solid {t.surface}; width:12px; margin:-6px 0; border-radius:7px;}}
        QSlider::handle:horizontal:hover {{background:{t.accent};}}
        QSlider::sub-page:horizontal:disabled {{background:{t.disabled};}}
        QToolButton#editorTab {{border:1px solid {t.border}; background:{t.surface}; padding:7px 14px; border-radius:5px;}}
        QToolButton#editorTab:checked {{background:{t.selected}; border-color:{t.accent};}}
        QLabel#editorHeading {{background:{t.surface}; padding:8px 10px; border-radius:4px;}}
        QTableWidget#bathSequence {{background:{t.surface}; border:1px solid {t.border}; border-radius:6px; gridline-color:{t.border};}}
        QTableWidget#bathSequence::item {{padding:7px;}}
        QTableWidget#bathSequence::item:selected {{background:{t.selected};}}
        QHeaderView::section {{background:{t.surface}; color:{t.muted}; padding:7px; border:0; border-bottom:1px solid {t.border};}}
        QProgressBar {{background:{t.surface}; border:1px solid {t.border}; border-radius:6px;}}
        QProgressBar::chunk {{background:{t.accent}; border-radius:5px;}}
        QScrollBar {{background:{t.surface};}}
        QScrollBar:vertical {{width:12px; margin:2px;}}
        QScrollBar::handle:vertical {{background:{t.border}; min-height:28px; border-radius:4px;}}
        QScrollBar::handle:horizontal {{background:{t.border}; min-width:28px;}}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{height:0;}}
        QToolTip {{background:{t.input}; color:{t.text}; border:1px solid {t.border}; padding:5px;}}
        QMenu {{background:{t.input}; color:{t.text};}}
        QMenu::item:selected {{background:{t.selected};}}
    """


def apply_theme(name: str, app: QtWidgets.QApplication | None = None) -> None:
    global _active_theme
    if name not in THEMES:
        raise ValueError("Unsupported UI theme")
    if app is None:
        from film_foundry.ui.qt import _application
        app = _application()
    _active_theme = name
    t = colors()
    palette = QtGui.QPalette()
    roles = {"Window": t.background, "WindowText": t.text, "Base": t.input,
             "AlternateBase": t.surface, "Text": t.text, "Button": t.button,
             "ButtonText": t.text, "Highlight": t.selected, "HighlightedText": t.text,
             "ToolTipBase": t.input, "ToolTipText": t.text, "PlaceholderText": t.muted}
    for name, color in roles.items():
        palette.setColor(getattr(QtGui.QPalette.ColorRole, name), QtGui.QColor(color))
    for name in ("Text", "WindowText", "ButtonText"):
        palette.setColor(QtGui.QPalette.ColorGroup.Disabled, getattr(QtGui.QPalette.ColorRole, name), QtGui.QColor(t.disabled))
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

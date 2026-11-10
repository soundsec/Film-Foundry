"""Film Foundry's small Qt form layer.

The processing controllers keep their authoritative settings in observable
value objects.  This module binds those values to native PySide6 widgets and
provides the layout, dialog, preview-image, and GUI-thread primitives used by
the current desktop tools.  It is intentionally smaller than a general UI
framework and contains no processing or preset authority.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from PIL import Image

if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets


def _application() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv[:1])
        app.setApplicationName("Film Foundry")
        from film_foundry import __version__
        app.setApplicationVersion(__version__)
        app.setWindowIcon(QtGui.QIcon(str(Path(__file__).resolve().parents[1] / "assets" / "film_foundry_icon.svg")))
        app.setQuitOnLastWindowClosed(True)
        # Use CJK metrics as well as CJK glyphs. Segoe UI fallback glyphs can
        # exceed the height Qt reserved for a Latin-only font on Windows.
        if sys.platform == "win32":
            families = QtGui.QFontDatabase.families()
            if "Microsoft YaHei UI" in families:
                app.setFont(QtGui.QFont("Microsoft YaHei UI", 10))
            elif "Microsoft YaHei" in families:
                app.setFont(QtGui.QFont("Microsoft YaHei", 10))
    from film_foundry.ui.theme import initialize_theme
    initialize_theme(app)
    return app


def _margins(value: Any) -> tuple[int, int, int, int]:
    if value is None:
        return (0, 0, 0, 0)
    if isinstance(value, (int, float)):
        amount = int(value)
        return (amount, amount, amount, amount)
    parts = tuple(int(part) for part in value)
    if len(parts) == 2:
        return (parts[0], parts[1], parts[0], parts[1])
    if len(parts) == 4:
        return parts
    return (0, 0, 0, 0)


def _alignment(anchor: str | None) -> QtCore.Qt.AlignmentFlag:
    value = str(anchor or "center").lower()
    horizontal = QtCore.Qt.AlignmentFlag.AlignHCenter
    vertical = QtCore.Qt.AlignmentFlag.AlignVCenter
    if "w" in value or value == "left":
        horizontal = QtCore.Qt.AlignmentFlag.AlignLeft
    elif "e" in value or value == "right":
        horizontal = QtCore.Qt.AlignmentFlag.AlignRight
    if "n" in value or value == "top":
        vertical = QtCore.Qt.AlignmentFlag.AlignTop
    elif "s" in value or value == "bottom":
        vertical = QtCore.Qt.AlignmentFlag.AlignBottom
    return horizontal | vertical


class ObservableValue:
    """Observable controller value synchronized with one or more Qt views."""

    def __init__(self, value: Any = None) -> None:
        self._value = self._coerce(value)
        self._traces: list[Callable[..., Any]] = []
        self._views: list[tuple[Widget | None, Callable[[Any], None]]] = []
        self._notifying = False

    def _coerce(self, value: Any) -> Any:
        return value

    def get(self) -> Any:
        return self._value

    def set(self, value: Any) -> None:
        self._value = self._coerce(value)
        if self._notifying:
            return
        self._notifying = True
        try:
            for owner, update in tuple(self._views):
                if owner is not None and owner._destroyed:
                    self._views.remove((owner, update))
                    continue
                try:
                    update(self._value)
                except RuntimeError:
                    # Qt deletes widgets asynchronously.  A language rebuild
                    # can therefore leave a view callback alive for one event
                    # turn; retire it instead of letting stale C++ wrappers
                    # break later configuration changes.
                    if (owner, update) in self._views:
                        self._views.remove((owner, update))
            for callback in tuple(self._traces):
                callback("value", "", "write")
        finally:
            self._notifying = False

    def trace_add(self, mode: str, callback: Callable[..., Any]) -> str:
        del mode
        self._traces.append(callback)
        return f"trace-{len(self._traces)}"

    def _bind_view(self, update: Callable[[Any], None], owner: Widget | None = None) -> None:
        self._views.append((owner, update))
        if owner is not None:
            owner._bound_variables.append(self)
        update(self._value)

    def _unbind_owner(self, owner: Widget) -> None:
        self._views = [item for item in self._views if item[0] is not owner]


class TextValue(ObservableValue):
    def _coerce(self, value: Any) -> str:
        return "" if value is None else str(value)


class FloatValue(ObservableValue):
    def _coerce(self, value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0


class IntegerValue(ObservableValue):
    def _coerce(self, value: Any) -> int:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0


class BoolValue(ObservableValue):
    def _coerce(self, value: Any) -> bool:
        if isinstance(value, str):
            return value.strip().lower() not in {"", "0", "false", "no", "off"}
        return bool(value)


@dataclass
class _CompatEvent:
    widget: Any
    width: int = 0
    height: int = 0
    x_root: int = 0
    y_root: int = 0
    delta: int = 0
    num: int = 0


class _EventFilter(QtCore.QObject):
    def __init__(self, owner: "Widget") -> None:
        super().__init__(owner.qt)
        self.owner = owner
        self.callbacks: dict[QtCore.QEvent.Type, list[Callable[..., Any]]] = {}

    def add(self, event_type: QtCore.QEvent.Type, callback: Callable[..., Any]) -> None:
        self.callbacks.setdefault(event_type, []).append(callback)

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:  # noqa: N802
        callbacks = self.callbacks.get(event.type(), ())
        if not callbacks:
            return False
        point = self.owner.qt.mapToGlobal(QtCore.QPoint(0, 0))
        payload = _CompatEvent(
            widget=self.owner,
            width=self.owner.qt.width(),
            height=self.owner.qt.height(),
            x_root=point.x(),
            y_root=point.y(),
        )
        for callback in tuple(callbacks):
            callback(payload)
        return False


class _Dispatcher(QtCore.QObject):
    requested = QtCore.Signal(object, int)

    def __init__(self) -> None:
        super().__init__()
        self.requested.connect(self._schedule, QtCore.Qt.ConnectionType.QueuedConnection)

    @QtCore.Slot(object, int)
    def _schedule(self, callback: Callable[[], Any], delay_ms: int) -> None:
        QtCore.QTimer.singleShot(max(int(delay_ms), 0), callback)


class _TopLevelWidget(QtWidgets.QWidget):
    """Qt window whose native close also releases its form wrapper."""

    about_to_close = QtCore.Signal()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802
        self.about_to_close.emit()
        super().closeEvent(event)


class Widget:
    """Base wrapper shared by the supported Qt widget subset."""

    def __init__(self, parent: "Widget | None", qt_widget: QtWidgets.QWidget, **kwargs: Any) -> None:
        self.master = parent
        self.qt = qt_widget
        self._children: list[Widget] = []
        self._destroyed = False
        self._layout_mode: str | None = None
        self._grid_args: dict[str, Any] = {}
        self._pack_args: dict[str, Any] = {}
        self._layout: QtWidgets.QLayout | None = None
        self._column_weights: dict[int, int] = {}
        self._row_weights: dict[int, int] = {}
        self._padding = _margins(kwargs.pop("padding", None))
        self._text = str(kwargs.get("text", ""))
        self._event_filter: _EventFilter | None = None
        self._bound_variables: list[ObservableValue] = []
        qt_widget.destroyed.connect(self._mark_destroyed)
        if parent is not None:
            parent._children.append(self)
            # Form children become visible only after a layout owns them.
            qt_widget.hide()
        self.configure(**kwargs)

    def _content_widget(self) -> QtWidgets.QWidget:
        return self.qt

    def _ensure_grid_layout(self) -> QtWidgets.QGridLayout:
        if self._layout is None:
            layout = QtWidgets.QGridLayout(self._content_widget())
            layout.setContentsMargins(*self._padding)
            layout.setSpacing(6)
            self._layout = layout
        if not isinstance(self._layout, QtWidgets.QGridLayout):
            raise RuntimeError("Cannot mix pack and grid in the same Qt container")
        for column, weight in self._column_weights.items():
            self._layout.setColumnStretch(column, weight)
        for row, weight in self._row_weights.items():
            self._layout.setRowStretch(row, weight)
        return self._layout

    def _ensure_pack_layout(self, side: str = "top") -> QtWidgets.QBoxLayout:
        if self._layout is None:
            if side in {"left", "right"}:
                layout: QtWidgets.QBoxLayout = QtWidgets.QHBoxLayout(self._content_widget())
            else:
                layout = QtWidgets.QVBoxLayout(self._content_widget())
            layout.setContentsMargins(*self._padding)
            layout.setSpacing(6)
            self._layout = layout
        if not isinstance(self._layout, QtWidgets.QBoxLayout):
            raise RuntimeError("Cannot mix grid and pack in the same Qt container")
        return self._layout

    def grid(self, **kwargs: Any) -> "Widget":
        if not kwargs and self._grid_args:
            self.qt.show()
            return self
        self._grid_args = dict(kwargs)
        self._layout_mode = "grid"
        if self.master is None:
            return self
        layout = self.master._ensure_grid_layout()
        row = int(kwargs.get("row", 0))
        column = int(kwargs.get("column", 0))
        rowspan = int(kwargs.get("rowspan", 1))
        columnspan = int(kwargs.get("columnspan", 1))
        sticky = str(kwargs.get("sticky", ""))
        wraps = isinstance(self.qt, QtWidgets.QLabel) and self.qt.wordWrap()
        # A left-aligned word-wrapped label otherwise stays at Qt's narrow
        # size hint. Chinese then breaks every few characters inside the cell.
        horizontal_expand = ("e" in sticky and "w" in sticky) or wraps
        vertical_expand = "n" in sticky and "s" in sticky
        policy_x = QtWidgets.QSizePolicy.Policy.Expanding if horizontal_expand else QtWidgets.QSizePolicy.Policy.Preferred
        policy_y = QtWidgets.QSizePolicy.Policy.Expanding if vertical_expand else QtWidgets.QSizePolicy.Policy.Preferred
        policy = QtWidgets.QSizePolicy(policy_x, policy_y)
        policy.setHeightForWidth(wraps or self.qt.hasHeightForWidth())
        self.qt.setSizePolicy(policy)
        alignment = QtCore.Qt.AlignmentFlag(0)
        # Any non-zero cell alignment keeps the label at its size hint.
        if not wraps and not horizontal_expand:
            if "w" in sticky:
                alignment |= QtCore.Qt.AlignmentFlag.AlignLeft
            elif "e" in sticky:
                alignment |= QtCore.Qt.AlignmentFlag.AlignRight
            else:
                alignment |= QtCore.Qt.AlignmentFlag.AlignHCenter
        if not wraps and not vertical_expand:
            if "n" in sticky:
                alignment |= QtCore.Qt.AlignmentFlag.AlignTop
            elif "s" in sticky:
                alignment |= QtCore.Qt.AlignmentFlag.AlignBottom
            else:
                alignment |= QtCore.Qt.AlignmentFlag.AlignVCenter
        layout.addWidget(self.qt, row, column, rowspan, columnspan, alignment)
        self.qt.show()
        return self

    def pack(self, **kwargs: Any) -> "Widget":
        if not kwargs and self._pack_args:
            self.qt.show()
            return self
        self._pack_args = dict(kwargs)
        self._layout_mode = "pack"
        if self.master is None:
            return self
        side = str(kwargs.get("side", "top"))
        layout = self.master._ensure_pack_layout(side)
        fill = str(kwargs.get("fill", ""))
        expand = bool(kwargs.get("expand", False))
        wraps = isinstance(self.qt, QtWidgets.QLabel) and self.qt.wordWrap()
        policy_x = QtWidgets.QSizePolicy.Policy.Expanding if fill in {"x", "both"} or expand or wraps else QtWidgets.QSizePolicy.Policy.Preferred
        policy_y = QtWidgets.QSizePolicy.Policy.Expanding if fill in {"y", "both"} or expand else QtWidgets.QSizePolicy.Policy.Preferred
        policy = QtWidgets.QSizePolicy(policy_x, policy_y)
        policy.setHeightForWidth(wraps or self.qt.hasHeightForWidth())
        self.qt.setSizePolicy(policy)
        if layout.indexOf(self.qt) < 0:
            stretch = 1 if expand or (wraps and side in {"left", "right"}) else 0
            if side in {"right", "bottom"}:
                layout.addWidget(self.qt, stretch)
            else:
                layout.addWidget(self.qt, stretch)
        self.qt.show()
        return self

    def pack_forget(self) -> None:
        self.qt.hide()

    def grid_remove(self) -> None:
        self.qt.hide()

    def grid_info(self) -> dict[str, Any]:
        return dict(self._grid_args)

    def grid_slaves(self) -> list["Widget"]:
        return [child for child in reversed(self._children) if child._layout_mode == "grid"]

    def columnconfigure(self, index: int, weight: int = 0, **kwargs: Any) -> None:
        del kwargs
        self._column_weights[int(index)] = int(weight)
        if isinstance(self._layout, QtWidgets.QGridLayout):
            self._layout.setColumnStretch(int(index), int(weight))

    def rowconfigure(self, index: int, weight: int = 0, **kwargs: Any) -> None:
        del kwargs
        self._row_weights[int(index)] = int(weight)
        if isinstance(self._layout, QtWidgets.QGridLayout):
            self._layout.setRowStretch(int(index), int(weight))

    def configure(self, **kwargs: Any) -> None:
        if "padding" in kwargs:
            self._padding = _margins(kwargs["padding"])
            if self._layout is not None:
                self._layout.setContentsMargins(*self._padding)
        if "state" in kwargs:
            self.qt.setEnabled(str(kwargs["state"]) != "disabled")
        if "width" in kwargs:
            try:
                self.qt.setMinimumWidth(max(int(kwargs["width"]) * 8, 1))
            except (TypeError, ValueError):
                pass
        if "takefocus" in kwargs and not kwargs["takefocus"]:
            self.qt.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)

    config = configure

    def cget(self, key: str) -> Any:
        if key == "text":
            return self._text
        raise KeyError(key)

    def state(self, states: list[str] | tuple[str, ...] | None = None) -> tuple[str, ...]:
        if states is None:
            return () if self.qt.isEnabled() else ("disabled",)
        for value in states:
            if value == "disabled":
                self.qt.setEnabled(False)
            elif value == "!disabled":
                self.qt.setEnabled(True)
        return () if self.qt.isEnabled() else ("disabled",)

    def bind(self, sequence: str, callback: Callable[..., Any], add: str | None = None) -> str:
        del add
        if sequence == "<Return>" and isinstance(self.qt, QtWidgets.QLineEdit):
            self.qt.returnPressed.connect(lambda: callback(_CompatEvent(self)))
            return sequence
        if sequence == "<<ComboboxSelected>>" and isinstance(self.qt, QtWidgets.QComboBox):
            self.qt.activated.connect(lambda _index: callback(_CompatEvent(self)))
            return sequence
        event_types = {
            "<FocusOut>": QtCore.QEvent.Type.FocusOut,
            "<Enter>": QtCore.QEvent.Type.Enter,
            "<Leave>": QtCore.QEvent.Type.Leave,
            "<Configure>": QtCore.QEvent.Type.Resize,
        }
        event_type = event_types.get(sequence)
        if event_type is not None:
            if self._event_filter is None:
                self._event_filter = _EventFilter(self)
                self.qt.installEventFilter(self._event_filter)
            self._event_filter.add(event_type, callback)
        return sequence

    def bind_all(self, sequence: str, callback: Callable[..., Any], add: str | None = None) -> str:
        return self.bind(sequence, callback, add)

    def unbind_all(self, sequence: str) -> None:
        del sequence

    def winfo_children(self) -> list["Widget"]:
        return [child for child in self._children if not child._destroyed]

    def winfo_exists(self) -> bool:
        return not self._destroyed

    def winfo_height(self) -> int:
        return self.qt.height()

    def winfo_rootx(self) -> int:
        return self.qt.mapToGlobal(QtCore.QPoint(0, 0)).x()

    def winfo_rooty(self) -> int:
        return self.qt.mapToGlobal(QtCore.QPoint(0, 0)).y()

    def _mark_destroyed(self, *_args: Any) -> None:
        if self._destroyed:
            return
        self._destroyed = True
        for child in tuple(self._children):
            child._mark_destroyed()
        for variable in tuple(self._bound_variables):
            variable._unbind_owner(self)
        self._bound_variables.clear()
        if self.master is not None and self in self.master._children:
            self.master._children.remove(self)

    def destroy(self) -> None:
        if self._destroyed:
            return
        self._mark_destroyed()
        try:
            self.qt.hide()
            self.qt.deleteLater()
        except RuntimeError:
            # The native object may already have been deleted by a user close.
            pass

    def after(self, delay_ms: int, callback: Callable[[], Any]) -> str:
        root = self
        while root.master is not None:
            root = root.master
        return root.after(delay_ms, callback)  # type: ignore[no-any-return]

    def after_idle(self, callback: Callable[[], Any]) -> str:
        return self.after(0, callback)

    def after_cancel(self, identifier: str) -> None:
        root = self
        while root.master is not None:
            root = root.master
        root.after_cancel(identifier)

    def update(self) -> None:
        _application().processEvents()

    def lift(self) -> None:
        self.qt.raise_()

    def lower(self) -> None:
        self.qt.lower()

    def focus_force(self) -> None:
        self.qt.activateWindow()
        self.qt.setFocus()

    def set_tooltip(self, text: str) -> None:
        self.qt.setToolTip(text)


class ApplicationWindow(Widget):
    def __init__(self) -> None:
        self.app = _application()
        self._dispatcher = _Dispatcher()
        self._timers: dict[str, QtCore.QTimer] = {}
        self._timer_counter = 0
        qt_window = _TopLevelWidget()
        qt_window.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        super().__init__(None, qt_window)
        qt_window.about_to_close.connect(self._mark_destroyed)

    def title(self, value: str) -> None:
        self.qt.setWindowTitle(str(value))

    def geometry(self, value: str) -> None:
        match = re.match(r"^(?:(\d+)x(\d+))?(?:\+(-?\d+)\+(-?\d+))?$", str(value))
        if not match:
            return
        width, height, x_pos, y_pos = match.groups()
        if width and height:
            self.qt.resize(int(width), int(height))
        if x_pos is not None and y_pos is not None:
            self.qt.move(int(x_pos), int(y_pos))

    wm_geometry = geometry

    def minsize(self, width: int, height: int) -> None:
        self.qt.setMinimumSize(int(width), int(height))

    def mainloop(self) -> int:
        self.qt.show()
        return int(self.app.exec())

    def after(self, delay_ms: int, callback: Callable[[], Any]) -> str:
        if self._destroyed:
            return ""
        self._timer_counter += 1
        identifier = f"after-{self._timer_counter}"

        def invoke() -> None:
            self._timers.pop(identifier, None)
            if not self._destroyed:
                callback()

        if QtCore.QThread.currentThread() is self.app.thread():
            timer = QtCore.QTimer(self.qt)
            timer.setSingleShot(True)
            timer.timeout.connect(invoke)
            self._timers[identifier] = timer
            timer.start(max(int(delay_ms), 0))
        else:
            self._dispatcher.requested.emit(invoke, int(delay_ms))
        return identifier

    def after_cancel(self, identifier: str) -> None:
        timer = self._timers.pop(identifier, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()

    def destroy(self) -> None:
        super().destroy()
        self.app.quit()


class SecondaryWindow(ApplicationWindow):
    def __init__(self, parent: Widget | None = None, *, embedded: bool = False) -> None:
        self.app = _application()
        self._dispatcher = _Dispatcher()
        self._timers = {}
        self._timer_counter = 0
        qt_parent = parent.qt if parent is not None else None
        qt_window = _TopLevelWidget(qt_parent, QtCore.Qt.WindowType.Widget if embedded else QtCore.Qt.WindowType.Window)
        qt_window.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        Widget.__init__(self, parent, qt_window)
        qt_window.about_to_close.connect(self._mark_destroyed)
        if not embedded:self.qt.show()

    def mainloop(self) -> int:
        self.qt.show()
        return 0

    def destroy(self) -> None:
        Widget.destroy(self)

    def wm_overrideredirect(self, enabled: bool) -> None:
        self.qt.setWindowFlag(QtCore.Qt.WindowType.FramelessWindowHint, bool(enabled))
        self.qt.show()


class Frame(Widget):
    def __init__(self, parent: Widget, **kwargs: Any) -> None:
        super().__init__(parent, QtWidgets.QWidget(parent._content_widget()), **kwargs)


class GroupBox(Widget):
    def __init__(self, parent: Widget, text: str = "", **kwargs: Any) -> None:
        self.group = QtWidgets.QGroupBox(str(text), parent._content_widget())
        super().__init__(parent, self.group, text=text, **kwargs)

    def configure(self, **kwargs: Any) -> None:
        super().configure(**kwargs)
        if "text" in kwargs:
            self._text = str(kwargs["text"])
            self.group.setTitle(self._text)


def _apply_font(widget: QtWidgets.QWidget, font: Any) -> None:
    if not isinstance(font, (tuple, list)) or len(font) < 2:
        return
    current = widget.font()
    if font[0]:
        current.setFamily(str(font[0]))
    current.setPointSize(int(font[1]))
    if len(font) >= 3:
        current.setBold("bold" in str(font[2]).lower())
    widget.setFont(current)


class _FormLabel(QtWidgets.QLabel):
    """Reserve height at the actual assigned width, including aligned labels.

    QGridLayout can use the unwrapped size hint for a left-aligned spanning
    label even though its final width needs multiple lines. QLabel's own
    height-for-width also underestimates Chinese, so the row is measured
    from the font instead.
    """
    def hasHeightForWidth(self):
        return self.wordWrap() or super().hasHeightForWidth()

    def heightForWidth(self, width):
        if not self.wordWrap() or width <= 0:
            return super().heightForWidth(width)
        flags = int(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.TextFlag.TextWordWrap)
        rect = self.fontMetrics().boundingRect(0, 0, max(1, int(width) - 2), 10000, flags, self.text())
        return max(self.fontMetrics().height(), rect.height() + 2)

    def _fit_wrapped_height(self):
        if self.wordWrap() and self.width() > 0:
            height=self.heightForWidth(self.width())
            if height >= 0 and self.minimumHeight() != height:
                self.setMinimumHeight(height)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        self._fit_wrapped_height()

    def setText(self,text):
        super().setText(text)
        # Hidden/new controls still have a placeholder width. Reserving that
        # height inflates their parent before the page receives its real grid.
        if self.isVisible():
            self._fit_wrapped_height()

    def changeEvent(self,event):
        super().changeEvent(event)
        if self.isVisible() and event.type() in (QtCore.QEvent.Type.FontChange,QtCore.QEvent.Type.StyleChange):
            self._fit_wrapped_height()


class Label(Widget):
    def __init__(self, parent: Widget, text: str = "", textvariable: ObservableValue | None = None, **kwargs: Any) -> None:
        label = _FormLabel(str(text), parent._content_widget())
        label.setAlignment(_alignment(kwargs.get("anchor", "w")))
        if kwargs.get("wraplength"):
            label.setWordWrap(True)
        if kwargs.get("font"):
            _apply_font(label, kwargs["font"])
        self._photo: Any = None
        super().__init__(parent, label, text=text, **kwargs)
        if textvariable is not None:
            textvariable._bind_view(lambda value: label.setText(str(value)), self)

    def configure(self, **kwargs: Any) -> None:
        super().configure(**kwargs)
        if "text" in kwargs:
            self._text = str(kwargs["text"])
            self.qt.setText(self._text)
        if "image" in kwargs:
            self._photo = kwargs["image"]
            pixmap = getattr(self._photo, "pixmap", None)
            if isinstance(pixmap, QtGui.QPixmap):
                self.qt.setPixmap(pixmap)


class Button(Widget):
    def __init__(self, parent: Widget, text: str = "", command: Callable[[], Any] | None = None, **kwargs: Any) -> None:
        button = QtWidgets.QPushButton(str(text), parent._content_widget())
        if command is not None:
            button.clicked.connect(lambda _checked=False: command())
        super().__init__(parent, button, text=text, **kwargs)

    def configure(self, **kwargs: Any) -> None:
        super().configure(**kwargs)
        if "text" in kwargs:
            self._text = str(kwargs["text"])
            self.qt.setText(self._text)


class TextInput(Widget):
    def __init__(self, parent: Widget, textvariable: ObservableValue | None = None, **kwargs: Any) -> None:
        edit = QtWidgets.QLineEdit(parent._content_widget())
        super().__init__(parent, edit, **kwargs)
        self.variable = textvariable
        if textvariable is not None:
            def update(value: Any) -> None:
                text = str(value)
                if edit.text() != text:
                    blocker = QtCore.QSignalBlocker(edit)
                    edit.setText(text)
                    del blocker

            textvariable._bind_view(update, self)
            edit.textChanged.connect(textvariable.set)

    def get(self) -> str:
        return self.qt.text()


class _ScrollSafeChoice(QtWidgets.QComboBox):
    def wheelEvent(self, event):
        # A closed preset menu must not replace a recipe while its page scrolls.
        event.ignore()


class _ScrollSafeSlider(QtWidgets.QSlider):
    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class _ScrollSafeNumber(QtWidgets.QDoubleSpinBox):
    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class Choice(Widget):
    def __init__(
        self,
        parent: Widget,
        textvariable: ObservableValue | None = None,
        values: tuple[Any, ...] | list[Any] = (),
        state: str = "normal",
        **kwargs: Any,
    ) -> None:
        combo = _ScrollSafeChoice(parent._content_widget())
        combo.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        combo.addItems([str(value) for value in values])
        combo.setEditable(state != "readonly")
        super().__init__(parent, combo, state=state, **kwargs)
        self.variable = textvariable
        if textvariable is not None:
            def update(value: Any) -> None:
                text = str(value)
                blocker = QtCore.QSignalBlocker(combo)
                index = combo.findText(text)
                if index >= 0:
                    combo.setCurrentIndex(index)
                elif combo.isEditable():
                    combo.setEditText(text)
                else:
                    combo.setCurrentIndex(-1)
                del blocker

            textvariable._bind_view(update, self)
            combo.currentTextChanged.connect(textvariable.set)

    def configure(self, **kwargs: Any) -> None:
        super().configure(**kwargs)
        if "values" in kwargs:
            values = [str(value) for value in kwargs["values"]]
            current = self.variable.get() if self.variable is not None else self.qt.currentText()
            blocker = QtCore.QSignalBlocker(self.qt)
            self.qt.clear()
            self.qt.addItems(values)
            index = self.qt.findText(str(current))
            self.qt.setCurrentIndex(index)
            del blocker
        if "state" in kwargs:
            state = str(kwargs["state"])
            self.qt.setEditable(state != "readonly")
            self.qt.setEnabled(state != "disabled")


class Toggle(Widget):
    def __init__(
        self,
        parent: Widget,
        text: str = "",
        variable: ObservableValue | None = None,
        command: Callable[[], Any] | None = None,
        **kwargs: Any,
    ) -> None:
        checkbox = QtWidgets.QCheckBox(str(text), parent._content_widget())
        super().__init__(parent, checkbox, text=text, **kwargs)
        self.variable = variable
        if variable is not None:
            def update(value: Any) -> None:
                blocker = QtCore.QSignalBlocker(checkbox)
                checkbox.setChecked(bool(value))
                del blocker

            variable._bind_view(update, self)
            checkbox.toggled.connect(variable.set)
        if command is not None:
            checkbox.clicked.connect(lambda _checked=False: command())

    def configure(self, **kwargs: Any) -> None:
        super().configure(**kwargs)
        if "text" in kwargs:
            self._text = str(kwargs["text"])
            self.qt.setText(self._text)


class Radio(Widget):
    def __init__(
        self,
        parent: Widget,
        text: str = "",
        variable: ObservableValue | None = None,
        value: Any = None,
        command: Callable[[], Any] | None = None,
        **kwargs: Any,
    ) -> None:
        radio = QtWidgets.QRadioButton(str(text), parent._content_widget())
        super().__init__(parent, radio, text=text, **kwargs)
        self.variable = variable
        self.value = value
        if variable is not None:
            def update(current: Any) -> None:
                blocker = QtCore.QSignalBlocker(radio)
                radio.setChecked(current == value)
                del blocker

            variable._bind_view(update, self)
            radio.toggled.connect(lambda checked: variable.set(value) if checked else None)
        if command is not None:
            radio.clicked.connect(lambda _checked=False: command())


class Slider(Widget):
    _STEPS = 10000

    def __init__(
        self,
        parent: Widget,
        from_: float = 0.0,
        to: float = 1.0,
        variable: ObservableValue | None = None,
        command: Callable[[str], Any] | None = None,
        orient: str = "horizontal",
        **kwargs: Any,
    ) -> None:
        orientation = QtCore.Qt.Orientation.Vertical if orient == "vertical" else QtCore.Qt.Orientation.Horizontal
        slider = _ScrollSafeSlider(orientation, parent._content_widget())
        slider.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        slider.setRange(0, self._STEPS)
        self.minimum = float(from_)
        self.maximum = float(to)
        self.variable = variable
        self.command = command
        super().__init__(parent, slider, **kwargs)
        if variable is not None:
            variable._bind_view(self._set_value, self)
            slider.valueChanged.connect(self._changed)

    def _set_value(self, value: Any) -> None:
        span = self.maximum - self.minimum
        ratio = 0.0 if span == 0.0 else (float(value) - self.minimum) / span
        blocker = QtCore.QSignalBlocker(self.qt)
        self.qt.setValue(round(max(0.0, min(1.0, ratio)) * self._STEPS))
        del blocker

    def _changed(self, position: int) -> None:
        value = self.minimum + (self.maximum - self.minimum) * position / self._STEPS
        if self.variable is not None:
            self.variable.set(value)
        if self.command is not None:
            self.command(str(value))

    def configure(self, **kwargs: Any) -> None:
        if "from_" in kwargs:
            self.minimum = float(kwargs["from_"])
        if "to" in kwargs:
            self.maximum = float(kwargs["to"])
        super().configure(**kwargs)
        if getattr(self, "variable", None) is not None:
            self._set_value(self.variable.get())


class NumberInput(Widget):
    def __init__(
        self,
        parent: Widget,
        from_: float = 0.0,
        to: float = 1.0,
        increment: float = 0.1,
        textvariable: ObservableValue | None = None,
        command: Callable[[], Any] | None = None,
        **kwargs: Any,
    ) -> None:
        spin = _ScrollSafeNumber(parent._content_widget())
        spin.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        spin.setRange(float(from_), float(to))
        spin.setSingleStep(max(abs(float(increment)), 1e-9))
        spin.setDecimals(min(8, max(2, len(f"{abs(float(increment)):.8f}".rstrip("0").split(".")[-1]))))
        spin.setKeyboardTracking(False)
        super().__init__(parent, spin, **kwargs)
        self.variable = textvariable
        if textvariable is not None:
            def update(value: Any) -> None:
                blocker = QtCore.QSignalBlocker(spin)
                spin.setValue(float(value))
                del blocker

            textvariable._bind_view(update, self)
            spin.valueChanged.connect(textvariable.set)
        if command is not None:
            spin.valueChanged.connect(lambda _value: command())


class Separator(Widget):
    def __init__(self, parent: Widget, orient: str = "horizontal", **kwargs: Any) -> None:
        frame = QtWidgets.QFrame(parent._content_widget())
        frame.setFrameShape(QtWidgets.QFrame.Shape.VLine if orient == "vertical" else QtWidgets.QFrame.Shape.HLine)
        frame.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        super().__init__(parent, frame, **kwargs)


class PhotoImage:
    def __init__(self, image: Image.Image | None = None, **kwargs: Any) -> None:
        if image is None:
            image = kwargs.get("image")
        if image is None:
            raise ValueError("PhotoImage requires a PIL image")
        rgba = image.convert("RGBA")
        data = rgba.tobytes("raw", "RGBA")
        qimage = QtGui.QImage(data, rgba.width, rgba.height, rgba.width * 4, QtGui.QImage.Format.Format_RGBA8888).copy()
        self.pixmap = QtGui.QPixmap.fromImage(qimage)


def _dialog_filter(filetypes: Any) -> str:
    if not filetypes:
        return "All files (*)"
    filters: list[str] = []
    for label, patterns in filetypes:
        values = str(patterns).replace(";", " ")
        filters.append(f"{label} ({values})")
    return ";;".join(filters)


def _dialog_parent(kwargs: dict[str, Any]) -> QtWidgets.QWidget | None:
    parent = kwargs.get("parent")
    if isinstance(parent, Widget):
        return parent.qt
    if isinstance(parent, QtWidgets.QWidget):
        return parent
    return QtWidgets.QApplication.activeWindow()


class _FileDialog:
    @staticmethod
    def askopenfilename(**kwargs: Any) -> str:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            _dialog_parent(kwargs),
            str(kwargs.get("title", "Open")),
            str(kwargs.get("initialdir", "")),
            _dialog_filter(kwargs.get("filetypes")),
        )
        return path

    @staticmethod
    def askdirectory(**kwargs: Any) -> str:
        return QtWidgets.QFileDialog.getExistingDirectory(
            _dialog_parent(kwargs),
            str(kwargs.get("title", "Select folder")),
            str(kwargs.get("initialdir", "")),
        )

    @staticmethod
    def asksaveasfilename(**kwargs: Any) -> str:
        initial = str(kwargs.get("initialfile", ""))
        directory = str(kwargs.get("initialdir", ""))
        start = os.path.join(directory, initial) if initial else directory
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            _dialog_parent(kwargs),
            str(kwargs.get("title", "Save")),
            start,
            _dialog_filter(kwargs.get("filetypes")),
        )
        extension = str(kwargs.get("defaultextension", ""))
        if path and extension and not os.path.splitext(path)[1]:
            path += extension
        return path


class _MessageBox:
    @staticmethod
    def askyesno(title: str, message: str, **kwargs: Any) -> bool:
        result = QtWidgets.QMessageBox.question(_dialog_parent(kwargs), str(title), str(message))
        return result == QtWidgets.QMessageBox.StandardButton.Yes

    @staticmethod
    def showerror(title: str, message: str, **kwargs: Any) -> None:
        QtWidgets.QMessageBox.critical(_dialog_parent(kwargs), str(title), str(message))

    @staticmethod
    def showinfo(title: str, message: str, **kwargs: Any) -> None:
        QtWidgets.QMessageBox.information(_dialog_parent(kwargs), str(title), str(message))


class _SimpleDialog:
    @staticmethod
    def askstring(title: str, prompt: str, **kwargs: Any) -> str | None:
        text, accepted = QtWidgets.QInputDialog.getText(
            _dialog_parent(kwargs),
            str(title),
            str(prompt),
            text=str(kwargs.get("initialvalue", "")),
        )
        return str(text) if accepted else None


file_dialog = _FileDialog()
message_box = _MessageBox()
text_dialog = _SimpleDialog()

ui = SimpleNamespace(
    Application=ApplicationWindow,
    Window=SecondaryWindow,
    Widget=Widget,
    Value=ObservableValue,
    TextValue=TextValue,
    FloatValue=FloatValue,
    IntegerValue=IntegerValue,
    BoolValue=BoolValue,
    Frame=Frame,
    Group=GroupBox,
    Label=Label,
    Button=Button,
    TextInput=TextInput,
    Choice=Choice,
    Toggle=Toggle,
    Radio=Radio,
    Slider=Slider,
    NumberInput=NumberInput,
    Separator=Separator,
)

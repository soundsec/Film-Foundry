"""Shared stage navigation, independent of process state and execution."""
from film_foundry.ui.qt import QtCore,QtGui,QtWidgets
from film_foundry.ui.color_schemes import selection_rule, text_on
from film_foundry.ui.theme import colors


def visited_operations(session):
    if session.state is None and session.group is None:
        return set()
    try:
        return {step['operation'] for step in session.recipe()['steps']}
    except ValueError:
        return None


def operation_status(visited, operations, available):
    from film_foundry.ui.curve_editor import text
    done=bool(visited and visited.intersection(operations))
    if done:
        return text('已执行 · ● 可执行','Completed · ● Available') if available else text('已执行','Completed')
    if available:return text('● 可执行','● Available')
    return text('未到达','Upcoming') if visited is not None else text('无步骤记录','No step record')


class StageButton(QtWidgets.QPushButton):
    stage_changed = QtCore.Signal()

    def __init__(self,number,*,compact=False):
        super().__init__()
        self.number = number
        self.compact = compact
        self.state = "editable"
        self.trailing_width = 0
        self.stage_title = self.stage_status = ""
        self.available = self.show_title = self.visited = False
        self.setCheckable(True)
        self.setFixedHeight(32 if compact else 54)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Fixed)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)

    def set_stage(self, title, status, *, available=False, visited=False):
        if (title, status, available, visited) == (self.stage_title, self.stage_status, self.available, self.visited):
            return
        self.stage_title, self.stage_status, self.available = title, status, available
        self.visited=visited
        self.setText(f"{self.number}  {title}\n{status}")
        self.setAccessibleName(f"{self.number} · {title} · {status}")
        self.setToolTip(self.accessibleName())
        self.stage_changed.emit()
        self.update()

    def nextCheckState(self):
        self.setChecked(True)

    def compact_widths(self):
        metrics = self.fontMetrics()
        bold = QtGui.QFont(self.font())
        bold.setBold(True)
        number = metrics.horizontalAdvance(f"{self.number:02d}") + 16
        title = QtGui.QFontMetrics(bold).horizontalAdvance(self.stage_title)
        named = number + 6 + title + 10
        expanded = named + 10 + metrics.horizontalAdvance(self.stage_status) + 14
        return number, named, expanded

    def _paint_compact(self, painter, ink):
        align = QtCore.Qt.AlignmentFlag.AlignVCenter | QtCore.Qt.AlignmentFlag.AlignLeft
        number = f"{self.number:02d}"
        painter.setPen(ink)
        if not self.show_title:
            painter.drawText(self.rect(), QtCore.Qt.AlignmentFlag.AlignCenter, number)
        else:
            number_width = self.fontMetrics().horizontalAdvance(number)
            painter.drawText(QtCore.QRect(8, 0, number_width, self.height()), align, number)
            x = 8 + number_width + 6
            font = self.font()
            font.setBold(self.isChecked())
            painter.setFont(font)
            width = max(0, self.width() - x - 8)
            title = painter.fontMetrics().elidedText(self.stage_title, QtCore.Qt.TextElideMode.ElideRight, width)
            painter.drawText(QtCore.QRect(x, 0, width, self.height()), align, title)
            x += painter.fontMetrics().horizontalAdvance(title) + 10
            if self.isChecked() and self.width() - x - 6 >= self.fontMetrics().horizontalAdvance(self.stage_status):
                painter.setFont(self.font())
                painter.drawText(QtCore.QRect(x, 0, self.width()-x-6, self.height()), align, self.stage_status)
                return
        if self.visited:
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            painter.setPen(QtGui.QPen(ink,1.7))
            x=self.width()-12
            painter.drawLine(QtCore.QPointF(x,7),QtCore.QPointF(x+3,10))
            painter.drawLine(QtCore.QPointF(x+3,10),QtCore.QPointF(x+9,3))
        elif self.available:
            painter.setBrush(ink)
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.drawEllipse(QtCore.QRectF(self.width()-7, 4, 4, 4))

    def paintEvent(self, event):
        t = colors()
        painter = QtGui.QPainter(self)
        enabled = self.isEnabled()
        selected = self.isChecked()
        background = t.background if self.compact else t.surface
        if enabled:
            if self.isDown() or self.underMouse():
                background = t.hover
            elif selected:
                background = t.selected
        painter.fillRect(self.rect(), QtGui.QColor(background))

        ink = QtGui.QColor(text_on(background) if enabled else t.disabled)
        if self.compact:
            self._paint_compact(painter, ink)
        else:
            self._paint_film(painter, ink)

        rule = 2 if selected else 1
        marker = selection_rule(t.accent, background)
        rule_color = (marker if enabled else t.disabled) if selected else t.border
        painter.fillRect(0, self.height() - rule, self.width(), rule, QtGui.QColor(rule_color))
        if self.hasFocus():
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.setPen(QtGui.QPen(QtGui.QColor(marker), 1, QtCore.Qt.PenStyle.DotLine))
            painter.drawRect(self.rect().adjusted(2, 2, -3, -3))

    def _paint_film(self, painter, ink):
        left = 9
        top = 5
        title_line, _, status = self.text().partition("\n")
        title = title_line.split("  ", 1)[-1]
        font = self.font()
        painter.setFont(font)
        number_width = painter.fontMetrics().horizontalAdvance("00") + 7
        text_left = left + number_width
        align = QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter
        painter.setPen(ink)
        painter.drawText(QtCore.QRect(left, top, number_width, 23), align, f"{self.number:02d}")

        title_box = QtCore.QRect(text_left, top, max(0, self.width() - text_left - left - self.trailing_width), 23)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(ink)
        elide = QtCore.Qt.TextElideMode.ElideRight
        painter.drawText(title_box, align, painter.fontMetrics().elidedText(title, elide, title_box.width()))

        font.setBold(False)
        font.setPointSizeF(max(8, font.pointSizeF() * .86))
        painter.setFont(font)
        painter.setPen(ink)
        status_box = QtCore.QRect(left, top + 23, max(0, self.width() - 2 * left), 18)
        painter.drawText(status_box, align, painter.fontMetrics().elidedText(status, elide, status_box.width()))


class StageStrip(QtWidgets.QWidget):
    """A single row. Reserve every number, expand the selection, then fit whole labels."""
    def __init__(self):
        super().__init__()
        self.buttons = []
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
        self.setFixedHeight(max(32, self.fontMetrics().height() + 10))

    def add_stage(self, button):
        button.setParent(self)
        button.setFixedHeight(self.height())
        self.buttons.append(button)
        button.toggled.connect(self.reflow)
        button.stage_changed.connect(self.reflow)
        button.show()
        self.reflow()
        self.updateGeometry()

    def clear(self):
        for button in self.buttons:
            button.hide()
            button.deleteLater()
        self.buttons.clear()
        self.updateGeometry()

    def minimumSizeHint(self):
        number_widths = sum(b.compact_widths()[0] for b in self.buttons)
        return QtCore.QSize(number_widths + max(0, len(self.buttons)-1), self.height())

    def reflow(self):
        if not self.buttons:
            return
        count = len(self.buttons)
        sizes = [b.compact_widths() for b in self.buttons]
        widths = [number for number, _, _ in sizes]
        spare = max(0, self.width() - (count-1) - sum(widths))
        active = next((i for i, b in enumerate(self.buttons) if b.isChecked()), None)
        named = set()
        if active is not None:
            extra = min(spare, sizes[active][2] - widths[active])
            widths[active] += extra
            spare -= extra
            named.add(active)
        # Neighbours of the selected operation get their names back first.
        order = sorted(range(count), key=lambda i: abs(i - active)) if active is not None else range(count)
        for i in order:
            extra = sizes[i][1] - widths[i]
            if i != active and extra <= spare:
                named.add(i)
                widths[i] += extra
                spare -= extra
        share, remainder = divmod(spare, count)
        x = 0
        for i, button in enumerate(self.buttons):
            width = widths[i] + share + int(i < remainder)
            button.show_title = i in named
            button.setGeometry(x, 0, width, self.height())
            button.update()
            x += width + 1

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reflow()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QtCore.QEvent.Type.FontChange and hasattr(self, "buttons"):
            self.setFixedHeight(max(32, self.fontMetrics().height() + 10))
            for button in self.buttons:
                button.setFixedHeight(self.height())
            self.updateGeometry()
            self.reflow()


def sync_tab_titles(window):
    """The tab supplies the title; standalone groups retain their own heading."""
    for name in ('material_group','process_group','defect_group','finish_group'):
        group=getattr(window,name,None)
        if group is None:continue
        if not hasattr(group,'_standalone_title'):
            group._standalone_title=group.title()
            group.setAccessibleName(group.title())
            group.setToolTip((group.title()+'\n'+group.toolTip()).strip())
        in_tabs=window.config_tabs.indexOf(group)>=0 and window.config_tabs.count()>1
        group.setTitle('' if in_tabs else group._standalone_title)
        group.setObjectName('tabPageGroup')
        group.setStyleSheet('QGroupBox#tabPageGroup {border:0;margin-top:0;padding-top:0;}' if in_tabs else '')

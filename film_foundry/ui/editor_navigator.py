"""Section filtering over live editor widgets; no replacement draft or form."""
from functools import wraps
from film_foundry.ui.qt import QtCore, QtWidgets
from film_foundry.ui.curve_editor import text


def batch_parameter_update(refresh_name):
    """Apply an entire preset before drawing its preview once."""
    def decorate(method):
        @wraps(method)
        def update(self, *args, **kwargs):
            previous = getattr(self, '_loading_parameters', False)
            self._loading_parameters = True
            try:
                return method(self, *args, **kwargs)
            finally:
                self._loading_parameters = previous
                if not previous:getattr(self, refresh_name)()
        return update
    return decorate


def install_editor_navigator(editor):
    if getattr(editor, 'field_navigator', None) is not None:
        return
    scroll = editor.controls_scroller
    content = scroll.content.qt
    grid = content.layout()
    host_layout = scroll.qt.parentWidget().layout()
    if not isinstance(grid, QtWidgets.QGridLayout) or not isinstance(host_layout, QtWidgets.QGridLayout):
        return
    entries = []
    for i in range(grid.count()):
        widget = grid.itemAt(i).widget()
        if widget is not None:
            row, col, rows, cols = grid.getItemPosition(i)
            entries.append((row, col, rows, cols, widget, widget.isHidden()))
    entries.sort(key=lambda item: (item[0], item[1]))
    headings = [(r, w) for r, c, rs, cs, w, hidden in entries
                if isinstance(w, QtWidgets.QLabel) and w.font().bold() and cs > 1]
    for _, label in headings:
        full = label.text()
        label.setToolTip((label.toolTip() + '\n' + full).strip())
        label.setText(full.split('（',1)[0].split('(',1)[0].strip())
        label.setObjectName('editorHeading')
        label.setWordWrap(False)
        label.setMinimumHeight(36)
        label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Fixed)
        grid.setAlignment(label, QtCore.Qt.AlignmentFlag(0))
    labels = [(r, w) for r, c, rs, cs, w, hidden in entries
              if isinstance(w, QtWidgets.QLabel) and w.text().strip() and (r, w) not in headings]
    for _, label in labels:
        label.setWordWrap(True)
        label.setMinimumWidth(150)
        label.setMaximumWidth(200)
        grid.setAlignment(label, QtCore.Qt.AlignmentFlag.AlignVCenter)
    grid.setHorizontalSpacing(12)
    grid.setColumnStretch(0,0)
    grid.setColumnMinimumWidth(0,150)
    grid.setColumnStretch(1,1)
    grid.setColumnStretch(2,1)
    grid.setColumnStretch(3,0)
    if not headings and not labels:
        return
    bar = QtWidgets.QWidget()
    layout = QtWidgets.QGridLayout(bar)
    layout.setContentsMargins(0, 0, 0, 6)
    sections = QtWidgets.QComboBox()
    sections.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    sections.setMinimumContentsLength(12)
    sections.addItem(text('全部分组', 'All sections'), None)
    for row, label in headings:
        sections.addItem(label.text(), row)
    search = QtWidgets.QLineEdit()
    search.setPlaceholderText(text('搜索名称、术语或字段…', 'Search names, terms or fields…'))
    search.setClearButtonEnabled(True)
    search.setAccessibleName(text('搜索参数', 'Search parameters'))
    matches = QtWidgets.QComboBox()
    matches.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    matches.setMinimumContentsLength(12)
    matches.setAccessibleName(text('搜索结果', 'Search results'))
    layout.addWidget(sections, 0, 0, 1, 2)
    layout.addWidget(search, 1, 0)
    layout.addWidget(matches, 1, 1)
    layout.setColumnStretch(0, 1)
    layout.setColumnStretch(1, 1)
    empty = QtWidgets.QLabel(text('没有匹配的参数', 'No matching parameters'))
    layout.addWidget(empty, 2, 0, 1, 2)
    empty.hide()
    grid.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
    # A field may span several rows, for example a 3x3 matrix.
    anchors = sorted(set(r for r, _ in labels + headings))
    def next_anchor(row):
        return next((r for r in anchors if r > row), grid.rowCount())
    def section_end(row):
        return next((r for r, _ in headings if r > row), grid.rowCount())
    def refresh(*_):
        query = search.text().strip().casefold()
        selected = sections.currentData()
        matches.blockSignals(True)
        matches.clear()
        hit_fields = [(row, label) for row, label in labels
                      if query in (label.text() + ' ' + label.toolTip()).casefold()]
        hit_sections = [(row, label) for row, label in headings
                        if query and query in (label.text() + ' ' + label.toolTip()).casefold()]
        ranges = ([(r, next_anchor(r)) for r, _ in hit_fields] +
                  [(r, section_end(r)) for r, _ in hit_sections]) if query else (
                      [(0, grid.rowCount())] if selected is None else [(selected, section_end(selected))])
        visible = {r for r, _, _, _, _, _ in entries if any(a <= r < b for a, b in ranges)}
        for row, _ in headings:
            if any(row <= r < section_end(row) for r in visible):
                visible.add(row)
        for row, _, _, _, widget, originally_hidden in entries:
            widget.setVisible(row in visible and not originally_hidden)
        for row, label in (hit_fields + hit_sections if query else headings or labels):
            if row in visible:
                matches.addItem(label.text(), label)
        matches.blockSignals(False)
        matches.setVisible(bool(query))
        empty.setVisible(bool(query) and not visible)
        scroll.qt.verticalScrollBar().setValue(0)
    def focus(*_):
        target = matches.currentData()
        if target is not None:
            scroll.qt.ensureWidgetVisible(target, 20, 30)
    search.textChanged.connect(refresh)
    search.returnPressed.connect(focus)
    sections.currentIndexChanged.connect(refresh)
    matches.activated.connect(focus)
    index = host_layout.indexOf(scroll.qt)
    row, col, rs, cs = host_layout.getItemPosition(index)
    host_layout.removeWidget(scroll.qt)
    container = QtWidgets.QWidget()
    stack = QtWidgets.QVBoxLayout(container)
    stack.setContentsMargins(0, 0, 0, 0)
    stack.addWidget(bar)
    stack.addWidget(scroll.qt, 1)
    host_layout.addWidget(container, row, col, rs, cs)
    editor.field_navigator = bar
    editor.parameter_search = search
    editor.parameter_matches = matches
    editor.parameter_sections = sections
    if headings:
        sections.setCurrentIndex(1)
    refresh()


def install_editor_splitter(editor, shell, controls, preview):
    """Adjustable preview with the same embedded/standalone draft widgets."""
    layout = shell.qt.layout()
    layout.removeWidget(controls.qt)
    layout.removeWidget(preview.qt)
    split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
    split.setChildrenCollapsible(False)
    split.addWidget(controls.qt)
    split.addWidget(preview.qt)
    controls.qt.setMinimumWidth(490)
    editor.controls_scroller.qt.setMinimumWidth(470)
    preview.qt.setMinimumWidth(220)
    split.setStretchFactor(0, 3)
    split.setStretchFactor(1, 2)
    split.setSizes([620, 400])
    layout.addWidget(split, 1, 0, 1, 2)
    editor.editor_splitter = split


class FittedCurvePreview(QtCore.QObject):
    """Keep axes and legends visible as the editor splitter moves."""
    def __init__(self, label):
        super().__init__(label)
        self.label = label
        self.pixmap = None
        label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Ignored)
        label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        label.installEventFilter(self)

    def set_pixmap(self, pixmap):
        self.pixmap = pixmap
        self.refresh()

    def refresh(self):
        size = self.label.contentsRect().size()
        if self.pixmap is not None and size.width() > 0 and size.height() > 0:
            self.label.setPixmap(self.pixmap.scaled(size, QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                                                   QtCore.Qt.TransformationMode.SmoothTransformation))

    def eventFilter(self, watched, event):
        if event.type() == QtCore.QEvent.Type.Resize:
            self.refresh()
        return False

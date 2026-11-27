"""Edit a material's physical coating order from the lens-facing emulsion to the support."""
from film_foundry.contracts.coating_stack import MODEL, RECORDS, has_layer_separation, split_emulsion, validate_stack
from film_foundry.ui.curve_editor import text
from film_foundry.ui.qt import QtCore, QtWidgets


def separation_hint():
    return text("这款材料只有整片记录，缺少可区分各层的响应或透射密度。可在等效记录模式调整材料。",
                "This stock lacks independent coating responses or optical densities. Edit its effective records instead.")


def available(widget, enabled, hint):
    widget.setEnabled(enabled)
    widget.setToolTip(hint)
    widget.setAccessibleDescription(hint)


class CoatingEditButton(QtWidgets.QPushButton):
    def __init__(self, open_editor):
        super().__init__()
        self.clicked.connect(open_editor)

    def show_stock(self, film):
        supported = film.supports_ordered_coatings
        count = len(film.coating_stack["layers"]) if supported else 0
        self.setText(text("编辑 {n} 层…", "Edit {n} coatings…").format(n=count) if count
                     else text("编辑层结构…", "Edit coatings…"))
        available(self, supported, text("查看或调整乳剂和夹层。", "Inspect or edit emulsions and filters.")
                  if supported else separation_hint())


class LayerNameDelegate(QtWidgets.QStyledItemDelegate):
    def displayText(self, value, locale):
        if value == "yellow-filter":
            return text("黄色滤光夹层", "Yellow filter")
        record, _, speed = str(value).partition("-")
        names = {"B": ("B 组", "Group B"), "G": ("G 组", "Group G"),
                 "R": ("R 组", "Group R"), "Ag": ("银像", "Silver")}
        if record in names and speed in ("fast", "slow"):
            kind = text("快层", "fast") if speed == "fast" else text("慢层", "slow")
            return text(*names[record]) + " · " + kind
        return super().displayText(value, locale)


class CoatingNumberDelegate(QtWidgets.QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        field = QtWidgets.QDoubleSpinBox(parent)
        field.setDecimals(6)
        field.setRange(*((-24., 24.) if index.column() == 2 else (.0001, 32.)))
        field.setSingleStep(.1 if index.column() == 2 else .05)
        return field

    def setEditorData(self, editor, index):
        editor.setValue(float(index.data()))

    def setModelData(self, editor, model, index):
        editor.interpretText()
        model.setData(index, str(editor.value()))


class CoatingEditor(QtWidgets.QDialog):
    def __init__(self, film, parent=None):
        super().__init__(parent)
        self.setWindowTitle(text("感光涂层", "Film coatings")+" · "+film.name)
        self.mono = film.effective_record_count == 1
        stack = film.editable_coatings()
        layout = QtWidgets.QVBoxLayout(self)
        options = QtWidgets.QHBoxLayout()
        options.addWidget(QtWidgets.QLabel(film.name))
        options.addStretch()
        self.stock_basis = QtWidgets.QCheckBox(text("沿用材料曝光校准", "Inherit stock exposure calibration"))
        self.stock_basis.setChecked(stack.get("exposure_basis") == "stock")
        self.stock_basis.setToolTip(text("沿用本材料的测光、反装响应及黑白曝光模型。新增涂层的透射密度叠加在此基础上。关闭后从片基和涂层计算光路。",
                                         "Use the stock's metering, base-first response and monochrome exposure model. Added coating densities act on this input. Clear to model the path from support and coatings."))
        self.basis_hint = self.stock_basis.toolTip()
        options.addWidget(self.stock_basis)
        layout.addLayout(options)
        layout.addWidget(QtWidgets.QLabel(text("正装从上方入光，反装从下方入光。片基默认在末端，可插入表中定位。",
                                                "Light enters from the top normally and bottom in reverse. Insert the support to move it from the bottom.")))
        self.table = QtWidgets.QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([text("涂层名称","Layer ID"),text("成像通道","Image channel"),
                                             text("速度 EV","Speed EV"),text("涂布量","Amount"),
                                             text("透射密度 R G B","Transmission OD R G B"),
                                             text("感度权重 RGB","RGB sensitivity"),text("波段 / 光谱曲线","Band / spectrum")])
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setDefaultSectionSize(max(25, self.table.fontMetrics().height()+8))
        self.table.setItemDelegateForColumn(0, LayerNameDelegate(self.table))
        for col in (2, 3):
            self.table.setItemDelegateForColumn(col, CoatingNumberDelegate(self.table))
        layout.addWidget(self.table, 1)
        for row in stack["layers"]:
            self.add_row(row)
        hint = (text("光谱升维已开启：在当前估计光谱上计算乳剂响应和夹层透射，再形成潜影。", "Spectral lift on: coating response and transmission use the current estimated spectrum before latent formation.")
                if film.spectral_exposure_profile is not None else
                text("光谱升维已关闭：使用 RGB 感度和透射密度。窄带和曲线会保存；在材料编辑器的「光谱」菜单开启升维后生效。",
                     "Spectral lift off: RGB sensitivity and density apply. Bands and curves are saved. Enable spectral lift in the material editor's Spectrum menu to use them."))
        notice = QtWidgets.QLabel(hint)
        notice.setWordWrap(True)
        notice.setToolTip(text("来源照片提供 RGB；入射光谱由当前模型估计。切换影响新的曝光，已保存的潜影保持原样。",
                               "The input photo supplies RGB. The current model estimates the incident spectrum. Switching affects new exposures; saved latents retain their exposure."))
        layout.addWidget(notice)
        self.chemistry = QtWidgets.QWidget()
        row_layout = QtWidgets.QHBoxLayout(self.chemistry)
        row_layout.setContentsMargins(0,0,0,0)
        self.factors = {}
        for key, title, maximum in (("development_rate",text("本层显影速率 ×","Layer development rate ×"),8.),
                                    ("coupler_fraction",text("成色剂容量 ×","Coupler capacity ×"),1.)):
            row_layout.addWidget(QtWidgets.QLabel(title))
            field = QtWidgets.QDoubleSpinBox()
            field.setRange(0.,maximum)
            field.setDecimals(4)
            field.setSingleStep(.05)
            row_layout.addWidget(field)
            self.factors[key] = field
            field.valueChanged.connect(self.save_factors)
        row_layout.addStretch()
        layout.addWidget(self.chemistry)
        self.table.currentCellChanged.connect(self.show_factors)
        self.actions = QtWidgets.QWidget()
        self.row_buttons = {}
        line = QtWidgets.QHBoxLayout(self.actions)
        line.setContentsMargins(0,0,0,0)
        for zh, en, fn in (("拆分乳剂","Split emulsion",self.split_selected),
                           ("加乳剂","Add emulsion",lambda:self.append("emulsion")),
                           ("加夹层","Add filter",lambda:self.append("filter")),
                           ("编辑光谱…","Edit spectrum…",self.edit_curve),
                           ("定位片基","Place support",lambda:self.append("support")),
                           ("上移","Move up",lambda:self.move(-1)), ("下移","Move down",lambda:self.move(1)),
                           ("移除","Remove",self.remove)):
            button = QtWidgets.QPushButton(text(zh,en))
            button.clicked.connect(fn)
            self.row_buttons[zh] = button
            line.addWidget(button)
        layout.addWidget(self.actions)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        self.apply_button = buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok)
        self.apply_button.setText(text("应用", "Apply"))
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setText(text("取消", "Cancel"))
        buttons.accepted.connect(self.apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.table.itemChanged.connect(self.update_actions)
        self.table.selectRow(0)
        self.resize(940, 458)

    def update_actions(self):
        index = self.table.currentRow()
        rows = self.table.rowCount()
        selected = 0 <= index < rows
        emulsion = selected and self.record_at(index) is not None
        amount = self.table.item(index, 3) if emulsion else None
        try:
            can_split = amount is not None and float(amount.text()) >= .0002
        except ValueError:
            can_split = False
        can_remove = selected and (not emulsion or sum(self.record_at(i) is not None for i in range(rows)) > 1)
        supports = [i for i in range(rows) if self.is_support(i)]
        physical = bool(supports) or any(self.band_value(i) for i in range(rows))
        if physical:
            self.stock_basis.setChecked(False)
        self.stock_basis.setEnabled(not physical)
        self.stock_basis.setToolTip(text("定位片基、选择波段或自定义光谱时，须按实际层序计算光路。", "A positioned support, passband or custom spectrum requires physical optics.")
                                   if physical else self.basis_hint)
        for key, enabled, reason in (
            ("编辑光谱…", selected and not self.is_support(index), text("选择乳剂或滤光夹层。", "Select an emulsion or filter.")),
            ("定位片基", not supports, text("已有片基，选中后用上移或下移定位。", "Select the existing support and move it.")),
            ("上移", index > 0, text("已在乳剂面一端。", "Already at the emulsion surface.")),
            ("下移", 0 <= index < rows-1, text("已在片基一端。", "Already at the support.")),
            ("拆分乳剂", can_split, text("请选择涂布量至少 0.0002 的乳剂。", "Select an emulsion with an amount of at least 0.0002.")),
            ("移除", can_remove, text("至少要保留一层感光乳剂。", "Keep at least one photosensitive emulsion.")),
        ):
            available(self.row_buttons[key], enabled, "" if enabled else reason)
        if all(self.table.item(i, 1) is not None or self.table.cellWidget(i, 1) is not None for i in range(rows)):
            try:
                self.read_stack()
                reason = ""
            except ValueError as exc:
                reason = str(exc)
            available(self.apply_button, not reason, reason)
            self.status.setText(reason)

    def add_row(self, row, index=None):
        i = self.table.rowCount() if index is None else index
        self.table.insertRow(i)
        emulsion = row["kind"] == "emulsion"
        support = row["kind"] == "support"
        def rgb(values):
            return " ".join(str(v) for v in values)
        for col, value in ((0,row["id"]), (2,row.get("speed_ev", "")), (3,row.get("amount","")),
                           (4,text("随材料","From stock") if support else rgb(row["density_rgb"])), (5,rgb(row.get("sensitivity_rgb", ())))):
            item = QtWidgets.QTableWidgetItem(str(value))
            if not emulsion and (col in (2,3,5) or support and col == 4):
                item.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
                item.setToolTip(text("片基使用材料的支撑体密度。", "Support density comes from the stock.") if support else
                                text("夹层可编辑 RGB 透射密度与光谱透射曲线。", "A filter has RGB transmission densities and an optional spectral transmission curve."))
            self.table.setItem(i,col,item)
        self.table.item(i,0).setData(QtCore.Qt.ItemDataRole.UserRole,
                                    {k:row.get(k, 1.) for k in ("development_rate","coupler_fraction")} if emulsion else None)
        if not support:
            bands = QtWidgets.QComboBox()
            bands.addItem(text("材料 / RGB", "Stock / RGB") if emulsion else text("RGB 密度", "RGB density"), None)
            presets = [(400,430), (480,510), (590,620)] if emulsion else []
            for lo, hi in dict.fromkeys(presets + ([tuple(row["band_nm"])] if "band_nm" in row else [])):
                bands.addItem(f"{lo}–{hi}", (lo, hi))
                if [lo, hi] == row.get("band_nm"):
                    bands.setCurrentIndex(bands.count()-1)
            bands.addItem(text("自定义曲线", "Custom curve"), "curve")
            self.table.item(i,0).setData(QtCore.Qt.ItemDataRole.UserRole+2,row.get("spectrum", [[380.,1.],[780.,1.]]))
            if "spectrum" in row:
                bands.setCurrentIndex(bands.count()-1)
            self.table.setCellWidget(i,6,bands)
            def select_band():
                pos = self.table.indexAt(bands.pos()).row()
                if pos >= 0 and emulsion:
                    self.table.item(pos,5).setFlags(QtCore.Qt.ItemFlag.NoItemFlags if bands.currentData() not in (None,"curve") else
                                                   QtWidgets.QTableWidgetItem().flags())
                self.update_actions()
            if row.get("band_nm"):
                self.table.item(i,5).setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
            bands.currentIndexChanged.connect(select_band)
        else:
            empty = QtWidgets.QTableWidgetItem()
            empty.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
            self.table.setItem(i,6,empty)
        if emulsion and not self.mono:
            combo = QtWidgets.QComboBox()
            for key, dye in zip(RECORDS, (text("C · 青", "C · Cyan"), text("M · 品红", "M · Magenta"), text("Y · 黄", "Y · Yellow"))):
                combo.addItem(dye, key)
            combo.setCurrentIndex(combo.findData(row["record"]))
            self.table.setCellWidget(i,1,combo)
            combo.currentIndexChanged.connect(self.update_actions)
        else:
            label = text("银像乳剂", "Silver emulsion") if emulsion else text("片基", "Support") if support else text("滤色夹层", "Filter")
            item = QtWidgets.QTableWidgetItem(label)
            item.setFlags(item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, row.get("record"))
            item.setData(QtCore.Qt.ItemDataRole.UserRole+1, row["kind"])
            self.table.setItem(i, 1, item)

    def is_support(self, index):
        item = self.table.item(index,1)
        return item is not None and item.data(QtCore.Qt.ItemDataRole.UserRole+1) == "support"

    def band_value(self, index):
        combo = self.table.cellWidget(index,6)
        return combo.currentData() if combo else None

    def edit_curve(self):
        from film_foundry.ui.coating_curve import CoatingCurveDialog
        i = self.table.currentRow()
        choice = self.table.cellWidget(i,6)
        if choice is None:
            return
        item = self.table.item(i,0)
        role = QtCore.Qt.ItemDataRole.UserRole+2
        points = item.data(role)
        band = choice.currentData()
        if band and band != "curve":
            lo, hi = band
            points = [[nm, float(lo <= nm <= hi)] for nm in sorted({380, max(380,lo-5), lo, hi, min(780,hi+5), 780})]
        dialog = CoatingCurveDialog(points, self.record_at(i) is not None, self)
        if dialog.exec():
            item.setData(role, dialog.points())
            choice.setCurrentIndex(choice.findData("curve"))
            self.update_actions()

    def record_at(self, index):
        choice = self.table.cellWidget(index, 1)
        if choice is not None:
            return choice.currentData()
        item = self.table.item(index, 1)
        return item.data(QtCore.Qt.ItemDataRole.UserRole) if item is not None else None

    def show_factors(self):
        item = self.table.item(self.table.currentRow(),0)
        values = item.data(QtCore.Qt.ItemDataRole.UserRole) if item else None
        self.chemistry.setEnabled(bool(values))
        self.chemistry.setToolTip("" if values else text("只有乳剂参与显影与成色。", "Only emulsions develop an image."))
        available(self.factors["coupler_fraction"], bool(values) and not self.mono,
                  text("银像黑白乳剂只形成银像。", "Silver B&W emulsions form a silver image.") if self.mono else self.chemistry.toolTip())
        for key,field in self.factors.items():
            field.blockSignals(True)
            field.setValue(values[key] if values else 1.)
            field.blockSignals(False)
        self.update_actions()

    def save_factors(self):
        item = self.table.item(self.table.currentRow(),0)
        if item is not None:
            item.setData(QtCore.Qt.ItemDataRole.UserRole, {k:f.value() for k,f in self.factors.items()})

    def append(self, kind):
        used = {self.table.item(i,0).text() for i in range(self.table.rowCount())}
        n = 1
        while f"{kind}-{n}" in used:
            n += 1
        row = dict(kind=kind, id=f"{kind}-{n}", density_rgb=[0.,0.,0.])
        if kind == "emulsion":
            row.update(record="G", amount=1., speed_ev=0.)
        index = self.table.currentRow()+1
        self.add_row(row, index)
        self.table.selectRow(index)
        self.update_actions()

    def read_row(self, index):
        def cell(col):
            return self.table.item(index,col).text().strip()
        def rgb(col):
            return [float(v) for v in cell(col).replace(",", " ").split()]
        key = self.record_at(index)
        if self.is_support(index):
            return dict(id=cell(0), kind="support", density_rgb=[0.,0.,0.])
        row = dict(id=cell(0),kind="emulsion" if key else "filter",density_rgb=rgb(4))
        mode = self.band_value(index)
        if mode == "curve":
            row["spectrum"] = self.table.item(index,0).data(QtCore.Qt.ItemDataRole.UserRole+2)
        if key:
            row.update(record=key, speed_ev=float(cell(2)), amount=float(cell(3)))
            row.update(self.table.item(index,0).data(QtCore.Qt.ItemDataRole.UserRole))
            if mode and mode != "curve":
                row["band_nm"] = list(mode)
            elif cell(5):
                row["sensitivity_rgb"] = rgb(5)
        return row

    def move(self, delta):
        i = self.table.currentRow()
        if i >= 0 and 0 <= i+delta < self.table.rowCount():
            try:
                row = self.read_row(i)
            except ValueError as exc:
                self.status.setText(str(exc))
                return
            self.table.removeRow(i)
            self.add_row(row, i+delta)
            self.table.selectRow(i+delta)

    def remove(self):
        i = self.table.currentRow()
        if i >= 0 and self.row_buttons["移除"].isEnabled():
            self.table.removeRow(i)
            self.update_actions()

    def split_selected(self):
        i = self.table.currentRow()
        if i < 0:
            return
        try:
            rows = [self.read_row(j) for j in range(self.table.rowCount())]
            if rows[i]["kind"] != "emulsion":
                raise ValueError(text("请选择要拆分的乳剂层。", "Select an emulsion to split."))
            first, second = split_emulsion(rows, i)
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.table.removeRow(i)
        self.add_row(first, i)
        self.add_row(second, i+1)
        self.table.selectRow(i+1)

    def read_stack(self):
        rows = [self.read_row(i) for i in range(self.table.rowCount())]
        stack = validate_stack(dict(model=MODEL, layers=rows,
                                    exposure_basis="stock" if self.stock_basis.isChecked() else "physical"))
        if not has_layer_separation(stack, silver_only=self.mono):
            raise ValueError(text("保留有独立响应的乳剂或有透射密度的夹层后即可应用。",
                                  "Keep separate emulsion responses or a filter with optical density before applying."))
        return stack

    def apply(self):
        try:
            self.result_stack = self.read_stack()
            self.accept()
        except ValueError as exc:
            self.status.setText(str(exc))

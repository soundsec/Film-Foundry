"""Selection UI over the portable preset application service."""
from copy import deepcopy
from film_foundry.ui.qt import QtCore, QtWidgets
from film_foundry.ui.curve_editor import text
from film_foundry.app import preset_exchange as exchange

LABELS = {'film':('材料','Material'), 'solution':('药水','Solution'),
    'develop':('冲洗流程','Process'), 'scanner':('扫描','Scanner'), 'capture':('曝光与片边','Exposure & edges')}


def label(kind):return text(*LABELS[kind])


def detail(item):
    kind, data = item['kind'], item['payload']
    if kind == 'solution':
        from film_foundry.ui.roll_process_dialog import ACTIONS
        return text(*ACTIONS.get(data['action'],(data['action'],data['action'])))
    if kind == 'develop':
        recipe = data.get('develop', {})
        count = len(recipe.get('process_steps') or ())
        programs={'auto':('随材料选择','Match material'),'legacy_density':('传统密度流程','Legacy density'),
            'bw_negative':('黑白负片','B&W negative'),'bw_reversal':('黑白反转','B&W reversal'),
            'color_negative':('彩色负片','Colour negative'),'color_reversal':('彩色反转','Colour reversal'),
            'color_negative_bleach_bypass':('彩负留银','Colour negative bleach bypass')}
        name = text(f'{count} 个自定义步骤',f'{count} custom steps') if count else text(*programs.get(recipe.get('program_key','auto'),('自定义工艺','Custom process')))
        solutions = len(exchange.solution_items(recipe))
        return name + (text(f' · 内含 {solutions} 种药水',f' · {solutions} embedded solutions') if solutions else '')
    if kind=='film':
        film=data.get('film',{});mono=film.get('color_process') in ('bw','monochrome','black_white','black-and-white')
        positive=film.get('image_polarity')=='positive' or film.get('medium_process') in ('positive','slide','reversal')
        return text('黑白' if mono else '彩色','B&W ' if mono else 'Colour ')+text('正片' if positive else '负片','positive' if positive else 'negative')
    if kind=='scanner':return text('扫描解释、影调与色彩','Interpretation, tone & colour')
    return text('曝光时间、片边与画面打印','Exposure duration, edges & imprint')


class SharePresetsDialog(QtWidgets.QDialog):
    def __init__(self, items, parent=None,*,select_all=False):
        super().__init__(parent);self.items=deepcopy(items)
        self.setWindowTitle(text('分享预设 · FFC2','Share presets · FFC2'));self.resize(760,570)
        layout=QtWidgets.QVBoxLayout(self)
        self.table=QtWidgets.QTableWidget(len(items),3)
        self.table.setHorizontalHeaderLabels([text('分享','Share'),text('类型','Type'),text('名称','Name')])
        self.table.horizontalHeader().setSectionResizeMode(2,QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide();self.table.setColumnWidth(0,60);self.table.setColumnWidth(1,115)
        self.checks=[]
        for row,item in enumerate(items):
            check=QtWidgets.QCheckBox();check.setChecked(select_all or item['kind'] not in ('solution','capture'))
            self.table.setCellWidget(row,0,check);self.checks.append(check)
            cell=QtWidgets.QTableWidgetItem(label(item['kind']));cell.setFlags(cell.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
            cell.setToolTip(detail(item));self.table.setItem(row,1,cell)
            self.table.setItem(row,2,QtWidgets.QTableWidgetItem(item['name']))
        if len(items)==1:self.checks[0].setChecked(True)
        layout.addWidget(self.table,1)
        self.legacy=QtWidgets.QCheckBox(text('兼容旧版配方码','Legacy recipe compatibility'))
        self.legacy.setToolTip(text('仅每类一个材料／流程／扫描／曝光配置；不支持独立药水。新版合集可含多项，旧客户端需更新后才能接收；手机不支持的工艺不会自动补齐。',
            'One material/process/scanner/exposure per category; no standalone solutions. Collections require an updated client. Unsupported mobile operations are not added by importing.'))
        layout.addWidget(self.legacy)
        self.code=QtWidgets.QPlainTextEdit();self.code.setReadOnly(True);self.code.setMaximumHeight(130)
        layout.addWidget(self.code);self.status=QtWidgets.QLabel();self.status.setWordWrap(True);layout.addWidget(self.status)
        bar=QtWidgets.QHBoxLayout();layout.addLayout(bar)
        self.copy_button=QtWidgets.QPushButton(text('复制分享码','Copy code'));self.copy_button.clicked.connect(self.copy_code);bar.addWidget(self.copy_button)
        save=QtWidgets.QPushButton(text('保存分享文件…','Save share file…'));save.clicked.connect(self.save_code);bar.addWidget(save)
        bar.addStretch();close=QtWidgets.QPushButton(text('关闭','Close'));close.clicked.connect(self.reject);bar.addWidget(close)
        for check in self.checks:check.toggled.connect(self.refresh)
        self.table.itemChanged.connect(self.refresh);self.legacy.toggled.connect(self.refresh);self.refresh()

    def selected(self):
        return [{**item,'name':self.table.item(row,2).text().strip()} for row,item in enumerate(self.items) if self.checks[row].isChecked()]

    def refresh(self,*_):
        try:
            selected=self.selected();code=exchange.legacy_code(selected) if self.legacy.isChecked() else exchange.encode_items(selected)
            self.code.setPlainText(code);self.status.setText(text(f'{len(selected)} 项 · 不含图像、输出位置或运行质量设置',f'{len(selected)} entries · No images, output location or runtime quality'))
        except (ValueError,TypeError) as exc:self.code.clear();self.status.setText(str(exc))
        self.copy_button.setEnabled(bool(self.code.toPlainText()))

    def copy_code(self):
        QtWidgets.QApplication.clipboard().setText(self.code.toPlainText())
        self.status.setText(text('已复制分享码','Share code copied'))

    def save_code(self):
        if not self.code.toPlainText():return
        path,_=QtWidgets.QFileDialog.getSaveFileName(self,text('保存分享码','Save share code'),'presets.ffc2','FFC2 (*.ffc2)')
        if path:
            from film_foundry.core.atomic_io import atomic_write_bytes
            try:atomic_write_bytes(path,(self.code.toPlainText()+'\n').encode('utf-8'))
            except OSError as exc:self.status.setText(str(exc))


class ReceivePresetsDialog(QtWidgets.QDialog):
    def __init__(self,items,parent=None,*,on_apply=None,can_apply=None,on_saved=None,root=None,keep=True):
        super().__init__(parent);self.items=deepcopy(items);self.on_apply=on_apply;self.on_saved=on_saved;self.root=root
        self.setWindowTitle(text('接收预设','Receive presets'));self.resize(850,520)
        layout=QtWidgets.QVBoxLayout(self)
        self.table=QtWidgets.QTableWidget(len(items),5);self.table.verticalHeader().hide()
        self.table.setHorizontalHeaderLabels([text('保留','Keep'),text('应用','Apply'),text('类型','Type'),text('保存名称','Save as'),text('内容','Contents')])
        self.table.setColumnWidth(0,55);self.table.setColumnWidth(1,55);self.table.setColumnWidth(2,115)
        self.table.horizontalHeader().setSectionResizeMode(3,QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4,QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.keep_checks=[];self.apply_checks=[]
        for row,item in enumerate(items):
            save=QtWidgets.QCheckBox();save.setChecked(keep);apply=QtWidgets.QCheckBox()
            allowed=on_apply is not None and item['kind']!='solution' and (can_apply is None or can_apply(item['kind']))
            apply.setEnabled(allowed);apply.setChecked(allowed and not keep)
            if not allowed:apply.setToolTip(text('药水需在具体药浴中选用；已冻结的阶段只能保存预设供以后使用。','Solutions are chosen inside a bath; frozen stages can only be saved for later use.'))
            self.keep_checks.append(save);self.apply_checks.append(apply)
            self.table.setCellWidget(row,0,save);self.table.setCellWidget(row,1,apply)
            for col,value in ((2,label(item['kind'])),(3,item['name']),(4,detail(item))):
                cell=QtWidgets.QTableWidgetItem(value)
                if col!=3:cell.setFlags(cell.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
                cell.setToolTip(detail(item));self.table.setItem(row,col,cell)
        self.table.setToolTip(text('流程内嵌药水始终跟随流程；取消保留独立药水只是不另存药水预设。重名自动另存，不覆盖已有文件。',
            'Embedded solutions stay with their process. Unchecking a standalone solution only skips its library entry. Name collisions are saved separately.'))
        layout.addWidget(self.table,1)
        self.status=QtWidgets.QLabel();self.status.setWordWrap(True);layout.addWidget(self.status)
        buttons=QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok|QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setText(text('确认接收','Receive'))
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setText(text('取消','Cancel'))
        buttons.accepted.connect(self.commit);buttons.rejected.connect(self.reject);layout.addWidget(buttons)

    def selection(self,checks):
        return [{**item,'name':self.table.item(row,3).text().strip()} for row,item in enumerate(self.items) if checks[row].isChecked()]

    def commit(self):
        try:
            keep=self.selection(self.keep_checks);apply=self.selection(self.apply_checks)
            if not keep and not apply:raise ValueError(text('请选择要保留或应用的项目','Select entries to keep or apply'))
            # Validate conflicts before writing anything. The owner validates
            # frozen stages again in case the current task changed meanwhile.
            kinds=[item['kind'] for item in apply]
            if len(kinds)!=len(set(kinds)):raise ValueError(text('当前工作每类只能应用一项','Apply only one entry per category'))
            for item in (*keep,*apply):exchange.checked_item(item)
            if apply:self.on_apply(apply,validate_only=True)
            paths=exchange.save_items(keep,root=self.root)
            try:
                if apply:self.on_apply(apply)
            except Exception:
                for path in paths:path.unlink(missing_ok=True)
                raise
            if self.on_saved:self.on_saved()
            self.accept()
        except (ValueError,TypeError,OSError) as exc:self.status.setText(str(exc))


class PresetLibrary(QtWidgets.QWidget):
    def __init__(self,*,current,apply,can_apply,edit,changed,parent=None):
        super().__init__(parent);self.current=current;self.apply=apply;self.can_apply=can_apply;self.edit=edit;self.changed=changed
        layout=QtWidgets.QVBoxLayout(self);bar=QtWidgets.QHBoxLayout();layout.addLayout(bar)
        self.kind=QtWidgets.QComboBox();self.kind.addItem(text('所有类型','All types'),None)
        for key in exchange.KINDS:self.kind.addItem(label(key),key)
        bar.addWidget(self.kind)
        self.source=QtWidgets.QComboBox();self.source.addItem(text('全部来源','All sources'),None);self.source.addItem(text('我的预设','My presets'),False);self.source.addItem(text('内置','Built in'),True);bar.addWidget(self.source)
        self.search=QtWidgets.QLineEdit();self.search.setPlaceholderText(text('查找名称或工艺','Find name or process'));bar.addWidget(self.search,1)
        self.table=QtWidgets.QTableWidget(0,4);self.table.setHorizontalHeaderLabels([text('名称','Name'),text('类型','Type'),text('来源','Source'),text('内容','Contents')])
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide();self.table.horizontalHeader().setSectionResizeMode(0,QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3,QtWidgets.QHeaderView.ResizeMode.Stretch);layout.addWidget(self.table,1)
        actions=QtWidgets.QHBoxLayout();layout.addLayout(actions)
        for zh,en,callback in (('接收分享码…','Receive code…',self.receive),('导入文件…','Import file…',self.import_file),
                ('分享所选…','Share selected…',self.share),('分享当前配置…','Share current…',self.share_current),
                ('应用所选…','Apply selected…',self.apply_selected),('编辑…','Edit…',self.edit_selected),('移除','Remove',self.remove)):
            button=QtWidgets.QPushButton(text(zh,en));button.clicked.connect(callback);actions.addWidget(button)
        self.status=QtWidgets.QLabel();layout.addWidget(self.status)
        self.kind.currentIndexChanged.connect(lambda:self.refresh(reload=False));self.source.currentIndexChanged.connect(lambda:self.refresh(reload=False));self.search.textChanged.connect(lambda:self.refresh(reload=False))
        self.table.doubleClicked.connect(self.edit_selected);self.refresh()

    def refresh(self,*_,reload=True):
        if reload:
            from film_foundry.tools.editor_ui import localized_preset_name
            from film_foundry.app.paths import preset_root
            self.all_records=exchange.library_records()
            for record in self.all_records:
                if record['builtin']:
                    record['name']=localized_preset_name(record['kind'],record.get('key',record['path'].stem),record['name'],selected_path=record['path'],builtin_dir=preset_root()/record['kind'])
        records=self.all_records;query=self.search.text().strip().casefold()
        self.records=[r for r in records if (self.kind.currentData() is None or r['kind']==self.kind.currentData())
            and (self.source.currentData() is None or r['builtin']==self.source.currentData())
            and (not query or query in (r['name']+' '+str(r.get('payload',''))).casefold())]
        self.table.setRowCount(len(self.records))
        for row,record in enumerate(self.records):
            for col,value in enumerate((record['name'],label(record['kind']),text('内置','Built in') if record['builtin'] else text('我的','Mine'),record.get('error') or detail(record))):
                cell=QtWidgets.QTableWidgetItem(value);cell.setToolTip(str(record['path'])+'\n'+value);self.table.setItem(row,col,cell)

    def selected(self):return [self.records[index.row()] for index in self.table.selectionModel().selectedRows()]

    def entries(self):
        return [{key:r[key] for key in ('kind','name','payload')} for r in self.selected() if 'payload' in r]

    def present(self,dialog):
        from film_foundry.ui.inline_editor import present_inline_editor
        present_inline_editor(self,dialog)

    def received(self):self.changed();self.refresh()

    def receive_items(self,items,keep=True):
        self.present(ReceivePresetsDialog(items,self,on_apply=self.apply,can_apply=self.can_apply,on_saved=self.received,keep=keep))

    def receive(self):
        value,ok=QtWidgets.QInputDialog.getMultiLineText(self,text('输入分享码','Enter share code'),'FFC2:')
        if ok and value.strip():
            try:self.receive_items(exchange.read_items(value))
            except (ValueError,TypeError) as exc:self.status.setText(str(exc))

    def import_file(self):
        path,_=QtWidgets.QFileDialog.getOpenFileName(self,text('导入预设','Import preset'),'','Presets (*.ffc2 *.txt *.json)')
        if not path:return
        try:
            from pathlib import Path
            import json
            content=Path(path).read_text(encoding='utf-8-sig')
            if content.lstrip().startswith('FFC2:'):items=exchange.read_items(content)
            else:
                data=json.loads(content)
                if not isinstance(data,dict):raise ValueError(text('预设必须是对象','Preset must be an object'))
                if data.get('kind')=='film_foundry_bath_solution':items=[exchange.read_preset(path,'solution')]
                else:
                    if 'chemistry' in data:data['develop']=data.pop('chemistry')
                    items=exchange.split_recipe(data,Path(path).stem)
                    items.extend(exchange.solution_items(data.get('develop',{})))
            if not items:raise ValueError(text('文件没有可接收的预设','No supported presets in file'))
            self.receive_items(items)
        except (ValueError,TypeError,OSError) as exc:self.status.setText(str(exc))

    def share(self):
        if self.entries():self.present(SharePresetsDialog(self.entries(),self,select_all=True))

    def share_current(self):self.present(SharePresetsDialog(exchange.current_items(self.current()),self))

    def apply_selected(self):
        if self.entries():self.receive_items(self.entries(),keep=False)

    def edit_selected(self,*_):
        entries=self.entries()
        if len(entries)!=1:self.status.setText(text('请选择一项编辑','Select one entry to edit'));return
        self.edit(entries[0])

    def remove(self):
        records=[r for r in self.selected() if not r['builtin']]
        if not records:return
        if QtWidgets.QMessageBox.question(self,text('移除用户预设','Remove user presets'),text(f'移除所选 {len(records)} 项？已有工作和分享码中的副本不受影响。',f'Remove {len(records)} entries? Copies in saved work and share codes remain.'))!=QtWidgets.QMessageBox.StandardButton.Yes:return
        try:
            for record in records:exchange.remove_user_preset(record)
            self.received()
        except (ValueError,OSError) as exc:self.status.setText(str(exc))

"""A roll-level front end; lifecycle and persistence belong to the application."""
from pathlib import Path
from film_foundry.ui.qt import QtCore,QtWidgets
from film_foundry.ui.curve_editor import text
from film_foundry.app.electronic_roll import RollSession,import_roll
from film_foundry.contracts.data_kernel import PHONE_ROLL_CAPACITY_VALUES


class RollWorkspace(QtWidgets.QWidget):
    def __init__(self,storage,submit,configure,back,parent=None,*,cancel=None,present=None):
        super().__init__(parent);self.storage=Path(storage);self.submit=submit;self.configure=configure;self.session=None;self.present=present
        layout=QtWidgets.QVBoxLayout(self);bar=QtWidgets.QHBoxLayout();layout.addLayout(bar)
        title=QtWidgets.QLabel(text('折腾模式 · 整卷暗房','Darkroom · Whole roll'));title.setStyleSheet('font-size:22px;font-weight:600');bar.addWidget(title);bar.addStretch()
        button=QtWidgets.QPushButton(text('图库','Library'));button.clicked.connect(back);bar.addWidget(button)
        self.cancel=QtWidgets.QPushButton(text('停止任务','Stop task'));self.cancel.clicked.connect(lambda:cancel() if cancel else None);bar.addWidget(self.cancel);self.cancel.hide()
        row=QtWidgets.QHBoxLayout();layout.addLayout(row)
        self.rolls=QtWidgets.QComboBox();row.addWidget(self.rolls,1);self.rolls.currentIndexChanged.connect(self.select)
        for zh,en,callback in (('导入电子胶卷…','Import roll…',self.import_dialog),('导入旧潜影…','Import legacy latents…',self.import_legacy),('导出电子胶卷…','Export roll…',self.export_dialog)):
            button=QtWidgets.QPushButton(text(zh,en));button.clicked.connect(callback);row.addWidget(button)
        from film_foundry.ui.roll_strips import RollStripView
        self.display=QtWidgets.QStackedWidget();layout.addWidget(self.display,1)
        self.tree=QtWidgets.QTreeWidget();self.tree.setHeaderLabels([text('格号','Frame'),text('曝光','Exposure'),text('处理','Processing')]);self.display.addWidget(self.tree)
        self.strips=RollStripView();self.display.addWidget(self.strips);self.light_tables=[]
        self.status=QtWidgets.QLabel();self.status.setWordWrap(True);layout.addWidget(self.status)
        actions=QtWidgets.QHBoxLayout();layout.addLayout(actions)
        self.settings=QtWidgets.QPushButton(text('冲洗配方…','Bath recipe…'));self.settings.clicked.connect(lambda:configure(self.session));actions.addWidget(self.settings)
        self.length=QtWidgets.QComboBox();self.length.addItem(text('每段 6 格','6 frames / strip'),6);self.length.addItem(text('每段 4 格','4 frames / strip'),4)
        self.length.currentIndexChanged.connect(self.segment_changed);actions.addWidget(self.length)
        self.lamp=QtWidgets.QPushButton(text('灯台观察','Light table'));self.lamp.clicked.connect(self.open_light_table);actions.addWidget(self.lamp);actions.addStretch()
        self.scan_settings=QtWidgets.QPushButton(text('扫描设置…','Scanner…'));self.scan_settings.clicked.connect(lambda:configure(self.session,'settings_scan'));actions.addWidget(self.scan_settings)
        self.develop=QtWidgets.QPushButton(text('整卷冲洗','Develop roll'));self.develop.clicked.connect(lambda:self.start('develop'));actions.addWidget(self.develop)
        self.scan=QtWidgets.QPushButton(text('整卷扫描','Scan roll'));self.scan.clicked.connect(lambda:self.start('scan'));actions.addWidget(self.scan)
        self.develop.setToolTip(text('开始后固定本卷药水；中断可续做，完成的格子不重新冲洗。空格也经过同一程序。','The bath freezes when started. Resume unfinished frames; developed frames are not redeveloped. Empty slots undergo the same process.'))
        self.refresh()

    def set_busy(self,busy):
        for button in self.findChildren(QtWidgets.QPushButton):
            if button is not self.cancel:button.setEnabled(not busy)
        self.rolls.setEnabled(not busy);self.cancel.setVisible(busy)
        if not busy:self.select()

    def refresh(self):
        current=str(self.session.path) if self.session else self.rolls.currentData()
        self.rolls.blockSignals(True);self.rolls.clear()
        for path in self.storage.glob('*/session.json'):
            try:
                session=RollSession(path);roll=session.data['roll']
                self.rolls.addItem(f"{roll.get('label') or roll['roll_id']} · {roll['capacity']}",str(path))
            except (ValueError,OSError):continue
        self.rolls.setCurrentIndex(max(0,self.rolls.findData(current)));self.rolls.blockSignals(False);self.select()

    def select(self):
        path=self.rolls.currentData();self.session=RollSession(path) if path else None
        self.tree.clear()
        if self.session:
            data=self.session.data
            for frame in data['roll']['frames']:
                done=data['frames'].get(str(frame['frame_index']))
                state=text('已扫描','Scanned') if done and done.get('cover') else text('已冲洗','Developed') if done else text('待冲洗','Undeveloped')
                QtWidgets.QTreeWidgetItem(self.tree,[str(frame['frame_index']),text('未曝光 · 海带','Unexposed') if frame['state']=='empty' else text('已曝光','Exposed'),state])
            phases={'sealed':('已封卷','Sealed'),'developing':('冲洗未完成 · 续做使用已冻结药水','Incomplete · resume with frozen chemistry'),'developed':('整卷已冲洗','Roll developed'),'scanned':('整卷已扫描','Roll scanned')}
            self.status.setText(text(*phases[data['phase']]))
        self.develop.setEnabled(bool(self.session and self.session.data['phase'] in ('sealed','developing')))
        self.scan.setEnabled(bool(self.session and self.session.data['phase'] in ('developed','scanned')))
        self.settings.setEnabled(bool(self.session and self.session.data['phase']=='sealed'))
        formed=bool(self.session and self.session.data['phase'] in ('developed','scanned'))
        self.lamp.setEnabled(formed);self.scan_settings.setEnabled(formed);self.length.setVisible(formed)
        self.display.setCurrentWidget(self.strips if formed else self.tree)
        if formed:
            self.length.blockSignals(True);self.length.setCurrentIndex(max(0,self.length.findData(data.get('strip_length',6))));self.length.blockSignals(False)
            self.strips.display(data,self.length.currentData())

    def segment_changed(self):
        if self.session and self.session.data['phase'] in ('developed','scanned'):
            try:
                self.session.data['strip_length']=self.length.currentData();self.session.save()
                self.strips.display(self.session.data,self.length.currentData())
            except OSError as exc:self.status.setText(str(exc))

    def open_light_table(self):
        if not self.session or self.session.data['phase'] not in ('developed','scanned'):return
        from film_foundry.ui.roll_strips import RollLightTableDialog
        window=RollLightTableDialog(self.session.data,self.strips.selected,self);self.light_tables.append(window)
        window.destroyed.connect(lambda:self.light_tables.remove(window) if window in self.light_tables else None)
        if self.present:self.present(window)
        else:window.show()

    def import_dialog(self):
        path,_=QtWidgets.QFileDialog.getOpenFileName(self,text('导入整卷','Import roll'),'','Electronic roll (*.ffroll *.ffroll.json)')
        if path:self.submit(lambda:import_roll(path,self.storage),self.imported)

    def imported(self,session):self.session=session;self.refresh()

    def import_legacy(self):
        paths,_=QtWidgets.QFileDialog.getOpenFileNames(self,text('选择一卷的全部潜影','Select all exposed frames of one roll'),'','Latents (*.darkroom_latent.npz)')
        if not paths:return
        capacity,ok=QtWidgets.QInputDialog.getItem(self,text('胶卷容量','Roll capacity'),text('封卷前标称张数','Nominal frame capacity'),[str(v) for v in PHONE_ROLL_CAPACITY_VALUES],editable=False)
        if not ok:return
        answer=QtWidgets.QMessageBox.question(self,text('确认曝光格齐全','Confirm complete selection'),text('已选齐本卷所有曝光格？未选中的格号将作为未曝光底片冲洗；漏传文件不能当空格。','Have you selected every exposed frame? Remaining positions will be developed as unexposed slots. Missing files are not empty slots.'))
        if answer!=QtWidgets.QMessageBox.StandardButton.Yes:return
        self.submit(lambda:import_roll(paths[0],self.storage,paths=paths,capacity=int(capacity),confirmed_complete=True),self.imported)

    def export_dialog(self):
        if not self.session:return
        path,_=QtWidgets.QFileDialog.getSaveFileName(self,text('导出电子胶卷','Export electronic roll'),'roll.ffroll','Electronic roll (*.ffroll)')
        if path:
            session=self.session;self.submit(lambda:session.export(path),lambda _:None)

    def start(self,stage):
        if self.session:self.configure(self.session,stage)

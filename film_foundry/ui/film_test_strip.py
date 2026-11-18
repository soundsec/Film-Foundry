"""Transactional film test strips; worker callbacks never touch Qt widgets."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

from film_foundry.app.control import CancelFlag,WorkflowCancelled
from film_foundry.app.film_test_strip import available_axes,build_variants,variant_config,FilmStripSession,trial_values
from film_foundry.ui.qt import QtCore,QtGui,QtWidgets
from film_foundry.ui.curve_editor import text
from film_foundry.ui.i18n import tr
from film_foundry.ui.comparison_view import ComparisonView,comparison_choice


_LABELS={
    'time_min':('冲洗时间 · min','Development time · min'),
    'temperature_c':('药液温度 · °C','Bath temperature · °C'),
    'concentration':('药液浓度 · ×','Bath concentration · ×'),
    'agitation':('搅拌强度','Agitation'),
    'agitation_time_s':('累计搅拌时间 · s','Agitation time · s'),
    'bath_transfer_s':('入浴变温时间 · s','Bath transition · s'),
    'exposure_ev':('曝光补偿 · EV','Exposure · EV'),
    'print_exposure_ev':('成片曝光 · EV','Output exposure · EV'),
    'negative_contrast':('底片反差','Negative contrast'),
    'print_contrast':('成片对比度','Output contrast'),
    'transmission_light_ev':('观片光源 · EV','Viewing light · EV'),
    'transmission_light_temperature_k':('观片色温 · K','Viewing temperature · K'),
}


def axis_label(axis):
    keys=[str(k) for k in axis.path if not isinstance(k,int)]
    key=keys[-1];label=text(*_LABELS[key]) if key in _LABELS else tr('label.'+key)
    if label=='label.'+key:label=key.replace('_',' ')
    stage={'film':text('曝光','Exposure'),'develop':text('冲洗','Development'),'scan':text('观察','Observation')}[axis.stage]
    if axis.path[0]=='bath':
        from film_foundry.ui.roll_process_dialog import ACTIONS
        stage=text(f'第 {axis.path[1]+1} 浴','Bath '+str(axis.path[1]+1))+' · '+text(*ACTIONS[axis.bath_action])
    suffix=''.join(f' [{i+1}]' for i in axis.path[1:] if isinstance(i,int)) if axis.path[0]!='bath' else ''
    return f'{stage} · {label}{suffix}'


def value_label(value):
    return f'{value[0]:g} EV · {value[1]:g} min' if isinstance(value,tuple) else f'{value:g}'


class _Signals(QtCore.QObject):
    result=QtCore.Signal(int,int,object,object)
    finished=QtCore.Signal(int,str,bool)


class FilmTestStripDialog(QtWidgets.QDialog):
    def __init__(self,path,mode,base,frozen,apply,parent=None,*,path_seed_offset=None,renderer=FilmStripSession,backend=None):
        super().__init__(parent);self.setWindowTitle(text('试条 / 包围','Test strip / bracket'))
        self.resize(1040,670);self.setMinimumSize(720,520)
        self.setProperty('disposablePreview',True)
        self.frame_settings=(1,0.)
        self.path=path;self.mode=mode;self.base=deepcopy(base);self.frozen=deepcopy(frozen)
        self.apply_result=apply;self.offset=path_seed_offset;self.renderer=renderer
        self.backend=backend;self.rotation=0 if backend else base.output.rotation_quarters
        self.axes=backend.axes if backend else available_axes(base,mode);self.busy=False;self.generation=0;self.closing=False
        self.cancel=CancelFlag();self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='film-test-strip')
        self.signals=_Signals();self.signals.result.connect(self._result);self.signals.finished.connect(self._finished)
        pool=self.pool;self.destroyed.connect(lambda *_:pool.shutdown(wait=False,cancel_futures=True))
        self.frames={};self.variants=[];self.reference=None;self.session=None
        layout=QtWidgets.QVBoxLayout(self)
        from pathlib import Path
        note=QtWidgets.QLabel(text('来源：','Source: ')+Path(path).name+'\n'+text('固定来源与随机身份。可比较单个参数，或同时比较曝光和第二个参数。默认沿用正式处理设置，试算尺寸由下方长边决定。',
            'Compare a single setting or a two-axis exposure matrix with the same source and seed. Trials use the formal processing settings by default at the selected size.'))
        note.setWordWrap(True);layout.addWidget(note)
        if backend:note.setText(backend.note)
        self.scope_note=QtWidgets.QLabel();self.scope_note.setWordWrap(True);self.scope_note.hide();layout.addWidget(self.scope_note)
        self.axis=QtWidgets.QComboBox();self.axis.setEditable(True);self.axis.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.NoInsert)
        self.axis.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored,QtWidgets.QSizePolicy.Policy.Fixed)
        self.axis.setMinimumContentsLength(20)
        for entry in self.axes:self.axis.addItem(backend.label(entry) if backend else axis_label(entry),entry)
        self.axis.completer().setFilterMode(QtCore.Qt.MatchFlag.MatchContains)
        self.axis.completer().setCompletionMode(QtWidgets.QCompleter.CompletionMode.PopupCompletion)
        layout.addWidget(self.axis)
        self.axis_note=QtWidgets.QLabel();self.axis_note.setWordWrap(True);layout.addWidget(self.axis_note)
        row=QtWidgets.QHBoxLayout();self.start=QtWidgets.QDoubleSpinBox();self.stop=QtWidgets.QDoubleSpinBox()
        for title,control in ((text('从','From'),self.start),(text('到','To'),self.stop)):
            control.setDecimals(5);control.setRange(-1e9,1e9);control.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons);row.addWidget(QtWidgets.QLabel(title));row.addWidget(control,1)
        self.count=QtWidgets.QSpinBox();self.count.setRange(2,9);self.count.setValue(5)
        self.count.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons)
        row.addWidget(QtWidgets.QLabel(text('条数','Trials')));row.addWidget(self.count)
        self.edge=QtWidgets.QComboBox()
        for value in (480,640,900):self.edge.addItem(str(value)+' px',value)
        self.edge.setCurrentIndex(1);row.addWidget(self.edge)
        self.run_button=QtWidgets.QPushButton(text('生成试条','Generate'));self.run_button.clicked.connect(self.generate);row.addWidget(self.run_button)
        layout.addLayout(row)
        row=QtWidgets.QHBoxLayout();layout.addLayout(row)
        self.spacing=QtWidgets.QComboBox()
        for key,zh,en in [('linear','等距','Linear'),('geometric','等比','Geometric'),('list','自定取值','Custom values')]:
            self.spacing.addItem(text(zh,en),key)
        row.addWidget(self.spacing)
        self.values=QtWidgets.QLineEdit();self.values.setPlaceholderText(text('用逗号分隔，例如 1, 2, 4','Comma-separated, e.g. 1, 2, 4'))
        self.values.hide();row.addWidget(self.values,1)
        self.draft=QtWidgets.QCheckBox(text('使用主预览草稿算法','Use display draft processing'))
        self.draft.setVisible(not backend);row.addWidget(self.draft)
        self.matrix=QtWidgets.QCheckBox(text('二维试条','Matrix'))
        self.matrix.setVisible(not backend and mode in ('full','develop'));row.addWidget(self.matrix)
        self.second=QtWidgets.QComboBox()
        for path in [('chemistry','time_min'),('chemistry','temperature_c'),('chemistry','concentration'),('look','negative_contrast'),('look','print_contrast')]:
            entry=next((a for a in self.axes if a.path==path),None)
            if entry:self.second.addItem(axis_label(entry),entry)
        self.second.hide();row.addWidget(self.second)
        self.times=QtWidgets.QLineEdit();self.times.setPlaceholderText(text('第二轴取值列表','Second-axis values'))
        self.times.hide();row.addWidget(self.times,1)
        def switch_matrix(active):
            if active:
                idx=next(i for i,entry in enumerate(self.axes) if entry.path==('look','exposure_ev'))
                self.axis.setCurrentIndex(idx)
                if self.second.currentData() is None:return
                t=self.second.currentData().current
                self.times.setText(', '.join(f'{t*k:g}' for k in (.8,1.,1.2)))
            self.second.setVisible(active);self.times.setVisible(active);self.axis.setEnabled(not active);self._invalidate()
        self.second.currentIndexChanged.connect(lambda:switch_matrix(self.matrix.isChecked()))
        self.matrix.toggled.connect(switch_matrix)
        self.spacing.currentIndexChanged.connect(lambda:self.values.setVisible(self.spacing.currentData()=='list'))
        for signal in (self.spacing.currentIndexChanged,self.values.textChanged,self.times.textChanged,self.draft.toggled):
            signal.connect(self._invalidate)
        self.viewer=ComparisonView();layout.addWidget(self.viewer,1)
        view_row=QtWidgets.QHBoxLayout();self.comparison=comparison_choice(self.viewer);view_row.addWidget(self.comparison)
        self.viewer.setToolTip(text('对比当前值参照与选中试条；单浴参照和各条使用同一独立药液定义。单图时按住可看参照，滚轮缩放、拖动平移。','Compare the current-value reference with the selected trial. Bath references use the same independent solution as the trials. Hold for the reference, scroll to zoom, drag to pan.'))
        if backend:self.viewer.setToolTip(backend.note)
        fit=QtWidgets.QPushButton(text('适合窗口','Fit'));fit.clicked.connect(self.viewer.fit);view_row.addWidget(fit)
        self.all_trials=QtWidgets.QPushButton(text('全部并排 / 局部…','Compare all / region…'))
        self.all_trials.clicked.connect(self.compare_all);view_row.addWidget(self.all_trials)
        self.selection_label=QtWidgets.QLabel();view_row.addWidget(self.selection_label);view_row.addStretch();layout.addLayout(view_row)
        self.tiles=QtWidgets.QListWidget();self.tiles.setViewMode(QtWidgets.QListView.ViewMode.IconMode)
        self.tiles.setFlow(QtWidgets.QListView.Flow.LeftToRight);self.tiles.setWrapping(False);self.tiles.setMovement(QtWidgets.QListView.Movement.Static)
        self.tiles.setIconSize(QtCore.QSize(120,70));self.tiles.setFixedHeight(126);self.tiles.setSpacing(4)
        self.tiles.currentRowChanged.connect(self.select);layout.addWidget(self.tiles)
        self.grid=QtWidgets.QTableWidget()
        self.grid.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.grid.horizontalHeader().setMinimumSectionSize(100)
        self.grid.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.grid.setMaximumHeight(220)
        self.grid.currentCellChanged.connect(lambda r,c,*_:self.tiles.setCurrentRow(r*self.grid.columnCount()+c) if r>=0 and c>=0 else None)
        layout.addWidget(self.grid);self.grid.hide()
        self.status=QtWidgets.QLabel();self.status.setWordWrap(True);layout.addWidget(self.status)
        buttons=QtWidgets.QHBoxLayout();self.cancel_button=QtWidgets.QPushButton(text('停止计算','Stop rendering'));self.cancel_button.clicked.connect(self.stop_render)
        self.use_button=QtWidgets.QPushButton(text('采用选中参数','Use selected value'));self.use_button.clicked.connect(self.adopt)
        close=QtWidgets.QPushButton(text('关闭','Close'));close.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button);buttons.addStretch();buttons.addWidget(self.use_button);buttons.addWidget(close);layout.addLayout(buttons)
        self.axis.currentIndexChanged.connect(self._axis_changed)
        self.axis.editTextChanged.connect(self._invalidate)
        for control in (self.start,self.stop,self.count):control.valueChanged.connect(self._invalidate)
        self.edge.currentIndexChanged.connect(self._invalidate)
        self._axis_changed();self._set_busy(False)

    def trial_label(self,value):
        if isinstance(value,tuple):
            return f'{value[0]:g} EV · {self.second.currentText().split(" · ",1)[-1]} {value[1]:g}'
        return value_label(value)

    def compare_all(self):
        from film_foundry.ui.trial_comparison import TrialComparison
        tiles = [(self.trial_label(value), ComparisonView.pixmap(self.frames[index]).transformed(
            QtGui.QTransform().rotate(90*self.rotation)))
            for index,(value,_) in enumerate(self.variants) if index in self.frames]
        if tiles:
            columns = len({v[0] for v,_ in self.variants}) if self.matrix.isChecked() else None
            indices=[i for i in range(len(self.variants)) if i in self.frames]
            def formal(parent,region):
                from film_foundry.ui.formal_region import FormalRegion
                from film_foundry.ui.inline_editor import present_inline_editor
                selected=self.tiles.currentRow()
                start=indices.index(selected) if selected in indices else 0
                choices=[(label,self.variants[index][1]) for (label,_),index in zip(tiles,indices)]
                bounds=region.getCoords() if region.width()<1 or region.height()<1 else None
                native=FormalRegion(parent,self.session,choices,getattr(self,'formal_scaled',False),[pixmap for _,pixmap in tiles],bounds)
                native.choice.setCurrentIndex(start)
                present_inline_editor(parent,native)
            dialog=TrialComparison(tiles,columns,self,formal=formal)
            def choose(n,adopt):
                i=indices[n];self.tiles.setCurrentRow(i)
                if self.matrix.isChecked():self.grid.setCurrentCell(*divmod(i,columns))
                if adopt:dialog.accept();self.adopt()
            dialog.chosen.connect(choose)
            dialog.exec()

    def _invalidate(self,*_):
        self.all_trials.setEnabled(False)
        self.grid.clear();self.grid.setRowCount(0);self.grid.setVisible(self.matrix.isChecked());self.tiles.setVisible(not self.matrix.isChecked())
        self.variants=[];self.frames={};self.reference=None;self.tiles.clear();self.viewer.clear_image()
        self.selection_label.clear()
        self.use_button.setEnabled(False);self.status.setText(text('设置范围后生成；原工作尚未改变。','Set the range and generate. Your work is unchanged.'))

    def _axis_changed(self,*_):
        axis=self.axis.currentData()
        if axis is None:return
        from film_foundry.contracts.process_steps import CONDITION_RANGES
        low,high=(-1e9,1e9)
        if axis.path[0]=='bath' or axis.path[0]=='chemistry' and len(axis.path)==2:
            low,high=CONDITION_RANGES.get(axis.path[-1],(low,high))
        if self.backend:low,high=self.backend.bounds(axis)
        span=2 if axis.integer else .5 if axis.path[-1].endswith('_ev') else max(abs(axis.current)*.2,.1)
        for control,value in ((self.start,axis.current-span),(self.stop,axis.current+span)):
            control.setDecimals(0 if axis.integer else 5);control.setRange(low,high);control.setValue(value)
        detail=text(f'当前值 {axis.current:g}','Current value '+f'{axis.current:g}')
        detail+=text('。单浴参照与各条使用同一独立药液定义，采用时一并保留。','; the bath reference and trials share an independent solution, retained on adoption.') if axis.path[0]=='bath' else text('。已关闭的功能或被单浴覆盖的公共参数可能不生效。','; disabled features or common values overridden by a bath may have no effect.')
        if self.backend:detail=text(f'当前值 {axis.current:g}；','Current value '+f'{axis.current:g}; ')+self.backend.scope(axis)
        self.axis_note.setText(detail);self.axis.setToolTip('.'.join(map(str,axis.path)))
        self._invalidate()

    def _set_busy(self,busy):
        self.all_trials.setEnabled(not busy and bool(self.frames))
        self.busy=busy;self.setProperty('workspaceBusy',busy)
        for control in (self.axis,self.start,self.stop,self.count,self.edge,self.run_button,self.spacing,self.values,self.matrix,self.times,self.second,self.draft):
            control.setEnabled(not busy)
        self.axis.setEnabled(not busy and not self.matrix.isChecked())
        self.cancel_button.setEnabled(busy);self.use_button.setEnabled(not busy and self.tiles.currentRow() in self.frames)

    def generate(self):
        if self.busy:return
        try:
            axis=self.axis.currentData()
            if axis is None or self.axis.currentText()!=self.axis.itemText(self.axis.currentIndex()):raise ValueError(text('请从列表选择参数。','Choose a parameter from the list.'))
            values = trial_values(self.start.value(), self.stop.value(), self.count.value(),
                                  self.spacing.currentData(), self.values.text())
            if self.backend:
                variants,reference,session=self.backend.prepare(axis,self.start.value(),self.stop.value(),self.count.value(),self.edge.currentData(),values=values)
            else:
                variants=build_variants(self.base,self.mode,axis,self.start.value(),self.stop.value(),self.count.value(),values=values)
                reference=variant_config(self.base,axis,axis.current) if axis.path[0]=='bath' else deepcopy(self.base)
                session=self.renderer(self.path,self.mode,self.frozen,long_edge=self.edge.currentData(),path_seed_offset=self.offset)
                session.frame_settings=self.frame_settings
                session.draft = self.draft.isChecked()
                if self.matrix.isChecked():
                    times = trial_values(0,0,2,'list',self.times.text())
                    if len(times)*len(values)>25:raise ValueError(text('矩阵上限 25 格。','Limit the matrix to 25 cells.'))
                    time_axis = self.second.currentData()
                    if time_axis is None:raise ValueError(text('当前配方没有可用的第二轴。','No second axis available for this recipe.'))
                    variants = [((ev, minutes), cfg) for minutes in times for ev, exposure_cfg in variants
                                for _,cfg in build_variants(exposure_cfg,self.mode,time_axis,0,0,2,values=[minutes])]
        except Exception as exc:self.status.setText(str(exc));return
        self._invalidate();self.variants=variants;self.session=session;self.generation+=1;generation=self.generation
        self.cancel=CancelFlag();cancel=self.cancel;self._set_busy(True)
        if self.matrix.isChecked():
            self.grid.setColumnCount(len(values));self.grid.setRowCount(len(times))
            self.grid.setHorizontalHeaderLabels([f'{v:g} EV' for v in values])
            axis_name=self.second.currentText().split(' · ',1)[-1]
            unit=axis_name.rsplit(' · ',1)[1] if ' · ' in axis_name else ''
            self.grid.setVerticalHeaderLabels([f'{v:g} {unit}'.strip() for v in times])
            for r in range(len(times)):self.grid.verticalHeaderItem(r).setToolTip(axis_name)
            for r in range(len(times)):
                self.grid.setRowHeight(r,70)
                for c in range(len(values)):self.grid.setItem(r,c,QtWidgets.QTableWidgetItem())
        for value,_ in variants:
            item=QtWidgets.QListWidgetItem(self.trial_label(value)+'\n'+text('等待','Waiting'));item.setSizeHint(QtCore.QSize(138,104));self.tiles.addItem(item)
        self.status.setText(text('正在计算当前参数参照…','Rendering the current-value reference…'))
        signals=self.signals
        reference_value = (axis.current,self.second.currentData().current) if self.matrix.isChecked() else axis.current
        requests=[(reference_value,reference)]
        destinations=[[-1]]
        for index,(value,cfg) in enumerate(variants):
            if cfg == reference:
                destinations[0].append(index)
            else:
                destinations.append([index])
                requests.append((value,cfg))
        def deliver(i,value,pixels):
            for index in destinations[i]:
                signals.result.emit(generation,index,value,pixels)
        def run():
            error='';cancelled=False
            try:session.run(requests,cancel,deliver)
            except WorkflowCancelled:cancelled=True
            except Exception as exc:error=str(exc)
            try:signals.finished.emit(generation,error,cancelled)
            except RuntimeError:pass
        self.pool.submit(run)

    def _result(self,generation,index,value,pixels):
        if generation!=self.generation or self.closing:return
        if index<0:self.reference=pixels;return
        self.frames[index]=pixels;item=self.tiles.item(index)
        thumbnail=ComparisonView.pixmap(pixels).transformed(QtGui.QTransform().rotate(self.rotation*90))
        item.setIcon(QtGui.QIcon(thumbnail));item.setText(self.trial_label(value))
        if self.matrix.isChecked():
            r,c=divmod(index,self.grid.columnCount())
            cell=QtWidgets.QLabel()
            cell.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            cell.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            cell.setPixmap(thumbnail.scaled(120,64,QtCore.Qt.AspectRatioMode.KeepAspectRatio,QtCore.Qt.TransformationMode.SmoothTransformation))
            cell.setToolTip(self.trial_label(value))
            self.grid.setCellWidget(r,c,cell)
            if self.grid.currentRow()<0:self.grid.setCurrentCell(r,c)
        if self.tiles.currentRow()<0:self.tiles.setCurrentRow(index)
        self.status.setText(text(f'已完成 {len(self.frames)} / {len(self.variants)} 条',f'Rendered {len(self.frames)} / {len(self.variants)}'))

    def _finished(self,generation,error,cancelled):
        if generation!=self.generation:return
        self._set_busy(False)
        self.status.setText(text('失败：','Failed: ')+error if error else text('已停止；可采用已完成的试条。','Stopped; completed trials can be adopted.') if cancelled else text('点选试条放大查看；采用只改参数，可在工作台撤回。','Select a trial to inspect. Adoption changes settings only and can be undone in the workbench.'))
        if error:self.use_button.setEnabled(False)
        if self.closing:self.reject()

    def select(self,index):
        if index in self.frames:
            self.viewer.set_pair(self.reference,self.frames[index])
            self.viewer.set_rotation(self.rotation)
            value=self.variants[index][0]
            self.selection_label.setText(text('选中 ','Selected ')+self.trial_label(value))
        self.use_button.setEnabled(not self.busy and index in self.frames)

    def stop_render(self):
        self.cancel.cancel();self.cancel_button.setEnabled(False)
        self.status.setText(text('将在当前工序计算结束后停止…','Stopping after the current operation…') if self.backend else
            text('正在停止，等待计算检查点…','Stopping at the next computation checkpoint…'))

    def adopt(self):
        index=self.tiles.currentRow()
        if self.busy or index not in self.frames:return
        try:
            self.session.check_source()
            self.apply_result(deepcopy(self.variants[index][1]),self.axis.currentData())
        except Exception as exc:self.status.setText(str(exc));return
        self.accept()

    def done(self,result):
        if self.busy:
            self.closing=True;self.stop_render();return
        self.pool.shutdown(wait=False,cancel_futures=True);super().done(result)

    def closeEvent(self,event):
        if self.busy:
            self.closing=True;self.stop_render();event.ignore();return
        super().closeEvent(event)


def open_test_strip(owner):
    from pathlib import Path
    from dataclasses import asdict
    from film_foundry.model.config import DarkroomConfig
    from film_foundry.app.source_seed import offset_for
    from film_foundry.app.workflow import iter_latent_files
    from film_foundry.ui.inline_editor import present_inline_editor
    if owner._active_roll is not None or owner._processing or owner._preview_running:
        owner.status.set(text('请在当前预览/处理完成后打开单片试条。','Open single-frame trials after the active preview or task finishes.'));return
    existing=getattr(owner,'_test_strip_dialog',None)
    if existing is not None:existing.raise_();existing.activateWindow();return existing
    try:
        mode=owner.pipeline_mode.get()
        if mode in ('full','expose','develop'):path=owner._first_input_image()
        elif mode=='develop_latent':
            paths=iter_latent_files(Path(owner.negative_path.get()));path=paths[0] if paths else None
        else:path=owner._first_negative_file()
        if path is None:raise ValueError(text('请先选择可预览的来源。','Choose a previewable source first.'))
        owner.edit_tools.record();before=owner.edit_tools.capture()
        base=DarkroomConfig.from_dict(before['config'])
        frozen=owner._freeze_incident_config(deepcopy(base))
    except Exception as exc:owner.status.set(str(exc));return
    identity=(mode,owner.input_path.get(),owner.negative_path.get(),tuple(owner._selected_photos))
    def apply(config,axis):
        current=(owner.pipeline_mode.get(),owner.input_path.get(),owner.negative_path.get(),tuple(owner._selected_photos))
        if current!=identity or owner.edit_tools.capture()!=before or owner._processing:
            raise ValueError(text('工作或参数已改变，请关闭并重新生成试条。','The work or settings changed; reopen and regenerate the strip.'))
        snapshot=deepcopy(before);snapshot['config']=asdict(config)
        if axis.stage=='scan' or dialog.matrix.isChecked() and dialog.second.currentData().stage=='scan':snapshot['link_scanner']=False
        if axis.stage=='develop' or dialog.matrix.isChecked() and dialog.second.currentData().stage=='develop':snapshot['auto_develop']=False
        owner.edit_tools.apply(snapshot);owner.edit_tools.record()
        owner.status.set(text('已采用试条参数，可撤回；正式执行才保存结果。','Trial settings adopted; Undo is available. Formal execution saves the result.'))
    dialog=FilmTestStripDialog(path,mode,base,frozen,apply,owner.film_workspace,path_seed_offset=offset_for(path,owner._path_seed_offsets))
    dialog.frame_settings=owner._focused_frame_settings(path)
    dialog.formal_scaled=bool(owner.run_as_preview.get())
    if len(owner._selected_photos)>1:
        dialog.scope_note.setText(text('批量工作：只比较当前来源（含逐片曝光）；采用后更新整批共用配方。','Batch work: compares the focused source with its frame exposure offset; adoption updates the recipe shared by the batch.'));dialog.scope_note.show()
    owner._test_strip_dialog=dialog;owner._preview_timer.stop()
    def finished(*_):
        owner._test_strip_dialog=None;owner._sync_activity_ui()
        if owner._preview_pending and owner.auto_preview.get():owner._preview_timer.start(owner.PREVIEW_DEBOUNCE_MS)
    dialog.finished.connect(finished)
    present_inline_editor(owner.film_workspace,dialog)
    return dialog

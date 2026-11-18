"""Persistent parameter shortcuts. Edits use the active work's normal history."""
from copy import deepcopy
from dataclasses import asdict
import json

from film_foundry.model.config import DarkroomConfig
from film_foundry.ui.qt import QtCore, QtWidgets
from film_foundry.ui.curve_editor import text
from film_foundry.ui.i18n import tr
from film_foundry.ui.preferences import load_preferences, save_preferences


def leaves(value, path=(), pinned=()):
    if isinstance(value,(dict,list,tuple)) and value and '.'.join(map(str,path)) in pinned:
        yield path,value
    if isinstance(value, dict) and value:
        for name, child in value.items():
            yield from leaves(child, (*path, name),pinned)
    elif isinstance(value, (list, tuple)) and value:
        for index, child in enumerate(value):
            yield from leaves(child, (*path, index),pinned)
    else:
        yield path, value


def film_editable(owner, path):
    from film_foundry.app.film_test_strip import _stage
    if owner._processing or owner._active_roll is not None:
        return False
    mode = owner.pipeline_mode.get()
    bank = path[0]
    if bank in ('processing','output','debug_output','comparison_grid','save_sidecar','fast_mode'):
        return True
    if bank in ('random_seed','seed_strategy'):
        return mode in ('full','develop','expose','develop_latent')
    if bank in ('enable_grain','enable_subtractive'):
        return mode in ('full','develop','develop_latent')
    if bank == 'process_control_mode':
        return mode in ('full','develop','develop_latent')
    stage = _stage(path) if len(path)>1 else 'film'
    return stage in {'full':('film','develop','scan'), 'expose':('film',), 'develop':('film','develop'),
                     'scan':('scan',), 'develop_latent':('develop',)}.get(mode,())


def set_film_parameter(owner, path, value):
    from film_foundry.app.film_test_strip import _set_path
    from film_foundry.core.runtime_validate import _validate_develop_runtime, _validate_scan_runtime
    if not film_editable(owner,path):
        raise ValueError(text('此阶段已冻结或未参与当前任务。','This stage is frozen or inactive.'))
    owner.edit_tools.record()
    before = owner.edit_tools.capture()
    old = before['config']
    for key in path:
        old=old[key]
    json.dumps(value,allow_nan=False)
    if (type(old) is int and type(value) is not int or
        type(old) is float and type(value) not in (int,float) or
        type(old) is bool and type(value) is not bool):
        raise ValueError(text('请保持此参数的数值类型。','Use the same numeric type for this parameter.'))
    updated = deepcopy(before)
    data = _set_path(updated['config'],path,value)
    if path[:3] == ('chemistry','effect_intent','amounts'):
        data['chemistry'][path[-1]] = value
    elif len(path)==2 and path[0]=='chemistry':
        amounts = (data['chemistry'].get('effect_intent') or {}).get('amounts',{})
        if path[1] in amounts:
            amounts[path[1]]=value
    config = DarkroomConfig.from_dict(data)
    mode = owner.pipeline_mode.get()
    if mode != 'scan':
        _validate_develop_runtime(config)
    if mode in ('full','scan'):
        _validate_scan_runtime(config)
    updated['config'] = asdict(config)
    if path[0]=='scanner':
        updated['link_scanner']=False
    if path[0]=='chemistry':
        updated['auto_develop']=False
    try:
        owner.edit_tools.apply(updated)
        effective = asdict(owner._config())
        for key in path:
            effective = effective[key]
        if json.dumps(effective,sort_keys=True) != json.dumps(value,sort_keys=True) and effective != value:
            raise ValueError(text('当前工艺或联动控件控制此值，请在相应工序中修改。','This value is controlled by the current process or a linked setting.'))
    except Exception:
        owner.edit_tools.apply(before)
        raise
    owner.edit_tools.record()


class ParameterPins(QtWidgets.QDialog):
    def __init__(self, owner, *, film):
        super().__init__(owner.root.qt if film else owner)
        self.owner, self.film = owner, film
        self.scope = 'film' if film else owner.kind
        saved = load_preferences().get('parameter_pins',{}).get(self.scope,[])
        self.pins = [key for key in saved if isinstance(key,str)] if isinstance(saved,list) else []
        self.setWindowTitle(text('钉选参数','Pinned parameters'))
        self.resize(640,520)
        layout = QtWidgets.QVBoxLayout(self)
        bar = QtWidgets.QHBoxLayout()
        layout.addLayout(bar)
        self.filter = QtWidgets.QLineEdit()
        self.filter.setPlaceholderText(text('搜索参数名或字段路径','Search by parameter name or field path'))
        bar.addWidget(self.filter,1)
        self.all = QtWidgets.QCheckBox(text('显示全部参数','Show all parameters'))
        self.all.setChecked(not self.pins)
        bar.addWidget(self.all)
        self.list = QtWidgets.QTreeWidget()
        self.list.setRootIsDecorated(False)
        self.list.setHeaderLabels([text('参数','Parameter'),text('当前值','Current value')])
        self.list.header().setStretchLastSection(False)
        self.list.header().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.list.header().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.list,1)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        actions=QtWidgets.QHBoxLayout()
        layout.addLayout(actions)
        self.pin=QtWidgets.QPushButton(text('钉选 / 取消钉选','Pin / unpin'))
        self.edit=QtWidgets.QPushButton(text('修改所选参数…','Edit selected parameter…'))
        actions.addWidget(self.pin);actions.addWidget(self.edit);actions.addStretch()
        close=QtWidgets.QPushButton(text('关闭','Close'));close.clicked.connect(self.accept);actions.addWidget(close)
        self.pin.clicked.connect(self.toggle)
        self.edit.clicked.connect(self.edit_current)
        self.list.itemDoubleClicked.connect(lambda *_:self.edit_current())
        self.list.currentItemChanged.connect(self.selection)
        self.filter.textChanged.connect(self.refresh)
        self.all.toggled.connect(self.refresh)
        self.refresh()

    def refresh(self,*_):
        from film_foundry.ui.material_workspace_state import controls
        prior=self.list.currentItem()
        active=prior.data(0,QtCore.Qt.ItemDataRole.UserRole) if prior else None
        self.data={}
        self.list.clear()
        if self.film:
            values=leaves(asdict(self.owner._config()),pinned=set(self.pins))
        else:
            from itertools import chain
            from film_foundry.app.material_scan import DEFAULTS
            self.controls=controls(self.owner)
            scan={**DEFAULTS,**self.owner.session.scan_adjustments}
            values=chain((((key,),self.control_value(control)) for key,control in self.controls.items()),
                         leaves(scan,('scan',),set(self.pins)))
        query=self.filter.text().strip().casefold()
        for path,value in values:
            name='.'.join(map(str,path))
            self.data[name]=(path,value)
            if not self.all.isChecked() and not query and name not in self.pins:continue
            field=next((part for part in reversed(path) if isinstance(part,str)),name).split('.')[-1]
            label=tr('label.'+field)
            title=name if label=='label.'+field else label+'  ·  '+name
            if query not in title.casefold():continue
            item=QtWidgets.QTreeWidgetItem(self.list,[('★ ' if name in self.pins else '')+title,str(value)[:64]])
            item.setToolTip(0,name);item.setData(0,QtCore.Qt.ItemDataRole.UserRole,name)
            if name==active:self.list.setCurrentItem(item)
        if self.list.currentItem() is None and self.list.topLevelItemCount():self.list.setCurrentItem(self.list.topLevelItem(0))
        self.selection()

    @staticmethod
    def control_value(control):
        if isinstance(control,QtWidgets.QComboBox):return control.currentText()
        if isinstance(control,QtWidgets.QCheckBox):return control.isChecked()
        return control.value()

    def key(self):
        item=self.list.currentItem()
        return item.data(0,QtCore.Qt.ItemDataRole.UserRole) if item else None

    def selection(self,*_):
        name=self.key()
        self.pin.setEnabled(name is not None)
        editable=False
        if name in self.data:
            editable=film_editable(self.owner,self.data[name][0]) if self.film else not self.owner.busy and (name not in self.controls or self.controls[name].isEnabled())
        self.edit.setEnabled(editable)

    def toggle(self):
        name=self.key()
        if not name:return
        pins=[key for key in self.pins if key!=name] if name in self.pins else [*self.pins,name]
        try:save_preferences(parameter_pins={self.scope:pins})
        except OSError as exc:self.status.setText(str(exc));return
        self.pins=pins;self.refresh()
        if hasattr(self.owner,"common_controls"):self.owner.common_controls.rebuild()

    def edit_current(self):
        name=self.key()
        if not name or not self.edit.isEnabled():return
        path,old=self.data[name]
        control=None if self.film else self.controls.get(name)
        try:
            if isinstance(control,QtWidgets.QComboBox):
                choices=[control.itemText(i) for i in range(control.count())]
                value,ok=QtWidgets.QInputDialog.getItem(self,name,name,choices,control.currentIndex(),False)
            elif type(old) is bool:
                value,ok=QtWidgets.QInputDialog.getItem(self,name,name,['false','true'],int(old),False)
                value=value=='true'
            else:
                value,ok=QtWidgets.QInputDialog.getText(self,name,name,text=old if isinstance(old,str) else json.dumps(old,ensure_ascii=False))
                if ok and not isinstance(old,str):value=json.loads(value)
            if not ok:return
            if self.film:set_film_parameter(self.owner,path,value)
            elif not self.owner.busy and path[0]=='scan':
                from film_foundry.app.film_test_strip import _set_path
                from film_foundry.app.material_scan import DEFAULTS, normalize_adjustments
                values={**DEFAULTS,**self.owner.session.scan_adjustments}
                adjusted=normalize_adjustments(_set_path(values,path[1:],value))
                self.owner.edit_tools.record_draft()
                self.owner.session.scan_adjustments=adjusted
                self.owner.edit_tools.record_draft()
                self.owner.request_preview()
            elif not self.owner.busy and control.isEnabled():
                self.owner.edit_tools.record_draft()
                if isinstance(control,QtWidgets.QComboBox):control.setCurrentIndex(control.findText(value))
                elif isinstance(control,QtWidgets.QCheckBox):control.setChecked(value)
                else:
                    if not isinstance(value,(int,float)) or isinstance(value,bool) or not control.minimum()<=value<=control.maximum():
                        raise ValueError(text('数值超出控件范围。','Value outside the control range.'))
                    if isinstance(control,QtWidgets.QSpinBox):
                        if int(value)!=value:
                            raise ValueError(text('请输入整数。','Enter a whole number.'))
                        value=int(value)
                    control.setValue(value)
                self.owner.edit_tools.record_draft()
            self.status.clear()
        except (ValueError,TypeError,KeyError,IndexError) as exc:self.status.setText(str(exc))
        self.refresh()


def open_pins(owner, *, film):
    dialog=ParameterPins(owner,film=film)
    try:dialog.exec()
    finally:dialog.deleteLater()

def control_caption(widget):
    if widget is None:return ''
    if widget.accessibleName():return widget.accessibleName()
    if isinstance(widget,QtWidgets.QAbstractButton) and widget.text():return widget.text()
    target=widget;host=widget.parentWidget()
    while host is not None:
        layout=host.layout()
        if isinstance(layout,QtWidgets.QFormLayout):
            caption=layout.labelForField(target)
            if isinstance(caption,QtWidgets.QLabel):return caption.text()
        target=host;host=host.parentWidget()
    return ''


def workbench_labels(owner,film):
    if film:
        items={}
        for path,key in CommonControls.fields.items():
            field=owner.fields.get(key)
            widget=getattr(field[-1] if isinstance(field,tuple) else field,'qt',None)
            if widget is not None:items[path]=widget
    else:
        from film_foundry.ui.material_workspace_state import controls
        items=controls(owner)
    return {key:caption for key,widget in items.items() if (caption:=control_caption(widget))}


class CommonControls(QtWidgets.QGroupBox):
    """Mirror the workbench's existing numeric, choice and toggle controls."""
    # Configuration paths with a directly bound workbench control.
    fields = {'chemistry.agitation': 'agitation',
              'chemistry.chemical_stain': 'chemical_stain',
              'chemistry.compensation': 'compensation',
              'chemistry.concentration': 'developer_concentration',
              'chemistry.developer_exhaustion': 'exhaustion',
              'chemistry.developer_type': 'developer_type',
              'chemistry.development_adjacency_radius': 'development_adjacency_radius',
              'chemistry.development_adjacency_strength': 'development_adjacency_strength',
              'chemistry.dye_damage_strength': 'dye_damage_strength',
              'chemistry.exposure_occlusion_debris': 'exposure_occlusion_debris',
              'chemistry.exposure_occlusion_dust': 'exposure_occlusion_dust',
              'chemistry.exposure_occlusion_hair': 'exposure_occlusion_hair',
              'chemistry.exposure_occlusion_insect': 'exposure_occlusion_insect',
              'chemistry.fixer_exhaustion': 'fixer_exhaustion',
              'chemistry.fixer_type': 'fixer_type',
              'chemistry.frame_long_edge_mm': 'frame_long_edge_mm',
              'chemistry.frame_size': 'frame_size',
              'chemistry.light_leak_strength': 'light_leak',
              'chemistry.process_variation': 'process_variation',
              'chemistry.push_stops': 'push',
              'chemistry.reticulation_strength': 'reticulation_strength',
              'chemistry.silver_plating': 'silver_plating',
              'chemistry.silver_retention': 'silver_retention',
              'chemistry.temperature_c': 'develop_temperature',
              'chemistry.time_min': 'develop_time',
              'chemistry.uneven_development': 'uneven_development',
              'comparison_grid': 'comparison_grid',
              'debug_output': 'debug_output',
              'enable_grain': 'enable_grain',
              'enable_halation': 'enable_halation',
              'enable_mtf': 'enable_mtf',
              'fast_mode': 'fast_mode',
              'film.material_degradation': 'material_degradation',
              'film.spatial_reference_short_edge_mm': 'spatial_reference_mm',
              'film_border.imprint_text': 'imprint_text',
              'film_border.imprint_time': 'imprint_time',
              'look.digital_artifact_suppression': 'artifact_suppression',
              'look.emulsion_mtf_strength': 'emulsion_mtf',
              'look.exposure_ev': 'exposure_ev',
              'look.grain_multiplier': 'grain',
              'look.grain_size_multiplier': 'grain_size',
              'look.halation_edge_compensation': 'halation_edge',
              'look.halation_multiplier': 'halation',
              'look.halation_sensitivity': 'halation_sensitivity',
              'look.negative_contrast': 'negative_contrast',
              'look.print_contrast': 'print_contrast',
              'look.print_exposure_ev': 'print_exposure',
              'output.anti_banding_strength': 'anti_banding',
              'output.export_layer_pack': 'export_layer_pack',
              'output.export_plate_set': 'export_plate_set',
              'output.export_transparent_plate': 'export_transparent_plate',
              'output.format': 'output_format',
              'output.save_scanner_raw': 'save_scanner_raw',
              'output.scanner_raw_border_percent': 'scanner_raw_border',
              'processing.quality_mode': 'quality_mode',
              'save_sidecar': 'save_sidecar',
              'scanner.include_clear_base_border': 'include_clear_base_border',
              'scanner.invert_transmission': 'scan_invert',
              'scanner.light_table_ev': 'light_table_ev',
              'scanner.light_table_temperature_k': 'light_table_temperature',
              'scanner.negative_backlight_ev': 'light_table_ev',
              'scanner.negative_backlight_temperature_k': 'light_table_temperature',
              'scanner.negative_channel_compensation_enabled': 'negative_channel_compensation',
              'scanner.negative_channel_compensation_strength': 'negative_channel_compensation_strength',
              'scanner.positive_scan_color_control_strength': 'positive_color_control',
              'scanner.print_color_shift.0': 'print_shift_r',
              'scanner.print_color_shift.1': 'print_shift_g',
              'scanner.print_color_shift.2': 'print_shift_b',
              'scanner.projection_black_adaptation': 'projection_black_adaptation',
              'scanner.projection_white_softness': 'projection_white_softness',
              'scanner.remove_base_mask': 'scan_remove_base',
              'scanner.scan_normalize': 'scan_normalize',
              'scanner.scan_normalize_strength': 'scan_strength',
              'scanner.scan_saturation': 'saturation',
              'scanner.transmission_light_ev': 'light_table_ev',
              'scanner.transmission_light_temperature_k': 'light_table_temperature',
              'seed_strategy': 'seed_strategy'}

    def __init__(self, owner, film, layout):
        super().__init__(text('常用','Favorites'))
        self.owner=owner;self.film=film;self.scope='film' if film else owner.kind
        layout_inside=QtWidgets.QVBoxLayout(self);layout_inside.setContentsMargins(2,1,2,2)
        self.scroll=QtWidgets.QScrollArea();self.scroll.setWidgetResizable(True);self.scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        content=QtWidgets.QWidget();self.body=QtWidgets.QFormLayout(content);self.body.setContentsMargins(4,2,4,2)
        self.scroll.setWidget(content);layout_inside.addWidget(self.scroll)
        layout.insertWidget(0,self)
        self.controls=[]
        self.sync_timer=QtCore.QTimer(self);self.sync_timer.setInterval(350)
        self.sync_timer.timeout.connect(self.sync)
        self.rebuild()

    def rebuild(self):
        while self.body.rowCount():self.body.removeRow(0)
        self.controls=[];self.extra=[]
        saved=load_preferences().get('parameter_pins',{}).get(self.scope,[])
        if not isinstance(saved,list):saved=[]
        from film_foundry.ui.material_workspace_state import controls
        from film_foundry.ui.control_reset import reset_button
        source={} if self.film else controls(self.owner)
        for name in saved:
            if not isinstance(name,str):continue
            field=self.fields.get(name) if self.film else name.rsplit('.',1)[-1]
            widgets=self.owner.fields.get(field) if self.film else None
            slider=None
            target=source.get(name)
            if target is not None and not isinstance(target,QtWidgets.QWidget):target=None
            if isinstance(widgets,tuple):
                slider=widgets[0].qt;target=widgets[-1].qt
            elif widgets is not None:target=getattr(widgets,'qt',None)
            if target is not None and hasattr(target,'slider'):slider=target.slider
            target=getattr(target,'spin',target)
            label=control_caption(target)
            if not label:
                field=field or name.rsplit('.',1)[-1]
                from film_foundry.ui.film_test_strip import _LABELS
                label=text(*_LABELS[field]) if field in _LABELS else tr('label.'+field)
                if label.startswith('label.'):label=field.replace('_',' ')
            if isinstance(target,(QtWidgets.QDoubleSpinBox,QtWidgets.QSpinBox)):
                editor=QtWidgets.QDoubleSpinBox() if isinstance(target,QtWidgets.QDoubleSpinBox) else QtWidgets.QSpinBox()
                if isinstance(editor,QtWidgets.QDoubleSpinBox):editor.setDecimals(target.decimals())
                editor.setRange(target.minimum(),target.maximum());editor.setSingleStep(target.singleStep());editor.setSuffix(target.suffix())
                editor.setKeyboardTracking(False)
                editor.valueChanged.connect(target.setValue)
                read,write=target.value,editor.setValue
            elif isinstance(target,QtWidgets.QComboBox):
                editor=QtWidgets.QComboBox();editor.setModel(target.model())
                editor.currentIndexChanged.connect(target.setCurrentIndex)
                read,write=target.currentIndex,editor.setCurrentIndex
            elif isinstance(target,QtWidgets.QCheckBox):
                editor=QtWidgets.QCheckBox();editor.toggled.connect(target.setChecked)
                read,write=target.isChecked,editor.setChecked
            else:
                path=tuple(int(part) if part.isdigit() else part for part in name.split('.'))
                value=self.extra_value(path)
                if type(value) in (bool,int,float,str):
                    self.add_extra(label,path,value)
                    continue
                jump=QtWidgets.QPushButton(label+'…')
                jump.clicked.connect(lambda _,key=name:self.advanced(key))
                self.body.addRow(jump)
                continue
            row=QtWidgets.QWidget();bar=QtWidgets.QHBoxLayout(row);bar.setContentsMargins(0,0,0,0)
            if slider is not None and isinstance(slider,QtWidgets.QSlider):
                mirror=QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
                mirror.setRange(slider.minimum(),slider.maximum());mirror.valueChanged.connect(slider.setValue)
                bar.addWidget(mirror,1);self.controls.append((slider,mirror,slider.value,mirror.setValue))
            bar.addWidget(editor);editor.setAccessibleName(label)
            original=getattr(target,'reset_button',None)
            if original is None and self.film and isinstance(widgets,tuple):
                grid=target.parentWidget().layout()
                idx=grid.indexOf(target)
                if isinstance(grid,QtWidgets.QGridLayout) and idx>=0:
                    pos=grid.getItemPosition(idx);item=grid.itemAtPosition(pos[0],4)
                    original=item.widget() if item else None
            if original is not None and hasattr(original,'click'):
                reset=reset_button(row);reset.clicked.connect(original.click);bar.addWidget(reset)
                self.controls.append((original,reset,None,None))
            self.body.addRow(label,row)
            self.controls.append((target,editor,read,write))
        self.scroll.setFixedHeight(min(150,self.body.sizeHint().height()+4))
        self.setVisible(bool(self.body.rowCount()))
        if self.body.rowCount() and self.isVisible():
            self.sync()
            self.sync_timer.start()
        else:self.sync_timer.stop()

    def showEvent(self,event):
        super().showEvent(event)
        if getattr(self,'sync_timer',None) is not None and self.body.rowCount():
            self.sync()
            self.sync_timer.start()

    def hideEvent(self,event):
        super().hideEvent(event)
        if getattr(self,'sync_timer',None) is not None:self.sync_timer.stop()

    def extra_value(self,path,config=None):
        if self.film:
            if config is None:config=self.owner._config()
            value=config
        else:
            from film_foundry.app.material_scan import DEFAULTS
            if path[0]!='scan':return None
            value={**DEFAULTS,**self.owner.session.scan_adjustments}
            path=path[1:]
        try:
            for key in path:
                value=value[key] if isinstance(value,(dict,list,tuple)) else getattr(value,key)
            return value
        except (IndexError,KeyError,AttributeError,TypeError):return None

    def add_extra(self,label,path,value):
        if type(value) is bool:
            editor=QtWidgets.QCheckBox();editor.setChecked(value)
            signal,read,write=editor.toggled,editor.isChecked,editor.setChecked
        elif isinstance(value,str):
            editor=QtWidgets.QLineEdit(value)
            signal,read,write=editor.editingFinished,editor.text,editor.setText
        else:
            if type(value) is int:
                editor=QtWidgets.QSpinBox();editor.setRange(-2147483647,2147483647)
                if not editor.minimum()<=value<=editor.maximum():
                    editor=QtWidgets.QLineEdit(str(value))
                    signal,read,write=editor.editingFinished,lambda:int(editor.text()),lambda v:editor.setText(str(v))
            else:
                editor=QtWidgets.QDoubleSpinBox();editor.setDecimals(6);editor.setRange(-1e12,1e12)
                editor.setSingleStep(.05)
            if isinstance(editor,QtWidgets.QAbstractSpinBox):
                editor.setValue(value);editor.setKeyboardTracking(False)
                signal,read,write=editor.editingFinished,editor.value,editor.setValue
        def commit(*_):
            try:
                if self.extra_value(path)==read():return
                if self.film:set_film_parameter(self.owner,path,read())
                else:
                    from film_foundry.app.film_test_strip import _set_path
                    from film_foundry.app.material_scan import DEFAULTS, normalize_adjustments
                    adjusted=normalize_adjustments(_set_path({**DEFAULTS,**self.owner.session.scan_adjustments},path[1:],read()))
                    self.owner.edit_tools.record_draft();self.owner.session.scan_adjustments=adjusted
                    self.owner.edit_tools.record_draft();self.owner.request_preview()
            except (ValueError,TypeError,OverflowError) as exc:
                QtWidgets.QToolTip.showText(editor.mapToGlobal(editor.rect().bottomLeft()),str(exc),editor)
                block=QtCore.QSignalBlocker(editor);write(self.extra_value(path));del block
        editor.setAccessibleName(label);signal.connect(commit)
        self.body.addRow(label,editor);self.extra.append((path,editor,write))

    def advanced(self,key):
        dialog=ParameterPins(self.owner,film=self.film)
        dialog.filter.setText(key)
        try:dialog.exec()
        finally:dialog.deleteLater();self.rebuild()

    def sync(self):
        if not self.isVisible():return
        if self.extra:
            try:config=self.owner._config() if self.film else None
            except (ValueError,TypeError):return
            for path,editor,update in self.extra:
                value=self.extra_value(path,config)
                editable=film_editable(self.owner,path) if self.film else not self.owner.busy
                editor.setEnabled(editable and value is not None)
                if value is not None and not editor.hasFocus():
                    block=QtCore.QSignalBlocker(editor);update(value);del block
        for target,editor,read,write in self.controls:
            editor.setEnabled(target.isEnabled())
            if read and not editor.hasFocus() and not (isinstance(editor,QtWidgets.QSlider) and editor.isSliderDown()):
                block=QtCore.QSignalBlocker(editor);write(read());del block

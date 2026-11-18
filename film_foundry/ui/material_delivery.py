"""Shared material definition, diagnostic and print actions for family pages."""
from dataclasses import asdict
from pathlib import Path
from film_foundry.ui.qt import QtCore, QtWidgets
from film_foundry.ui.curve_editor import text


class MaterialDeliveryMixin:
    def install_material_selection(self, layout):
        self.select_material_button=QtWidgets.QPushButton(text('选择材料…','Choose material…'))
        layout.addWidget(self.select_material_button)
        self.select_material_button.clicked.connect(self.choose_material)

    def _sync_material_selection(self, *_):
        if not hasattr(self,'select_material_button'):return
        from film_foundry.app.material_selection import material_choices,VARIANTS
        from film_foundry.ui.material_chooser import choice_name
        session=self.session
        editable=not self.busy and ((session.state is None and session.group is None) or getattr(self,'_planning_batch',False))
        values=asdict(session.material_type(**self.material_values()))
        if not getattr(self,'_planning_batch',False):
            if session.state is not None:values=asdict(session.state.material)
            elif session.group is not None:
                import json
                values=json.loads(session.group.provenance_json).get('material',{})
        record=next((r for r in material_choices(self.kind) if r['material']==values),None)
        if not values:name=text('定义未记录','Definition not recorded')
        elif record is None:
            key=next((key for key,fields in VARIANTS[self.kind] if fields and all(values.get(k)==v for k,v in fields.items())), 'custom')
            name=choice_name({'family':self.kind,'key':key})
            if key!='custom':name+=text(' · 自定义',' · Custom')
        else:name=choice_name(record)
        self.select_material_button.setText(text('材料 · ','Material · ')+name)
        self.select_material_button.setEnabled(editable)
        self.select_material_button.setToolTip(text('选择此材料族的类型；开始形成后材料冻结。数值可在材料页展开调整。',
            'Choose a type within this family. Material freezes once formation starts. Edit numerical properties in the Material panel.') if editable else text('材料已冻结。另建工作可更换材料；观看与导出不会改变它。','Material is frozen. Create new work to choose another material. Viewing and exporting leave it unchanged.'))
        if self.busy:self.select_material_button.setToolTip(text('当前操作完成后再选择材料。','Wait for the current operation before choosing a material.'))

    def choose_material(self):
        if self.busy or (not getattr(self,'_planning_batch',False) and (self.session.state is not None or self.session.group is not None)):return
        from film_foundry.ui.material_chooser import MaterialChooser
        from film_foundry.app.material_selection import material_choices
        values=asdict(self.session.material_type(**self.material_values()))
        key=next((r['key'] for r in material_choices(self.kind) if r['material']==values),None)
        path=self.session.source_path
        paths=[path] if path and self.session.source_info.get('kind')!='optical' and Path(path).suffix.lower()!='.npz' and Path(path).is_file() else []
        dialog=MaterialChooser(self,paths=paths,family=self.kind,current_key=key,current_material=values,fixed_family=True)
        self._material_chooser=dialog
        def apply():
            try:self.apply_material_definition(dialog.selection['material'])
            except (ValueError,TypeError,KeyError) as exc:self.status.setText(str(exc))
        dialog.accepted.connect(apply);dialog.finished.connect(dialog.deleteLater);dialog.open()

    def apply_material_definition(self, values):
        if self.busy or (not getattr(self,'_planning_batch',False) and (self.session.state is not None or self.session.group is not None)):
            raise ValueError(text('当前材料已经开始形成，不能替换定义','Material formation has started; the definition is frozen'))
        definition=self.session.material_type(**values)
        # Validate before touching any controls, including categorical values.
        for key,control in self.material_controls.items():
            value=getattr(definition,key)
            if isinstance(control,QtWidgets.QComboBox):
                if control.findData(value)<0:raise ValueError('Unsupported material value: '+key)
            elif not control.minimum()<=value<=control.maximum():raise ValueError('Value outside editor range: '+key)
        blockers=[QtCore.QSignalBlocker(c) for c in self.material_controls.values()]
        for key,control in self.material_controls.items():
            value=getattr(definition,key)
            if isinstance(control,QtWidgets.QComboBox):control.setCurrentIndex(control.findData(value))
            else:
                control.setValue(value)
                if hasattr(control,'set_reset_value'):control.set_reset_value(value)
        del blockers
        self._sync()

    def add_delivery_actions(self, menu):
        self.print_requested.connect(self._standalone_print_source)
        menu.addSeparator()
        self.load_definition_action = menu.addAction(text('载入材料定义…', 'Load material definition…'), self.load_definition)
        menu.addAction(text('保存材料定义…', 'Save material definition…'), self.save_definition)
        menu.addSeparator()
        menu.addAction(text('标靶预览', 'Diagnostic preview'), self.open_target_preview)
        menu.addAction(text('扫描与成片…','Scan & output…'),self.open_scan_controls)
        self.print_action = menu.addAction(text('送往印刷与制版', 'Send to print & plate studio'), self.send_to_print)
        self.print_action.setToolTip(text('将当前片体按当前观看条件输出到独立制版页；使用实际工作尺寸，原介质不变。', 'Send the selected body at working resolution and current viewing conditions. The medium remains unchanged.'))

    def _standalone_print_source(self,payload):
        if getattr(self,'_workspace_host',None) is not None:return
        from film_foundry.ui.printmaking_workspace import PrintmakingWorkspace
        page=PrintmakingWorkspace()
        page.set_source(**payload)
        page.setWindowTitle(text('印刷与制版','Print & plate studio'));page.resize(1080,760)
        if not hasattr(self,'_print_views'):self._print_views=[]
        self._print_views.append(page)
        page.closed.connect(lambda:self._print_views.remove(page) if page in self._print_views else None)
        page.show()

    def material_values(self):
        return {k: c.currentData() if isinstance(c, QtWidgets.QComboBox) else c.value()
                for k, c in self.material_controls.items()}

    def save_definition(self):
        from film_foundry.core.atomic_io import atomic_write_json
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, text('保存材料定义', 'Save material definition'), self.kind + '.material.json', 'JSON (*.json)')
        if not path:return
        try:
            self.session._protect_bundle_member(path)
            if self.session.source_path and Path(path).resolve() == Path(self.session.source_path).resolve():raise ValueError('Output must not replace its source')
            definition = self.session.material_type(**self.material_values())
            atomic_write_json(path, {'model': 'material_definition_v1', 'family': self.kind, 'material': asdict(definition)})
            self.status.setText(text('材料定义已保存', 'Material definition saved'))
        except Exception as exc:self.status.setText(str(exc))

    def load_definition(self):
        if self.busy or self.session.state is not None or self.session.group is not None:return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, text('载入材料定义', 'Load material definition'), '', 'JSON (*.json)')
        if not path:return
        try:
            from film_foundry.core.atomic_io import strict_json_load
            data = strict_json_load(path)
            if data.get('model') != 'material_definition_v1' or data.get('family') != self.kind:raise ValueError('Material definition belongs to another family')
            self.apply_material_definition(data['material'])
            self.status.setText(text('材料定义已载入', 'Material definition loaded'))
        except Exception as exc:self.status.setText(str(exc))

    def open_target_preview(self):
        from film_foundry.ui.material_preview import MaterialPreview
        from film_foundry.app.family_preview import FamilyPreviewSession
        from film_foundry.ui.inline_editor import present_inline_editor
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(text('材料标靶预览', 'Material diagnostic preview'))
        layout = QtWidgets.QVBoxLayout(dialog)
        preview = MaterialPreview(lambda: {'material': self.material_values(), 'view': asdict(self.conditions()),'scan_adjustments':self.session.scan_adjustments},
                                  renderer=FamilyPreviewSession(self.kind))
        preview.setMinimumSize(500, 430)
        preview.setToolTip(text('当前材料定义、固定种子、该材料族的参考流程。不会执行当前片体上的事故操作，也不改变当前工艺进度。480 像素仅用于快速比较。', 'Current material, fixed seed and family reference process. Does not execute the current body’s accidents or advance its processing. 480 pixels for comparison only.'))
        layout.addWidget(preview)
        self._target_preview = preview
        present_inline_editor(self.centralWidget(), dialog)

    def open_scan_controls(self):
        if self.busy:return
        from film_foundry.ui.material_scan_editor import MaterialScanEditor
        from film_foundry.ui.inline_editor import present_inline_editor
        dialog=MaterialScanEditor(self.session.scan_adjustments,self)
        def apply():
            self.session.scan_adjustments=dialog.result_settings
            self.request_preview()
        present_inline_editor(self.centralWidget(),dialog,apply)

    def send_to_print(self):
        if self.busy or self.session.group is None:return
        view, index = self.conditions(), max(0, self.body_choice.currentIndex())
        def prepare():
            body = self.session.group.bodies[index]
            image = self.session.preview(view, index)
            from film_foundry.app.print_sources import body_components
            components = body_components(body,view,image.shape)
            self.print_requested.emit({'image': image, 'components': components, 'provenance': {
                'name': self.session.source_name, 'path': self.session.source_path,
                'family': self.kind, 'group_id': self.session.group.group_id,
                'body_id': body.body_id, 'view': asdict(view),'scan_adjustments':dict(self.session.scan_adjustments)}})
        self._submit(prepare, text('准备制版来源…', 'Preparing print source…'), render=False)

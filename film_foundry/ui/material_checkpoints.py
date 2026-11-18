"""Open an independent material work page at an executed operation boundary."""
from pathlib import Path
from copy import deepcopy

from film_foundry.app.material_workspace import snapshot_document, save_material_workspace, load_material_workspace
from film_foundry.ui import material_workspace_state as drafts
from film_foundry.ui.qt import QtCore, QtWidgets
from film_foundry.ui.curve_editor import text
from film_foundry.ui.work_dialog import WorkDialog

_standalone=set()


def branch_page(owner,session,draft,title,*,historical=False):
    from film_foundry.ui.material_lab_window import create_material_window
    page=create_material_window(session.kind)
    try:
        page.session=session
        page._restore_controls();page._sync()
        if historical:
            # A future body's index and inspection maps need not exist at an earlier step.
            initial=drafts.capture(page)
            draft=deepcopy(draft)
            for key in ('operation','body_index','pending_optical'):
                draft[key]=initial[key]
            if 'inspection' in initial['values']:draft['values']['inspection']=initial['values']['inspection']
        drafts.restore(page,draft)
        publish=getattr(owner,'mount_checkpoint',None)
        if publish:publish(page,title)
        else:
            page.setWindowTitle(title);page.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose,True)
            _standalone.add(page);page.destroyed.connect(lambda *_:_standalone.discard(page));page.show()
        drafts.restore_presentation(page,draft)
        page._submit(lambda:None,text('打开检查点…','Opening checkpoint…'),body_index=draft['body_index'])
    except Exception:
        page.close();page.deleteLater();raise
    return page


class MaterialCheckpoints(WorkDialog):
    def __init__(self,owner):
        super().__init__(owner)
        self.owner=owner
        self.document=snapshot_document(owner.session,drafts.capture(owner),title=owner.windowTitle())
        self.setWindowTitle(text('工序检查点','Process checkpoints'));self.resize(540,290)
        layout=QtWidgets.QVBoxLayout(self)
        note=QtWidgets.QLabel(text('选择已执行的工序可另开分支。保存当前检查点会保留曝光图、材料状态和参数。',
            'Choose an executed boundary to continue in an independent page. Saving the current checkpoint includes the exposure pixels, material state and settings.'))
        note.setWordWrap(True);layout.addWidget(note)
        self.steps=QtWidgets.QListWidget()
        self.steps.setObjectName('checkpointSteps')
        self.steps.setUniformItemSizes(True)
        layout.addWidget(self.steps,1)
        self.boundaries=[]
        session=self.document.session
        try:steps=session.recipe()['steps']
        except ValueError:steps=[]
        if steps and session.image is not None:
            self.add(text('载入来源后 · 尚未开始','Source loaded · before processing'),0)
            for index,step in enumerate(steps,1):
                key=step['operation']
                label=text(*owner.inspector.labels.get(key,(key,key)))
                self.add(f'{index:02d}  {label}',index)
        else:self.add(text('当前工作状态','Current work state'),None)
        self.steps.setCurrentRow(self.steps.count()-1)
        layout.addWidget(self.status)
        row=QtWidgets.QHBoxLayout();layout.addLayout(row)
        self.branch=QtWidgets.QPushButton(text('从所选位置新建分支','Branch from selection'));row.addWidget(self.branch)
        self.save=QtWidgets.QPushButton(text('保存当前检查点…','Save current checkpoint…'));row.addWidget(self.save)
        self.open_button=QtWidgets.QPushButton(text('打开检查点…','Open checkpoint…'));row.addWidget(self.open_button)
        self.branch.clicked.connect(self.fork);self.save.clicked.connect(self.choose_save);self.open_button.clicked.connect(self.choose_open)
        self.running_changed.connect(self.sync);self.sync(False)

    def showEvent(self,event):
        super().showEvent(event)
        # The host reparents the dialog after population. Reflow under its final theme.
        self.steps.doItemsLayout()
        self.steps.scrollToItem(self.steps.currentItem())

    def add(self,label,boundary):
        self.steps.addItem(label);self.boundaries.append(boundary)

    def sync(self,busy):
        self.open_button.setEnabled(not busy);self.steps.setEnabled(not busy)
        session=self.document.session
        content=any(value is not None for value in (session.image,session.state,session.group))
        self.branch.setEnabled(not busy and content);self.save.setEnabled(not busy and content)

    def fork(self):
        i=self.steps.currentRow()
        if i<0:return
        document=self.document
        boundary=self.boundaries[i]
        historical=i<self.steps.count()-1
        title=text('分支 · ','Branch · ')+document.session.kind+' · '+self.steps.item(i).text()
        def publish(session):
            branch_page(self.owner,session,document.draft,title,historical=historical)
            self.accept()
        self.submit(lambda control:document.session.branch_at_step(boundary,control=control),publish,
                    text('正在准备分支…','Preparing branch…'))

    def choose_save(self):
        path,_=QtWidgets.QFileDialog.getSaveFileName(self,self.save.text(),'checkpoint.ffworkspace','*.ffworkspace')
        if path:self.save_to(path)

    def save_to(self,path):
        path=Path(path)
        if path.suffix.lower()!='.ffworkspace':path=Path(str(path)+'.ffworkspace')
        document=self.document
        def save(control):
            return save_material_workspace(path,[document],formation_precision='full')
        self.submit(save,lambda output:self.status.setText(text('已保存 · ','Saved · ')+str(output)),
                    text('正在保存检查点…','Saving checkpoint…'))

    def choose_open(self):
        path=QtWidgets.QFileDialog.getExistingDirectory(self,self.open_button.text())
        if path:self.open_from(path)

    def open_from(self,path):
        def publish(workspace):
            if len(workspace.documents)!=1 or workspace.documents[0].batch_paths is not None:
                raise ValueError(text('此目录包含整组工作，请从文件菜单打开材料工作区。','Open this multi-document or batch workspace from the File menu.'))
            doc=workspace.documents[0]
            branch_page(self.owner,doc.session,doc.draft,text('检查点 · ','Checkpoint · ')+Path(path).stem)
            self.accept()
        self.submit(lambda control:load_material_workspace(path),publish,text('读取检查点…','Loading checkpoint…'))


def open_checkpoints(owner):
    if owner.busy:return
    from film_foundry.ui.inline_editor import present_inline_editor
    dialog=MaterialCheckpoints(owner)
    present_inline_editor(owner,dialog)
    return dialog

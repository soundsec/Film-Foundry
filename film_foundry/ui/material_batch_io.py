"""Batch completion/resume controls shared by the seven material frontends."""
from film_foundry.ui.qt import QtWidgets
from film_foundry.ui.curve_editor import text


def install_resume(window):
    button=QtWidgets.QPushButton(text('继续已有批次…','Resume saved batch…'))
    button.setToolTip(text('使用清单中冻结的材料、工序和种子；保留已完成结果，重试失败与未开始的项目。',
        'Uses the recorded material, steps and seeds. Reuses complete results and retries failed or pending items.'))
    window.centralWidget().layout().addWidget(button);window.resume_batch_button=button
    button.clicked.connect(lambda:resume(window))


def capture_report(window,action):
    window._batch_report=action()


def resume(window,path=None):
    if window.busy:return False
    if not path:
        path,_=QtWidgets.QFileDialog.getOpenFileName(window,text('打开批次清单','Open batch record'),'','Material batch (material_batch.json)')
    if not path:return False
    from film_foundry.app.material_batch_run import load_plan,run_batch
    from film_foundry.app.control import CancelFlag,WorkflowControl
    try:
        _,plan=load_plan(path)
        if plan['settings']['kind']!=window.kind:raise ValueError(text('请在对应材料页恢复此批次。','Resume this batch in its matching material page.'))
    except Exception as exc:window.status.setText(str(exc));return False
    window._batch_cancel=CancelFlag();control=WorkflowControl(cancel_requested=window._batch_cancel.requested)
    return window._submit(lambda:capture_report(window,lambda:run_batch(path,on_result=window.result_ready.emit,control=control)),
        text('恢复批量处理…','Resuming batch…'),render=False)


def show_report(window):
    report=getattr(window,'_batch_report',None)
    if report is None:return
    window._batch_report=None
    message=text(f"完成 {report['completed']} · 复用 {report['reused']} · 失败 {len(report['failures'])}",
        f"Completed {report['completed']} · Reused {report['reused']} · Failed {len(report['failures'])}")
    if report['cancelled']:message+=text(' · 已停止，可继续',' · Stopped; can resume')
    window.status.setText(message)
    window.status.setToolTip(report['manifest']+'\n'+'\n'.join(f"{f['source']}: {f['error']}" for f in report['failures']))

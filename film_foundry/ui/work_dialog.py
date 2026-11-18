"""Run one cancellable, budgeted computation from a desktop work dialog."""
from film_foundry.app.control import WorkflowCancelled
from film_foundry.ui.preview_worker import PreviewWorker
from film_foundry.ui.qt import QtCore, QtWidgets
from film_foundry.ui.curve_editor import text


class WorkDialog(QtWidgets.QDialog):
    running_changed = QtCore.Signal(bool)

    def __init__(self,parent):
        super().__init__(parent)
        self.busy=False
        self.closing=False
        self.setProperty('disposablePreview',True)
        self.worker=PreviewWorker(self,'darkroom-dialog',self._finish_work)
        self.status=QtWidgets.QLabel()
        self.status.setWordWrap(True)

    def submit(self,work,publish,status):
        if self.busy:return
        self.busy=True
        self.status.setText(status)
        self.running_changed.emit(True)
        self.publish=publish
        self.flag=self.worker.submit(work)

    @QtCore.Slot(object)
    def _finish_work(self,future):
        publish,self.publish=self.publish,None
        self.busy=False
        self.running_changed.emit(False)
        if self.closing:self.reject();return
        try:
            result=future.result()
            if self.flag.requested():raise WorkflowCancelled()
            publish(result)
        except WorkflowCancelled:self.status.setText(text('已停止。','Stopped.'))
        except Exception as exc:self.status.setText(str(exc))

    def done(self,result):
        if self.busy:
            self.closing=True;self.flag.cancel()
            self.status.setText(text('正在停止…','Stopping…'))
            return
        self.worker.close()
        super().done(result)

    def closeEvent(self,event):
        if self.busy:self.reject();event.ignore();return
        super().closeEvent(event)

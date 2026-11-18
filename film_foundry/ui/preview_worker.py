"""Run a workspace's interactive preview and notify its Qt owner on completion."""
from concurrent.futures import ThreadPoolExecutor

from film_foundry.app.control import CancelFlag, WorkflowControl
from film_foundry.app.preview_budget import Priority, preview_budget
from film_foundry.ui.qt import QtCore


class _Completion(QtCore.QObject):
    finished = QtCore.Signal(object)


class PreviewWorker:
    def __init__(self, parent, name, finished):
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=name)
        self.events = _Completion(parent)
        self.events.finished.connect(finished, QtCore.Qt.ConnectionType.QueuedConnection)
        self.flag = None
        parent.destroyed.connect(self.close)

    def submit(self, work):
        self.flag = CancelFlag()
        control = WorkflowControl(cancel_requested=self.flag.requested)
        future = self.pool.submit(preview_budget.run, Priority.INTERACTIVE, work, control, check=control.check_cancel)
        signal = self.events.finished

        def notify(completed):
            try:
                signal.emit(completed)
            except RuntimeError:
                pass  # The owner closed during an indivisible native operation.

        future.add_done_callback(notify)
        return self.flag

    def cancel(self):
        if self.flag is not None:
            self.flag.cancel()

    def close(self):
        self.cancel()
        self.pool.shutdown(wait=False, cancel_futures=True)

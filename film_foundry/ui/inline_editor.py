"""Transactional configuration dialogs above an intact workspace."""
from film_foundry.ui.qt import QtCore, QtWidgets


class ConfigurationDialogs(QtCore.QObject):
    def __init__(self, owner):
        super().__init__(owner)
        self.frames = []
        owner._inline_editor_host = self  # Compatibility for existing draft inspectors.

    def open(self, owner, dialog, on_accept):
        dialog.setParent(owner, QtCore.Qt.WindowType.Dialog)
        dialog.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
        dialog.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog._inline_editor_host = self
        self.frames.append((owner, dialog))
        def finished(result):
            try:
                if result == QtWidgets.QDialog.DialogCode.Accepted and on_accept is not None:
                    on_accept()
            finally:
                self.frames[:] = [(parent, child) for parent, child in self.frames if child is not dialog]
        dialog.finished.connect(finished)
        dialog.open()
        return dialog


# Existing callers inspect pending drafts through this name; no widget reparenting host remains.
InlineEditorHost = ConfigurationDialogs


def present_inline_editor(owner, dialog, on_accept=None):
    current = owner
    while current is not None:
        host = getattr(current, '_inline_editor_host', None)
        if host is not None:
            return host.open(owner, dialog, on_accept)
        current = current.parentWidget()
    return ConfigurationDialogs(owner).open(owner, dialog, on_accept)

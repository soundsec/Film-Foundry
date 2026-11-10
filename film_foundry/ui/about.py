"""Desktop product information, separate from material/file-format identity."""
from film_foundry import __version__
from film_foundry.app.paths import resource_root
from film_foundry.identity import APP_DISPLAY_NAME, AUTHOR_NAME, REPOSITORY_URL
from film_foundry.ui.curve_editor import text
from film_foundry.ui.qt import QtCore, QtGui, QtWidgets
from film_foundry.ui.theme import colors


class AboutDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(text("关于 Film Foundry","About Film Foundry"))
        self.setMinimumWidth(440)
        layout=QtWidgets.QVBoxLayout(self);layout.setContentsMargins(24,24,24,20);layout.setSpacing(12)
        heading=QtWidgets.QHBoxLayout();layout.addLayout(heading)
        icon=QtWidgets.QLabel(self)
        icon.setPixmap(QtGui.QPixmap(str(resource_root()/"film_foundry/assets/film_foundry_icon_128.png")).scaled(
            64,64,QtCore.Qt.AspectRatioMode.KeepAspectRatio,QtCore.Qt.TransformationMode.SmoothTransformation))
        heading.addWidget(icon);heading.addSpacing(6)
        name=QtWidgets.QLabel(APP_DISPLAY_NAME,self);name.setObjectName("pageTitle")
        font=name.font();font.setPointSize(font.pointSize()+6);font.setBold(True);name.setFont(font)
        heading.addWidget(name);heading.addStretch(1)
        self.version_label=QtWidgets.QLabel(text("桌面版 ","Desktop ")+__version__,self)
        layout.addWidget(self.version_label)
        description=QtWidgets.QLabel(text("以材料与处理过程为结构的影像创作工具。","Image creation through materials and processes."),self)
        description.setWordWrap(True);layout.addWidget(description)
        self.author_label=QtWidgets.QLabel(text("作者：","Author: ")+AUTHOR_NAME,self)
        self.author_label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.author_label)
        self.repository_link=QtWidgets.QLabel(f'<a style="color:{colors().accent}" href="{REPOSITORY_URL}">{REPOSITORY_URL}</a>',self)
        self.repository_link.setOpenExternalLinks(True)
        self.repository_link.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextBrowserInteraction)
        self.repository_link.setWordWrap(True);layout.addWidget(self.repository_link)
        license_label=QtWidgets.QLabel("GPL-3.0-or-later",self);license_label.setObjectName("sectionNote");layout.addWidget(license_label)
        from film_foundry.app.seasonal_collection import installation_state, puzzle_cipher
        try:
            identity=installation_state()["identity"]
            stamp=QtWidgets.QLabel(f"ID {identity}\nXOR · HEX {puzzle_cipher(identity)}",self)
            stamp.setObjectName("sectionNote")
            stamp.setWordWrap(True)
            stamp.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
            stamp.setToolTip(text("以编号的 ASCII 字节循环异或，十六进制还原文字。", "Repeat the ID's ASCII bytes as the XOR key; decode the hex into text."))
            layout.addWidget(stamp)
        except OSError:
            pass
        buttons=QtWidgets.QDialogButtonBox(self)
        close=buttons.addButton(text("关闭","Close"),QtWidgets.QDialogButtonBox.ButtonRole.RejectRole)
        close.setDefault(True);buttons.rejected.connect(self.reject);layout.addWidget(buttons)


def show_about(parent):
    dialog=AboutDialog(parent)
    from film_foundry.ui.inline_editor import present_inline_editor
    present_inline_editor(parent,dialog)

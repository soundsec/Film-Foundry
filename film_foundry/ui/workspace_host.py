"""Native document tabs. Pages retain their own controllers and task lifetimes."""
from film_foundry.ui.qt import QtCore, QtWidgets
from film_foundry.ui.curve_editor import text


class WorkspaceHost(QtWidgets.QTabWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('workspaceDocuments')
        self.setDocumentMode(True)
        self.setMovable(True)
        self.setTabsClosable(True)
        self.tabBar().setExpanding(False)
        self.tabBar().setUsesScrollButtons(True)
        self.tabBar().setElideMode(QtCore.Qt.TextElideMode.ElideRight)
        self.tabBar().setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.tabBar().customContextMenuRequested.connect(self._tab_menu)
        self._pages = {}
        self.tabCloseRequested.connect(self.close_page)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(400)
        self.timer.timeout.connect(self.refresh_activity)
        self.timer.start()

    def _tab_menu(self, position):
        menu = QtWidgets.QMenu(self)
        for index in range(self.count()):
            page = self.widget(index)
            action = menu.addAction(self.tabToolTip(index).split('\n',1)[0])
            action.setCheckable(True)
            action.setChecked(page is self.currentWidget())
            action.triggered.connect(lambda checked=False, p=page: self.setCurrentWidget(p))
        index = self.tabBar().tabAt(position)
        page = self.widget(index)
        if page in self._pages and self._pages[page][2]:
            menu.addSeparator()
            menu.addAction(text('关闭此页', 'Close this page'), lambda: self.close_page(self.indexOf(page)))
        menu.exec(self.tabBar().mapToGlobal(position))

    def add_page(self, page, title, *, key=None, closable=True):
        if self.indexOf(page) >= 0:
            self.setCurrentWidget(page)
            return page
        page.hide()
        page.setParent(self, QtCore.Qt.WindowType.Widget)
        page.setWindowModality(QtCore.Qt.WindowModality.NonModal)
        self._pages[page] = (title, key, closable)
        index = self.addTab(page, title)
        if not closable:
            for side in (QtWidgets.QTabBar.ButtonPosition.LeftSide, QtWidgets.QTabBar.ButtonPosition.RightSide):
                self.tabBar().setTabButton(index, side, None)
        self.setTabToolTip(index, title)
        # A page can finish closing asynchronously. Listen once; consult its
        # current host so language/layout reconstruction cannot close an old tab.
        page._workspace_host = self
        if not getattr(page, '_workspace_close_connected', False):
            signal = getattr(page, 'workspace_closed', None)
            if signal is None:
                signal = page.finished if isinstance(page, QtWidgets.QDialog) else getattr(page, 'closed', None)
            if hasattr(signal, 'connect'):
                signal.connect(lambda *_, p=page: p._workspace_host.remove_page(p))
            page._workspace_close_connected = True
        self.setCurrentWidget(page)
        return page

    def find_page(self, key):
        return next((page for page, (_, identity, _) in self._pages.items() if identity == key), None)

    def close_page(self, index):
        page = self.widget(index)
        if page not in self._pages or not self._pages[page][2]:
            return
        if page.property('navigationLocked'):
            return
        guard=getattr(self,'before_close',None)
        if guard is not None and not guard(page):return
        # The page owns cancellation and close deferral; changing tabs never
        # calls close() and never destroys a processing session.
        if page.close():
            self.remove_page(page)

    def remove_page(self, page):
        if page not in self._pages:
            return
        self._pages.pop(page)
        self.removeTab(self.indexOf(page))
        page.hide()
        page.deleteLater()

    def detach_pages(self, parent, exclude=()):
        """Keep drafts and workers alive while the surrounding shell rebuilds."""
        saved = []
        for index in range(self.count()):
            page = self.widget(index)
            if page not in exclude:
                saved.append((page, *self._pages[page]))
        for page, *_ in saved:
            self._pages.pop(page)
            self.removeTab(self.indexOf(page))
            page.setParent(parent, QtCore.Qt.WindowType.Widget)
            page.hide()
        return saved

    def refresh_activity(self):
        for page, (title, _, _) in tuple(self._pages.items()):
            busy = bool(getattr(page, 'busy', False) or getattr(page, 'exporting', False) or page.property('workspaceBusy'))
            index = self.indexOf(page)
            source=getattr(getattr(page,'session',None),'source_name','')
            label=title
            if source and source not in title:label+=' · '+(source[:18]+'…' if len(source)>18 else source)
            self.setTabText(index, label + (' …' if busy else ''))
            hint=title+(' · '+source if source and source not in title else '')
            self.setTabToolTip(index, hint + (text(' · 后台处理中', ' · Processing in background') if busy else '')
                + text('\nCtrl+Tab 切换；右键查看全部页面。切换保留草稿。','\nCtrl+Tab to switch; right-click lists all pages. Switching preserves drafts.'))

    def busy_pages(self):
        return [page for page in self._pages
                if getattr(page, 'busy', False) or getattr(page, 'exporting', False)]

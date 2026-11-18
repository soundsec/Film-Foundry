"""Stage navigation and explicit task endpoints; neither action starts processing."""
from film_foundry.ui.qt import QtCore, QtWidgets
from film_foundry.ui.curve_editor import text
from film_foundry.ui.stage_button import StageButton


STAGES = (
    ('film', '曝光与潜影', 'Exposure & latent'),
    ('develop', '化学冲洗', 'Development'),
    ('scan', '观察与扫描', 'Observe & scan'),
    ('output', '保存与导出', 'Save & export'),
)
STATUS = {
    'editable': ('可调', 'Editable'),
    'frozen': ('已冻结 · 查看', 'Frozen · inspect'),
    'observe': ('仅观察', 'Observe only'),
    'skip': ('本次跳过', 'Skipped in this task'),
    'output': ('保存设置', 'Output settings'),
}


class CausalPipelineBar(QtWidgets.QWidget):
    stage_requested = QtCore.Signal(str)
    task_requested = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QHBoxLayout(self);layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)
        self.buttons = {};self.states = {};self.endpoints={};self.summaries={}
        for index, (key, zh, en) in enumerate(STAGES):
            button = StageButton(index+1)
            button.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Fixed)
            button.setMinimumWidth(110)
            button.clicked.connect(lambda checked=False, stage=key:self.stage_requested.emit(stage))
            layout.addWidget(button, 1);self.buttons[key] = button
            if key!='output':
                endpoint=QtWidgets.QToolButton(button);endpoint.setCheckable(True);endpoint.setFixedSize(60,24)
                endpoint.setObjectName('stageEndpoint');button.trailing_width=66
                endpoint.clicked.connect(lambda checked=False,k=key:self.task_requested.emit({'film':'expose','develop':'develop','scan':'full'}[k]))
                button.installEventFilter(self);self.endpoints[key]=endpoint
        self.scan_menu=QtWidgets.QMenu(self.endpoints['scan'])
        self.scan_menu.addAction(text('曝光 → 冲洗 → 扫描成片','Expose → develop → scan'),lambda:self.task_requested.emit('full'))
        self.scan_menu.addAction(text('跳过形成，仅扫描原图','Skip formation; scan the original only'),lambda:self.task_requested.emit('scan'))
        self.endpoints['scan'].setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.MenuButtonPopup)

    def eventFilter(self,watched,event):
        if event.type()==QtCore.QEvent.Type.Resize:
            key=next((k for k,b in self.buttons.items() if b is watched),None)
            if key in self.endpoints:self.endpoints[key].move(watched.width()-self.endpoints[key].width()-8,4)
        return False

    def sync(self, mode, *, photo_source, selected, summaries=None, busy=False):
        capture = mode in ('full', 'develop', 'expose')
        self.states = {
            'film': 'editable' if capture else 'skip' if photo_source else 'frozen',
            'develop': 'editable' if mode in ('full', 'develop', 'develop_latent') else
                       'frozen' if mode == 'scan' and not photo_source else 'skip',
            'scan': 'observe' if mode in ('full', 'scan') else 'skip',
            'output': 'output',
        }
        self.summaries=summaries or {}
        end={'expose':'film','develop':'develop','develop_latent':'develop','full':'scan','scan':'scan'}[mode]
        for key,endpoint in self.endpoints.items():
            active=key==end
            endpoint.setText(text('当前终点','Endpoint') if active else text('设为终点','Set endpoint'))
            endpoint.setFixedWidth(max(endpoint.fontMetrics().horizontalAdvance(text('当前终点','Endpoint')),
                                       endpoint.fontMetrics().horizontalAdvance(text('设为终点','Set endpoint')))+24)
            endpoint.move(self.buttons[key].width()-endpoint.width()-8,4)
            endpoint.setChecked(active);endpoint.setVisible(photo_source or active)
            endpoint.setEnabled(photo_source and not busy)
            endpoint.setToolTip(text('选择本次执行终点；正式处理与保存仍由底部主按钮启动。','Choose the task endpoint. The main action below starts formal processing and saving.'))
            endpoint.setAccessibleName(text('执行终点：','Task endpoint: ')+text(*dict((k,(zh,en)) for k,zh,en in STAGES)[key]))
        self.endpoints['scan'].setMenu(self.scan_menu if photo_source else None)
        for index, (key, zh, en) in enumerate(STAGES, 1):
            button = self.buttons[key];state = self.states[key]
            button.state = state
            summary=self.summaries.get(key,'')
            button.trailing_width=self.endpoints[key].width()+6 if key in self.endpoints and not self.endpoints[key].isHidden() else 0
            button.setText(f'{index}  {text(zh, en)}\n{text(*STATUS[state])}'+(' · '+summary if summary else ''))
            button.setAccessibleName(button.text().replace('\n', ' · '))
            button.setToolTip((summary+'\n' if summary else '')+self.explanation(key))
        self.select(selected)

    def select(self, selected):
        for key, button in self.buttons.items():button.setChecked(key == selected)

    def explanation(self, key):
        state = self.states[key]
        if state == 'frozen':
            return text('材料与曝光已保存在来源中；此工作沿用这些事实。',
                        'Material and exposure are stored in the source and retained by this task.') if key == 'film' else text(
                '底片已经形成，冲洗参数已冻结。可查看形成记录；有已记录的原潜影时，可另做冲洗并保留原底片。',
                'Development is frozen in this medium. Inspect its record or develop a recorded original latent again, retaining this medium.')
        if state == 'skip':
            return text('本次流程不执行这一步。照片可用阶段右侧的「设为终点」选择结束位置；已保存介质需从相应来源另建工作。',
                        'This task skips this stage. Choose a task for a photo, or start another task from the appropriate saved source.')
        if state == 'observe':
            return text('调整光源、扫描与成片外观，只读取来源介质，不改变已经形成的材料。',
                        'Adjust lighting, scan and image appearance. Observation only reads the source; it never changes formation.')
        if state == 'output':
            return text('设置本次保存的介质或成片；正式执行时选择保存位置。',
                        'Configure the medium or image to save; choose a destination when running the task.')
        return text('这些参数参与新的形成计算；已保存的潜影、底片与成片保留。',
                    'These controls affect new formation calculations. Saved latents, media and scans are retained.')


class PipelineStageDialog(QtWidgets.QDialog):
    def __init__(self, title, explanation, *, can_record=False, can_branch=False, frozen=False, latent=False, parent=None):
        super().__init__(parent)
        self.action = None;self.setWindowTitle(title);self.resize(510, 220)
        layout = QtWidgets.QVBoxLayout(self)
        note = QtWidgets.QLabel(explanation);note.setWordWrap(True);layout.addWidget(note)
        self.actions = {}
        for key, zh, en, enabled in (
            ('record', '查看形成记录', 'View formation record', can_record),
            ('branch', '从原潜影另做冲洗', 'Develop original latent again', can_branch),
        ):
            if not frozen or key=='branch' and latent:continue
            button = QtWidgets.QPushButton(text(zh, en));button.setEnabled(enabled)
            button.clicked.connect(lambda checked=False, action=key:self.choose(action))
            layout.addWidget(button);self.actions[key] = button
        if latent:
            detail=QtWidgets.QLabel(text('当前潜影可直接在冲洗阶段处理；若要更改材料或曝光，请回图库从原始来源新建。',
                'Develop this latent in the next stage. To change material or exposure, start from the original source in the library.'))
            detail.setWordWrap(True);detail.setObjectName('sectionNote');layout.addWidget(detail)
        elif frozen and not can_branch:
            detail = QtWidgets.QLabel(text('此来源没有可用的关联潜影。重做曝光需回图库选择原图；不会从底片反推潜影。',
                'No linked original latent is available here. Choose the original photo in the library to expose again; a latent is never reconstructed from a medium.'))
            detail.setWordWrap(True);detail.setObjectName('sectionNote');layout.addWidget(detail)
        close = QtWidgets.QPushButton(text('返回', 'Back'));close.clicked.connect(self.reject);layout.addWidget(close)

    def choose(self, action):
        self.action = action;self.accept()

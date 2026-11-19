"""A self-contained first darkroom session with the bundled example photograph."""
from copy import deepcopy
from pathlib import Path
from film_foundry.app.film_test_strip import FilmStripSession
from film_foundry.model.config import DarkroomConfig
from film_foundry.ui.qt import QtCore,QtGui,QtWidgets
from film_foundry.ui.curve_editor import text
from film_foundry.ui.image_view import ImageView
from film_foundry.ui.preferences import load_preferences,save_preferences
from film_foundry.ui.work_dialog import WorkDialog

SAMPLE=Path(__file__).resolve().parents[1]/'assets'/'astronaut.png'


class FirstTour(WorkDialog):
    def __init__(self,owner):
        super().__init__(owner.root.qt)
        self.owner=owner
        self.setWindowTitle(text('第一次暗房工作','Your first darkroom session'))
        self.resize(760,560)
        self.pages=[
            (text('选择来源','Select a source'),text('这里用内置照片走一遍胶片工作。平时可从图库导入自己的照片，选中后创建工作。','Follow this film workflow with the bundled photograph. For your own work, import a photo in the library, select it and create a workspace.')),
            (text('曝光','Expose'),text('调节曝光补偿，观察潜影中各处的可显影程度。工作台的曝光终点可单独保存潜影，留待以后冲洗。','Adjust exposure and inspect the developable latent image. The exposure endpoint in the workbench can save the latent for a later development.')),
            (text('冲洗','Develop'),text('这张密度预览来自示例配方冲洗后的底片。改变冲洗时间，再看看高光与阴影。已形成底片可反复扫描。','This density view comes from the developed negative. Change the bath time to compare highlights and shadows. You can scan a finished negative repeatedly.')),
            (text('观察与扫描','View and scan'),text('这一页显示同一曝光和冲洗设置的扫描成片。调整成片曝光，观察扫描如何改变显示效果。','Here is the scan for the same exposure and baths. Adjust output exposure to see how observation changes the displayed image.')),
            (text('保留工作与成片','Save work and output'),text('在工作台用「保存工作」保留来源和配方。要得到可分享的图像，选择扫描终点，点底部执行按钮并确认输出位置。材料页可在「快速配置 → 工序检查点」保留中途状态或另开分支。','In the workbench, Save work keeps the source and recipe. For a shareable image, select the scan endpoint and confirm the destination using the bottom run button. Material pages offer Process checkpoints in Quick settings for saving a stage or opening a branch.'))
        ]
        self.page=0
        cfg=DarkroomConfig();cfg.seed_strategy='fixed';cfg.random_seed=19
        self.config=cfg
        self.images={};self.sample=QtGui.QImage(str(SAMPLE))
        layout=QtWidgets.QVBoxLayout(self)
        self.heading=QtWidgets.QLabel();self.heading.setObjectName('sectionTitle');layout.addWidget(self.heading)
        self.instructions=QtWidgets.QLabel();self.instructions.setWordWrap(True);layout.addWidget(self.instructions)
        self.viewer=ImageView();layout.addWidget(self.viewer,1)
        controls=QtWidgets.QHBoxLayout();layout.addLayout(controls)
        self.caption=QtWidgets.QLabel();controls.addWidget(self.caption)
        self.value=QtWidgets.QDoubleSpinBox();self.value.setKeyboardTracking(False);controls.addWidget(self.value)
        self.update_button=QtWidgets.QPushButton(text('更新示例','Update example'));controls.addWidget(self.update_button)
        self.update_button.clicked.connect(self.refresh_image);controls.addStretch()
        layout.addWidget(self.status)
        row=QtWidgets.QHBoxLayout();layout.addLayout(row)
        self.previous=QtWidgets.QPushButton(text('上一步','Back'));row.addWidget(self.previous)
        self.previous.clicked.connect(lambda:self.go(self.page-1))
        self.next=QtWidgets.QPushButton();row.addWidget(self.next);self.next.clicked.connect(self.advance)
        self.practice=QtWidgets.QPushButton(text('用示例照片新建工作','Start work with the sample photo'))
        self.practice.clicked.connect(self.start_work);row.addWidget(self.practice)
        row.addStretch();skip=QtWidgets.QPushButton(text('结束导览','End tour'));row.addWidget(skip);skip.clicked.connect(self.reject)
        self.refresh_timer = QtCore.QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.setInterval(250)
        self.refresh_timer.timeout.connect(self.refresh_image)
        self.value.valueChanged.connect(self.schedule_refresh)
        self.finished.connect(self.refresh_timer.stop)
        self.running_changed.connect(self.sync)
        self.go(0)

    def schedule_refresh(self):
        self.next.setEnabled(False)
        self.refresh_timer.start()

    def advance(self):
        if self.page==len(self.pages)-1:self.accept()
        else:self.go(self.page+1)

    def start_work(self):
        self.accept()
        self.owner._open_catalog_source(str(SAMPLE),'film')

    def sync(self,busy):
        self.previous.setEnabled(not busy and self.page>0);self.next.setEnabled(not busy)
        self.value.setEnabled(not busy);self.update_button.setEnabled(not busy)
        self.practice.setVisible(self.page==len(self.pages)-1)

    def go(self,page):
        if self.busy:return
        if self.page in (1,2,3):self.commit_value()
        self.refresh_timer.stop()
        self.page=page
        title,body=self.pages[page]
        self.heading.setText(f'{page+1} / {len(self.pages)}   {title}')
        self.instructions.setText(body)
        self.next.setText(text('完成','Finish') if page==4 else text('下一步','Next'))
        for widget in (self.value,self.caption,self.update_button):widget.setVisible(page in (1,2,3))
        self.sync(False)
        if page in (1,2,3):
            caption,low,high,step,suffix,value={
                1:(text('曝光补偿','Exposure compensation'),-3,3,.5,' EV',self.config.exposure_ev),
                2:(text('冲洗时间','Development time'),1,30,.5,' min',self.config.chemistry.time_min),
                3:(text('成片曝光','Output exposure'),-3,3,.5,' EV',self.config.look.print_exposure_ev)
            }[page]
            self.caption.setText(caption)
            with QtCore.QSignalBlocker(self.value):
                self.value.setRange(low,high);self.value.setSingleStep(step)
                self.value.setSuffix(suffix);self.value.setValue(value)
            self.refresh_image()
        elif page==0:
            self.viewer.show_image(self.sample);self.status.setText(SAMPLE.name)
        else:
            self.refresh_image()

    def commit_value(self):
        if self.page==1:self.config.exposure_ev=self.value.value()
        elif self.page==2:self.config.chemistry.time_min=self.value.value()
        elif self.page==3:self.config.look.print_exposure_ev=self.value.value()

    def refresh_image(self):
        self.refresh_timer.stop()
        self.next.setEnabled(True)
        self.commit_value()
        mode={1:'expose',2:'develop',3:'full',4:'full'}[self.page]
        config=deepcopy(self.config)
        key=(mode,config.exposure_ev,config.chemistry.time_min,config.look.print_exposure_ev)
        if key in self.images:
            self.viewer.show_image(self.images[key]);self.status.clear();return
        self.viewer.clear_image()
        def render(control):
            session=FilmStripSession(SAMPLE,mode,config,long_edge=320)
            return session.render(config)
        def publish(image):
            from film_foundry.ui.comparison_view import ComparisonView
            frame=ComparisonView.pixmap(image).toImage()
            if len(self.images)>=5:self.images.pop(next(iter(self.images)))
            self.images[key]=frame;self.viewer.show_image(frame);self.status.clear()
        self.submit(render,publish,text('正在处理示例…','Processing example…'))


def dismiss_invitation(owner):
    try:save_preferences(tour_seen=True)
    except OSError as exc:owner.status.set(str(exc))
    banner=getattr(owner.library_view,'tour_banner',None)
    if banner is not None:banner.hide()


def open_tour(owner):
    from film_foundry.ui.inline_editor import present_inline_editor
    dialog=FirstTour(owner)
    dialog.finished.connect(lambda *_:dismiss_invitation(owner))
    present_inline_editor(owner.workspace_host.currentWidget(),dialog)
    return dialog


def install_invitation(owner,library):
    if load_preferences().get('tour_seen'):return
    parent=library.recovery_banner.parentWidget()
    banner=QtWidgets.QWidget(parent);library.tour_banner=banner
    row=QtWidgets.QHBoxLayout(banner);row.setContentsMargins(0,1,0,1)
    row.addWidget(QtWidgets.QLabel(text('第一次使用？用示例照片走一遍暗房流程。','New here? Explore the darkroom with a sample photo.')),1)
    start=QtWidgets.QPushButton(text('开始导览','Start tour'));row.addWidget(start)
    start.clicked.connect(lambda:open_tour(owner))
    skip=QtWidgets.QToolButton();skip.setText(text('跳过','Skip'));row.addWidget(skip)
    skip.clicked.connect(lambda:dismiss_invitation(owner))
    parent.layout().insertWidget(parent.layout().indexOf(library.recovery_banner)+1,banner)

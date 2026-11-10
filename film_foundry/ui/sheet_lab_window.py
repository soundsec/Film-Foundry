"""Sheet-material frontends: edit data, call sessions, observe immutable results."""
from film_foundry.core.material_structure import from_record
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields
import inspect
import json
import math
from film_foundry.app.material_lab_session import MaterialLabSession
from film_foundry.core import sheet_common
from film_foundry.core.material_family import ViewingConditions
from film_foundry.ui.qt import QtCore,QtWidgets,_application
from film_foundry.ui.curve_editor import text
from film_foundry.ui.image_view import ImageView
from film_foundry.ui.comparison_view import ComparisonView
from film_foundry.ui.material_lab_window import MaterialLabWindow,_Signals
from film_foundry.ui import sheet_workspace_design as design
from film_foundry.app import sheet_inspection

TITLES={'paper':('相纸工作室','Photographic Paper'),'wet':('湿版工作室','Wet Collodion'),
        'cyanotype':('蓝晒工作室','Cyanotype'),'platinum':('铂钯工作室','Platinum / Palladium')}
LABELS={'wetting_seconds':('润湿时间尺度 s','Wetting time scale s'),'drying_lock_strength':('干燥通透损失','Drying access loss'),
 'enhancement_seconds':('增强时间尺度 s','Intensification time scale s'),'enhancement_nucleation':('非影像成核','Background nucleation'),
 'silver_load':('手动增强供银','Manual enhancement supply'),
 'external_silver_load':('感光浴带入银量','Sensitizer silver carryover'),'physical_development_seconds':('外源银沉积时间尺度 s','Physical development time scale s'),
 'structure_model':('纹理模型','Structure model'),'material_long_edge_mm':('材料长边 mm','Material long edge mm'),
 'texture_scale_mm':('细纹尺度 mm','Fine texture scale mm'),'process_scale_mm':('工艺纹理尺度 mm','Process structure scale mm'),
 'paper_base':('纸基类型','Paper base'),'water_uptake_seconds':('吸水时间尺度 s','Water uptake time scale s'),
 'surface_gloss':('干纸光泽','Dry surface gloss'),'drydown':('干后影调加深','Dry-down strength'),
 'region':('处理范围','Treatment area'),'radius':('半径 / 短边','Radius / short edge'),'coverage':('有效接触','Effective contact'),'airflow':('通风程度','Airflow'),
 'pour_direction':('倒膜方向','Pour direction'),'flow_ridges':('流膜起伏','Flow ridges'),'edge_pooling':('排液边积膜','Drain-edge pooling'),
 'pinholes':('针孔缺膜','Pinholes'),'pour_uniformity':('显影液铺展均匀度','Developer coverage'),
 'brush_direction':('刷涂方向','Brush direction'),'brush_overlap':('笔触重叠','Brush overlap'),
 'pigment_binding':('颜料固着能力','Pigment binding'),'paper_buffer':('纸中碱性影响','Paper alkaline influence'),
 'coating_method':('上药方式','Coating method'),'paper_sizing':('纸张施胶程度','Paper sizing'),
 'kind':('乳剂类型','Emulsion'),'support':('承载与观看','Support / viewing'),
 'half_exposure':('半响应曝光','Half-response dose'),'response_contrast':('响应反差','Response contrast'),
 'maximum_density':('单位物质密度','Density per unit'),'paper_reflectance':('纸面反射率','Paper reflectance'),
 'warmth':('银像暖度','Silver warmth'),'coating_variation':('涂布不均','Coating variation'),
 'edge_loss':('边缘缺膜','Edge coating loss'),'texture':('载体纹理','Support texture'),
 'fog_level':('乳剂灰雾','Emulsion fog'),'sensitivity_remaining':('剩余感光能力','Remaining sensitivity'),
 'wash_seconds':('水洗时间尺度 s','Washing time scale s'),'drying_seconds':('干燥时间尺度 s','Drying time scale s'),
 'exposure_delay_s':('曝光前等待 s','Delay before exposure s'),'humidity':('相对湿度','Relative humidity'),
 'particle_reflectance':('银颗粒反射率','Silver reflectance'),'base_transmission':('玻璃透射率','Glass transmission'),
 'actinic_gain':('感光代理增益','Actinic proxy gain'),'green_sensitivity':('绿色代理响应','Green proxy sensitivity'),
 'sensitizer_remaining':('感光剂剩余活性','Sensitizer activity'),'oxidation_seconds':('氧化时间尺度 s','Oxidation time scale s'),
 'alkali_susceptibility':('碱损伤敏感性','Alkali susceptibility'),'platinum_fraction':('铂占比','Platinum fraction'),
 'clearing_seconds':('清除时间尺度 s','Clearing time scale s'),
 'seconds':('持续时间 s','Duration s'),'temperature_c':('温度 °C','Temperature °C'),'activity':('药浴活性','Bath activity'),
 'developer':('显影能力','Developer capability'),'bleach_activity':('漂白活性','Bleach activity'),'fix_activity':('定影活性','Fixer activity'),
 'water_changes':('换水量','Water exchange'),'strength':('额外受光剂量','Added light dose'),'edge':('受光边缘','Opening edge'),
 'oxygen':('氧化条件','Oxygen availability'),'amount':('清漆覆盖','Varnish coverage'),
 'fingerprints':('指纹','Fingerprints'),'reagent_residue':('药液残留','Reagent residue'),
 'position_x':('横向位置','Horizontal position'),'position_y':('纵向位置','Vertical position'),'angle_degrees':('方向 °','Angle °')}
OPS={'load_enhancer':('载入增强银','Charge intensifier'),'intensify':('加银增强','Intensify silver'),
 'prepare':('涂布','Coat plate'),'sensitize':('感光化','Sensitize'),'expose':('曝光','Expose'),
 'develop':('显影','Develop'),'stop':('停显','Stop development'),'fix':('定影','Fix'),'bleach_fix':('漂白定影','Bleach-fix'),
 'wash':('水洗','Wash'),'clear':('清除残余铁盐','Clear residual salts'),'oxidize':('氧化成蓝','Oxidize'),
 'alkali_contact':('接触碱性介质','Alkaline contact'),'light_leak':('误开见光','Accidental opening'),
 'handling':('施加表面痕迹','Handling marks'),'wait':('等待 / 干燥','Wait / dry'),'dry':('干燥','Dry'),'varnish':('上清漆','Varnish')}
STAGES={'enhancing':('增强浴已载入','Intensifier charged'),'coated':('已涂布','Coated'),'sensitized':('已感光化','Sensitized'),'exposed':('已曝光','Exposed'),
 'developed':('已显影','Developed'),'stopped':('已停显','Stopped'),'fixed':('已定影','Fixed'),'washed':('已水洗','Washed'),
 'oxidized':('已氧化','Oxidized'),'cleared':('已清除残盐','Cleared'),'dry':('已干燥','Dried'),'varnished':('已上清漆','Varnished')}
CHOICES={
 'structure_model':(('millimetres_v1','材料毫米坐标','Material millimetres'),('relative_v1','旧相对画幅','Legacy relative frame')),
 'paper_base':(('rc','RC 涂塑纸','Resin-coated'),('fiber','纤维纸基','Fibre base')),
 'region':(('all','整张','Whole sheet'),('spot','局部圆区','Local spot')),
 'coating_method':(('rod','玻璃棒上药','Glass rod'),('brush','毛刷上药','Brush')),
 'kind':(('variable_contrast','可变反差黑白','Variable-contrast B&W'),('graded','定级黑白','Graded B&W'),('direct_positive','直接正像','Direct positive'),('color','彩色显色相纸','Chromogenic color')),
 'support':(('glass_negative','玻璃负片 · 透射','Glass negative · transmission'),('ambrotype','玻璃黑底正像','Ambrotype'),('tintype','深色金属正像','Tintype')),
 'developer':(('material','匹配材料','Matched to material'),('bw','黑白显影','B&W developer'),('color','彩色显影','Color developer')),
 'edge':(('left','左','Left'),('right','右','Right'),('top','上','Top'),('bottom','下','Bottom'))}
for key in ('pour_direction','brush_direction'):
    CHOICES[key]=(('down','向下','Down'),('up','向上','Up'),('left','向左','Left'),('right','向右','Right'))
RANGES={'wetting_seconds':(.1,86400),'drying_lock_strength':(0,4),'enhancement_seconds':(.1,86400),
 'physical_development_seconds':(.1,86400),'half_exposure':(.001,20),'response_contrast':(.2,8),'maximum_density':(.01,10),
 'material_long_edge_mm':(10,1000),'texture_scale_mm':(.05,100),'process_scale_mm':(.1,200),
 'water_uptake_seconds':(.1,7200),'drydown':(0,.4),'radius':(.01,2),'airflow':(0,100),
 'wash_seconds':(1,7200),'drying_seconds':(1,86400),'exposure_delay_s':(0,86400),
 'actinic_gain':(.001,20),'oxidation_seconds':(1,7200),'clearing_seconds':(1,7200),
 'seconds':(0,86400),'temperature_c':(0,60),'activity':(0,100),'bleach_activity':(0,100),
 'fix_activity':(0,100),'water_changes':(0,100),'strength':(0,10),'oxygen':(0,100),'angle_degrees':(-180,180)}
TIPS={'external_silver_load':('感光浴带入的有限可溶银。显影消耗，定影水洗排出，重复显影不补银；v3及以上生效。','Finite soluble silver carried from sensitization. Consumed by development, removed by fixing/washing, never replenished by repeated development. V3 and later.'),'kind':('常规相纸把曝光光量形成负像，通常接收底片投影；直接正像纸可直接形成正像。','Ordinary paper forms a negative of the exposure, typically receiving a negative projection. Direct-positive paper forms a positive.'),
 'flow_ridges':('倒膜形成的厚薄起伏，会同时改变可成像银量、干燥速度和后续显影。','Pour thickness changes silver capacity, drying and development.'),
 'pigment_binding':('固着较弱时水洗会带走更多成像颜料；继续加长水洗可能使影像变淡。','Weak binding lets washing remove more image pigment; longer washing can fade the image.'),
 'paper_buffer':('纸中碱性条件会在水洗时加重蓝色颜料损失。','Alkaline paper conditions increase cyan pigment loss during washing.'),
 'paper_sizing':('影响感光液进入纤维的程度和残铁清除的可达性。','Affects sensitizer absorption into fibers and access for iron clearing.'),
 'coating_variation':('改变各处实际涂布量与可成像物质上限；0为均匀涂布。','Changes local coating and image-substance capacity; 0 gives a uniform coating.'),
 'edge_loss':('边缘涂层减少，降低当地可形成的影像量。','Reduced edge coating limits image substance there.'),
 'half_exposure':('产生一半感光响应所需的相对光量；不是ISO或绝对照度。','Relative dose for half response; not ISO or absolute illuminance.'),
 'seconds':('本次操作推进的工艺时间；不要求等待同样的现实时间。','Process duration for this operation; no matching wall-clock wait.'),
 'humidity':('0到1的相对湿度；湿版影响干燥速度，铁盐材料影响曝光响应。','Relative humidity from 0 to 1; controls wet-plate drying or iron-process exposure response.'),
 'actinic_gain':('蓝／绿RGB感光代理的增益；不表示还原出紫外光。','Gain for the blue/green RGB actinic proxy; does not recover UV.'),
 'exposure_delay_s':('感光化后、实际曝光前再等待此时长；湿版会在这段时间失水。','Additional delay after sensitization before exposure; the wet plate dries during this interval.'),
 'platinum_fraction':('0为纯钯，1为纯铂；改变实际金属盐组成与形成后的影调。','0 is pure palladium, 1 is pure platinum; changes salt composition and the formed tone.')}


TIPS.update({
 'wetting_seconds':('控制药浴重新润湿；润湿不消除此前干燥造成的通透性损失。仅新v4湿版。','Controls rewetting without reversing earlier drying-induced permeability loss. New v4 wet plates.'),
 'drying_lock_strength':('空气中失水会降低后续药浴可达性；0关闭不可逆损失，仍保湿润变化。不是胶棉膜的精确溶剂模型。','Air drying reduces later chemical access. 0 disables irreversible loss while retaining wetness dynamics; not a calibrated solvent model.'),
 'enhancement_nucleation':('0时增强仅围绕已有银像；提高后无影像区也能成核，供银和处理时间会共同放大底雾。','0 grows silver only around existing image centres. Higher values allow background nuclei; supply and time also increase fog.'),
 'enhancement_seconds':('已形成银像与有限增强银共同决定速度；该系数是创作响应，不是商品增强液标定。','Existing silver and finite intensifier supply determine growth. Creative response, not a calibrated commercial bath.'),
 'silver_load':('一次实际新增的归一化银量，按涂层和覆盖分配。继续增强只消耗余量；再次点击载入才补银。','Actual added normalized silver per charge, distributed by coating and coverage. Continued intensification only consumes the remainder; charging again adds supply.'),
 'paper_base':('RC 的吸水、洗净和干燥较快；纤维纸保留更多湿润与残留。各系数为创作模型，未按商品纸标定。',
                'RC wets, washes and dries faster; fibre paper retains more moisture and residue. Creative model, not a calibrated stock.'),
 'water_uptake_seconds':('控制纸基吸水，同时影响局部洗清的有效反应时间；恒定条件分段继续不会重算过去。',
                          'Controls hydration and effective local wash/clear time. Continued intervals do not recompute the past.'),
 'drydown':('仅控制纸面变干时的光学影调变化，不增加银、染料或金属；纸基和湿润程度共同影响可见响应。',
             'Optical tone change as the paper dries; does not add silver, dye or metal. Response also depends on base and moisture.'),
 'surface_gloss':('干纸表面光泽。湿润会增加表面反光，实际外观还取决于观看和照明角度。',
                   'Dry surface gloss. Moisture increases reflection; appearance also depends on viewing and light angles.'),
 'region':('局部圆区按画面短边定义半径；未接触部分保留原材料。可以移动位置、分次处理。',
            'Spot radius is relative to the short image edge. Uncontacted material is retained; move the spot between steps.'),
 'humidity':('空气干燥时决定平衡湿润程度；铁盐材料的初始湿度也参与曝光响应。操作参数只作用本次过程。',
              'Sets equilibrium moisture during air drying. Initial iron-paper humidity also affects exposure; operation settings apply to this interval.')})


class _Number(QtWidgets.QDoubleSpinBox):
    def textFromValue(self,value):return f'{value:.6f}'.rstrip('0').rstrip('.')


class SheetLabWindow(MaterialLabWindow):
    """Reuse archive/worker/observation chrome without old-family process controls."""
    def __init__(self,kind):
        _application();QtWidgets.QMainWindow.__init__(self)
        self.session=MaterialLabSession(kind);self.kind=kind;self.busy=False;self._closing=False;self.last_error=None
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='sheet-'+kind)
        self.signals=_Signals(self);self.signals.done.connect(self._completed)
        self.setWindowTitle('Film Foundry · '+text(*TITLES[kind]));self.resize(1180,800);self.setMinimumSize(820,580)
        root=QtWidgets.QWidget();self.setCentralWidget(root);layout=QtWidgets.QVBoxLayout(root)
        heading=QtWidgets.QHBoxLayout();layout.addLayout(heading)
        title=QtWidgets.QLabel(text(*TITLES[kind])+" · "+text("实验中","Experimental"));title.setStyleSheet('font-size:22px;font-weight:600;padding:8px');heading.addWidget(title);heading.addStretch()
        self.layout_button=QtWidgets.QPushButton();heading.addWidget(self.layout_button)
        self.layout_button.clicked.connect(lambda:self.set_workspace_layout('studio' if self._workspace_layout=='compact' else 'compact'))
        self.file_bar=QtWidgets.QWidget();bar=QtWidgets.QHBoxLayout(self.file_bar);bar.setContentsMargins(0,0,0,0);layout.addWidget(self.file_bar)
        for label,callback in [(('打开照片','Open image'),self.open_image_dialog),(('光学来源…','Optical source…'),self.open_medium_dialog),
            (('读取状态 / 介质','Open state / medium'),self.load_dialog),(('重新开始','Restart'),self.restart)]:
            button=QtWidgets.QPushButton(text(*label));bar.addWidget(button);button.clicked.connect(callback)
        self._add_recipe_menu(bar)
        bar.addStretch()
        from film_foundry.ui.material_processing import install_processing_bar
        install_processing_bar(self,layout)
        self.flow_bar=QtWidgets.QHBoxLayout();layout.addLayout(self.flow_bar);self.flow_buttons={};self.flow_sequence=()
        self.workspace_splitter=QtWidgets.QSplitter();layout.addWidget(self.workspace_splitter,1)
        self.controls_scroll,self.controls,self.left=self._panel();self.workspace_splitter.addWidget(self.controls_scroll)
        self.config_tabs=QtWidgets.QTabWidget();self.left.addWidget(self.config_tabs)
        self.material_group=QtWidgets.QGroupBox(text('材料 · 开始后冻结','Material · frozen after starting'))
        material_layout=QtWidgets.QVBoxLayout(self.material_group);self.material_sections=QtWidgets.QToolBox();material_layout.addWidget(self.material_sections)
        from film_foundry.ui.theme import colors
        palette=colors()
        self.material_sections.setStyleSheet(f'QToolBox::tab {{background:{palette.surface};border:0;border-bottom:1px solid {palette.border};padding:0 8px;min-height:26px;text-align:left;}} QToolBox::tab:selected {{background:{palette.selected};color:{palette.text};font-weight:600;}}')
        self.material_controls={};definition=self.session.material_type()
        self.material_rows={};known=set();sections=list(design.SECTIONS[kind])
        for _,_,keys in sections:known.update(keys)
        sections.append(('感光响应与身份','Response & identity',tuple(f.name for f in fields(definition) if f.name not in known)))
        for zh,en,keys in sections:
            page=QtWidgets.QWidget();form=self._form(page);self.material_sections.addItem(page,text(zh,en))
            for key in keys:
                control=self._control(form,key,getattr(definition,key));self.material_controls[key]=control;self.material_rows[key]=(form,control)
        self.seed=QtWidgets.QDoubleSpinBox();self.seed.setDecimals(0);self.seed.setRange(0,4294967295);form.addRow(text('片张身份','Sheet identity'),self.seed)
        self.config_tabs.addTab(self.material_group,text('材料','Material'))
        self.process_group=QtWidgets.QGroupBox(text('处理步骤','Processing'));process_layout=QtWidgets.QVBoxLayout(self.process_group)
        self.operation_choice=QtWidgets.QComboBox();process_layout.addWidget(self.operation_choice)
        self.operation_stack=QtWidgets.QStackedWidget();process_layout.addWidget(self.operation_stack)
        self.operation_controls={};self.operation_pages={}
        self.enhancement_intent={}
        for operation,function in self.session.module.OPERATION_FUNCTIONS.items():
            page=QtWidgets.QWidget();form=self._form(page);controls={}
            function=sheet_common.handling if operation=='handling' else function
            for key,param in inspect.signature(function).parameters.items():
                if param.default is inspect.Parameter.empty:continue
                value=param.default
                if value is None:value=definition.humidity
                controls[key]=self._control(form,key,value)
            self.operation_controls[operation]=controls;self.operation_pages[operation]=self.operation_stack.addWidget(page)
            if kind=='wet' and operation=='load_enhancer':
                enabled=QtWidgets.QCheckBox(text('启用供银期望','Silver supply intent'));form.addRow(enabled)
                strength=self._control(form,'silver_load',.25)
                form.labelForField(strength).setText(text('供银期望','Expected silver supply'))
                strength.setToolTip(text('0–1线性映射本次供银；关闭后用手动量。密度、反差、已有影像和背景成核会耦合，不保证图像明暗线性。',
                    'Maps 0–1 linearly to this charge. Disable to use manual supply. Density, contrast, existing centres and background nucleation are coupled; RGB is not linear.'))
                self.enhancement_intent={'enabled':enabled,'strength':strength}
                enabled.toggled.connect(lambda:self._sync())
            if kind=='wet' and operation in ('load_enhancer','intensify'):
                page.setToolTip(text('可选工序：定影后载入有限增强银，再分段增强，最后水洗结束增强。水洗和定影会排出未沉积银；已成银保留。观察不推进反应。',
                    'Optional: charge finite silver after fixing, intensify in intervals, then wash to end the bath. Washing/fixing removes undeveloped supply and retains deposited silver. Viewing does not advance reactions.'))
            if kind!='wet' and operation in ('wash','clear'):
                page.setToolTip(text('局部处理只作用于接触区域，并使当地纸基吸水。湿润历史影响后续洗清；接触比例不是有限药液库存。',
                    'Treat and wet only the contacted region. Hydration affects later washing/clearing; contact is not a finite bath inventory.'))
            if kind!='wet' and operation=='dry':
                page.setToolTip(text('显式推进空气干燥，保留纸面湿润状态；蓝晒同时氧化成蓝，其他两族不改变成像物质。',
                    'Advance air drying and retain paper moisture. Cyanotype also oxidizes to blue; other image pools remain unchanged.'))
        self.empty_page=self.operation_stack.addWidget(QtWidgets.QWidget())
        self.execute_button=QtWidgets.QPushButton(text('执行此步','Run this step'));process_layout.addWidget(self.execute_button)
        self.execute_button.clicked.connect(lambda:self.process(self.operation_choice.currentData()))
        self.standard_button=QtWidgets.QPushButton(text('按当前参数完成标准流程','Complete reference process'));process_layout.addWidget(self.standard_button)
        self.standard_button.setToolTip(text('用于尚未开始的片张。按材料声明的步骤顺序执行，使用各步骤当前设置；误开、碱损伤和指纹需单独施加。','For a fresh sheet. Uses the family reference sequence and current step settings. Opening, alkali damage and handling are separate actions.'))
        self.standard_button.clicked.connect(self.run_standard)
        process_layout.addStretch();self.config_tabs.addTab(self.process_group,text('工艺','Process'));self.left.addStretch()
        self.operation_choice.currentIndexChanged.connect(self._operation_changed)
        self.operation_choice.currentIndexChanged.connect(self._enable_execute)
        self.preview_surface=QtWidgets.QWidget();preview=QtWidgets.QVBoxLayout(self.preview_surface);self.workspace_splitter.addWidget(self.preview_surface)
        self.viewer=ComparisonView();preview.addWidget(self.viewer,1)
        from film_foundry.ui.material_inspector import MaterialInspector
        self.inspector=MaterialInspector(self,OPS);preview.addWidget(self.inspector)
        metric_row=QtWidgets.QHBoxLayout();preview.addLayout(metric_row);self.metric_bars=[]
        for _ in range(3):
            progress=QtWidgets.QProgressBar();progress.setRange(0,1000);progress.setTextVisible(True);progress.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            progress.setStyleSheet(f'QProgressBar::chunk {{background:{palette.selected};}}');metric_row.addWidget(progress);self.metric_bars.append(progress)
        self.body_choice=QtWidgets.QComboBox();self.body_choice.hide()
        self.view_group=QtWidgets.QGroupBox(text('观察','Observation'));view_layout=QtWidgets.QHBoxLayout(self.view_group);form=QtWidgets.QFormLayout();view_layout.addLayout(form)
        self.view_ev=self._number(form,text('观看曝光 EV','Viewing EV'),0,-10,10);preview.addWidget(self.view_group)
        self.inspection=QtWidgets.QComboBox();self.inspection.addItem(text('成像观察','Image'),'image');form.addRow(text('查看','Inspect'),self.inspection)
        self.inspection.setToolTip(text('材料分布图只作检查，导出始终使用成像观察。','Material maps are diagnostic only. Export always uses the image observer.'))
        self.view_form=form;self.view_angle=self.light_angle=None
        if kind in TITLES:
            self.angle_form=QtWidgets.QFormLayout();view_layout.addLayout(self.angle_form)
            self.view_angle=self._number(self.angle_form,text('观看倾角 °','View angle °'),15,-80,80)
            self.light_angle=self._number(self.angle_form,text('光源倾角 °','Light angle °'),-30,-80,80)
            self.view_angle.valueChanged.connect(self.request_preview);self.light_angle.valueChanged.connect(self.request_preview)
        self.view_group.setToolTip(text('只改变观看与导出，不改变材料形成，也不进入下一次光学曝光。','Changes observation/export only, never formation or the next optical exposure.'))
        save_bar=QtWidgets.QHBoxLayout();preview.addLayout(save_bar);self.save_buttons={}
        for key,zh,en in (('state','保存状态','Save state'),('products','保存最终介质','Save medium'),('png','导出图像','Export image')):
            button=QtWidgets.QPushButton(text(zh,en));save_bar.addWidget(button);button.clicked.connect(lambda checked=False,k=key:self.save_dialog(k));self.save_buttons[key]=button
        self.history_toggle=self.inspector.history_button;self.history=self.inspector.history
        self.status=QtWidgets.QLabel();layout.addWidget(self.status)
        self.progress=QtWidgets.QProgressBar();self.progress.setRange(0,0);layout.addWidget(self.progress);self.progress.hide()
        self.preview_timer=QtCore.QTimer(self);self.preview_timer.setSingleShot(True);self.preview_timer.setInterval(120);self.preview_timer.timeout.connect(self.refresh_preview)
        self.view_ev.valueChanged.connect(self.request_preview)
        self.inspection.currentIndexChanged.connect(self.request_preview)
        self.right_scroll,self.right_controls,self.right_layout=self._panel();self.workspace_splitter.addWidget(self.right_scroll)
        self.set_workspace_layout('compact');self._sync()
        from film_foundry.ui import material_structure_controls
        material_structure_controls.setup(self)
        if kind=='paper':self.material_controls['kind'].currentIndexChanged.connect(self._sync)
        if kind in ('cyanotype','platinum'):
            self.material_group.setToolTip(text('使用蓝/绿 RGB 作为感光代理，并非恢复紫外光谱。涂布量限制真实成像物质，参数是创作模型而非品牌标定。','Blue/green RGB is an actinic proxy, not recovered UV. Coating limits image substance. Creative model, not a calibrated stock.'))

    def _panel(self):
        scroll=QtWidgets.QScrollArea();scroll.setWidgetResizable(True);scroll.setMinimumWidth(285)
        content=QtWidgets.QWidget();box=QtWidgets.QVBoxLayout(content);scroll.setWidget(content)
        return scroll,content,box

    @staticmethod
    def _form(parent):
        form=QtWidgets.QFormLayout(parent)
        form.setRowWrapPolicy(QtWidgets.QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        return form

    def _control(self,form,key,value):
        label=text(*LABELS[key])
        if key in CHOICES:
            control=QtWidgets.QComboBox()
            for data,zh,en in CHOICES[key]:control.addItem(text(zh,en),data)
            control.setCurrentIndex(control.findData(value));form.addRow(label,control)
        else:
            low,high=RANGES.get(key,(0,1));control=_Number();control.setRange(low,high);control.setDecimals(6);control.setValue(value)
            control.setSingleStep(.05 if high<=10 else 1);control.setKeyboardTracking(False);form.addRow(label,control)
        control.setToolTip(text(*TIPS[key]) if key in TIPS else label)
        return control

    def set_workspace_layout(self,mode):
        if mode not in ('compact','studio'):raise ValueError('Unknown layout')
        self._workspace_layout=mode
        if mode=='studio':
            self.config_tabs.removeTab(self.config_tabs.indexOf(self.process_group));self.right_layout.addWidget(self.process_group);self.right_scroll.show();self.process_group.show()
            self.workspace_splitter.setSizes([310,560,310])
        else:
            self.right_layout.removeWidget(self.process_group)
            if self.config_tabs.indexOf(self.process_group)<0:self.config_tabs.addTab(self.process_group,text('工艺','Process'))
            self.right_scroll.hide();self.workspace_splitter.setSizes([340,840,0])
        self.workspace_splitter.setStretchFactor(1,1)
        self.layout_button.setText(text('单侧布局','Compact') if mode=='compact' else text('双侧布局','Studio'))
        self.layout_button.setToolTip(text('切换单侧分页 / 左材料右工艺','Switch compact tabs / materials left, processing right'))

    @staticmethod
    def _values(controls):return {key:control.currentData() if isinstance(control,QtWidgets.QComboBox) else control.value() for key,control in controls.items()}

    def conditions(self):
        def direction(control):
            angle=math.radians(control.value()) if control is not None else 0
            return (math.sin(angle),0.,math.cos(angle))
        return self._preserve_saved_view(ViewingConditions(exposure_ev=self.view_ev.value(),view_direction=direction(self.view_angle),light_direction=direction(self.light_angle)))

    def _submit(self,action,label,render=True,body_index=None,restore_controls=False):
        key=self.inspection.currentData()
        view=self.conditions()
        preview=(lambda:sheet_inspection.preview(self.session,key) if key in sheet_inspection.options(self.session) else self.session.preview(view)) if key!='image' else None
        return super()._submit(action,label,render,body_index,restore_controls,preview=preview)

    def _operation_changed(self):
        self.operation_stack.setCurrentIndex(self.operation_pages.get(self.operation_choice.currentData(),self.empty_page))
        operation=self.operation_choice.currentData()
        if operation:
            label=design.STEP_NAMES[self.kind].get(operation,OPS[operation]);self.execute_button.setText(text(*label))
        for key,button in self.flow_buttons.items():button.setChecked(key==operation)

    def _select_step(self,operation):
        self.operation_choice.setCurrentIndex(self.operation_choice.findData(operation))
        if self._workspace_layout=='compact':self.config_tabs.setCurrentWidget(self.process_group)

    def _sync_workflow(self):
        sequence=design.WORKFLOWS[self.kind]
        if self.kind=='paper' and self.material_controls['kind'].currentData()=='color':sequence=tuple('bleach_fix' if x=='fix' else x for x in sequence)
        if self.flow_sequence!=sequence:
            while self.flow_bar.count():self.flow_bar.takeAt(0).widget().deleteLater()
            self.flow_buttons={};self.flow_sequence=sequence
            for i,operation in enumerate(sequence):
                label=design.STEP_NAMES[self.kind].get(operation,OPS[operation])
                button=QtWidgets.QPushButton(f'{i+1}  '+text(*label));button.setCheckable(True)
                button.clicked.connect(lambda checked=False,op=operation:self._select_step(op));self.flow_bar.addWidget(button);self.flow_buttons[operation]=button
        for op,button in self.flow_buttons.items():
            button.setEnabled(not self.busy);button.setToolTip(text('配置本步；只能在允许的阶段执行。','Configure this step. Execution requires a valid material stage.'))

    def _sync(self):
        session=self.session;state=session.state;group=session.group
        self.processing_bar.sync()
        self._sync_recipe_actions()
        self.file_bar.setEnabled(not self.busy);self.process_group.setEnabled(not self.busy);self.view_group.setEnabled(not self.busy)
        self.material_group.setEnabled(not self.busy)
        editable=not self.busy and ((state is None and group is None) or getattr(self,'_planning_batch',False))
        for control in self.material_controls.values():control.setEnabled(editable)
        self.seed.setEnabled(editable)
        self._sync_workflow()
        selected=self.operation_choice.currentData();self.operation_choice.blockSignals(True);self.operation_choice.clear()
        # All operation parameter pages remain selectable before starting, for batch/reference planning.
        available=session.operations
        listed=tuple(dict.fromkeys((*self.flow_sequence,*session.module.OPERATION_FUNCTIONS)))
        for operation in listed:self.operation_choice.addItem(text(*OPS[operation]),operation)
        index=self.operation_choice.findData(selected)
        stage=state.stage if state else 'products' if group else None
        if stage!=getattr(self,'_last_stage',None) or selected not in available:
            next_op=design.next_step(self.kind,state,available,self.flow_sequence)
            if next_op:index=self.operation_choice.findData(next_op)
        self._last_stage=stage
        self.operation_choice.setCurrentIndex(max(0,index));self.operation_choice.blockSignals(False);self._operation_changed()
        self.execute_button.setEnabled(not self.busy and self.operation_choice.currentData() in available)
        self.standard_button.setEnabled(not self.busy and state is None and group is None and session.image is not None)
        for key,button in self.save_buttons.items():button.setEnabled(not self.busy and (state is not None or group is not None) and (key!='products' or group is not None))
        if state and not getattr(self,'_planning_batch',False):
            for key,control in self.material_controls.items():self._set_value(control,getattr(state.material,key))
            self.seed.setValue(state.seed)
        from film_foundry.contracts.material_capabilities import FAMILIES as capabilities
        version=state.schema_version if state else group.bodies[0].schema_version if group else capabilities[session.module.FAMILY]['current_version']
        for key in design.V2_KEYS[self.kind]:
            form,control=self.material_rows[key];form.setRowVisible(control,version>=2 or getattr(self,'_planning_batch',False))
        if self.kind=='wet':
            control=self.operation_controls['develop']['pour_uniformity'];control.setEnabled(version>=2)
            modern=version>=4
            for key in ('wetting_seconds','drying_lock_strength','enhancement_seconds','enhancement_nucleation'):
                self.material_controls[key].setEnabled(editable and modern)
            for operation,keys in {'sensitize':('coverage','temperature_c'),'develop':('coverage',),'fix':('coverage','temperature_c'),
                                   'wash':('coverage','temperature_c'),'dry':('humidity','temperature_c')}.items():
                for key in keys:self.operation_controls[operation][key].setEnabled(modern and not self.busy)
            self.enhancement_intent['enabled'].setEnabled(modern and not self.busy)
            intent=self.enhancement_intent['enabled'].isChecked()
            self.enhancement_intent['strength'].setEnabled(modern and not self.busy and intent)
            self.operation_controls['load_enhancer']['silver_load'].setEnabled(modern and not self.busy and not intent)
            reflect=self.material_controls['support'].currentData()!='glass_negative'
            self.angle_form.setRowVisible(self.view_angle,reflect);self.angle_form.setRowVisible(self.light_angle,reflect)
        else:
            modern=version>=(2 if self.kind=='paper' else 3)
            for key in design.PAPER_KEYS:
                form,control=self.material_rows[key];form.setRowVisible(control,modern or getattr(self,'_planning_batch',False))
            for operation in ('wash','clear'):
                for key,control in self.operation_controls.get(operation,{}).items():
                    if key in ('region','position_x','position_y','radius','coverage','temperature_c'):control.setEnabled(modern)
            self.angle_form.setRowVisible(self.view_angle,modern);self.angle_form.setRowVisible(self.light_angle,modern)
        from film_foundry.ui import material_structure_controls
        material_structure_controls.sync(self)
        if not self.busy:
            stage=text(*STAGES[state.stage]) if state else text('最终介质','Final medium') if group else text('等待曝光','Ready to expose') if session.image is not None else text('打开照片或光学来源','Open image or optical source')
            shape=next(iter(state.fields.values())).shape[:2] if state else next(iter(group.bodies[0].fields.values())).shape[:2] if group else session.image.shape[:2] if session.image is not None else None
            self.status.setText(stage+(f' · {shape[1]} × {shape[0]}' if shape else '')+(f' · {state.elapsed_seconds:g} s' if state else ''))
            self.inspector.sync()
            selected_map=self.inspection.currentData();self.inspection.blockSignals(True);self.inspection.clear();self.inspection.addItem(text('成像观察','Image'),'image')
            for key in sheet_inspection.options(session):self.inspection.addItem(text(*design.INSPECT[key]),key)
            self.inspection.setCurrentIndex(max(0,self.inspection.findData(selected_map)));self.inspection.blockSignals(False)
            values=sheet_inspection.metrics(session)
            for i,bar in enumerate(self.metric_bars):
                bar.setVisible(i<len(values))
                if i<len(values):
                    key,value=values[i];bar.setValue(round(min(1,max(0,value))*1000));bar.setFormat(text(*design.METRICS[key])+f'  {value:.0%}')
                    bar.setToolTip(text('由当前材料场抽样统计；不改变材料或推进工艺。','Sampled from the current material fields; does not change material or advance processing.'))

    def _enable_execute(self):self.execute_button.setEnabled(not self.busy and self.operation_choice.currentData() in self.session.operations)

    @staticmethod
    def _set_value(control,value):
        if isinstance(control,QtWidgets.QComboBox):control.setCurrentIndex(control.findData(value))
        else:control.setValue(value)

    def _restore_controls(self):
        if self.session.state is None:
            if self.session.group:
                material=from_record(self.session.material_type,json.loads(self.session.group.bodies[0].presentation_json)['material'])
                # Earlier bodies omit newer, inapplicable controls. Resolve their
                # display values without upgrading or rewriting the saved body.
                for key,control in self.material_controls.items():self._set_value(control,getattr(material,key))
            return
        for event in json.loads(self.session.state.process_json).get('events',[]):
            for key,control in self.operation_controls.get(event.get('operation'),{}).items():
                if key in event:
                    if isinstance(event[key],dict) and event[key].get('kind')=='spatial_fraction':
                        control.setToolTip(text('上一操作使用空间分布，当前数字是下一次操作设置，不是恢复出的分布。','The recorded step used a spatial field. This number configures the next step; it does not reconstruct that field.'))
                    else:self._set_value(control,event[key])

    def process(self,operation):
        if not self.processing_bar.source_ready():return False
        material=self._values(self.material_controls);seed=int(self.seed.value());params=self._values(self.operation_controls.get(operation,{}))
        if self.kind=='wet':
            if operation=='load_enhancer' and self.enhancement_intent['enabled'].isChecked():
                from film_foundry.app.wet_plate_intent import silver_supply_parameters
                params.update(silver_supply_parameters(strength=self.enhancement_intent['strength'].value()))
            if self.session.state is not None and self.session.state.schema_version<4:
                added={'sensitize':('coverage','temperature_c'),'develop':('coverage',),'fix':('coverage','temperature_c'),
                       'wash':('coverage','temperature_c'),'dry':('humidity','temperature_c')}
                for key in added.get(operation,()):params.pop(key,None)
        if self.kind!='wet' and self.session.state is not None and self.session.state.schema_version<(2 if self.kind=='paper' else 3):
            for key in ('region','position_x','position_y','radius','coverage','temperature_c'):
                if operation in ('wash','clear'):params.pop(key,None)
        return self._submit(lambda:self.session.process(operation,params,material,seed),text(*OPS[operation]))

    def run_standard(self):
        if not self.processing_bar.source_ready():return False
        material=self._values(self.material_controls);seed=int(self.seed.value());overrides={op:self._values(c) for op,c in self.operation_controls.items()}
        return self._submit(lambda:self.session.run_standard(material,seed,overrides),text('处理材料…','Processing material…'))

    def open_image_dialog(self):
        path,_=QtWidgets.QFileDialog.getOpenFileName(self,text('打开图像','Open image'),'','Images (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.webp *.dng)')
        if not path:return
        preserve=self.kind=='wet' and self.session.state is not None and self.session.state.stage in ('coated','sensitized')
        size=self.work_size.value()
        self.replace_work(lambda:self._submit(lambda:self.session.open_image(path,size,preserve),text('读取图像…','Reading image…')),preserve=preserve)



class SheetBatchWindow(SheetLabWindow):
    def __init__(self,kind,paths,*,load_source=True):
        self.paths=list(paths);self._batch_cancel=None;self._planning_batch=True;super().__init__(kind)
        self.batch_button=QtWidgets.QPushButton(text(f'处理选中的 {len(paths)} 张…',f'Process {len(paths)} images…'))
        self.centralWidget().layout().addWidget(self.batch_button);self.batch_button.clicked.connect(self.start_batch)
        from film_foundry.ui.material_batch_io import install_resume
        install_resume(self)
        if load_source:self._submit(lambda:self.session.open_image(paths[0],1600),text('读取首张图像','Reading first image'))

    def _sync(self):
        super()._sync();self.material_group.setEnabled(not self.busy)
        if not self.busy:self._batch_cancel=None
        if hasattr(self,'batch_button'):
            self.resume_batch_button.setEnabled(not self.busy)
            self.batch_button.setEnabled(not self.busy or self._batch_cancel is not None)
            self.batch_button.setText(text('停止批量处理','Stop batch') if self._batch_cancel else text(f'处理选中的 {len(self.paths)} 张…',f'Process {len(self.paths)} images…'))

    def start_batch(self,output=None):
        if isinstance(output,bool):output=None
        if self._batch_cancel:self._batch_cancel.cancel();return
        if self.busy:return
        if output is None:
            from film_foundry.ui.output_destination import OutputDestinationDialog
            from film_foundry.ui.inline_editor import present_inline_editor
            from film_foundry.ui.material_processing import batch_processing_details
            dialog=OutputDestinationDialog(text(f'已选 {len(self.paths)} 张',f'{len(self.paths)} images selected'),self.centralWidget(),details=batch_processing_details(self))
            present_inline_editor(self.centralWidget(),dialog,lambda:self.start_batch(dialog.directory))
            return
        material=self._values(self.material_controls);definition=self.session.material_type(**material)
        steps=[(op,{**params,**self._values(self.operation_controls.get(op,{}))}) for op,params in self.session.module.default_operations(definition)]
        size=self.work_size.value();seed=int(self.seed.value());view=self.conditions()
        from film_foundry.app.workspace_jobs import process_material_photos
        from film_foundry.app.control import CancelFlag,WorkflowControl
        self._batch_cancel=CancelFlag();control=WorkflowControl(cancel_requested=self._batch_cancel.requested)
        from film_foundry.ui.material_batch_io import capture_report
        adjustments=dict(self.session.scan_adjustments);storage_precision=self.session.storage_precision
        self._submit(lambda:capture_report(self,lambda:process_material_photos(self.kind,self.paths,output,material,steps,view,size,seed,on_result=self.result_ready.emit,control=control,scan_adjustments=adjustments,storage_precision=storage_precision)),text('批量处理中…','Processing batch…'),render=False)

    def closeEvent(self,event):
        if self._batch_cancel:self._batch_cancel.cancel()
        super().closeEvent(event)

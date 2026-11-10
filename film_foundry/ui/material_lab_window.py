"""Material pages with standalone compatibility. Processing stays in application/core."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields
import json
import math
from pathlib import Path
from film_foundry.app.material_lab_session import MaterialLabSession
from film_foundry.core.material_family import ViewingConditions
from film_foundry.ui.qt import QtCore,QtGui,QtWidgets,_application
from film_foundry.ui.image_view import ImageView
from film_foundry.ui.comparison_view import ComparisonView
from film_foundry.ui.curve_editor import text
from film_foundry.ui.material_delivery import MaterialDeliveryMixin

TITLES={"peel":("撕拉片工作室","Peel-apart Studio"),"integral":("即显工作室","Instant Studio"),"plate":("银版工作室","Silver Plate Studio")}
OPERATIONS={"environment":("设置后续环境","Set next environment"),"contaminate":("局部污染","Contaminate"),"clean":("清除污染","Clean residue"),"prepare":("准备银版","Prepare plate"),"polish":("抛光","Polish"),"sensitize":("感光化","Sensitize"),
    "expose":("曝光","Expose"),"spread":("铺药与接触","Spread reagent"),"advance":("推进处理时间","Advance time"),
    "peel":("撕开","Peel apart"),"eject":("出片","Eject"),"stabilize":("推进中和","Advance neutralization"),
    "finish":("保留当前成片","Keep current print"),
    "develop":("显影","Develop"),"fix":("定影","Fix"),"gild":("镀金","Gild"),"surface":("均匀表面处理","Uniform surface treatment"),
    'load_gilding':('载入金浴','Load gold bath'),'rinse_gilding':('冲去剩余金液','Rinse soluble gold'),
    "light_leak":("误开见光一次","Accidental opening"),"weather":("施加局部退化","Apply local wear"),
    "clear_negative":("清理负片残留","Clear negative residue"),'handling':('施加表面痕迹','Apply handling marks')}
STAGES={"fixing":"定影中","gilding":"镀金中","blank":"已备版","polished":"已抛光","sensitized":"已感光化","exposed":"已曝光","contact":"接触转移中",
    "ejected":"已出片","reacting":"显影转移中","stable":"已稳定","finished":"已保留当前成片","separated":"已撕开","cleared":"负片已清理","developed":"已显影","fixed":"已定影","gilded":"已镀金"}
PARAMETERS={"fixing_seconds":("定影时间尺度 s","Fixing time scale s",.1,7200),
    "developer_capacity":("显影剂容量","Developer capacity",0,10),
    "alkali_capacity":("碱容量","Alkali capacity",0,10),
    "neutralizer_capacity":("中和层容量","Neutralizer capacity",0,10),
    "neutralization_delay_seconds":("中和延迟 s","Neutralization delay s",0,86400),
    "neutralization_seconds":("中和释放时间 s","Acid release time s",.1,86400),
    "interlayer_adhesion":("片间粘附","Interlayer adhesion",0,1),
    "receiver_cohesion":("接收层内聚强度","Receiver cohesion",0,1),
    "donor_cohesion":("供体层内聚强度","Donor cohesion",0,1),
    "separation_variation":("局部强度差异","Local strength variation",0,1),
    "material_long_edge_mm":("材料长边 mm","Material long edge mm",10,1000),
    "texture_scale_mm":("细纹尺度 mm","Fine texture scale mm",.05,100),
    "process_scale_mm":("工艺纹理尺度 mm","Process structure scale mm",.1,200),
    "gilding_seconds":("镀金时间尺度 s","Gilding time scale s",.1,7200),
    'gold_capacity':('金液容量','Gold solution capacity',0,10),
    'gold_adhesion':('金层附着增强','Gold adhesion strength',0,1),
    "light_sensitivity":("受光敏感度","Light sensitivity",0,2),
    "temperature_sensitivity":("温度响应强度","Temperature sensitivity",0,2),
    "inhibitor_sensitivity":("污染抑制敏感度","Inhibitor sensitivity",0,2),
    "initial_contamination":("初始材料污染","Initial material contamination",0,1),
    "half_exposure":("半响应曝光","Half-response exposure",.001,100),
    "reaction_seconds":("反应时间尺度（秒）","Reaction time scale (s)",.1,3600),
    "maximum_density":("单位物质密度","Density per unit",.01,10),"paper_reflectance":("纸面反射率","Paper reflectance",.01,1),
    "stabilization_seconds":("旧材料稳定时间 s","Legacy stabilization s",.1,7200),
    "roughness":("金属粗糙度","Metal roughness",.02,1),"metal_reflectance":("金属反射率","Metal reflectance",.01,1),
    "particle_reflectance":("颗粒反射率","Particle reflectance",.01,1),
    "response_contrast":("成像响应反差","Exposure response contrast",.5,3),
    "batch_variation":("批次差异强度","Batch variation",0,1)}


class _Signals(QtCore.QObject):
    done=QtCore.Signal(object,object,str)


class MaterialLabWindow(MaterialDeliveryMixin, QtWidgets.QMainWindow):
    closed=QtCore.Signal()
    result_ready=QtCore.Signal(object)
    print_requested=QtCore.Signal(object)
    def __init__(self,kind):
        _application();super().__init__()
        self.session=MaterialLabSession(kind);self.kind=kind
        self.busy=False;self._closing=False;self.last_error=None
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix="material-"+kind)
        self.signals=_Signals(self);self.signals.done.connect(self._completed)
        self.setWindowTitle("Film Foundry · "+text(*TITLES[kind]));self.resize(1160,800)
        self.setMinimumSize(780,560)
        root=QtWidgets.QWidget();self.setCentralWidget(root);layout=QtWidgets.QVBoxLayout(root)
        heading=QtWidgets.QHBoxLayout();layout.addLayout(heading)
        title=QtWidgets.QLabel(text(*TITLES[kind])+" · "+text("实验中","Experimental"));title.setStyleSheet("font-size:22px;font-weight:600;padding:8px;");heading.addWidget(title)
        title.setToolTip(text("工艺时间按步骤推进；导出采用工作尺寸，可选择白边","Process time advances explicitly; export uses working dimensions and optional borders"))
        heading.addStretch();self.layout_button=QtWidgets.QPushButton();heading.addWidget(self.layout_button)
        self.layout_button.clicked.connect(lambda:self.set_workspace_layout("studio" if self._workspace_layout=="compact" else "compact"))
        self.file_bar=QtWidgets.QWidget();toolbar=QtWidgets.QHBoxLayout(self.file_bar);toolbar.setContentsMargins(0,0,0,0)
        for zh,en,callback in (("打开图像 / 新建","Open image / New",self.open_image_dialog),('光学来源…','Optical source…',self.open_medium_dialog),("读取材料存档","Open archive",self.load_dialog)):
            button=QtWidgets.QPushButton(text(zh,en));button.clicked.connect(callback);toolbar.addWidget(button)
        restart=QtWidgets.QPushButton(text("重新开始","Restart"));restart.clicked.connect(self.restart);toolbar.addWidget(restart)
        self._add_recipe_menu(toolbar)
        toolbar.addStretch();layout.addWidget(self.file_bar)
        from film_foundry.ui.material_processing import install_processing_bar
        install_processing_bar(self,layout)
        split=QtWidgets.QSplitter();self.workspace_splitter=split;layout.addWidget(split,1)
        scroll=QtWidgets.QScrollArea();scroll.setWidgetResizable(True);scroll.setMinimumWidth(310)
        self.controls_scroll=scroll
        self.controls=QtWidgets.QWidget();self.left=QtWidgets.QVBoxLayout(self.controls);scroll.setWidget(self.controls);split.addWidget(scroll)
        self.material_group,form=self._group("材料定义（开始后冻结）","Material (frozen after starting)")
        self.material_controls={};default=self.session.material_type()
        for field in fields(default):
            key=field.name
            if key=="border_style":continue
            if key=='structure_model':
                control=QtWidgets.QComboBox()
                control.addItem(text('材料毫米坐标','Material millimetres'),'millimetres_v1');control.addItem(text('旧相对画幅','Legacy relative frame'),'relative_v1')
                control.setCurrentIndex(control.findData(getattr(default,key)));form.addRow(text('纹理模型','Structure model'),control)
                control.setToolTip(text('新材料在固定坐标中形成纹理，再采样到工作尺寸；旧档保留原模型，观看不重建纹理。','New materials form textures in fixed coordinates, then sample to working pixels. Old archives retain their generator; viewing never rebuilds it.'))
            elif key=="transfer":
                control=QtWidgets.QComboBox();control.addItem(text("彩色染料转移","Color dye transfer"),"dye");control.addItem(text("黑白银化合物转移","B&W silver transfer"),"silver_compound")
                form.addRow(text("成像物质","Transfer"),control)
            elif key=="batch_seed":
                control=QtWidgets.QSpinBox();control.setRange(0,2147483647);control.setValue(getattr(default,key))
                form.addRow(text("批次编号","Batch identity"),control)
            elif key=="donor_mode":
                control=QtWidgets.QComboBox()
                choices=(("供体残片","Donor residue","residue"),("黑白正负双片","B&W positive/negative","silver_negative"))
                for zh,en,value in choices:control.addItem(text(zh,en),value)
                control.setCurrentIndex(control.findData(getattr(default,key)))
                form.addRow(text("片体类型","Sheet type"),control)
            else:
                zh,en,low,high=PARAMETERS[key];control=self._number(form,text(zh,en),getattr(default,key),low,high)
            self.material_controls[key]=control
        from film_foundry.ui import material_structure_controls
        material_structure_controls.setup(self)
        if kind=="peel":
            self.material_controls["donor_mode"].currentIndexChanged.connect(lambda:self._match_donor(True))
            self.material_controls["transfer"].currentIndexChanged.connect(lambda:self._match_donor(False))
        self.seed=QtWidgets.QDoubleSpinBox();self.seed.setDecimals(0);self.seed.setRange(0,4294967295);form.addRow(text("片张身份","Sheet identity"),self.seed)
        self.process_group,form=self._group("工艺控制","Processing")
        self.process_form=form
        self.seconds=self._number(form,text("推进秒数","Advance seconds"),30,0,7200)
        self.process_controls={}
        if kind!="plate":
            self.direction=QtWidgets.QComboBox()
            for zh,en,key in (("向下","Down","down"),("向上","Up","up"),("向左","Left","left"),("向右","Right","right")):self.direction.addItem(text(zh,en),key)
            form.addRow(text("铺药方向","Spread direction"),self.direction)
            for key,zh,en,value,lo,hi in (("temperature_c","温度 °C","Temperature °C",20,-10,50),
                ("coverage","药剂覆盖","Reagent coverage",1,0,1),("contact","接触程度","Contact",1,0,1),
                ("spread_seconds","铺药用时","Spread seconds",2,0,120),("roller_streaks","滚轴条痕","Roller streaks",0,0,1),
                ("edge_loss","边缘缺药","Edge loss",0,0,1),("reagent_load","手动药囊载量","Manual paste load",1,0,4)):
                if key=="temperature_c":continue
                self.process_controls[key]=self._number(form,text(zh,en),value,lo,hi)
            self.shortage_enabled=QtWidgets.QCheckBox(text('启用缺药期望','Reagent shortage intent'));form.addRow(self.shortage_enabled)
            self.shortage_strength=self._number(form,text('缺药比例','Supplied load shortfall'),0,0,1)
            self.shortage_enabled.toggled.connect(lambda:self._sync())
            self.shortage_strength.setToolTip(text('线性减少药囊载量；同时改变覆盖、速度、可成像总量、供体显影和中和比例。图像明暗不保证线性。关闭时恢复手动载量。',
                'Linearly reduces supplied paste. Couples coverage, speed, image conversion, donor development and neutralization ratio. RGB is not linear. Disable to use manual load.'))
            from film_foundry.core.transfer_inventory import MATERIAL_LIMITS
            for key in MATERIAL_LIMITS:self.material_controls[key].setToolTip(text('新材料的归一化容量与时间尺度，并非实测浓度或pH。显影消耗还原剂；酸层消耗碱。开始后冻结，旧档不启用。',
                'Normalized capacities and time scales, not measured concentration or pH. Development spends reducing agent; the acid layer neutralizes alkali. Frozen after starting; inactive in legacy versions.'))
            self.process_controls['reagent_load'].setToolTip(self.shortage_strength.toolTip())
        else:
            for key,zh,en,value in (("streaks","抛光纹","Polish streaks",.15),("strength","感光化强度","Sensitization",1),
                ("amount","镀金目标","Gilding target",.5),("damage","本次表面磨损","Surface abrasion",0),("tarnish","本次变色","Added tarnish",0)):
                self.process_controls[key]=self._number(form,text(zh,en),value,0,1)
            self.process_controls["polish_angle"]=self._number(form,text("抛光方向 °","Polish direction °"),0,-180,180)
        self.defect_group,form=self._group("材料退化与事故","Deterioration and accidents")
        self.left.removeWidget(self.defect_group);self.left.insertWidget(0,self.defect_group)
        if kind!="plate":self.process_controls["temperature_c"]=self._number(form,text("显影温度 °C","Development temperature °C"),20,-10,50)
        self.defect_controls={}
        defect_parameters=(("reagent_decay","药剂退化","Reagent decay"),("contact_defects","局部接触不良","Contact defects"),
            ("reagent_leak","药剂渗漏","Reagent leakage"),("spread_unevenness","铺药失衡","Uneven spreading")) if kind!="plate" else (
            ("scratches","局部划伤","Scratches"),("pitting","表面蚀斑","Surface pits"),("edge_tarnish","边缘变色","Edge tarnish"))
        for key,zh,en in defect_parameters:self.defect_controls[key]=self._number(form,text(zh,en),0,0,1)
        if kind=="integral":
            self.defect_controls["light_protection"]=self._number(form,text("显影避光（1=充分）","Light protection (1=full)"),1,0,1)
            self.defect_controls["ambient_light"]=self._number(form,text("环境光（1=参考亮度）","Ambient light (1=reference)"),1,0,10)
            self.defect_controls["solar_warming"]=self._number(form,text("日晒额外升温 °C","Additional solar warming °C"),0,0,30)
            self.defect_controls["solar_warming"].setToolTip(text("手动指定额外片温，非自动估算。与遮光独立，最终片温上限50°C。","Explicit temperature rise, not an automatic estimate. Independent of shielding; final temperature is capped at 50°C."))
        self.leak_strength=self._number(form,text("误开见光强度","Accidental light dose"),0,0,1)
        self.leak_edge=QtWidgets.QComboBox()
        for zh,en,key in (("左边","Left","left"),("右边","Right","right"),("上边","Top","top"),("下边","Bottom","bottom")):self.leak_edge.addItem(text(zh,en),key)
        form.addRow(text("开启方向","Opening edge"),self.leak_edge)
        self.operation_buttons={}
        for key in (("light_leak",) if kind!="plate" else ("light_leak","weather")):
            button=QtWidgets.QPushButton(text(*OPERATIONS[key]));button.clicked.connect(lambda _=False,k=key:self.process(k));form.addRow(button);self.operation_buttons[key]=button
        hint_text=("数值是下一次操作的强度，0=不增加。重复施加会累积；误开见光仅限显影前。", "Values specify the next action; 0 adds nothing. Repeated actions accumulate. Opening precedes development.") if kind=="plate" else (
            ("避光0=无遮挡、1=充分保护；缺陷强度0=关闭。新材料铺药后可分段设置环境；旧材料条件固定，误开操作可累积。", "Protection: 0=unshielded, 1=protected. Defect strength 0=off. New sheets support segmented environment; legacy sheets freeze conditions at spreading; opening actions accumulate.") if kind=="integral" else
            ("缺陷强度0=关闭。新材料铺药后可分段设置环境；旧材料条件固定；误开仅限铺药前，重复操作会累积。", "Defect strength 0=off. New sheets support segmented environment; legacy sheets freeze conditions at spreading; repeated opening before spreading accumulates."))
        self.defect_group.setToolTip(text(*hint_text))
        operation_box=QtWidgets.QGroupBox(text("处理步骤","Process steps"));operation_layout=QtWidgets.QGridLayout(operation_box);self.left.insertWidget(0,operation_box)
        keys={"peel":("expose","spread","environment","advance","contaminate","clean","peel","clear_negative"),"integral":("expose","eject","spread","environment","advance","contaminate","clean","stabilize","finish"),
              "plate":("prepare","polish","sensitize","expose","develop","fix","load_gilding","gild","rinse_gilding","finish","surface")}[kind]
        for index,key in enumerate(keys):
            button=QtWidgets.QPushButton(text(*OPERATIONS[key]));button.clicked.connect(lambda _=False,k=key:self.process(k))
            operation_layout.addWidget(button,index//2,index%2);self.operation_buttons[key]=button
        self.finish_controls={}
        self.finish_group,form=self._group('表面与负片清理','Surface & negative clearing')
        if kind!='plate':
            for key,zh,en,value,lo,hi in (('fingerprints','指纹强度','Fingerprints',0,0,1),('reagent_residue','药水残留','Reagent residue',0,0,1),
                ('position_x','横向位置','Horizontal position',.5,0,1),('position_y','纵向位置','Vertical position',.5,0,1),('angle_degrees','指纹方向 °','Print angle °',0,-180,180)):
                self.finish_controls[key]=self._number(form,text(zh,en),value,lo,hi)
            button=QtWidgets.QPushButton(text(*OPERATIONS['handling']));button.clicked.connect(lambda:self.process('handling'));form.addRow(button);self.operation_buttons['handling']=button
        self.clearing_controls={}
        if kind=='peel':
            for key,zh,en,value,lo,hi in (('strength','负片刷洗力度','Negative clearing strength',1,0,1),('marks','刷洗痕迹','Brush marks',0,0,1),('angle_degrees','刷洗方向 °','Brush direction °',0,-180,180)):
                self.clearing_controls[key]=self._number(form,text(zh,en),value,lo,hi)
        self.operation_controls={};self.intent_controls={}
        if kind=='plate':
            from film_foundry.app.material_operations import resolve_parameters
            for operation in ('fix','gild'):
                defaults=resolve_parameters(self.session.module,operation,{})
                self.operation_controls[operation]={key:self._number(form,text(zh,en),defaults[key],lo,hi) for key,zh,en,lo,hi in (
                    ('seconds','药浴时间 s','Bath duration s',0,86400),('activity','药浴活性','Bath activity',0,100),
                    ('temperature_c','药浴温度 °C','Bath temperature °C',0,60),('coverage','有效接触比例','Effective contact',0,1))}
            self.operation_controls['gild']['amount']=self.process_controls['amount']
            self.operation_controls['load_gilding']={
                'gold_load':self._number(form,text('手动金液载量','Manual gold load'),.5,0,1),
                'coverage':self._number(form,text('金液覆盖','Gold solution coverage'),1,0,1)}
            self.operation_controls['rinse_gilding']={'strength':self._number(form,text('冲去比例','Rinsed fraction'),1,0,1)}
            self.gold_intent={'enabled':QtWidgets.QCheckBox(text('启用供金期望','Gold supply intent')),
                              'strength':self._number(form,text('供金比例','Gold supply fraction'),.5,0,1)}
            form.addRow(self.gold_intent['enabled']);self.gold_intent['enabled'].toggled.connect(lambda:self._sync())
            hint=text('线性控制本次金液载量；实际沉积受材料容量、时间、接触和目标限制。耦合金属色调及颗粒附着；不防硫化、不补回丢失影像。关闭时用手动载量。',
                'Linearly controls this gold load. Capacity, time, contact and target limit deposition. Couples tint and particle adhesion; neither prevents sulfide tarnish nor restores lost image. Disable to use manual load.')
            for control in self.gold_intent.values():control.setToolTip(hint)
            for key in ('gold_capacity','gold_adhesion'):self.material_controls[key].setToolTip(hint)
        if kind!='plate':
            from film_foundry.app.material_operations import resolve_parameters
            definitions={
                'environment':(('temperature_c','片温 °C','Sheet temperature °C',-10,50),
                    ('light_protection','遮光程度','Light protection',0,1),('ambient_light','相对环境光','Relative ambient light',0,10),
                    ('solar_warming','额外升温 °C','Additional warming °C',0,30)),
                'contaminate':(('amount','污染量','Contamination amount',0,1),('position_x','横向位置','Horizontal position',0,1),
                    ('position_y','纵向位置','Vertical position',0,1),('radius','范围','Radius',.01,1)),
                'clean':(('strength','清除比例','Removal fraction',0,1),)}
            for operation,specs in definitions.items():
                defaults=resolve_parameters(self.session.module,operation,{})
                self.operation_controls[operation]={key:self._number(form,text(zh,en),defaults[key],lo,hi) for key,zh,en,lo,hi in specs}
            if kind=='peel':
                defaults=resolve_parameters(self.session.module,'peel',{})
                self.operation_controls['peel']={key:self._number(form,text(zh,en),defaults[key],lo,hi) for key,zh,en,lo,hi in (
                    ('angle_degrees','撕开角度 °','Peel angle °',10,180),('speed_mm_s','撕开速度 mm/s','Peel speed mm/s',.1,1000))}
                defaults=resolve_parameters(self.session.module,'clean',{})
                for key,zh,en,lo,hi in (('abrasion','机械磨损','Abrasion',0,1),('marks','刷洗不匀','Brush variation',0,1),('angle_degrees','刷洗方向 °','Brush angle °',-180,180)):
                    self.operation_controls['clean'][key]=self._number(form,text(zh,en),defaults[key],lo,hi)
                for operation,controls in (('clean',self.operation_controls['clean']),('handling',self.finish_controls)):
                    control=QtWidgets.QComboBox();control.addItem(text('接收片','Receiver'),'receiver');control.addItem(text('供体／负片','Donor / negative'),'donor')
                    control.setCurrentIndex(control.findData(resolve_parameters(self.session.module,operation,{})['target']))
                    form.addRow(text('操作片体','Target sheet'),control);controls['target']=control
                for key in ('interlayer_adhesion','receiver_cohesion','donor_cohesion','separation_variation'):
                    self.material_controls[key].setToolTip(text('仅新v4：粘附与两层内聚强度共同决定撕开时哪层局部留在另一片。温度、速度、角度和接触也参与，不能独立保证某一外观。',
                        'New v4 only: adhesion and cohesion determine which layer stays on the other sheet. Temperature, speed, angle and contact also participate; appearance is coupled.'))
            self.environment_mode=QtWidgets.QComboBox()
            self.environment_mode.addItem(text('原理参数','Process parameters'),'parameters')
            self.environment_mode.addItem(text('偏暖期望','Warm-shift intent'),'warm_shift')
            form.addRow(text('驱动方式','Drive mode'),self.environment_mode)
            self.intent_controls['mode']=self.environment_mode
            self.intent_controls['strength']=self._number(form,text('偏暖失衡','Warm imbalance'),0,0,1)
            self.environment_mode.setToolTip(text('期望值线性映射为20–40°C的后续片温；同时改变转移速度、通道损失和反应药痕，不保证屏幕RGB线性。0关闭偏暖，已有成像不会回退。',
                'Intent maps linearly to 20–40°C for future intervals, coupling transfer speed, channel loss and reaction stains. RGB is not linear; zero disables warming without undoing formed material.'))
            self.environment_mode.currentIndexChanged.connect(lambda:self._sync())
        self.config_tabs=QtWidgets.QTabWidget();self.left.addWidget(self.config_tabs)
        groups=[(self.material_group,'材料','Material'),(self.process_group,'工艺','Process'),(self.defect_group,'事故','Defects')]
        if kind!='plate':groups.append((self.finish_group,'表面','Surface'))
        else:self.finish_group.hide()
        for group,zh,en in groups:
            self.left.removeWidget(group)
            self.config_tabs.addTab(group,text(zh,en))
        self.view_group=QtWidgets.QGroupBox(text("观看条件（不改材料）","Viewing (read-only)"))
        view_layout=QtWidgets.QHBoxLayout(self.view_group);form=QtWidgets.QFormLayout();second_form=QtWidgets.QFormLayout();view_layout.addLayout(form);view_layout.addLayout(second_form)
        self.view_ev=self._number(form,text("观看曝光 EV","Viewing EV"),0,-10,10)
        self.border_choice=QtWidgets.QComboBox()
        for zh,en,key in (("纯图像（默认）","Image only (default)","none"),("撕拉片 · 四边等宽","Peel-apart · equal borders","equal"),
                          ("拍立得 · 短边药囊留白","Instant · pod on a short edge","pod_short")):
            self.border_choice.addItem(text(zh,en),key)
        if kind!="plate":form.addRow(text("输出样式","Output style"),self.border_choice)
        self.view_angle=self.light_angle=self.background=None
        if kind=="plate":
            self.view_angle=self._number(second_form,text("观看倾角 °","View tilt °"),25,-85,85)
            self.light_angle=self._number(form,text("光源倾角 °","Light tilt °"),0,-85,85)
            self.background=self._number(second_form,text("背景亮度","Background level"),.02,0,1)
        self.left.addStretch()
        right=QtWidgets.QWidget();right_layout=QtWidgets.QVBoxLayout(right);split.addWidget(right);split.setSizes([360,780])
        self.preview_surface=right
        from film_foundry.ui.preferences import load_preferences
        self.status=QtWidgets.QLabel();self.status.setWordWrap(True);right_layout.addWidget(self.status)
        self.body_choice=QtWidgets.QComboBox();right_layout.addWidget(self.body_choice)
        self.viewer=ComparisonView();right_layout.addWidget(self.viewer,1)
        from film_foundry.ui.material_inspector import MaterialInspector
        self.inspector=MaterialInspector(self,OPERATIONS);right_layout.addWidget(self.inspector)
        right_layout.addWidget(self.view_group)
        self.save_buttons={}
        save_row=QtWidgets.QGridLayout();right_layout.addLayout(save_row)
        for i,(key,zh,en) in enumerate((("state","保存材料状态","Save state"),("products","保存全部介质","Save products"),
            ("body","保存选中片体","Save selected body"),("png","导出观察图像","Export observation"))):
            button=QtWidgets.QPushButton(text(zh,en));button.clicked.connect(lambda _=False,k=key:self.save_dialog(k));save_row.addWidget(button,i//2,i%2);self.save_buttons[key]=button
        self.progress=QtWidgets.QProgressBar();self.progress.setRange(0,0);self.progress.hide();layout.addWidget(self.progress)
        self.preview_timer=QtCore.QTimer(self);self.preview_timer.setSingleShot(True);self.preview_timer.setInterval(180);self.preview_timer.timeout.connect(self.refresh_preview)
        self.body_choice.currentIndexChanged.connect(self.request_preview)
        self.border_choice.currentIndexChanged.connect(self.request_preview)
        for control in (self.view_ev,self.view_angle,self.light_angle,self.background):
            if control:control.valueChanged.connect(self.request_preview)
        from film_foundry.ui.material_workflow import MaterialWorkflow
        self.workflow=MaterialWorkflow(self,layout,operation_box,OPERATIONS)
        self.set_workspace_layout(load_preferences().get('workspace_layout','compact'))
        self._sync()

    def set_workspace_layout(self,mode):
        if mode not in ("compact","studio"):raise ValueError("Unknown workspace layout")
        self._workspace_layout=mode
        self.workflow.set_layout(mode)
        self.layout_button.setText(text("单侧布局","Compact") if mode=="compact" else text("双侧布局","Studio"))
        self.layout_button.setToolTip(text('切换单侧分页 / 左材料右工艺','Switch compact tabs / materials left, processing right'))

    def _match_donor(self,from_donor):
        if self.session.state is not None or self.session.group is not None:return
        donor=self.material_controls["donor_mode"];transfer=self.material_controls["transfer"]
        if from_donor and donor.currentData()=="silver_negative":transfer.setCurrentIndex(transfer.findData("silver_compound"))
        elif not from_donor and transfer.currentData()=="dye":donor.setCurrentIndex(donor.findData("residue"))

    def _group(self,zh,en):
        box=QtWidgets.QGroupBox(text(zh,en));form=QtWidgets.QFormLayout(box);form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.left.addWidget(box);return box,form

    def _number(self,form,label,value,low,high):
        spin=QtWidgets.QDoubleSpinBox();spin.setRange(low,high);spin.setDecimals(3);spin.setSingleStep(.05 if high<=10 else 1);spin.setValue(value)
        spin.setKeyboardTracking(False);form.addRow(label,spin);return spin

    def conditions(self):
        def direction(control):
            a=math.radians(control.value());return (math.sin(a),0.,math.cos(a))
        return self._preserve_saved_view(ViewingConditions(exposure_ev=self.view_ev.value(),include_border=self.border_choice.currentData()!="none",border_style=self.border_choice.currentData(),
            view_direction=direction(self.view_angle) if self.view_angle else (0,0,1),
            light_direction=direction(self.light_angle) if self.light_angle else (0,0,1),
            background_rgb=(self.background.value(),)*3 if self.background else (.02,)*3))

    def _preserve_saved_view(self,current):
        from dataclasses import fields,replace
        base=getattr(self,'_saved_result_view',None);prior=getattr(self,'_saved_view_controls',None)
        if base is None or prior is None:return current
        return replace(base,**{f.name:getattr(current,f.name) for f in fields(current) if getattr(current,f.name)!=getattr(prior,f.name)})

    def _apply_result_view(self,view):
        self._saved_view_controls=None
        self.view_ev.setValue(view.exposure_ev)
        for name,direction in (('view_angle',view.view_direction),('light_angle',view.light_direction)):
            control=getattr(self,name,None)
            if control is not None:control.setValue(math.degrees(math.atan2(direction[0],direction[2])))
        if getattr(self,'background',None) is not None:self.background.setValue(sum(view.background_rgb)/3)
        if hasattr(self,'border_choice'):
            index=self.border_choice.findData(view.border_style if view.include_border else 'none')
            if index>=0:self.border_choice.setCurrentIndex(index)
        self._saved_view_controls=self.conditions();self._saved_result_view=view

    def _submit(self,action,label,render=True,body_index=None,restore_controls=False,preview=None):
        if self.busy:return False
        self.edit_tools.before_submit()
        self._restore_after_load=restore_controls
        self.preview_timer.stop();self.busy=True;self.last_error=None;self._sync();self.progress.show();self.status.setText(label)
        self.edit_tools.sync()
        if getattr(self,'optical_panel',None) is not None:self.optical_panel.setEnabled(False)
        view=self.conditions();index=max(0,self.body_choice.currentIndex()) if body_index is None else body_index
        self._preview_body_index=index
        def work():
            action()
            return (preview() if preview is not None else self.session.preview(view,index)) if render else None
        future=self.pool.submit(work)
        def done(task):
            try:result=task.result();error=None
            except Exception as exc:result=None;error=str(exc)
            self.signals.done.emit(result,error,label)
        future.add_done_callback(done);return True

    @QtCore.Slot(object,object,str)
    def _completed(self,image,error,label):
        self.busy=False;self.progress.hide();self.last_error=error
        self.edit_tools.completed()
        if getattr(self,'optical_panel',None) is not None:self.optical_panel.setEnabled(True)
        if error is None and self._restore_after_load:self._restore_controls()
        self._sync()
        selection=getattr(self,'_catalog_body_selection',None)
        if error is None and selection is not None:
            self._catalog_body_selection=None
            self.select_catalog_body(selection[0],rework=selection[1])
        loaded=getattr(self,'_loaded_result_view',None)
        if loaded is not None:
            self._loaded_result_view=None
            if error is None:
                self._apply_result_view(loaded)
                self.body_choice.setCurrentIndex(min(self._preview_body_index,self.body_choice.count()-1))
                self.request_preview()
        if image is not None:
            if self.body_choice.count():
                self.body_choice.blockSignals(True);self.body_choice.setCurrentIndex(min(self._preview_body_index,self.body_choice.count()-1));self.body_choice.blockSignals(False)
            self.viewer.show_array(image)
        self.inspector.sync()
        draft=getattr(self,'_workspace_restore_draft',None)
        if draft is not None:
            self._workspace_restore_draft=None
            from film_foundry.ui.material_workspace_state import restore_presentation
            restore_presentation(self,draft)
        if error:self.status.setText(text("操作失败：","Operation failed: ")+error)
        elif image is None:self.status.setText(self.status.text()+"\n"+text("已保存","Saved"))
        if error is None and getattr(self,'_planning_batch',False):
            from film_foundry.ui.material_batch_io import show_report
            show_report(self)
        if self._closing:self.close()

    def _restore_controls(self):
        state=self.session.state
        if state is None:return
        snapshot=json.loads(state.process_json)
        for key,control in self.operation_controls.get('environment',{}).items():
            value=snapshot.get('initial_temperature_c',snapshot.get('temperature_c',20.)) if key=='temperature_c' else snapshot.get(key,1. if key in ('light_protection','ambient_light') else 0.)
            control.setValue(value)
        keys={"streaks":"polish_streaks","strength":"sensitization","amount":"gilding"}
        for key,control in self.process_controls.items():
            value=snapshot.get("initial_temperature_c",snapshot.get("temperature_c")) if key=="temperature_c" else snapshot.get(keys.get(key,key))
            if isinstance(value,(int,float)):control.setValue(value)
            elif key in ("damage","tarnish") and state.schema_version<4:
                control.setValue(float(state.fields[key].mean()))
                control.setToolTip(text("显示当前表面场均值；点击应用会以这些数值均匀覆盖表面。",
                    "Shows the current field mean. Apply replaces the surface with these uniform values."))
        for key,control in self.defect_controls.items():
            if self.kind!="plate":control.setValue(snapshot.get(key,1 if key in ("light_protection","ambient_light") else 0))
        if self.kind=="plate":self.process_controls["polish_angle"].setValue(snapshot.get("polish_angle",0))
        events=snapshot.get("events",[])
        if self.kind=='plate' and state.schema_version>=3:
            from film_foundry.app.material_operations import resolve_parameters
            for operation,controls in self.operation_controls.items():
                latest=next((event for event in reversed(events) if event.get('operation')==operation),{})
                defaults=resolve_parameters(self.session.module,operation,{})
                for key,control in controls.items():
                    value=latest.get(key,defaults[key])
                    if isinstance(value,(int,float)):control.setValue(value)
            if state.schema_version>=4:
                latest=next((event for event in reversed(events) if event.get('operation')=='surface'),{})
                for key in ('damage','tarnish'):
                    value=latest.get(key,0.)
                    if isinstance(value,(int,float)):self.process_controls[key].setValue(value)
        if self.kind=='peel':
            from film_foundry.app.material_operations import resolve_parameters
            for operation,controls in (('peel',self.operation_controls['peel']),('clean',self.operation_controls['clean']),('handling',self.finish_controls),('clear_negative',self.clearing_controls)):
                latest=next((event for event in reversed(events) if event.get('operation')==operation),{})
                defaults=resolve_parameters(self.session.module,operation,{})
                for key,control in controls.items():
                    value=latest.get(key,defaults[key])
                    if isinstance(control,QtWidgets.QComboBox):control.setCurrentIndex(control.findData(value))
                    else:control.setValue(value)
        opening=next((event for event in reversed(events) if event.get("operation")=="light_leak"),{})
        self.leak_strength.setValue(opening.get("strength",0))
        self.leak_edge.setCurrentIndex(self.leak_edge.findData(opening.get("edge","left")))
        if self.kind=="plate":
            wear=next((event for event in reversed(events) if event.get("operation")=="weather"),{})
            for key,control in self.defect_controls.items():control.setValue(wear.get(key,0))
        if self.kind!="plate" and "direction" in snapshot:
            self.direction.setCurrentIndex(self.direction.findData(snapshot["direction"]))

    def _sync(self):
        session=self.session;state=session.state;group=session.group
        self.processing_bar.sync()
        self._sync_recipe_actions()
        self.file_bar.setEnabled(not self.busy);self.controls.setEnabled(not self.busy);self.body_choice.setEnabled(not self.busy);self.view_group.setEnabled(not self.busy)
        self.material_group.setEnabled(not self.busy)
        editable=not self.busy and ((state is None and group is None) or getattr(self,'_planning_batch',False))
        for control in (*self.material_controls.values(),self.seed):control.setEnabled(editable)
        # QTabWidget owns page visibility; showing its inactive pages directly
        # overlays their group titles and controls on the selected page.
        self.config_tabs.setVisible(not (state is None and group is not None))
        for control in self.defect_controls.values():control.setEnabled(("spread" if self.kind!="plate" else "weather") in session.operations)
        if state is not None and state.schema_version==1 and self.kind!="plate":
            for key in ("reagent_leak","spread_unevenness","light_protection","ambient_light","solar_warming"):
                if key in self.defect_controls:self.defect_controls[key].setEnabled(False)
        self.leak_strength.setEnabled("light_leak" in session.operations);self.leak_edge.setEnabled("light_leak" in session.operations)
        for op,button in self.operation_buttons.items():button.setEnabled(not self.busy and op in session.operations)
        for control in self.finish_controls.values():control.setEnabled('handling' in session.operations)
        if self.kind=='peel':self.finish_controls['target'].setEnabled('handling' in session.operations and state is not None and state.schema_version>=4)
        for control in self.clearing_controls.values():control.setEnabled('clear_negative' in session.operations)
        for operation,controls in self.operation_controls.items():
            for key,control in controls.items():
                enabled=not self.busy and operation in session.operations
                if self.kind=='plate' and state is not None and state.schema_version<3 and key!='amount':enabled=False
                if operation=='environment' and key in ('temperature_c','solar_warming') and self.environment_mode.currentData()=='warm_shift':enabled=False
                if self.kind=='peel' and (operation=='peel' or (operation=='clean' and key!='strength')):
                    enabled=enabled and (state is None or state.schema_version>=4) and (operation!='clean' or (state is not None and state.stage in ('separated','cleared')))
                control.setEnabled(enabled)
        for key,control in self.intent_controls.items():
            control.setEnabled(not self.busy and 'environment' in session.operations and (key=='mode' or self.environment_mode.currentData()=='warm_shift'))
        self.seconds.setEnabled(any(op in session.operations for op in ("advance","develop")))
        for key,control in self.process_controls.items():
            allowed=("spread" in session.operations) if self.kind!="plate" else ({"streaks":"polish","polish_angle":"polish","strength":"sensitize","amount":"gild","damage":"surface","tarnish":"surface"}[key] in session.operations)
            control.setEnabled(allowed)
        if self.kind!='plate':
            finite=state is None or state.schema_version>=(5 if self.kind=='peel' else 4)
            allowed=not self.busy and finite and 'spread' in session.operations
            self.shortage_enabled.setEnabled(allowed)
            self.shortage_strength.setEnabled(allowed and self.shortage_enabled.isChecked())
            self.process_controls['reagent_load'].setEnabled(allowed and not self.shortage_enabled.isChecked())
            from film_foundry.core.transfer_inventory import MATERIAL_LIMITS
            for key in MATERIAL_LIMITS:self.material_controls[key].setEnabled(editable and finite)
            if self.kind=='integral':self.material_controls['stabilization_seconds'].setEnabled(editable and not finite)
        else:
            modern=state is None or state.schema_version>=4
            allowed=not self.busy and modern and ('load_gilding' in session.operations or getattr(self,'_planning_batch',False))
            self.gold_intent['enabled'].setEnabled(allowed)
            self.gold_intent['strength'].setEnabled(allowed and self.gold_intent['enabled'].isChecked())
            self.operation_controls['load_gilding']['gold_load'].setEnabled(allowed and not self.gold_intent['enabled'].isChecked())
            for key in ('gold_capacity','gold_adhesion'):self.material_controls[key].setEnabled(editable and modern)
            hint=text('本次新增磨损和变色；重复累积，0不改变已有状态，丢失颗粒不可恢复。','Adds abrasion and tarnish. Repeated actions accumulate, zero preserves existing damage, lost particles cannot be restored.') if modern else text('旧材料：直接替换表面场。','Legacy material: replaces the surface fields.')
            for key in ('damage','tarnish'):self.process_controls[key].setToolTip(hint)
        if self.kind=="plate" and state is not None and state.schema_version==1:self.process_controls["polish_angle"].setEnabled(False)
        if self.kind!="plate":self.direction.setEnabled("spread" in session.operations)
        if self.kind!="plate":
            # These inputs may have become spatial fields; never display defaults as recovered settings.
            for key in ("coverage","contact"):
                self.process_form.setRowVisible(self.process_controls[key],state is None or state.stage in ("exposed","ejected"))
        count=len(group.bodies) if group else 0
        ids=[self.body_choice.itemData(i) for i in range(self.body_choice.count())]
        new_ids=[(b.body_id,b.role) for b in group.bodies] if group else []
        if ids!=new_ids:
            self.body_choice.blockSignals(True);self.body_choice.clear()
            names={"receiver":text("接收片","Receiver"),"donor_residue":text("供体残片（非保证可用负片）","Donor residue (not a guaranteed negative)"),
                   "integral_print":text("完整即显照片","Sealed instant print"),"silver_plate":text("银版","Silver plate"),"silver_negative":text("黑白负片 · 透射观看","B&W negative · transmission")}
            for body in group.bodies if group else ():self.body_choice.addItem(names.get(body.role,body.role),(body.body_id,body.role))
            self.body_choice.blockSignals(False)
        self.body_choice.setVisible(count>1)
        self.save_buttons['body'].setVisible(self.kind=='peel')
        self.save_buttons['products'].setText(text('保存关联片体','Save related bodies') if self.kind=='peel' else text('保存最终介质','Save final medium'))
        present=state is not None or group is not None
        for key,button in self.save_buttons.items():button.setEnabled(not self.busy and (group is not None if key in ("products","body") else present))
        if state and not getattr(self,'_planning_batch',False):
            for key,control in self.material_controls.items():
                value=getattr(state.material,key)
                if isinstance(control,QtWidgets.QComboBox):control.setCurrentIndex(control.findData(value))
                else:control.setValue(value)
            self.seed.setValue(state.seed)
        if not self.busy:
            stage=text(STAGES.get(state.stage,state.stage),state.stage) if state else text("已读取介质","Loaded products") if group else text("等待图像","Open an image")
            dimensions=next(iter(state.fields.values())).shape[:2] if state else next(iter(group.bodies[0].fields.values())).shape[:2] if group else session.image.shape[:2] if session.image is not None else None
            size=f" · {dimensions[1]}×{dimensions[0]}" if dimensions else ""
            elapsed=f" · {state.elapsed_seconds:g} s" if state else ""
            events=json.loads(state.process_json).get("events",[]) if state else []
            history=(text(" · 已记录事件 "," · Recorded events ")+str(len(events))) if events else ""
            self.status.setText(f"{stage}{elapsed}{size}{history}\n{session.source_name}")
        from film_foundry.ui import material_structure_controls
        material_structure_controls.sync(self)
        self.workflow.sync()
        if not self.busy:self.inspector.sync()

    def process(self,operation):
        if not self.processing_bar.source_ready():return False
        material={k:c.currentData() if isinstance(c,QtWidgets.QComboBox) else c.value() for k,c in self.material_controls.items()}
        params={}
        if operation in self.operation_controls:
            params={k:c.currentData() if isinstance(c,QtWidgets.QComboBox) else c.value() for k,c in self.operation_controls[operation].items()}
            if self.kind=='peel' and self.session.state is not None:
                if operation=='peel' and self.session.state.schema_version<4:params={}
                if operation=='clean' and (self.session.state.schema_version<4 or self.session.state.stage=='exposed'):params={'strength':params['strength']}
            if self.kind=='plate' and self.session.state is not None and self.session.state.schema_version<3:
                params={'amount':params['amount']} if operation=='gild' else {}
            if operation=='load_gilding' and self.gold_intent['enabled'].isChecked():
                from film_foundry.app.plate_gold_intent import gold_supply_parameters
                params.update(gold_supply_parameters(strength=self.gold_intent['strength'].value()))
            if operation=='environment' and self.environment_mode.currentData()=='warm_shift':
                from film_foundry.app.transfer_intent import warm_shift_parameters
                params=warm_shift_parameters(strength=self.intent_controls['strength'].value(),
                    light_protection=params['light_protection'],ambient_light=params['ambient_light'])
        elif operation=='handling':
            params={k:c.currentData() if isinstance(c,QtWidgets.QComboBox) else c.value() for k,c in self.finish_controls.items()}
            if self.kind=='peel' and self.session.state is not None and self.session.state.schema_version<4:params.pop('target',None)
        elif operation=='clear_negative':params={k:c.value() for k,c in self.clearing_controls.items()}
        elif operation in ("advance","develop"):params={"seconds":self.seconds.value()}
        elif operation=="spread":
            params={k:c.value() for k,c in self.process_controls.items()}|{"direction":self.direction.currentData()}|{k:c.value() for k,c in self.defect_controls.items()}
            if self.session.state.schema_version<(5 if self.kind=='peel' else 4):params.pop('reagent_load',None)
            elif self.shortage_enabled.isChecked():
                from film_foundry.app.transfer_intent import reagent_shortage_parameters
                params.update(reagent_shortage_parameters(strength=self.shortage_strength.value()))
        elif operation=="light_leak":params={"strength":self.leak_strength.value(),"edge":self.leak_edge.currentData()}
        elif operation=="weather":params={k:c.value() for k,c in self.defect_controls.items()}
        elif operation in ("polish","sensitize","gild"):
            key={"polish":"streaks","sensitize":"strength","gild":"amount"}[operation];params={key:self.process_controls[key].value()}
            if operation=="polish":params["angle_degrees"]=self.process_controls["polish_angle"].value()
        elif operation=="surface":params={k:self.process_controls[k].value() for k in ("damage","tarnish")}
        seed=int(self.seed.value())
        return self._submit(lambda:self.session.process(operation,params,material,seed),text(*OPERATIONS[operation]),body_index=1 if operation=="clear_negative" or params.get('target')=='donor' else 0)

    def request_preview(self,*_):
        if not self.busy:self.preview_timer.start()

    def refresh_preview(self):
        if self.session.state is not None or self.session.group is not None:self._submit(lambda:None,text("更新观看…","Updating view…"))

    def _add_recipe_menu(self,layout):
        self.recipe_button=QtWidgets.QToolButton();self.recipe_button.setText(text('材料与配方','Material & recipe'))
        self.recipe_button.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        menu=QtWidgets.QMenu(self.recipe_button)
        self.save_recipe_action=menu.addAction(text('保存操作配方…','Save operation recipe…'),self.save_recipe_dialog)
        self.apply_recipe_action=menu.addAction(text('应用操作配方…','Apply operation recipe…'),self.apply_recipe_dialog)
        self.add_delivery_actions(menu)
        menu.setToolTipsVisible(True)
        self.recipe_button.setMenu(menu);layout.addWidget(self.recipe_button)
        self.recipe_button.setToolTip(text('保存材料、身份及完整处理顺序；应用时从当前曝光来源重新形成。','Save material, random identity and ordered steps; apply to form a new material from the current exposure source.'))

    def _sync_recipe_actions(self):
        try:self.session.recipe();available=True
        except ValueError:available=False
        self.save_recipe_action.setEnabled(not self.busy and available)
        self.apply_recipe_action.setEnabled(not self.busy and self.session.image is not None)
        self.load_definition_action.setEnabled(not self.busy and self.session.state is None and self.session.group is None)
        self.print_action.setEnabled(not self.busy and self.session.group is not None)

    def save_recipe_dialog(self):
        path,_=QtWidgets.QFileDialog.getSaveFileName(self,text('保存操作配方','Save operation recipe'),self.kind+'.ffrecipe.json','Recipe (*.ffrecipe.json)')
        if not path:return
        if not path.lower().endswith('.json'):path+='.ffrecipe.json'
        self._submit(lambda:self.session.save_recipe(path),text('保存配方…','Saving recipe…'),render=False)

    def apply_recipe_dialog(self):
        path,_=QtWidgets.QFileDialog.getOpenFileName(self,text('应用操作配方','Apply operation recipe'),'','Recipe (*.ffrecipe.json *.json)')
        if not path:return
        from film_foundry.app.material_recipes import load_recipe
        self.replace_work(lambda:self._submit(lambda:self.session.replay_recipe(load_recipe(path)),text('应用配方…','Applying recipe…'),body_index=0,restore_controls=True))

    def open_image_dialog(self):
        path,_=QtWidgets.QFileDialog.getOpenFileName(self,text("打开图像","Open image"),"","Images (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.webp *.dng)")
        if not path:return
        preserve=self.kind=="plate" and self.session.state is not None and self.session.state.stage in ("blank","polished","sensitized")
        size=self.work_size.value()
        self.replace_work(lambda:self._submit(lambda:self.session.open_image(path,size,preserve),text("读取图像…","Reading image…"),body_index=0),preserve=preserve)

    def open_medium_dialog(self):
        path,_=QtWidgets.QFileDialog.getOpenFileName(self,text('选择最终介质','Choose a final medium'),'','Material (*.npz)')
        if path:self.choose_optical_source(path)

    def choose_optical_source(self,path,*,choices=None,body_id=None):
        if self.busy:return False
        from film_foundry.app.optical_cascade import source_choices
        from film_foundry.ui.optical_source_panel import OpticalSourcePanel
        try:panel=OpticalSourcePanel(path,source_choices(path) if choices is None else choices,self)
        except (ValueError,OSError,KeyError) as exc:self.status.setText(str(exc));return False
        layout=self.centralWidget().layout()
        previous=getattr(self,'optical_panel',None)
        if previous is not None:layout.removeWidget(previous);previous.hide();previous.deleteLater()
        self.optical_panel=panel;layout.insertWidget(layout.indexOf(self.workspace_splitter),panel)
        if body_id is not None:
            index=panel.body.findData(body_id)
            if index<0:raise ValueError('Selected body is not present in the optical source')
            panel.body.setCurrentIndex(index)
        panel.cancelled.connect(panel.hide)
        def apply():
            if self.busy:return
            state=self.session.state
            preserve=self.kind in ('plate','wet') and state is not None and state.stage in ('blank','polished','coated','sensitized')
            try:body_id,relay=panel.selection()
            except ValueError as exc:self.status.setText(str(exc));return
            size=self.work_size.value()
            def replace():
                if self._submit(lambda:self.session.open_medium(path,body_id=body_id,relay=relay,long_edge=size,preserve_preparation=preserve),text('读取光学来源…','Reading optical source…'),body_index=0):panel.hide()
            self.replace_work(replace,preserve=preserve)
        panel.applied.connect(apply)
        panel.show()
        return True

    def confirm_replace(self):
        if self.session.state is None and self.session.group is None:return True
        return QtWidgets.QMessageBox.question(self,text("替换当前材料","Replace current material"),
            text("请先保存需要保留的材料。继续会替换当前工作页中的状态。","Save material you want to keep. Continuing replaces this workspace's state."))==QtWidgets.QMessageBox.StandardButton.Yes

    def replace_work(self,action,*,preserve=False):
        if self.busy:return False
        if preserve:action();return True
        protect=getattr(self,'protect_replacement',None)
        if protect:return protect(action)
        if self.confirm_replace():action();return True
        return False

    def restart(self,_approved=False):
        if not _approved:return self.replace_work(lambda:self.restart(True))
        previous=self.session;self.session=MaterialLabSession(self.kind)
        self.session.image=previous.image;self.session.source_name=previous.source_name if previous.image is not None else "";self.session.source_path=previous.source_path
        self.session.optical_source=previous.optical_source;self.session.optical_body_id=previous.optical_body_id;self.session.optical_relay=previous.optical_relay
        self.session.optical_reference=previous.optical_reference
        self.session.source_info=previous.source_info.copy()
        self.session.storage_precision=previous.storage_precision
        self.viewer.source=None;self.viewer._sync_source();self._sync()
        if self.session.image is not None:self._submit(lambda:None,text("准备新材料…","Preparing new material…"),body_index=0)

    def load_dialog(self):
        path,_=QtWidgets.QFileDialog.getOpenFileName(self,text("读取材料存档","Open material archive"),"","Material (*.ffmaterial.npz *.npz result.json)")
        if path:self.replace_work(lambda:self.open_archive(path))

    def open_archive(self,path):
        from film_foundry.app.material_result_store import result_record,load_result
        record=result_record(path)
        if record is None:return self._submit(lambda:self.session.load(path),text('读取材料…','Reading material…'),body_index=0,restore_controls=True)
        index=next((i for i,body in enumerate(record.get('bodies',[])) if record['artifacts'].get(body['role'])==str(Path(path).resolve())),0)
        def load():
            session,loaded,view=load_result(path,kind=self.kind)
            self._preview_body_index=next((i for i,b in enumerate(session.group.bodies) if loaded['artifacts'].get(b.role)==str(Path(path).resolve())),0)
            self.session=session;self._loaded_result_view=view
            self.result_ready.emit(loaded)
        return self._submit(load,text('读取整组材料…','Reading material group…'),render=False,body_index=index,restore_controls=True)

    def select_catalog_body(self,body_id,*,rework=False):
        if self.busy:
            self._catalog_body_selection=(body_id,rework);return
        group=self.session.group
        index=next((i for i,b in enumerate(group.bodies) if b.body_id==body_id),0) if group else 0
        self._preview_body_index=index
        if group:self.body_choice.setCurrentIndex(index)
        if rework and self.kind=='peel':
            if group and group.bodies[index].role in ('donor_residue','silver_negative'):
                for controls in (self.operation_controls.get('clean',{}),self.finish_controls):
                    target=controls.get('target')
                    if target is not None:target.setCurrentIndex(target.findData('donor'))
            if 'clear_negative' in self.session.operations:
                self.config_tabs.setCurrentWidget(self.process_group)
                self.workflow.choice.setCurrentIndex(self.workflow.choice.findData('clear_negative'))
                self.operation_buttons['clear_negative'].setFocus()
            elif 'clean' in self.session.operations:
                self.config_tabs.setCurrentWidget(self.process_group)
                self.workflow.choice.setCurrentIndex(self.workflow.choice.findData('clean'))
                self.operation_buttons['clean'].setFocus()
        if group:self.request_preview()

    def save_dialog(self,kind):
        if self.busy:return
        from film_foundry.ui.material_export_dialog import MaterialExportDialog
        index=max(0,self.body_choice.currentIndex());view=self.conditions()
        dialog=MaterialExportDialog(self.session,kind,view,index,self)
        self._export_dialog=dialog
        dialog.accepted.connect(lambda:self._save_selected(kind,dialog.selection,index,view))
        dialog.open()

    def _save_selected(self,kind,selection,index,view):
        if self.busy:return
        path,bit_depth,quality=selection
        self._save_material_output(kind,path,bit_depth,quality,index,view)

    def _save_material_output(self,kind,path,bit_depth,quality,index,view):
        action=(lambda:self.session.export_image(path,view,index,bit_depth=bit_depth,quality=quality)) if kind=="png" else (lambda:self.session.save_products(path)) if kind=="products" else (lambda:self.session.save(path,index if kind=="body" else None))
        def save_and_index():
            action()
            if kind=='png':
                from film_foundry.app.material_result_store import observation_result_record
                self.result_ready.emit(observation_result_record(self.session,path,view,index))
                return
            if self.session.group is not None:
                from film_foundry.app.workspace_jobs import archive_material_result
                from film_foundry.app.desktop_catalog import DesktopCatalog
                cache=getattr(self.session,'_result_archive_cache',None)
                root=cache['root'] if cache else DesktopCatalog().path.parent/'material_results'
                record=archive_material_result(self.session,root,view)
                record['artifacts']['saved:'+kind]=str(Path(path).resolve())
                record['artifacts']['export:'+Path(path).name]=str(Path(path).resolve())
                self.result_ready.emit(record)
        self._submit(save_and_index,text("保存中…","Saving…"),render=False)

    def closeEvent(self,event):
        if self.busy:
            self._closing=True;self.status.setText(text("本次操作完成后关闭","Closing after this operation"));event.ignore();return
        self.processing_bar.reload_timer.stop()
        self.preview_timer.stop();self.pool.shutdown(wait=False,cancel_futures=True);self.closed.emit();event.accept()


def create_material_window(kind):
    from film_foundry.app.material_lab_session import SHEET_FAMILIES
    if kind in SHEET_FAMILIES:
        from film_foundry.ui.sheet_lab_window import SheetLabWindow
        return SheetLabWindow(kind)
    return MaterialLabWindow(kind)

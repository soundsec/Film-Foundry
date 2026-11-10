"""Data-only event sequence editor; numerical responses remain in core."""
from copy import deepcopy
from film_foundry.ui.qt import QtWidgets,QtCore
from film_foundry.ui.curve_editor import text
from film_foundry.contracts.process_events import EVENT_MODEL,EVENT_MODEL_V2,EVENT_MODEL_V3,EVENT_MODEL_V4,EVENT_MODEL_V5,DISTRIBUTION_FIELDS,EVENT_FIELDS,RESPONSE_RANGES,RESPONSE_V2_RANGES,RESPONSE_MODEL_V2,validate_process_events,validate_process_response
from film_foundry.contracts.process_events import EVENT_MODEL_V6,EVENT_MODEL_V7,EVENT_MODEL_V8,EVENT_MODEL_V9,EVENT_MODEL_V10,EVENT_MODEL_V11,EVENT_MODEL_V12,EVENT_MODEL_V13,EVENT_MODEL_V14,EVENT_MODEL_V15,PLACEMENT,RESPONSE_MODEL_V3,RESPONSE_V3_RANGES
from film_foundry.contracts.process_events import RESPONSE_MODEL_V4,RESPONSE_V4_DEFAULTS,RESPONSE_V4_RANGES
from film_foundry.contracts.carbon_backing import DEFAULTS_V2 as BACKING_DEFAULTS,MODEL as BACKING_V1,MODEL_V2 as BACKING_V2,RANGES as BACKING_RANGES,validate_carbon_backing
from film_foundry.contracts.process_events import CONTACT_KINDS,ROTARY_INSTABILITY_FIELDS,MENISCUS_FIELDS,BOUNDARY_FIELDS,EDGE_WETTING_FIELDS,WETTING_CHOICES

KINDS={'fixer_contact':('误接触定影液','Accidental fixer contact'),'developer_inhibitor':('携入显影抑制剂','Development inhibitor carryover'),'bath_contact':('药浴浸润与气泡','Bath contact & air bubbles'),'contaminate':('药液残留／污染','Residue / contamination'),
    'wash':('换水清洗','Water washing'),'temperature':('转移到不同温度','Temperature transition'),
    'harden':('乳剂硬化','Emulsion hardening'),'silver_bath':('含银液与还原剂接触','Silver-bearing solution')}
KINDS['dye_bath']=('染料侵蚀液接触','Dye erosion contact')
KINDS['backing_condition']=('碳背层软化预浴','Carbon backing prebath')
KINDS['emulsion_shear']=('湿乳剂横向剪切拖移','Wet emulsion horizontal shear')
KINDS['emulsion_wear']=('湿乳剂擦伤与脱落','Wet emulsion wear')
KINDS['backing_wash']=('碳背层清洗与搬运','Carbon backing wash')
KINDS['rotary_contact']=('旋转冲洗','Rotary processing')
LABELS={'before_bath':('进入此药浴前携入','Carry in before bath'),'concentration_mmol_l':('抑制剂浓度 mmol/L','Inhibitor mmol/L'),'after_bath':('执行位置','Run after'),'bath':('药浴序号','Bath index'),'fill_s':('注液时间 s','Filling time s'),'immersion_fraction':('浸没比例','Submerged fraction'),
    'angle_degrees':('注液方向 °','Filling direction °'),'bubble_radius_mm':('气泡半径 mm','Air bubble radius mm'),
    'bubble_release_s':('气泡脱离时间 s','Bubble release time s'),'center_x':('横向位置 0–1','Horizontal position 0–1'),'center_y':('纵向位置 0–1','Vertical position 0–1'),
    'radius_mm':('接触半径 mm（0=全片）','Contact radius mm (0=whole frame)'),
    'concentration_mg_l':('污染物浓度 mg/L','Contaminant mg/L'),'retained_ml_m2':('残液体积 mL/m²','Retained solution mL/m²'),
    'contact_s':('吸附接触时间 s','Binding contact time s'),'duration_s':('持续时间 s','Duration s'),
    'water_changes':('换水次数','Fresh water exchanges'),'temperature_c':('液温 °C','Bath temperature °C'),
    'transfer_s':('变温过渡时间 s','Temperature ramp s'),'hold_s':('保持时间 s','Holding time s'),
    'concentration_g_l':('硬化剂浓度 g/L','Hardener g/L'),'silver_mg_l':('溶解银浓度 mg/L','Dissolved silver mg/L'),
    'solution_ml_m2':('接触液量 mL/m²','Solution volume mL/m²'),'reducer_mmol_l':('还原当量 mmol/L','Reducing equivalents mmol/L')}

LABELS.update({'distribution_variation':('铺布不均程度','Distribution variation'),
    'distribution_scale_mm':('铺布纹理尺度 mm','Distribution scale mm'),
    'distribution_seed':('铺布身份','Distribution identity')})
LABELS.update({'liquid_level_gradient':('轴向液位梯度','Axial liquid-level gradient'),
    'meniscus_retention_s':('弯月面滞留时间 s','Meniscus retention time s'),
    'edge_width_mm':('接触过渡宽度 mm','Contact transition width mm'),
    'boundary_variation':('边界不规则度','Boundary irregularity')})
LABELS.update({'meniscus_width_mm':('液面过渡宽度 mm','Meniscus transition width mm'),
    'meniscus_roughness_mm':('液面起伏 mm','Meniscus roughness mm'),
    'residue_concentration_mg_l':('残液溶质浓度 mg/L','Retained solute concentration mg/L'),
    'evaporation_time_s':('蒸发时间常数 s','Evaporation time constant s')})
WETTING_LABELS={'wetting_model':('接触模型','Contact model'),'contact_edge':('浸液侧','Immersed edge')}
WETTING_OPTION_LABELS={'periodic_arc_v1':('旧版周期展开','Legacy periodic arc'),
    'edge_front_v1':('贴边液面','Edge-connected front'),'bottom':('下边','Bottom'),'top':('上边','Top')}

def _control_value(control):
    return control.currentData() if isinstance(control,QtWidgets.QComboBox) else control.value()

LABELS['travel_mm']=('横向拖动 mm（正值向右）','Horizontal travel mm (positive right)')
LABELS['band_width_mm']=('作用带宽 mm（0=全片）','Band width mm (0=whole frame)')
LABELS['pressure']=('接触压力 0–1','Contact pressure 0–1')
LABELS.update({'rotation_rpm':('转速 r/min','Rotation speed r/min'),
    'rotation_instability':('滚动不稳定性','Rotation instability'), 'rotation_seed':('随机种子','Random seed'),
    'pause_at_fraction':('停转时刻（比例）','Pause start (fraction)'), 'pause_s':('停转时长 s','Pause duration s')})
LABELS.update({'alkalinity_g_l':('预浴碱活性当量 g/L','Prebath alkali activity g/L'),
    'agitation':('液体搅动','Liquid agitation'),'back_rubbing':('背面擦洗','Back rubbing'),
    'front_rubbing':('乳剂面擦洗','Emulsion rubbing'),'emulsion_contact':('乳剂接触含碳残液比例','Emulsion contact with carbon suspension')})

class ProcessOperationsEditor(QtWidgets.QWidget):
    def __init__(self,value=None,parent=None):
        super().__init__(parent);self.rows=deepcopy((validate_process_events(value) or {'events':[]})['events']);self.original_model=(value or {}).get('model');self.current=-1;self.controls={};self.loading=False
        layout=QtWidgets.QVBoxLayout(self);bar=QtWidgets.QHBoxLayout();layout.addLayout(bar)
        self.kind=QtWidgets.QComboBox()
        for kind,names in KINDS.items():self.kind.addItem(text(*names),kind)
        bar.addWidget(self.kind,1)
        for zh,en,callback in (('添加','Add',self.add),('移除','Remove',self.remove),('上移','Up',lambda:self.move(-1)),('下移','Down',lambda:self.move(1))):
            button=QtWidgets.QPushButton(text(zh,en));button.clicked.connect(callback);bar.addWidget(button)
        split=QtWidgets.QSplitter();layout.addWidget(split,1);self.list=QtWidgets.QListWidget();split.addWidget(self.list)
        scroll=QtWidgets.QScrollArea();scroll.setWidgetResizable(True);self.form_widget=QtWidgets.QWidget();self.form=QtWidgets.QFormLayout(self.form_widget);scroll.setWidget(self.form_widget);split.addWidget(scroll);split.setStretchFactor(1,2)
        self.setToolTip(text('浸润操作绑定药浴序号；其余操作按列表顺序发生在药浴完成后。半径0表示整片。这里只指定操作与数量，不输入期望事故强度。','Contact binds to a bath index. Other operations run in order after the baths. Radius 0 covers the frame. Inputs describe quantities and operations, not desired defect strengths.'))
        self.list.currentRowChanged.connect(self.select);self.refresh()
        self.setToolTip(text('浸润绑定药浴序号。其余操作可插入指定药浴之后；0为全部药浴结束。同一位置按列表顺序执行。','Contact binds to a bath. Other operations run after the selected bath; 0 means after all baths. List order applies within each position.'))

    def commit(self):
        if 0<=self.current<len(self.rows):
            for name,control in self.controls.items():
                # A displayed decimal is not the source precision. Merely
                # visiting a row must preserve the compiled physical values.
                if _control_value(control)!=self.display_values[name]:self.rows[self.current][name]=_control_value(control)

    def refresh(self,index=0):
        self.current=-1;self.list.blockSignals(True);self.list.clear()
        for i,row in enumerate(self.rows):
            item=QtWidgets.QListWidgetItem(f'{i+1:02d} · '+text(*KINDS[row['kind']]))
            item.setSizeHint(QtCore.QSize(0,32));self.list.addItem(item)
        self.list.blockSignals(False);self.list.setCurrentRow(min(index,len(self.rows)-1));self.select(self.list.currentRow())

    def select(self,index):
        if self.loading:return
        self.commit();self.loading=True;self.current=index;self.controls={};self.display_values={}
        while self.form.rowCount():self.form.removeRow(0)
        if 0<=index<len(self.rows):
            row=self.rows[index]
            fields=dict(EVENT_FIELDS[row['kind']])
            if row['kind'] in ('backing_condition','backing_wash') or row['kind'] in ('emulsion_wear','emulsion_shear') and self.original_model in (EVENT_MODEL_V10,EVENT_MODEL_V11,EVENT_MODEL_V12,EVENT_MODEL_V13,EVENT_MODEL_V14,EVENT_MODEL_V15):
                position='after_bath' if 'after_bath' in row or row['kind'] in ('emulsion_wear','emulsion_shear') and 'before_bath' not in row else 'before_bath'
                self.position=QtWidgets.QComboBox()
                self.position.addItem(text('药浴之前','Before bath'),'before_bath');self.position.addItem(text('药浴之后','After bath'),'after_bath')
                self.position.setCurrentIndex(self.position.findData(position));self.form.addRow(text('插入位置','Insertion'),self.position)
                self.position.currentIndexChanged.connect(self.change_position)
                fields={position:(1,1,32) if position=='before_bath' else (0,0,32),**fields}
                self.form_widget.setToolTip(text('默认在首浴之前，先软化再清洗。换水排出松脱颗粒；含碳残液接触乳剂会再附着。通用去附加层不去碳。擦洗尚不模拟乳剂划伤。','Default: before bath 1, soften then wash. Fresh water removes loose particles; dirty emulsion contact can redeposit them. Generic auxiliary removal does not remove carbon. Rubbing does not yet scratch emulsion.'))
            else:
                self.form_widget.setToolTip('')
                if row['kind'] not in (*CONTACT_KINDS,'developer_inhibitor','fixer_contact'):fields={'after_bath':(0,0,32),**fields}
            if row['kind']=='rotary_contact':
                self.form_widget.setToolTip(text('单幅按一周展开。浸液比例是接触弧比例，不是药液毫升数；离液后残液逐渐衰减。停转时刻0–1对应药浴开始至结束；停转时长0为连续旋转。搅拌强度仍控制基础反应速率。无三维滚筒、液体输运或槽容量耗竭。',
                    'One frame is unwrapped over one turn. Immersion is wet arc fraction, not millilitres. Residual liquid access decays after emergence. Pause start 0–1 spans the bath; zero duration means continuous rotation. Agitation sets base kinetics. No 3D tank, fluid transport or bath capacity depletion.'))
            if row['kind']=='emulsion_wear':
                if self.original_model not in (EVENT_MODEL_V10,EVENT_MODEL_V11,EVENT_MODEL_V12,EVENT_MODEL_V13,EVENT_MODEL_V14,EVENT_MODEL_V15):fields['after_bath']=(0,0,0)
                self.form_widget.setToolTip(text('新操作可插入指定药浴之前或之后；旧v9保持末段处理。压力、时长、湿润、硬化和已有损伤决定缺层，已移走的银盐、银和染料不再参与后续药浴。片基和背层保留；整组成像乳剂一同移除，没有坐标位移。','New operations run before or after a selected bath; legacy v9 remains terminal. Removed halide, silver and dye cannot react in later baths. Pressure, duration, hydration, hardening and damage determine loss. Support and backing remain. Imaging layers leave together; no coordinate displacement.'))
            if row['kind']=='emulsion_shear':
                self.form_widget.setToolTip(text('沿图像横向拖动湿乳剂；作用带内每行整体移动，硬化、湿润与热损伤决定可动程度。成像物质与乳剂所属附着物同行，片基/背层保留。出框部分记账移出，不回卷；不模拟折叠或垂直位移。','Rows shear horizontally according to pressure, duration and material response. Image material and emulsion-owned deposits move; support and backing stay. Out-of-frame matter is recorded and discarded, never wrapped. No folding or vertical motion.'))
            if row['kind']=='rotary_contact':
                for key,options in WETTING_CHOICES.items():
                    combo=QtWidgets.QComboBox()
                    for option in options:combo.addItem(text(*WETTING_OPTION_LABELS[option]),option)
                    combo.setCurrentIndex(combo.findData(row.get(key,options[0])))
                    self.controls[key]=combo;self.display_values[key]=combo.currentData()
                    self.form.addRow(text(*WETTING_LABELS[key]),combo)
                    combo.setToolTip(text('贴边模型：连续滚动按周期平均接触，停转或慢转保留连接上/下边的起伏液面。旧模型仅用于复现旧配方。残液是有限溶质，可在后续清洗中减少，吸附部分保留。','Edge model: moving rotation averages contact; pauses or slow motion preserve an irregular front connected to the selected edge. Legacy reproduces old recipes. Finite retained solute can be washed away; bound material remains.'))
            for key,(default,low,high) in fields.items():
                spin=QtWidgets.QSpinBox() if key in ('bath','after_bath','before_bath','distribution_seed','rotation_seed') else QtWidgets.QDoubleSpinBox()
                if key not in ('bath','after_bath','before_bath','distribution_seed','rotation_seed'):spin.setDecimals(3)
                if key=='after_bath':
                    spin.setSpecialValueText(text('全部药浴结束后','After all baths'))
                    spin.setToolTip(text('正整数表示第几步药浴之后；改动浴表顺序后请核对。','A positive integer selects the preceding bath. Check it after reordering baths.'))
                label=text('定影活性浓度 mmol/L','Fixing activity mmol/L') if key=='concentration_mmol_l' and row['kind']=='fixer_contact' else text(*LABELS[key])
                if key=='concentration_mmol_l' and row['kind']=='dye_bath':label=text('侵蚀活性浓度 mmol/L','Erosion activity mmol/L')
                spin.setRange(low,high);spin.setValue(row.get(key,default));self.controls[key]=spin;self.display_values[key]=spin.value();self.form.addRow(label,spin)
                if row['kind']=='rotary_contact':spin.setToolTip(self.form_widget.toolTip())
                if key=='rotation_instability':
                    spin.setSingleStep(.05)
                    spin.setToolTip(text('0不施加随机位姿变化，轴向液位梯度仍可改变条带宽度。增加后，在四个滚动时段间离散改变液面位置和角度；幅度随数值增加，最大位置偏移为一周的20%、倾斜25°。停转时保持姿态，转速0仅保留初始姿态。改变实际药液接触；全浸液不产生条带，连续快转通常较均匀。',
                        'Zero disables pose variation; axial liquid-level gradient can still taper the band. Four moving intervals have seeded position and angle changes, scaling up to 20% of a turn and 25 degrees. Pauses freeze pose; zero rpm keeps the initial pose. Alters actual liquid contact. Full immersion remains uniform; fast continuous rotation is usually more even.'))
                if key in MENISCUS_FIELDS:
                    spin.setToolTip(text('轴向液位差使湿润带两侧不再平行；正负值改变收缩方向。滞留时间仅延长停转时上沿弯月面附近残液的反应时间，不超过药浴时钟。同一接触影响主反应、灰雾和坚膜。0保持旧接触行为；这是简化几何与排液模型。','Axial depth variation tapers the wet band; its sign reverses the taper. Retention extends residual contact near the stationary meniscus within the bath clock. Primary reactions, fog and hardening share this contact. Zero keeps legacy behaviour. Reduced geometry and drainage model.'))
                if key in BOUNDARY_FIELDS:
                    spin.setToolTip(text('在实际接触场中生成毫米尺度的过渡与不规则边界，影响局部物质反应或移除；不模糊最终照片。使用铺布种子，0保留原轮廓；半径0仍为全片。','Millimetre-scale transitions and seeded irregular boundaries act on real contact, reaction and removal, not final-image blur. Zero preserves the old contour; radius zero remains whole-frame.'))
                if key=='rotation_seed':spin.setToolTip(text('相同种子复现同一组位置与角度；更改种子可换一组，不改变颗粒或其他事故的随机序列。随配方与中途档保存。',
                    'The same seed reproduces the poses. Change it for another arrangement without changing grain or other random streams. Saved with the recipe and processing checkpoint.'))
            if row['kind']=='rotary_contact':
                self.wetting_legacy_tooltips={key:control.toolTip() for key,control in self.controls.items()}
                self.wetting_legacy_form_tooltip=self.form_widget.toolTip()
                self.controls['wetting_model'].currentIndexChanged.connect(self.update_wetting_controls)
                self.update_wetting_controls()
        self.loading=False

    def update_wetting_controls(self):
        enabled=self.controls['wetting_model'].currentData()=='edge_front_v1'
        self.form_widget.setToolTip(self.wetting_legacy_form_tooltip)
        for key,tip in self.wetting_legacy_tooltips.items():self.controls[key].setToolTip(tip)
        for key in (*EDGE_WETTING_FIELDS,'contact_edge'):
            self.controls[key].setEnabled(enabled)
            self.controls[key].setToolTip(self.controls['wetting_model'].toolTip())
        if enabled:
            tip=text('浸液区连接所选上边或下边，只有一条起伏的自由液面；液位梯度改变倾斜方向。连续快转趋向均匀，停转保留局部接触。滞留影响已有残液的有效反应；蒸发把有限溶质集中到柔和边界，后续清洗可移除未吸附部分。不是流体力学仿真。',
                'The wet region connects to the selected edge with one irregular free surface. Level gradient tilts it. Fast rotation averages contact; pauses preserve local contact. Retention affects residual reaction, evaporation concentrates finite solute into a soft rim, and later washing removes its soluble fraction. Reduced model, not fluid dynamics.')
            self.form_widget.setToolTip(tip)
            for key in (*MENISCUS_FIELDS,'immersion_fraction','rotation_rpm','rotation_instability','pause_s','pause_at_fraction'):
                self.controls[key].setToolTip(tip)

    def change_position(self):
        if self.loading:return
        self.commit();row=self.rows[self.current];key=self.position.currentData()
        old='after_bath' if key=='before_bath' else 'before_bath'
        # Keep the emitting combo alive. Rebuilding its form inside its own
        # signal deletes the sender and can crash during Qt shutdown.
        spin=self.controls.pop(old);previous=spin.value();row.pop(old,None)
        spin.setRange(1 if key=='before_bath' else 0,32)
        spin.setSpecialValueText('' if key=='before_bath' else text('全部药浴结束后','After all baths'))
        spin.setValue(max(1,previous) if key=='before_bath' else previous)
        row[key]=spin.value();self.controls[key]=spin;self.display_values.pop(old,None);self.display_values[key]=spin.value()
        self.form.labelForField(spin).setText(text(*LABELS[key]))

    def add(self):
        self.commit();kind=self.kind.currentData();row={'kind':kind,**{key:limits[0] for key,limits in EVENT_FIELDS[kind].items()}}
        index=next((i for i,r in enumerate(self.rows) if r['kind'] not in CONTACT_KINDS),len(self.rows)) if kind in CONTACT_KINDS else len(self.rows)
        if kind=='rotary_contact':
            row.update(wetting_model='edge_front_v1',contact_edge='bottom',liquid_level_gradient=0.,meniscus_retention_s=12.,residue_concentration_mg_l=500.);self.original_model=EVENT_MODEL_V15
        elif 'edge_width_mm' in row:
            row.update(edge_width_mm=.4,boundary_variation=.65)
            if self.original_model!=EVENT_MODEL_V15:self.original_model=EVENT_MODEL_V14
        elif kind=='emulsion_shear' and self.original_model not in (EVENT_MODEL_V12,EVENT_MODEL_V13,EVENT_MODEL_V14,EVENT_MODEL_V15):self.original_model=EVENT_MODEL_V11
        elif kind=='emulsion_wear' and self.original_model not in (EVENT_MODEL_V11,EVENT_MODEL_V12,EVENT_MODEL_V13,EVENT_MODEL_V14,EVENT_MODEL_V15):self.original_model=EVENT_MODEL_V10
        self.rows.insert(index,row);self.refresh(index)

    def remove(self):
        index=self.list.currentRow()
        if index>=0:self.rows.pop(index);self.refresh(index)

    def move(self,delta):
        self.commit();index=self.list.currentRow();target=index+delta
        if 0<=target<len(self.rows):self.rows[index],self.rows[target]=self.rows[target],self.rows[index];self.refresh(target)

    def read(self):
        self.commit()
        rows=deepcopy(self.rows)
        uses_distribution=self.original_model in (EVENT_MODEL_V5,EVENT_MODEL_V6,EVENT_MODEL_V7,EVENT_MODEL_V8,EVENT_MODEL_V9,EVENT_MODEL_V10,EVENT_MODEL_V11,EVENT_MODEL_V12,EVENT_MODEL_V13,EVENT_MODEL_V14,EVENT_MODEL_V15) or any(any(row.get(key,limits[0])!=limits[0] for key,limits in DISTRIBUTION_FIELDS.items()) for row in rows)
        if not uses_distribution:
            for row in rows:
                for key in DISTRIBUTION_FIELDS:row.pop(key,None)
        model=EVENT_MODEL_V3 if any(row['kind']=='developer_inhibitor' for row in rows) else EVENT_MODEL_V2
        if any(row['kind']=='fixer_contact' for row in rows):model=EVENT_MODEL_V4
        if uses_distribution:model=EVENT_MODEL_V5
        if self.original_model==EVENT_MODEL_V6 or any(row['kind']=='dye_bath' for row in rows):model=EVENT_MODEL_V6
        if self.original_model==EVENT_MODEL_V7 or any(row['kind']=='silver_bath' and row.get('after_bath',0)>0 or row['kind']=='dye_bath' and (row.get('after_bath',0)>0 or row.get('radius_mm',0)>0) for row in rows):model=EVENT_MODEL_V7
        if self.original_model==EVENT_MODEL_V8 or any(row['kind'] in ('backing_condition','backing_wash') for row in rows):model=EVENT_MODEL_V8
        if self.original_model==EVENT_MODEL_V9 or any(row['kind']=='emulsion_wear' for row in rows):model=EVENT_MODEL_V9
        if self.original_model==EVENT_MODEL_V10:model=EVENT_MODEL_V10
        if self.original_model==EVENT_MODEL_V11:model=EVENT_MODEL_V11
        if self.original_model==EVENT_MODEL_V12 or any(row['kind']=='rotary_contact' for row in rows):model=EVENT_MODEL_V12
        if self.original_model==EVENT_MODEL_V13 or any(row['kind']=='rotary_contact' and any(key in row for key in ROTARY_INSTABILITY_FIELDS) for row in rows):model=EVENT_MODEL_V13
        if self.original_model==EVENT_MODEL_V14 or any(any(key in row for key in (*MENISCUS_FIELDS,*BOUNDARY_FIELDS)) for row in rows):model=EVENT_MODEL_V14
        if self.original_model==EVENT_MODEL_V15 or any(row['kind']=='rotary_contact' and any(key in row for key in (*WETTING_CHOICES,*EDGE_WETTING_FIELDS)) for row in rows):model=EVENT_MODEL_V15
        if model not in (EVENT_MODEL_V7,EVENT_MODEL_V8,EVENT_MODEL_V9,EVENT_MODEL_V10,EVENT_MODEL_V11,EVENT_MODEL_V12,EVENT_MODEL_V13,EVENT_MODEL_V14,EVENT_MODEL_V15):
            for row in rows:
                if row['kind']=='dye_bath':
                    row.pop('after_bath',None)
                    for key in PLACEMENT:row.pop(key,None)
        return validate_process_events({'model':model,'events':rows}) if rows else None

RESPONSE_LABELS={'wash_exchange_s':('清洗交换时间常数 s','Wash exchange time constant s'),'binding_rate_s':('残留吸附速率 1/s','Residue binding rate 1/s'),
    'surface_silver_roughness':('表面银粗糙度','Surface silver roughness'),
    'surface_silver_directionality':('表面银反射方向性','Surface silver reflection directionality'),
    'dye_attack_l_mmol_s':('染料侵蚀响应 L/(mmol·s)','Dye erosion response L/(mmol·s)'),
    'inhibitor_half_response_mmol_m2':('显影抑制半响应载量 mmol/m²','Development inhibition half-response mmol/m²'),
    'fixer_contact_rate_l_mmol_s':('银盐溶解响应 L/(mmol·s)','Halide dissolution response L/(mmol·s)'),
    'initial_hardening':('初始硬化程度','Initial hardening'),'hardening_rate_l_g_s':('硬化速率 L/(g·s)','Hardening rate L/(g·s)'),
    'thermal_expansion_per_c':('湿乳剂膨胀系数 1/°C','Wet expansion coefficient 1/°C'),'relaxation_s':('乳剂松弛时间 s','Emulsion relaxation s'),
    'yield_strain':('屈服应变','Yield strain'),'damage_strain':('损伤应变尺度','Damage strain scale'),'hydration_s':('吸水时间常数 s','Hydration time constant s'),
    'silver_reduction_l_mmol_s':('银还原速率 L/(mmol·s)','Silver reduction L/(mmol·s)'),
    'silver_density_per_mg_m2':('表面银光学密度系数','Surface silver optical density coefficient'),'structure_density':('乳剂结构光学系数','Emulsion structure optical coefficient')}

class ProcessResponseDialog(QtWidgets.QDialog):
    def __init__(self,value,parent=None,*,carbon_backing=None,emulsion_integrity=None):
        super().__init__(parent);self.setWindowTitle(text('材料工艺响应','Material process response'));self.resize(660,650)
        existing=validate_process_response(value)
        self.original_model=existing['model']
        self.value=validate_process_response({**existing,'model':RESPONSE_MODEL_V4});self.controls={};layout=QtWidgets.QVBoxLayout(self)
        scroll=QtWidgets.QScrollArea();scroll.setWidgetResizable(True);body=QtWidgets.QWidget();form=QtWidgets.QFormLayout(body);scroll.setWidget(body);layout.addWidget(scroll)
        from film_foundry.contracts.emulsion_integrity import validate_emulsion_integrity,DEFAULTS as INTEGRITY_DEFAULTS,RANGES as INTEGRITY_RANGES,MODEL_V2 as INTEGRITY_V2,SHEAR_RATE_DEFAULT
        self.emulsion_integrity=validate_emulsion_integrity(emulsion_integrity)
        self.integrity_values=self.emulsion_integrity or dict(INTEGRITY_DEFAULTS);self.integrity_controls={}
        self.integrity_enabled=QtWidgets.QCheckBox(text('启用湿乳剂完整性','Enable wet emulsion integrity'))
        self.integrity_enabled.setChecked(self.emulsion_integrity is not None);form.addRow(self.integrity_enabled)
        self.integrity_body=QtWidgets.QWidget();integrity_form=QtWidgets.QFormLayout(self.integrity_body);form.addRow(self.integrity_body)
        integrity_names={'adhesion':('乳剂附着阈值','Emulsion adhesion threshold'),'wet_weakening':('湿润弱化','Hydration weakening'),'damage_weakening':('热损伤弱化','Thermal damage weakening'),'wear_rate_s':('脱落响应 1/s','Wear response 1/s')}
        for name,(low,high) in INTEGRITY_RANGES.items():
            spin=QtWidgets.QDoubleSpinBox();spin.setDecimals(6);spin.setRange(low,high);spin.setSingleStep(.01);spin.setValue(self.integrity_values[name]);self.integrity_controls[name]=spin;integrity_form.addRow(text(*integrity_names[name]),spin)
        self.shear_enabled=QtWidgets.QCheckBox(text('材料允许湿乳剂剪切拖移','Material permits wet emulsion shear'))
        self.shear_enabled.setChecked(self.integrity_values['model']==INTEGRITY_V2);integrity_form.addRow(self.shear_enabled)
        self.shear_rate=QtWidgets.QDoubleSpinBox();self.shear_rate.setDecimals(6);self.shear_rate.setRange(0.,10.);self.shear_rate.setSingleStep(.01)
        self.shear_rate.setValue(self.integrity_values.get('shear_rate_s',SHEAR_RATE_DEFAULT))
        self.shear_display=self.shear_rate.value();self.shear_rate.setEnabled(self.shear_enabled.isChecked())
        self.shear_enabled.toggled.connect(self.shear_rate.setEnabled)
        integrity_form.addRow(text('剪切响应 1/s','Shear response 1/s'),self.shear_rate)
        self.integrity_display={key:spin.value() for key,spin in self.integrity_controls.items()}
        self.integrity_body.setVisible(self.integrity_enabled.isChecked());self.integrity_enabled.toggled.connect(self.integrity_body.setVisible)
        self.integrity_enabled.setToolTip(text('冻结在新曝光材料中。需添加湿乳剂擦伤操作，可在指定药浴之前或之后执行；硬化提高阈值、湿润与热损伤降低阈值。可另启用横向剪切响应；参数未作实测标定。','Frozen in new exposures. Add wet emulsion wear before or after a selected bath. Hardening raises the threshold; hydration and thermal damage lower it. Horizontal shear response can be enabled separately; parameters are not calibrated measurements.'))
        self.carbon_backing=validate_carbon_backing(carbon_backing)
        self.backing_values=self.carbon_backing or dict(BACKING_DEFAULTS);self.backing_controls={}
        self.backing_enabled=QtWidgets.QCheckBox(text('材料具有可去除碳背层','Material has removable carbon backing'))
        self.backing_enabled.setChecked(self.carbon_backing is not None);form.addRow(self.backing_enabled)
        self.backing_body=QtWidgets.QWidget();backing_form=QtWidgets.QFormLayout(self.backing_body);form.addRow(self.backing_body)
        names={'initial_mg_m2':('初始碳载量 mg/m²','Initial carbon mg/m²'),
            'softening_l_g_s':('软化响应 L/(g·s)','Softening response L/(g·s)'),
            'detachment_rate_s':('松脱速率 1/s','Detachment rate 1/s'),
            'redeposition_rate_s':('乳剂再附着速率 1/s','Emulsion redeposition rate 1/s'),
            'front_removal_rate_s':('乳剂面清理速率 1/s','Front cleaning rate 1/s'),
            'density_per_mg_m2':('碳光学密度系数','Carbon optical density coefficient')}
        for name,(low,high) in BACKING_RANGES.items():
            spin=QtWidgets.QDoubleSpinBox();spin.setDecimals(6);spin.setRange(low,high);spin.setSingleStep(.01)
            spin.setValue(self.backing_values[name]);self.backing_controls[name]=spin;backing_form.addRow(text(*names[name]),spin)
        self.backing_display={key:spin.value() for key,spin in self.backing_controls.items()}
        self.backing_halation=QtWidgets.QCheckBox(text('曝光时吸收背面回返光','Absorb rear return during exposure'))
        self.backing_halation.setChecked(self.backing_values['model']==BACKING_V2)
        backing_form.addRow(self.backing_halation)
        self.backing_halation.setToolTip(text('用曝光时碳载量和同一光学密度系数计算双程吸收，只削弱背面光晕回返；不削弱主曝光、乳剂内扩散或片基导光。后续水洗不倒改潜影。旧材料需明确开启；系数非品牌标定。','Uses exposure-time carbon load and the same density coefficient for double-pass absorption of rear halation only. Primary exposure, emulsion spread and piping are unchanged. Later washing cannot re-expose the latent. Enable explicitly for legacy materials; coefficients are not stock measurements.'))
        target=QtWidgets.QWidget();line=QtWidgets.QHBoxLayout(target);line.setContentsMargins(0,0,0,0)
        self.backing_loss=QtWidgets.QDoubleSpinBox();self.backing_loss.setRange(0.,32.);self.backing_loss.setDecimals(3);self.backing_loss.setValue(2.);self.backing_loss.setSuffix(text(' 档',' stops'));line.addWidget(self.backing_loss)
        solve=QtWidgets.QPushButton(text('换算碳载量','Resolve carbon load'));solve.clicked.connect(self.resolve_backing_load);line.addWidget(solve)
        backing_form.addRow(text('期望回返衰减','Desired return loss'),target)
        target.setToolTip(text('按回返能量减少的档数反算初始碳载量，并开启防光晕耦合。每增加一档，回返能量减半；不承诺成片RGB线性。更多碳也增加后续清洗负担及未洗净时的密度，不能独立消除这些关联。只保存算出的材料参数。','Resolves initial load from stops of return attenuation and enables the coupling. One stop halves return energy, not display RGB. More carbon also increases washing demand and unwashed density; these effects remain linked. Only resolved stock parameters are saved.'))
        self.backing_error=QtWidgets.QLabel();self.backing_error.setWordWrap(True);self.backing_error.hide();backing_form.addRow(self.backing_error)
        self.backing_body.setVisible(self.backing_enabled.isChecked());self.backing_enabled.toggled.connect(self.backing_body.setVisible)
        self.backing_enabled.setToolTip(text('只用于新曝光材料；去碳需在逐浴操作中加入软化预浴和清洗。与旧通用附加层分开记账；可明确开启曝光防光晕耦合。这些系数是创作降阶参数。','Frozen for new exposures. Add backing prebath and wash operations to remove it. Separate from the legacy auxiliary layer; exposure antihalation coupling can be enabled explicitly. Coefficients are creative reduced-model parameters.'))
        self.surface_optics=QtWidgets.QCheckBox(text('启用表面银反射与透射色差','Enable surface silver reflection / transmission colour'))
        self.surface_optics.setChecked(self.original_model==RESPONSE_MODEL_V4);form.addRow(self.surface_optics)
        self.surface_optics.setToolTip(text('同一层沉积银决定反射与透射，增加散射也会降低透光。只作用新曝光材料；旧潜影和结果不会被改写。','The same silver layer controls reflection and transmission. More scattering reduces transmission. Applies to new exposures; saved latents and results remain unchanged.'))
        self.optics_body=QtWidgets.QWidget();optics=QtWidgets.QFormLayout(self.optics_body);form.addRow(self.optics_body)
        for name,(low,high) in RESPONSE_V4_RANGES.items():
            spin=QtWidgets.QDoubleSpinBox();spin.setDecimals(6);spin.setRange(low,high);spin.setSingleStep(.05);spin.setValue(self.value[name]);self.controls[name]=spin;optics.addRow(text(*RESPONSE_LABELS[name]),spin)
        for i,channel in enumerate('RGB'):
            spin=QtWidgets.QDoubleSpinBox();spin.setDecimals(6);spin.setRange(0,10);spin.setSingleStep(.001);spin.setValue(self.value['surface_silver_scattering_rgb'][i]);self.controls['scatter_'+channel]=spin
            optics.addRow(text('后向散射密度系数 ','Backscatter density coefficient ')+channel,spin)
        self.optics_body.setVisible(self.surface_optics.isChecked());self.surface_optics.toggled.connect(self.optics_body.setVisible)
        self.setToolTip(text('材料决定对药液和温度的响应。半响应载量越小，越容易被抑制；溶解响应越大，误接触定影液时银盐损失越快。仅用于新曝光材料，已存潜影保持冻结响应。','Material response to chemistry and temperature. Lower half-response load means stronger inhibition; greater dissolution response means faster damage from fixer. Saved latent responses remain frozen.'))
        for name,(low,high) in {**RESPONSE_V3_RANGES,**RESPONSE_V2_RANGES,**RESPONSE_RANGES}.items():
            spin=QtWidgets.QDoubleSpinBox();spin.setDecimals(6);spin.setRange(low,high);spin.setSingleStep(.001 if self.value[name]<1 else 1.);spin.setValue(self.value[name]);self.controls[name]=spin;form.addRow(text(*RESPONSE_LABELS[name]),spin)
        for i,channel in enumerate('RGB'):
            spin=QtWidgets.QDoubleSpinBox();spin.setDecimals(6);spin.setRange(0,10);spin.setSingleStep(.001);spin.setValue(self.value['residue_density_rgb_per_mg_m2'][i]);self.controls[channel]=spin
            form.addRow(text('残留密度系数 ','Residue density coefficient ')+channel,spin)
        self.display_values={key:control.value() for key,control in self.controls.items()}
        buttons=QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok|QtWidgets.QDialogButtonBox.StandardButton.Cancel);layout.addWidget(buttons);buttons.accepted.connect(self.apply);buttons.rejected.connect(self.reject)

    def read_backing(self):
        return validate_carbon_backing({**self.backing_values,
            **{key:spin.value() for key,spin in self.backing_controls.items() if spin.value()!=self.backing_display[key]},
            'model':BACKING_V2 if self.backing_halation.isChecked() else BACKING_V1})

    def resolve_backing_load(self):
        from film_foundry.core.carbon_backing import initial_mass_for_halation_loss
        try:
            material={**self.read_backing(),'model':BACKING_V2}
            mass=initial_mass_for_halation_loss(material,self.backing_loss.value())
            self.backing_controls['initial_mg_m2'].setValue(mass)
            self.backing_halation.setChecked(True);self.backing_enabled.setChecked(True)
            self.backing_error.clear();self.backing_error.hide()
        except ValueError as exc:
            self.backing_error.setText(str(exc));self.backing_error.show()

    def apply(self):
        from film_foundry.contracts.emulsion_integrity import validate_emulsion_integrity
        integrity={**self.integrity_values,**{key:spin.value() for key,spin in self.integrity_controls.items() if spin.value()!=self.integrity_display[key]}}
        if self.shear_enabled.isChecked():
            integrity['model']='wet_emulsion_integrity_v2'
            integrity['shear_rate_s']=self.shear_rate.value() if self.shear_rate.value()!=self.shear_display else self.integrity_values.get('shear_rate_s',self.shear_display)
        else:
            integrity['model']='wet_emulsion_integrity_v1';integrity.pop('shear_rate_s',None)
        self.emulsion_integrity=validate_emulsion_integrity(integrity) if self.integrity_enabled.isChecked() else None
        self.carbon_backing=self.read_backing() if self.backing_enabled.isChecked() else None
        for name in {**RESPONSE_RANGES,**RESPONSE_V2_RANGES,**RESPONSE_V3_RANGES}:
            if self.controls[name].value()!=self.display_values[name]:self.value[name]=self.controls[name].value()
        if self.surface_optics.isChecked():
            self.value['model']=RESPONSE_MODEL_V4
            for name in RESPONSE_V4_RANGES:
                if self.controls[name].value()!=self.display_values[name]:self.value[name]=self.controls[name].value()
            self.value['surface_silver_scattering_rgb']=tuple(self.controls['scatter_'+c].value() if self.controls['scatter_'+c].value()!=self.display_values['scatter_'+c] else self.value['surface_silver_scattering_rgb'][i] for i,c in enumerate('RGB'))
        else:
            for name in RESPONSE_V4_DEFAULTS:self.value.pop(name,None)
            self.value['model']=RESPONSE_MODEL_V3
            if self.original_model not in (RESPONSE_MODEL_V3,RESPONSE_MODEL_V4) and self.value['dye_attack_l_mmol_s']==.002:
                self.value.pop('dye_attack_l_mmol_s');self.value['model']=RESPONSE_MODEL_V2
        self.value['residue_density_rgb_per_mg_m2']=tuple(self.controls[c].value() if self.controls[c].value()!=self.display_values[c] else self.value['residue_density_rgb_per_mg_m2'][i] for i,c in enumerate('RGB'));self.value=validate_process_response(self.value);self.accept()

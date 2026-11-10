"""Scrollable recipe diagnostics, independent of image-size or render timing."""
from film_foundry.ui.qt import QtWidgets
from film_foundry.ui.curve_editor import text
from film_foundry.ui.roll_process_dialog import ACTIONS


class ProcessStateView(QtWidgets.QTreeWidget):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.setColumnCount(2);self.setHeaderLabels([text('参数','Parameter'),text('数值','Value')])
        self.header().setSectionResizeMode(0,QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.header().setSectionResizeMode(1,QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.setRootIsDecorated(True);self.setAlternatingRowColors(True)
        self.setToolTip(text('配方计算预览，不是成片测量。最终颗粒按各处实际保留产物加权，不能只凭首浴预测。',
            'Recipe calculations, not measurements of the finished image. Final grain uses locally retained products and cannot be predicted from the first bath alone.'))

    def update_state(self,state,program,recipe,*,local_grain_response=True):
        from film_foundry.contracts.formation_history import enabled
        from film_foundry.ui.bath_solution_dialog import FORMATION_TIPS
        self.clear()
        def item(parent,zh,en,value=None,tip=None):
            row=QtWidgets.QTreeWidgetItem(parent,[text(zh,en),'' if value is None else f'{value:.6g}'])
            if tip:
                for col in (0,1):row.setToolTip(col,tip)
            return row
        reference=item(self,'参考工艺条件','Reference conditions')
        for zh,en,value in (('显影活性','Development activity',state.activity),('反差系数','Contrast factor',state.gamma_factor),
                            ('趾部位移','Toe shift',state.toe_shift),('肩部位移','Shoulder shift',state.shoulder_shift)):
            item(reference,zh,en,value)
        if not enabled(recipe):
            for zh,en,value in (('灰雾密度增量','Fog density increment',state.d_min_shift),('颗粒度系数','Granularity factor',state.grain_factor),('颗粒尺度系数','Grain scale factor',state.grain_radius_factor)):
                item(reference,zh,en,value)
        reference.setExpanded(True)
        baths=item(self,'逐浴反应','Bath reactions');baths.setExpanded(True)
        grain_shown = False
        for index,step in enumerate(program.steps):
            number=(step.bath_index if step.bath_index is not None else index)+1
            labels=ACTIONS.get(step.action.value,(step.action.value,)*2)
            row=item(baths,f'{number} · {labels[0]}',f'{number} · {labels[1]}')
            if step.action.value=='destroy_dye' and step.dye_destruction_basis=='compatibility':
                row.setText(0,f'{number} · '+text('工艺兼容性','Process compatibility'))
                child=item(row,'染料稳定性','Dye stability')
                child.setText(1,text('由材料决定','Material-dependent'))
            else:
                item(row,'反应完成度','Reaction completion',step.strength,
                    text('算子的反应比例，实际生成量还受材料库存、可显影性和局部接触限制。','Operator reaction fraction; actual product mass is bounded by inventory, developability and local contact.'))
            response=step.formation_response
            if response:
                for key,zh,en,value in (
                    ('granularity_factor','颗粒度系数','Granularity factor',response.grain_factor),
                    ('grain_radius_factor','颗粒相关半径系数','Grain correlation radius factor',response.grain_radius_factor),
                    ('grain_clumping_factor','颗粒团聚系数','Grain clumping factor',response.grain_clump_factor),
                    ('chemical_fog_factor','化学灰雾反应量','Chemical fog reaction dose',response.fog_dose)):
                    if not local_grain_response and grain_shown and key != 'chemical_fog_factor':
                        continue
                    tip=text(*FORMATION_TIPS[key])
                    if key=='chemical_fog_factor':tip=text('本子反应的灰雾反应量，非最终灰雾密度；还受剩余银盐、材料敏感度及局部接触限制。','Chemical fog dose for this subreaction, not final fog density; bounded by halide, material susceptibility and local contact.')
                    item(row,zh,en,value,tip)
                grain_shown = True
            row.setExpanded(len(program.steps)<8)

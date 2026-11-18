"""Explicit material selection before opening a fresh work document."""
from pathlib import Path
from html import escape
from film_foundry.app.material_selection import material_choices, validate_selection
from film_foundry.ui.qt import QtCore, QtWidgets, _FormLabel
from film_foundry.ui.curve_editor import text


FAMILIES = (('film', '胶片', 'Film'), ('peel', '撕拉片', 'Peel-apart'),
            ('integral', '拍立得', 'Integral instant'), ('plate', '银版', 'Daguerreotype'),
            ('paper', '相纸', 'Photographic paper'), ('wet', '湿版', 'Wet collodion'),
            ('cyanotype', '蓝晒', 'Cyanotype'), ('platinum', '铂钯', 'Platinum / palladium'))
VARIANT_NAMES = {
    'peel': {'dye': ('彩色撕拉片', 'Colour peel-apart'), 'silver': ('黑白正负双片', 'B&W positive / negative')},
    'integral': {'dye': ('彩色拍立得', 'Colour instant'), 'silver': ('黑白拍立得', 'B&W instant')},
    'plate': {'default': ('达盖尔银版', 'Daguerreotype')},
    'paper': {'variable_contrast': ('可变反差黑白相纸', 'Variable-contrast B&W paper'),
              'graded': ('定级黑白相纸', 'Graded B&W paper'), 'direct_positive': ('直接正像相纸', 'Direct-positive paper'),
              'color': ('彩色显色相纸', 'Chromogenic colour paper')},
    'wet': {'glass_negative': ('玻璃负片', 'Glass negative'), 'ambrotype': ('玻璃黑底正像', 'Ambrotype'),
            'tintype': ('深色金属正像', 'Tintype')},
    'cyanotype': {'default': ('蓝晒纸', 'Cyanotype paper')},
    'platinum': {'default': ('铂钯纸', 'Platinum / palladium paper')},
}
# These describe existing processes; they do not drive operation order or defaults.
PROFILES = {
    'film': (('曝光 → 冲洗 → 扫描', 'Expose → develop → scan'),
             ('胶片介质、扫描成片；可保留潜影继续冲洗。', 'Film medium and scans; exposures can be retained for further development.'),
             ('透射观察；扫描单独调色。', 'Transmission viewing; independent scan adjustments.')),
    'peel': (('曝光 → 铺药 → 转移 → 撕开', 'Expose → spread → transfer → peel'),
             ('接收片与供体分别保留；彩色供体是残片，黑白正负双片可继续清理负片。',
              'Receiver and donor are retained separately. Colour leaves donor residue; the B&W pair supports negative clearing.'),
             ('接收片反射观看，已清理银负片透射观看。', 'Reflective receiver viewing; transmission for the cleared silver negative.')),
    'integral': (('曝光 → 出片 → 铺药 → 显影与稳定', 'Expose → eject → spread → develop / stabilize'),
                 ('一张完整即显照片。', 'One sealed instant photograph.'), ('纸面反射，可选择药囊白边。', 'Reflective print, with optional pod border.')),
    'plate': (('抛光 → 感光化 → 曝光 → 显影 → 定影', 'Polish → sensitize → expose → develop → fix'),
              ('一块银版，可继续镀金与表面处理。', 'One silver plate, with optional gilding and surface treatment.'),
              ('镜面反射；照明与观看角度影响正负观感。', 'Specular reflection; lighting and viewing angle affect image appearance.')),
    'paper': (('曝光 → 显影 → 定影或漂定 → 水洗与干燥', 'Expose → develop → fix / blix → wash / dry'),
              ('一张相纸；普通相纸通常承接底片投影。', 'One photographic print; ordinary paper typically receives a negative projection.'),
              ('纸面反射；湿干与纸基影响质感。', 'Paper reflection; moisture and paper base affect appearance.')),
    'wet': (('倒膜 → 银浴 → 湿态曝光 → 显影 → 定影', 'Coat → sensitize → expose wet → develop → fix'),
            ('玻璃负片或深色衬底上的银像正片。', 'A glass negative or a silver image on a dark backing.'),
            ('玻璃负片透射；黑底玻璃与金属正像反射。', 'Transmission for glass negatives; reflection for ambrotypes and tintypes.')),
    'cyanotype': (('接触曝光 → 水洗 → 氧化 → 干燥', 'Contact expose → wash → oxidize → dry'),
                  ('一张蓝色铁盐影像；通常用负片接触印相。', 'One blue iron-process print, typically contact-printed from a negative.'),
                  ('纸面反射，保留刷涂与纸纤维。', 'Paper reflection with coating and fibre structure.')),
    'platinum': (('接触曝光 → 显影 → 清除残铁 → 水洗与干燥', 'Contact expose → develop → clear iron → wash / dry'),
                 ('一张铂钯金属影像；通常用负片接触印相。', 'One platinum / palladium print, typically contact-printed from a negative.'),
                 ('纸面反射；金属比例与纸张影响影调。', 'Paper reflection; metal ratio and paper affect tone.')),
}


def choice_name(record):
    if record.get('custom_name'):return record['custom_name']
    if record['family'] == 'film':
        from film_foundry.app.seasonal_collection import localized_name
        from film_foundry.ui.i18n import current_language,tr
        if record.get('origin')=='custom':return record.get('name',record['key'])
        translation='preset.film.'+record['key'];name=tr(translation)
        return localized_name('film', record['key'], current_language()) or (name if name!=translation else record.get('name',record['key']))
    return text(*VARIANT_NAMES[record['family']].get(record['key'], ('自定义材料', 'Custom material')))


class MaterialChooser(QtWidgets.QDialog):
    def __init__(self, parent=None, *, paths=(), family='film', current_key=None, current_material=None, fixed_family=False):
        super().__init__(parent)
        self.setWindowTitle(text('选择材料', 'Choose material'));self.resize(960,620)
        self.setMinimumSize(760,480)
        self.selection=None;self.records=[];self.remembered={family:current_key};self._family=None;self.custom={}
        if current_material is not None and not any(r['material']==current_material for r in material_choices(family)):
            self.custom[family]=[{'family':family,'key':'current','material':current_material,'custom_name':text('当前自定义材料','Current custom material')}]
            self.remembered[family]='current'
        root=QtWidgets.QVBoxLayout(self)
        self.source=QtWidgets.QLabel(text('来源 · {count} 张图像', 'Source · {count} images').format(count=len(paths)) if len(paths)>1
                                   else text('来源 · ', 'Source · ')+Path(paths[0]).name if paths else text('材料定义', 'Material definition'))
        self.source.setTextFormat(QtCore.Qt.TextFormat.PlainText);self.source.setToolTip('\n'.join(map(str,paths)))
        self.source.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored,QtWidgets.QSizePolicy.Policy.Preferred)
        root.addWidget(self.source)
        split=QtWidgets.QHBoxLayout();root.addLayout(split,1)
        self.families=QtWidgets.QListWidget();self.families.setFixedWidth(146);split.addWidget(self.families)
        self.families.setWordWrap(True);self.families.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for key,zh,en in FAMILIES:
            item=QtWidgets.QListWidgetItem(text(zh,en));item.setData(QtCore.Qt.ItemDataRole.UserRole,key)
            item.setToolTip(text(zh,en))
            item.setSizeHint(QtCore.QSize(130,43));self.families.addItem(item)
        self.families.setVisible(not fixed_family)
        center=QtWidgets.QVBoxLayout();split.addLayout(center,4)
        self.search=QtWidgets.QLineEdit();self.search.setPlaceholderText(text('搜索此类材料', 'Search this family'));self.search.setClearButtonEnabled(True);center.addWidget(self.search)
        filters=QtWidgets.QHBoxLayout();center.addLayout(filters);self.filters={}
        names={'color':('色彩不限','Any colour'), 'polarity':('正负不限','Any polarity'), 'origin':('来源不限','Any origin')}
        values={'color':(('color','彩色','Colour'),('monochrome','黑白','B&W')),
                'polarity':(('negative','负片','Negative'),('positive','正片','Positive')),
                'origin':(('builtin','内置','Built-in'),('custom','自设','Custom'))}
        for key in names:
            combo=QtWidgets.QComboBox();combo.addItem(text(*names[key]),'')
            for value,zh,en in values[key]:combo.addItem(text(zh,en),value)
            self.filters[key]=combo;filters.addWidget(combo);combo.currentIndexChanged.connect(self.filter)
        self.variants=QtWidgets.QCheckBox(text('显示更多胶片变体','More film variants'));center.addWidget(self.variants)
        self.choices=QtWidgets.QListWidget();self.choices.setWordWrap(True);center.addWidget(self.choices,1)
        from film_foundry.ui.preset_collection import CollectionControls
        self.collection=CollectionControls(lambda:self.choices.currentItem().data(QtCore.Qt.ItemDataRole.UserRole) if self.choices.currentItem() is not None and not self.choices.currentItem().isHidden() else None)
        center.insertWidget(1,self.collection);self.collection.changed.connect(self.filter)
        self.empty=QtWidgets.QLabel(text('没有匹配的材料','No matching material'));center.addWidget(self.empty)
        self.load=QtWidgets.QPushButton(text('载入材料文件…','Load material file…'));center.addWidget(self.load);self.load.clicked.connect(self.load_file)
        self.load.setToolTip(text('读取此材料族已保存的 material_definition_v1 JSON；不会改动原文件。','Read a saved material_definition_v1 JSON for this family. The file is unchanged.'))
        details=QtWidgets.QGroupBox(text('所选材料','Selected material'));details.setMinimumWidth(270);split.addWidget(details,3)
        info=QtWidgets.QVBoxLayout(details)
        self.name=_FormLabel();self.name.setWordWrap(True);self.name.setTextFormat(QtCore.Qt.TextFormat.PlainText);info.addWidget(self.name)
        self.badge=QtWidgets.QLabel(text('实验中','Experimental'));info.addWidget(self.badge)
        from film_foundry.ui.selection_preview import SelectionPreview
        self.preview=SelectionPreview(self,self.choices,lambda item:item.data(QtCore.Qt.ItemDataRole.UserRole),source=paths[0] if paths else None)
        info.addWidget(self.preview)
        self.description=QtWidgets.QTextBrowser();self.description.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.description.setMinimumHeight(40)
        self.description.setOpenLinks(False);info.addWidget(self.description,1)
        self.error=QtWidgets.QLabel();self.error.setWordWrap(True);root.addWidget(self.error);self.error.hide()
        buttons=QtWidgets.QDialogButtonBox();root.addWidget(buttons)
        self.apply=buttons.addButton(text('使用此材料' if fixed_family else '以此材料创建工作', 'Use material' if fixed_family else 'Create work with this material'),QtWidgets.QDialogButtonBox.ButtonRole.AcceptRole)
        self.apply.setObjectName('primaryAction');buttons.addButton(text('取消','Cancel'),QtWidgets.QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.confirm);buttons.rejected.connect(self.reject)
        self.families.currentRowChanged.connect(self.family_changed)
        self.choices.currentItemChanged.connect(self.describe);self.search.textChanged.connect(self.filter)
        self.variants.toggled.connect(self.reload)
        self.families.setCurrentRow(next(i for i,f in enumerate(FAMILIES) if f[0]==family))

    def family_changed(self,*_):
        self._family=self.families.currentItem().data(QtCore.Qt.ItemDataRole.UserRole)
        self.search.clear()
        for combo in self.filters.values():
            blocker=QtCore.QSignalBlocker(combo);combo.setCurrentIndex(0);del blocker
            combo.setVisible(self._family=='film')
        self.variants.setVisible(self._family=='film');self.badge.setVisible(self._family!='film');self.load.setVisible(self._family!='film');self.error.hide();self.reload()

    def reload(self,*_):
        blocker=QtCore.QSignalBlocker(self.choices);self.choices.clear()
        self.records=material_choices(self._family,include_variants=self.variants.isChecked())+self.custom.get(self._family,[])
        existing={r['key'] for r in self.records}
        self.records.extend(r for r in self.collection.store.material_files(self._family) if r['key'] not in existing)
        if self._family=='film' and self.remembered.get('film') and not any(r['key']==self.remembered['film'] for r in self.records):
            current=next((r for r in material_choices('film',include_variants=True) if r['key']==self.remembered['film']),None)
            if current:self.records.insert(0,current)
        for record in self.records:
            label=choice_name(record)
            if self._family=='film':
                polarity=text('反转片','Reversal') if record['polarity']=='positive' else text('负片','Negative')
                kind=text('黑白','B&W') if record['color']=='monochrome' else text('彩色','Colour')
                label+='\n'+kind+' · '+polarity
            item=QtWidgets.QListWidgetItem(label);item.setData(QtCore.Qt.ItemDataRole.UserRole,record)
            item.setToolTip(label);self.choices.addItem(item)
            if record['key']==self.remembered.get(self._family):self.choices.setCurrentItem(item)
        del blocker
        self.preview.reset()
        self.filter()

    def filter(self,*_):
        words=self.search.text().casefold().split();visible=[]
        for i in range(self.choices.count()):
            item=self.choices.item(i);record=item.data(QtCore.Qt.ItemDataRole.UserRole)
            match=self.collection.matches(record) and all(w in (item.text()+' '+record['key']).casefold() for w in words)
            if self._family=='film':match=match and all(not c.currentData() or record.get(k)==c.currentData() for k,c in self.filters.items())
            item.setHidden(not match)
            if match:visible.append(item)
        if self.choices.currentItem() not in visible:self.choices.setCurrentItem(visible[0] if visible else None)
        self.empty.setVisible(not visible);self.describe()
        self.preview.selection_changed()

    def describe(self,*_):
        self.collection.sync()
        item=self.choices.currentItem();valid=item is not None and not item.isHidden()
        self.apply.setEnabled(valid)
        if not valid:
            self.name.clear();self.description.clear()
            return
        record=item.data(QtCore.Qt.ItemDataRole.UserRole);family=record['family'];self.remembered[family]=record['key']
        self.name.setText(choice_name(record))
        details={key:text(*pair) for key,pair in zip(('flow','output','view'),PROFILES[family])}
        material=record.get('material',{})
        if family=='film':
            details['output']=text('标准冲洗形成正片，再独立扫描成片。','Standard development forms a positive transparency, followed by an independent scan.') if record['polarity']=='positive' else text('标准冲洗形成负片，扫描反相得到正像。','Standard development forms a negative; scan inversion yields a positive image.')
        elif family=='peel':
            silver=material.get('transfer')=='silver_compound' and material.get('donor_mode')=='silver_negative'
            details['output']=text('接收正片与银像负片；撕开后可继续清理、刷洗负片。','A receiver positive and a silver negative; clear or brush the negative after peeling.') if silver else text('接收正片与供体残片；残片不等同于完整可用负片。','A receiver positive and donor residue; the residue is not a complete usable negative.')
            if not silver:details['view']=text('接收片反射观看，供体残片可独立查看。','Reflective receiver viewing; donor residue can be inspected separately.')
        elif family=='paper':
            if material.get('kind')=='direct_positive':details['output']=text('一张直接正像相纸，可直接从照片曝光。','One direct-positive print, suitable for exposure from a photograph.')
            details['flow']=text('曝光 → 显影 → 漂白定影 → 水洗与干燥','Expose → develop → blix → wash / dry') if material.get('kind')=='color' else text('曝光 → 显影 → 定影 → 水洗与干燥','Expose → develop → fix → wash / dry')
        elif family=='wet':
            negative=material.get('support')=='glass_negative'
            details['output']=text('一块玻璃负片，可继续透射观察或接触印相。','One glass negative, available for transmission viewing or contact printing.') if negative else text('一块深色衬底上的银像正片。','One silver-image positive on a dark backing.')
            details['view']=text('玻璃负片透射观看。','Transmission viewing through the glass negative.') if negative else text('银像与深色衬底共同形成反射正像。','Reflection from the silver image against the dark backing.')
        if family in ('cyanotype','platinum') or (family=='paper' and record['material']['kind']!='direct_positive'):
            hint=('照片亮部提供较多曝光，通常得到较暗影像。要印正像，请从底片选择接触印相；这里不会自动反相。',
                  'Bright source pixels expose more and usually form darker areas. For a positive print, use a negative as an optical source. No automatic inversion here.')
        else:hint=('照片作为曝光来源；进入后可调整材料与对应工艺。','The photograph supplies exposure. Material and process remain adjustable before formation.')
        details['input']=text(*hint)
        captions=(('flow','处理流程','Process'),('output','得到什么','Products'),('view','观看方式','Viewing'),('input','曝光来源','Exposure input'))
        self.description.setHtml(''.join('<p style="margin-top:16px;margin-bottom:5px"><b>'+escape(text(zh,en))+'</b></p><p style="margin:0">'+escape(details[key])+'</p>' for key,zh,en in captions))

    def load_file(self):
        path,_=QtWidgets.QFileDialog.getOpenFileName(self,text('载入材料定义','Load material definition'),'','JSON (*.json)')
        if not path:return
        try:
            from film_foundry.core.atomic_io import strict_json_load
            data=strict_json_load(path)
            if data.get('model')!='material_definition_v1' or data.get('family')!=self._family:
                raise ValueError(text('请选择当前材料族的材料定义文件','Choose a material definition for the selected family'))
            record=validate_selection(dict(data,key=str(Path(path).resolve())))
            record['custom_name']=Path(path).name
            self.custom.setdefault(self._family,[])
            self.custom[self._family]=[r for r in self.custom[self._family] if r['key']!=record['key']]+[record]
            self.remembered[self._family]=record['key'];self.search.clear();self.reload();self.error.hide()
        except (OSError,ValueError,TypeError,KeyError) as exc:self.error.setText(str(exc));self.error.show()

    def confirm(self):
        item=self.choices.currentItem()
        if item is None or item.isHidden():return
        try:self.selection=validate_selection(item.data(QtCore.Qt.ItemDataRole.UserRole))
        except (OSError,ValueError,TypeError,KeyError) as exc:self.error.setText(str(exc));self.error.show();return
        self.collection.used(self.selection)
        self.accept()

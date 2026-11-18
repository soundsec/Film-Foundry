"""Small persisted favorites / recent selections, independent of work recipes."""
import json
from pathlib import Path
from film_foundry.core.atomic_io import atomic_output_path
from film_foundry.ui.preferences import preferences_path
from film_foundry.ui.qt import QtCore,QtWidgets
from film_foundry.ui.curve_editor import text


class PresetCollection:
    def __init__(self):
        path=preferences_path();self.path=path.with_name(path.stem+'_selections.json')

    def read(self):
        try:
            if self.path.stat().st_size>1024*1024:return {}
            value=json.loads(self.path.read_text(encoding='utf-8'))
            return {key:[v for v in value.get(key,[]) if isinstance(v,str)][:limit] for key,limit in (('favorites',512),('recent',40))}
        except (OSError,ValueError,TypeError,AttributeError):return {}

    @staticmethod
    def identity(record, bank='material'):
        key=record.get('key')
        if not key or key=='current':return None
        family=record.get('family','film') if bank=='material' else bank
        return json.dumps([family,key],ensure_ascii=False,separators=(',',':'))

    def update(self, identity, *, favorite=None):
        if identity is None:return
        value=self.read()
        key='recent' if favorite is None else 'favorites'
        items=[entry for entry in value.get(key,[]) if entry!=identity]
        if favorite is not False:items.insert(0,identity)
        value[key]=items[:40 if key=='recent' else 512]
        with atomic_output_path(self.path) as temporary:
            temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')

    def material_files(self,family):
        """Resolve only explicitly remembered material JSON references, never media."""
        from film_foundry.core.atomic_io import strict_json_load
        from film_foundry.app.material_selection import validate_selection
        values=self.read();records=[];seen=set()
        for identity in values.get('favorites',[])+values.get('recent',[]):
            try:
                kind,key=json.loads(identity)
                if kind!=family or key in seen:continue
                seen.add(key);path=Path(key)
                if not path.is_absolute() or path.suffix.lower()!='.json' or path.stat().st_size>1024*1024:continue
                value=strict_json_load(path)
                if value.get('model')!='material_definition_v1' or value.get('family')!=family:continue
                record=validate_selection(dict(value,key=key));record['custom_name']=path.name;records.append(record)
            except (OSError,ValueError,TypeError,KeyError,AttributeError):continue
        return records


class CollectionControls(QtWidgets.QWidget):
    changed=QtCore.Signal()

    def __init__(self, record, *, bank='material', parent=None):
        super().__init__(parent);self.record=record;self.bank=bank;self.store=PresetCollection();self.values=self.store.read()
        row=QtWidgets.QHBoxLayout(self);row.setContentsMargins(0,0,0,0)
        self.scope=QtWidgets.QComboBox()
        for key,zh,en in (('','全部','All'),('favorites','收藏','Favorites'),('recent','最近选用','Recently selected')):self.scope.addItem(text(zh,en),key)
        row.addWidget(self.scope,1)
        self.star=QtWidgets.QToolButton();row.addWidget(self.star)
        self.star.clicked.connect(self.toggle);self.scope.currentIndexChanged.connect(self.changed)
        self.scope.setToolTip(text('与搜索和其他筛选共同生效。最近记录只在确认选用后更新。','Combined with search and other filters. Recent selections update only on confirmation.'))
        self.sync()

    def identity(self,record):return self.store.identity(record,self.bank)

    def matches(self,record):
        scope=self.scope.currentData()
        return not scope or self.identity(record) in self.values.get(scope,[])

    def sync(self):
        record=self.record();identity=self.identity(record) if record else None
        self.star.setEnabled(identity is not None)
        favorite=identity in self.values.get('favorites',[])
        self.star.setText(text('★ 已收藏','★ Saved') if favorite else text('☆ 收藏','☆ Save'))

    def toggle(self):
        record=self.record()
        if not record:return
        identity=self.identity(record)
        try:self.store.update(identity,favorite=identity not in self.values.get('favorites',[]))
        except OSError as exc:self.star.setToolTip(str(exc));return
        self.values=self.store.read();self.sync();self.changed.emit()

    def used(self,record):
        try:self.store.update(self.identity(record))
        except OSError:pass  # A preference failure must not prevent material adoption.

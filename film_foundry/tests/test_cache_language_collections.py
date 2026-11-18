"""Persistent display-only reuse and user-supplied desktop preferences."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import numpy as np
import pytest
from PIL import Image
from film_foundry.tests.test_desktop_usability import desktop,photos,wait


def test_thumbnail_disk_bound_generation_and_damage(tmp_path):
    from film_foundry.app.thumbnail_cache import ThumbnailCache
    cache=ThumbnailCache(tmp_path/'thumbs.db',max_bytes=70,max_entries=2)
    _,generation=cache.lookup('a');cache.put('a',b'x'*30,generation)
    other=ThumbnailCache(cache.path,max_bytes=70,max_entries=2)
    assert other.lookup('a')[0]==b'x'*30
    cache.put('b',b'y'*30,generation)
    with cache.connect() as db:
        before=db.total_changes
        cache.lookup('a')
        assert db.total_changes==before
        db.execute("UPDATE thumbnails SET used=0 WHERE key='a'")
    cache.lookup('a');cache.put('c',b'z'*30,generation)
    assert cache.lookup('b')[0] is None and cache.statistics()['entries']==2
    other.clear();cache.put('old',b'old',generation)
    assert cache.statistics()['entries']==0 and cache.path.stat().st_size<65536
    cache.close_thread_connection()
    cache.path.write_bytes(b'damaged')
    assert cache.lookup('a')==(None,None)  # Disposable failures cannot block rendering.


def test_reference_thumbnails_reopen_invalidate_and_repair(tmp_path,monkeypatch):
    from film_foundry.app.selection_preview import SelectionPreviewSession
    from film_foundry.app.material_selection import material_choices
    from film_foundry.app.material_lab_session import MaterialLabSession
    from film_foundry.app.thumbnail_cache import ThumbnailCache
    from film_foundry.app.control import WorkflowControl
    monkeypatch.setenv('FILM_FOUNDRY_UI_SETTINGS',str(tmp_path/'ui.json'))
    calls=[]
    monkeypatch.setattr(MaterialLabSession,'run_standard',lambda self,material,**kwargs:calls.append(deepcopy(material)))
    monkeypatch.setattr(MaterialLabSession,'preview',lambda *_:np.full((16,24,3),.4,np.float32))
    record=material_choices('plate')[0];control=WorkflowControl()
    first=SelectionPreviewSession(long_edge=48).render(record,False,control)
    assert np.array_equal(first,SelectionPreviewSession(long_edge=48).render(record,False,control)) and len(calls)==1
    changed=deepcopy(record);changed['material']['roughness']=.234
    SelectionPreviewSession(long_edge=48).render(changed,False,control);assert len(calls)==2
    SelectionPreviewSession(long_edge=64).render(record,False,control);assert len(calls)==3
    with sqlite3.connect(ThumbnailCache().path) as db:db.execute("UPDATE thumbnails SET image=?",(b'bad PNG',))
    SelectionPreviewSession(long_edge=48).render(record,False,control);assert len(calls)==4
    path=tmp_path/'source.png';Image.new('RGB',(30,20),'red').save(path)
    session=SelectionPreviewSession(source=path,long_edge=48);session.render(record,True,control)
    SelectionPreviewSession(source=path,long_edge=48).render(record,True,control);assert len(calls)==5
    Image.new('RGB',(32,20),'blue').save(path)
    with pytest.raises(ValueError,match='Source changed'):session.render(record,True,control)
    SelectionPreviewSession(source=path,long_edge=48).render(record,True,control);assert len(calls)==6


def test_film_recipe_and_library_cache_keys(tmp_path,monkeypatch):
    from film_foundry.app.selection_preview import SelectionPreviewSession
    from film_foundry.app.preview_session import PreviewSession
    from film_foundry.model.config import DarkroomConfig
    from film_foundry.ui import desktop_library
    from film_foundry.ui.qt import _application
    _application();monkeypatch.setenv('FILM_FOUNDRY_UI_SETTINGS',str(tmp_path/'ui.json'))
    config=DarkroomConfig();calls=[]
    monkeypatch.setattr(SelectionPreviewSession,'film_config',lambda *_:deepcopy(config))
    def render(*_,**__):calls.append(1);return None,np.zeros((20,30,3),np.float32),False
    monkeypatch.setattr(PreviewSession,'render_target',render)
    from film_foundry.app.control import WorkflowControl
    for _ in range(2):SelectionPreviewSession().render({'key':'one'},False,WorkflowControl())
    assert len(calls)==1
    config.exposure_ev+=1
    SelectionPreviewSession().render({'key':'one'},False,WorkflowControl());assert len(calls)==2
    path=photos(tmp_path)[0];expected=desktop_library.thumbnail(path)
    monkeypatch.setattr(desktop_library,'image_preview',lambda *_:pytest.fail('Disk hit decoded the source'))
    assert desktop_library.thumbnail(path)==expected


def pack(tmp_path,code='fr_FR',**entries):
    value=dict(format='film-foundry-language',version=1,code=code,name='Français',fallback='en_US',translations=entries)
    path=tmp_path/'pack.json';path.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8');return path


def test_language_import_fallback_placeholders_and_template(tmp_path,monkeypatch):
    from film_foundry.ui import i18n,language_packs as packs
    from film_foundry.ui.curve_editor import text
    from film_foundry.ui.preferences import save_preferences,load_preferences
    monkeypatch.setenv('FILM_FOUNDRY_UI_SETTINGS',str(tmp_path/'ui.json'))
    original=i18n.current_language()
    try:
        path=pack(tmp_path,**{'text:Choose material':'Choisir un matériau','key:theme.dark':'Sombre','text:Value {value:.1f}':'Valeur {value:.1f}'})
        assert packs.import_pack(path)=='fr_FR';i18n.set_language('fr_FR');save_preferences(language='fr_FR')
        assert load_preferences()['language']=='fr_FR' and 'fr_FR' in i18n.available_languages()
        monkeypatch.delenv('FILM_FOUNDRY_LANG',raising=False);monkeypatch.setattr(i18n,'_active_language',None)
        assert i18n.current_language()=='fr_FR'  # A fresh runtime reads the installed pack and preference.
        assert text('选择材料','Choose material')=='Choisir un matériau' and i18n.tr('theme.dark')=='Sombre'
        assert text('未翻译','Untranslated')=='Untranslated'
        target=tmp_path/'template.json';packs.export_template(target,'fr_FR');value=json.loads(target.read_text(encoding='utf-8'))
        assert value['translations']['text:Choose material']=='Choisir un matériau'
        assert value['reference']['text:Choose material']['zh']=='选择材料'
        assert len(value['translations'])>2500
        bad=pack(tmp_path,**{'text:Value {value:.1f}':'Valeur {missing}'})
        with pytest.raises(ValueError,match='placeholders'):packs.import_pack(bad)
        assert text('选择材料','Choose material')=='Choisir un matériau'
        with pytest.raises(ValueError,match='new language code'):packs.import_pack(pack(tmp_path,code='../bad'))
        with pytest.raises(ValueError,match='new language code'):packs.import_pack(pack(tmp_path,code='zh_CN'))
    finally:i18n.set_language(original)


def test_external_language_settings_preserve_work(desktop,tmp_path,monkeypatch):
    from film_foundry.ui.qt import QtWidgets
    from film_foundry.ui import i18n
    from film_foundry.ui.material_chooser import MaterialChooser
    p=desktop;p._open_catalog_source(photos(tmp_path)[0],'film');p.exposure_ev.set(.65);before=p._config()
    path=pack(tmp_path,**{'text:Choose material':'Choisir un matériau','key:theme.dark':'Sombre'})
    p.open_global_settings();dialog=p._settings_dialog
    monkeypatch.setattr(QtWidgets.QFileDialog,'getOpenFileName',lambda *_:(str(path),'JSON'))
    dialog.import_language_button.click();assert dialog.language.findData('fr_FR')>=0
    dialog.language.setCurrentIndex(dialog.language.findData('fr_FR'))
    assert i18n.current_language()=='fr_FR' and p._config()==before
    d=MaterialChooser(p.root.qt,family='plate',fixed_family=True);assert d.windowTitle()=='Choisir un matériau'
    d.reject();d.deleteLater();dialog.reject();p._apply_global_language('zh_CN')


def test_favorites_recent_cancel_and_original_indices(desktop,tmp_path):
    from film_foundry.ui.preset_picker import PresetPicker
    from film_foundry.ui.material_chooser import MaterialChooser
    from film_foundry.ui.preset_collection import PresetCollection
    records=[dict(key='a',origin='builtin'),dict(key='b',origin='custom')]
    d=PresetPicker(desktop.root.qt,'Test',['Same','Same'],1,records)
    d.collection.star.click();d.reject()
    assert not PresetCollection().read().get('recent')
    d=PresetPicker(desktop.root.qt,'Test',['Same','Same'],0,records)
    d.collection.scope.setCurrentIndex(1)
    assert d.results.item(0).isHidden() and not d.results.item(1).isHidden()
    d._apply();assert d.selected_index==1
    d=PresetPicker(desktop.root.qt,'Test',['Same','Same'],0,records)
    d.collection.scope.setCurrentIndex(2);assert d.results.currentRow()==1
    d.reject()
    material=MaterialChooser(desktop.root.qt,family='plate',fixed_family=True)
    material.collection.star.click();material.confirm()
    reopened=MaterialChooser(desktop.root.qt,family='plate',fixed_family=True)
    reopened.collection.scope.setCurrentIndex(2);assert reopened.apply.isEnabled()
    reopened.collection.scope.setCurrentIndex(1);reopened.collection.star.click()
    assert not reopened.apply.isEnabled();reopened.reject()


def test_custom_material_favorites_reopen_by_reference(desktop,tmp_path,monkeypatch):
    from film_foundry.ui.material_chooser import MaterialChooser
    from film_foundry.ui.qt import QtWidgets,QtCore
    from film_foundry.app.material_selection import material_choices
    record=material_choices('plate')[0]
    value=dict(model='material_definition_v1',family='plate',material=record['material'])
    path=tmp_path/'my-plate.json';path.write_text(json.dumps(value),encoding='utf-8')
    monkeypatch.setattr(QtWidgets.QFileDialog,'getOpenFileName',lambda *_:(str(path),'JSON'))
    d=MaterialChooser(desktop.root.qt,family='plate',fixed_family=True);d.load_file()
    assert d.choices.currentItem().data(QtCore.Qt.ItemDataRole.UserRole)['key']==str(path.resolve())
    d.collection.star.click();d.reject()
    d=MaterialChooser(desktop.root.qt,family='plate',fixed_family=True);d.collection.scope.setCurrentIndex(1)
    assert d.apply.isEnabled() and d.choices.currentItem().text()=='my-plate.json'
    d.reject();path.unlink()
    d=MaterialChooser(desktop.root.qt,family='plate',fixed_family=True);d.collection.scope.setCurrentIndex(1)
    assert not d.apply.isEnabled();d.reject()

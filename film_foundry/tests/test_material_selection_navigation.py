"""Only selection ownership, routing and the already-exposed material boundary."""
from copy import deepcopy
from pathlib import Path
import pytest
from film_foundry.tests.test_unified_workspace import panel, source, wait
from film_foundry.ui.qt import QtCore, _application
from film_foundry.app.material_selection import material_choices


def select(dialog, family, key):
    for row in range(dialog.families.count()):
        if dialog.families.item(row).data(QtCore.Qt.ItemDataRole.UserRole)==family:
            dialog.families.setCurrentRow(row);break
    for row in range(dialog.choices.count()):
        if dialog.choices.item(row).data(QtCore.Qt.ItemDataRole.UserRole)['key']==key:
            dialog.choices.setCurrentRow(row);return
    raise AssertionError((family,key))


def test_gallery_requires_explicit_material_and_cancellation_keeps_work(panel,tmp_path):
    path=source(tmp_path)
    panel._open_catalog_source(path,'film');before=deepcopy(panel._session_payload())
    panel.open_library();library=panel.library_view
    library.catalog.add([path]);library.refresh();library.grid.setCurrentRow(0)
    library.open_selected();dialog=panel._material_chooser
    assert dialog.isVisible() and panel.workspace_host.currentWidget() is library
    select(dialog,'paper','direct_positive');dialog.reject()
    after=panel._session_payload()
    before.pop('created_at',None);after.pop('created_at',None)
    assert after==before
    library.open_selected();dialog=panel._material_chooser
    select(dialog,'film','monochrome_reversal_transparency')
    dialog.search.setText('no-matching-material-987');assert not dialog.apply.isEnabled()
    dialog.search.clear();select(dialog,'film','monochrome_reversal_transparency');dialog.confirm()
    assert panel.film_preset.get()=='monochrome_reversal_transparency'
    assert panel.develop_preset.get()=='standard_bw_reversal'
    from film_foundry.app.workflow import material_color_category
    assert material_color_category(panel._config())=='monochrome'
    assert panel.pipeline_mode.get()=='full' and panel.link_scanner.get()
    assert panel.workspace_host.currentWidget() is panel.film_workspace


def test_seven_choices_reach_their_own_process_and_freeze_after_exposure(panel,tmp_path):
    path=source(tmp_path)
    for family,key in (('peel','silver'),('integral','silver'),('plate','default'),('paper','direct_positive'),
                       ('wet','tintype'),('cyanotype','default'),('platinum','default')):
        dialog=panel._choose_catalog_material([path]);select(dialog,family,key);dialog.confirm()
        page=panel._material_windows[family];wait(lambda:not page.busy)
        assert page.last_error is None
        record=next(r for r in material_choices(family) if r['key']==key)
        for field,value in page.material_values().items():assert value==record['material'][field],(family,field)
        assert page.session.state is None and page.session.group is None
        assert page.config_tabs.currentWidget() is page.process_group
        assert page.select_material_button.isEnabled()
        assert Path(page.session.source_path)==Path(path)
        if family=='paper':
            for step in ('handling','expose'):
                page.operation_choice.setCurrentIndex(page.operation_choice.findData(step));_application().processEvents()
            viewport=page.controls_scroll.viewport()
            position=page.execute_button.mapTo(viewport,QtCore.QPoint(0,0))
            assert 0<=position.y()<viewport.height()-page.execute_button.height()
    page=panel._material_windows['peel'];page.process('expose');wait(lambda:not page.busy)
    assert page.last_error is None and page.session.state.material.transfer=='silver_compound'
    assert not page.select_material_button.isEnabled()
    before=page.session.state
    with pytest.raises(ValueError):
        page.apply_material_definition(material_choices('peel')[0]['material'])
    assert page.session.state is before
    # Draft selection is reversible before exposure and retains custom values.
    page=panel._material_windows['paper'];page.material_controls['warmth'].setValue(.32)
    page.choose_material();chooser=page._material_chooser
    assert chooser.choices.currentItem().data(QtCore.Qt.ItemDataRole.UserRole)['key']=='current'
    chooser.reject();assert page.material_controls['warmth'].value()==.32


def test_batch_and_custom_material_selection_keep_definition_and_sources(panel,tmp_path,monkeypatch):
    import json
    from PIL import Image
    from film_foundry.ui.qt import QtWidgets
    paths=[source(tmp_path),str(tmp_path/'second.png')];Image.new('RGB',(36,24),'gray').save(paths[1])
    choice=material_choices('paper')[2];choice['material']['warmth']=.21
    file=tmp_path/'my-paper.material.json'
    file.write_text(json.dumps(dict(model='material_definition_v1',family='paper',material=choice['material'])),encoding='utf-8')
    dialog=panel._choose_catalog_material(paths);select(dialog,'paper','direct_positive')
    monkeypatch.setattr(QtWidgets.QFileDialog,'getOpenFileName',lambda *a,**k:(str(file),'JSON'))
    dialog.load_file();assert dialog.apply.isEnabled();dialog.confirm()
    page=panel.workspace_host.currentWidget();wait(lambda:not page.busy)
    assert page.last_error is None and page.kind=='paper' and page.paths==paths
    assert page.material_controls['kind'].currentData()=='direct_positive'
    assert page.material_controls['warmth'].value()==.21
    assert page.session.state is None
    assert page.standard_button.isEnabled()

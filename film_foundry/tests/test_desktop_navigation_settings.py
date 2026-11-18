from film_foundry.tests.test_desktop_usability import desktop
from film_foundry.ui.qt import _application


def test_global_appearance_keeps_open_work_and_unsubmitted_settings(desktop):
    from film_foundry.ui.preferences import load_preferences
    from film_foundry.app.desktop_settings import load_settings
    from film_foundry.ui.i18n import current_language,set_language
    from film_foundry.ui.theme import current_theme,apply_theme
    panel=desktop;panel.show_workbench();panel.develop_temperature.set(31)
    panel._set_scan_advanced(True)
    before=panel._config();workspace=load_settings()['workspace']
    material=panel._show_material_workspace('plate')
    panel.open_global_settings();dialog=panel._settings_dialog
    try:
        dialog.workspace.setText(workspace+'-draft')
        dialog.theme.setCurrentIndex(dialog.theme.findData('ivory'))
        assert current_theme()=='ivory' and load_preferences()['theme']=='ivory'
        dialog.language.setCurrentIndex(dialog.language.findData('en_US'));_application().processEvents()
        assert current_language()=='en_US' and load_preferences()['language']=='en_US'
        assert panel.workspace_host.currentWidget() is material
        assert panel._config()==before and panel._scan_advanced
        assert load_preferences()['scan_advanced']
        assert dialog.workspace.text()==workspace+'-draft'
        dialog.reject();_application().processEvents()
        assert load_settings()['workspace']==workspace
    finally:
        dialog.close();set_language('zh_CN');apply_theme('dark')


def test_file_actions_keep_distinct_archives_and_navigation_only_opens_pages(desktop,monkeypatch):
    panel=desktop;called=[]
    monkeypatch.setattr(panel._workspace_files,'save',lambda **kw:called.append(('material',kw)))
    monkeypatch.setattr(panel._film_workspace_files,'save',lambda **kw:called.append(('film',kw)))
    for action in panel.file_menu.actions():
        if action.text() in ('实验材料工作区','胶片工作'):
            action.menu().actions()[1].trigger()
    assert called==[('material',{'save_as':True}),('film',{'save_as':True})]
    labels=[a.text() for a in panel.workspace_menu.menu().actions()]
    assert not any('保存' in label or '编辑器' in label or '工具' in label for label in labels)
    materials=next(a.menu() for a in panel.workspace_menu.menu().actions() if a.text()=='实验材料')
    materials.actions()[-1].trigger();_application().processEvents()
    assert panel.workspace_host.currentWidget().kind=='platinum'


def test_language_change_respects_open_editor_draft(desktop):
    from film_foundry.ui.i18n import current_language
    panel=desktop;panel.show_workbench();guide=panel._open_context_guide()
    panel.open_global_settings();dialog=panel._settings_dialog
    dialog.language.setCurrentIndex(dialog.language.findData('en_US'))
    assert current_language()=='zh_CN' and dialog.language.currentData()=='zh_CN'
    assert guide.isVisible() and '编辑页' in dialog.appearance_status.text()
    dialog.reject();guide.reject()

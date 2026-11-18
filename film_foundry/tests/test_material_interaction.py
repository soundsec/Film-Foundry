import numpy as np
import pytest
from film_foundry.tests.test_material_windows import isolate_material_catalog,wait
from film_foundry.ui.qt import _application,QtCore,QtWidgets
from PySide6.QtTest import QTest
from film_foundry.ui.material_lab_window import create_material_window


@pytest.mark.parametrize('kind',['plate','wet'])
def test_source_preview_reset_and_view_drag_retain_material(kind):
    window=create_material_window(kind);window.show()
    try:
        assert window.guide_button.isVisible()
        source=np.full((8,12,3),.25,np.float32);window.session.image=source;window._sync()
        assert window.viewer.source is not None and window.source_preview_badge.isVisible()
        assert window.session.state is None and window.session.image is source
        assert window.viewer.result.cacheKey()==window.viewer.original.cacheKey()
        window.set_workspace_layout('studio')
        window.resize(1100,850);_application().processEvents()
        assert window.source_preview_badge.pos().x()==10 and window.source_preview_badge.pos().y()==10
        assert window.config_tabs.tabBar().isVisible()
        assert window.config_tabs.indexOf(window.view_group)>=0
        if kind=='plate':
            window.workflow.select_flow(('fix',))
            assert not window.operation_buttons['fix'].isEnabled()
            assert '预设参数' in window.workflow.action_scope.text() and '曝光' in window.workflow.action_scope.text()
        else:
            window._select_step('fix');assert not window.execute_button.isEnabled()
            assert '预设参数' in window.step_note.text()
        np.testing.assert_array_equal(source,np.full((8,12,3),.25,np.float32))
        values=window.material_values()
        key=next(k for k,c in window.material_controls.items() if hasattr(c,'set_reset_value') and c.maximum()>c.value()+.02)
        value=values[key];values[key]=value+.01;window.apply_material_definition(values)
        control=window.material_controls[key];control.setValue(value+.02)
        assert control.reset_button.isEnabled();control.reset_button.click()
        assert control.value()==pytest.approx(value+.01,abs=1e-3)
        assert control.buttonSymbols()==QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons
        control.setFocus();QTest.keyClick(control,QtCore.Qt.Key.Key_Up)
        assert control.value()>value+.01
        control.reset_button.click()
        window.run_standard();wait(window)
        assert window.session.state is not None and not window.source_preview_badge.isVisible()
        state=window.session.state;fields={key:array.copy() for key,array in state.fields.items()}
        view=window.view_angle;default=view.value();view.slider.setValue(750)
        window.preview_timer.stop();window.refresh_preview()
        assert window.view_group.isEnabled()  # Dragging remains possible while observation runs.
        view.slider.setValue(400);wait(window)
        # Flush the queued latest observation without invoking another process step.
        window.preview_timer.stop();window.refresh_preview();wait(window)
        assert window.session.state is state and window.session.image is source
        for key,array in fields.items():np.testing.assert_array_equal(state.fields[key],array)
        view.spin.reset_button.click();assert view.value()==default
        window.preview_timer.stop()
    finally:
        wait(window);window.close();_application().processEvents()

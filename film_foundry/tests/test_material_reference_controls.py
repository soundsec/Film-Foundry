"""UI reference execution must retain current controls and honest end stages."""
import numpy as np
import pytest
from film_foundry.tests.test_material_windows import wait, isolate_material_catalog
from film_foundry.ui.material_lab_window import MaterialLabWindow
from film_foundry.ui.qt import _application


@pytest.mark.parametrize('kind', ['peel', 'integral', 'plate'])
def test_reference_button_uses_current_controls_and_preserves_result(kind):
    window=MaterialLabWindow(kind);window.show()
    try:
        assert not window.standard_button.isEnabled()
        window.session.image=np.full((8,12,3),.25,np.float32);window._sync()
        assert window.seconds.isEnabled()
        assert window.operation_buttons['prepare' if kind=='plate' else 'expose'].isEnabled()
        assert not window.operation_buttons['fix' if kind=='plate' else 'advance'].isEnabled()
        if kind=='plate':assert window.operation_controls['fix']['seconds'].isEnabled()
        window.seconds.setValue(47);window.seed.setValue(123)
        if kind!='plate':window.process_controls['temperature_c'].setValue(27)
        if kind=='peel':
            window.shortage_enabled.setChecked(True);window.shortage_strength.setValue(.2)
            window.material_controls['donor_mode'].setCurrentIndex(window.material_controls['donor_mode'].findData('silver_negative'))
        else:
            if kind=='plate':window.operation_controls['fix']['seconds'].setValue(720)
            else:window.seconds.setValue(720)
        assert window.standard_button.isEnabled()
        window.standard_button.click();wait(window)
        state=window.session.state;assert state.seed==123
        steps=window.session.recipe()['steps'];ops=[s['operation'] for s in steps]
        assert ops==({'peel':['expose','spread','advance','peel'],
            'integral':['expose','eject','spread','advance'],
            'plate':['prepare','polish','sensitize','expose','develop','fix']}[kind])
        if kind!='plate':
            spread=next(s['parameters'] for s in steps if s['operation']=='spread')
            assert spread['temperature_c']==27
            if kind=='peel':assert spread['reagent_load']==.8
        assert window.session.group is not None
        assert not window.standard_button.isEnabled() and window.run_standard() is False
        assert window.session.state is state
        assert window.workflow.choice.currentData() in window.session.operations
        assert all(op not in window.workflow.main_operations for op in window.workflow.optional_actions)
        for layout in ('studio','compact'):
            window.set_workspace_layout(layout);assert window.session.state is state
        if kind=='peel':
            window.workflow.optional_actions['clear_negative'].trigger()
            assert window.workflow.choice.currentData()=='clear_negative'
            assert window.session.state is state  # Choosing an optional action never executes it.
    finally:window.close();_application().processEvents()


def test_reference_keeps_incomplete_neutralization_explicit():
    window=MaterialLabWindow('integral')
    try:
        window.session.image=np.full((8,12,3),.25,np.float32)
        window.material_controls['neutralizer_capacity'].setValue(0);window._sync()
        window.run_standard();wait(window)
        assert window.session.state.stage=='reacting' and window.session.group is None
        assert window.session.recipe()['steps'][-1]['operation']=='stabilize'
        assert window.workflow.optional_actions['finish'].isEnabled()
        assert not window.standard_button.isEnabled()
    finally:window.close();_application().processEvents()

from copy import deepcopy
import numpy as np
from film_foundry.tests.test_electronic_roll import roll
from film_foundry.tests.test_material_windows import isolate_material_catalog
from film_foundry.tests.test_desktop_usability import wait
from film_foundry.app.control import CancelFlag
from film_foundry.app.roll_peek import RollPeek
from film_foundry.ui.roll_process_dialog import RollProcessDialog
from film_foundry.ui.qt import QtCore,QtWidgets,_application


def test_roll_peek_does_not_write_or_freeze_roll(roll):
    session,config,latent,_=roll;before=deepcopy(session.data)
    paths=list(session.path.parent.iterdir());stamps={p:p.stat().st_mtime_ns for p in paths if p.is_file()}
    image=RollPeek(session.path,config).render(CancelFlag())
    assert image.ndim==3 and np.isfinite(image).all()
    assert session.data==before and list(session.path.parent.iterdir())==paths
    assert stamps=={p:p.stat().st_mtime_ns for p in stamps}


def test_cheat_modal_uses_uncommitted_draft_and_discards_image(roll):
    _application()
    session,config,_,_=roll;captured=[]
    def peek(cfg):captured.append(deepcopy(cfg));return RollPeek(session.path,cfg)
    d=RollProcessDialog(session.process_config(config),peek=peek);d.show()
    try:
        d.controls['temperature_c'].setValue(24);before=deepcopy(d.config)
        d.open_peek();child=d._peek_dialog;assert child is not None,d.status.text()
        assert captured[0].chemistry.temperature_c==24
        assert d.config.chemistry.temperature_c==before.chemistry.temperature_c
        assert not d.tabs.isEnabled() and not d.peek_button.isEnabled()
        assert child.windowModality()==QtCore.Qt.WindowModality.ApplicationModal
        _application().processEvents();assert _application().activeModalWidget() is child
        wait(lambda:not child.busy);assert child.viewer.source is not None,child.status.text()
        child.reject();assert child.viewer.source is None
        assert d._peek_dialog is None and d.tabs.isEnabled()
        assert session.data['phase']=='sealed' and not session.data.get('recipe_draft')
    finally:d.close();_application().processEvents()

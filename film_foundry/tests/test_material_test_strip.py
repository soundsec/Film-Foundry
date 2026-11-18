from copy import deepcopy
import numpy as np
import pytest
from film_foundry.tests.test_material_windows import isolate_material_catalog,wait
from film_foundry.tests.test_desktop_usability import wait as until
from film_foundry.ui.qt import _application,QtCore,QtWidgets
from film_foundry.ui.material_lab_window import create_material_window
from film_foundry.ui.material_test_strip import MaterialTrials,open_material_strip
from film_foundry.app.control import CancelFlag
from film_foundry.app.material_test_strip import MaterialStripSession
from film_foundry.tests.test_desktop_usability import desktop


@pytest.mark.parametrize('kind',['peel','integral','plate','paper','wet','cyanotype','platinum'])
def test_material_trials_real_reference_isolated_and_adoption_undo(kind):
    w=create_material_window(kind);w.show()
    try:
        source=np.tile(np.linspace(.03,.7,12,dtype=np.float32)[None,:,None],(8,1,3))
        w.session.image=source;w._sync();w.preview_timer.stop()
        backend=MaterialTrials(w);axis=next(a for a in backend.axes if a.path==('view_ev',))
        variants,reference,runner=backend.prepare(axis,-1,1,3,32)
        images=[];runner.run(variants,CancelFlag(),lambda i,v,p:images.append(p))
        np.testing.assert_array_equal(images[1],runner.render(reference))
        assert not np.allclose(images[0],images[2])
        assert w.session.state is None and w.session.group is None and w.session.image is source
        backend.adopt(variants[2][1],axis);assert w.view_ev.value()==1
        w.preview_timer.stop();w.edit_tools.travel(False);wait(w);assert w.view_ev.value()==0
        w.preview_timer.stop();w.edit_tools.travel(True);wait(w);assert w.view_ev.value()==1
        assert w.session.state is None
    finally:w.preview_timer.stop();wait(w);w.close();_application().processEvents()


def test_existing_material_trials_only_current_step_and_readonly_view():
    w=create_material_window('peel');w.show()
    try:
        w.session.image=np.full((8,12,3),.25,np.float32);w._sync()
        w.process('expose');wait(w);w.process('spread');wait(w)
        state=w.session.state;fields={k:v.copy() for k,v in state.fields.items()}
        w.workflow.choice.setCurrentIndex(w.workflow.choice.findData('advance'))
        backend=MaterialTrials(w)
        assert not any(a.path[0].startswith('material_controls.') for a in backend.axes)
        axis=next(a for a in backend.axes if a.path==('seconds',))
        variants,reference,runner=backend.prepare(axis,5,20,2,32)
        seen=[];runner.run(variants,CancelFlag(),lambda i,v,p:seen.append(p))
        assert len(seen)==2 and w.session.state is state
        for k,v in fields.items():np.testing.assert_array_equal(v,state.fields[k])
        axis=next(a for a in backend.axes if a.path==('view_ev',))
        variants,_,_=backend.prepare(axis,-1,1,2,32)
        assert all(c['operation'] is None for _,c in variants)
    finally:w.preview_timer.stop();wait(w);w.close();_application().processEvents()


def test_material_dialog_cancel_waits_and_parent_close(monkeypatch):
    from threading import Event
    ready=Event();release=Event()
    def slow(self,config):ready.set();release.wait(8);return np.zeros((8,12,3),np.float32)
    monkeypatch.setattr(MaterialStripSession,'render',slow)
    w=create_material_window('wet');w.show()
    try:
        w.session.image=np.full((8,12,3),.2,np.float32);w._sync()
        d=open_material_strip(w);assert d is not None
        d.generate();until(ready.is_set);assert d.busy
        assert not w.close();assert d.closing and w.isVisible()
        release.set();until(lambda:not d.busy);_application().processEvents()
        assert not w.isVisible() and w.session.state is None
    finally:release.set();w.close();_application().processEvents()


def test_embedded_material_trial_adopts_only_parameter_and_root_waits(desktop,monkeypatch):
    from threading import Event
    panel=desktop;w=panel._mount_material_workspace(create_material_window('plate'),'trial-test')
    w.session.image=np.full((8,12,3),.25,np.float32);w._sync()
    d=open_material_strip(w);assert d is not None
    index=next(i for i in range(d.axis.count()) if d.axis.itemData(i).path==('seconds',))
    d.axis.setCurrentIndex(index);d.start.setValue(20);d.stop.setValue(60);d.count.setValue(2)
    d.generate();until(lambda:not d.busy);assert len(d.frames)==2,d.status.text()
    d.tiles.setCurrentRow(1);d.adopt();assert w.seconds.value()==60 and w.session.state is None
    entered=Event();release=Event()
    def slow(self,config):entered.set();release.wait(8);return np.zeros((8,12,3),np.float32)
    monkeypatch.setattr(MaterialStripSession,'render',slow)
    d=open_material_strip(w);d.generate();until(entered.is_set)
    monkeypatch.setattr(QtWidgets.QMessageBox,'question',lambda *a,**kw:QtWidgets.QMessageBox.StandardButton.Yes)
    panel.work_sessions.bypass=True
    try:
        assert not panel._allow_window_close() and d.cancel.requested()
        release.set();until(lambda:not panel.root.winfo_exists())
    finally:release.set()

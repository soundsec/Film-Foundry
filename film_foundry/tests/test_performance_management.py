from copy import deepcopy
from dataclasses import asdict
from threading import Event
import numpy as np
import pytest
from film_foundry.app.material_lab_session import MaterialLabSession
from film_foundry.app.material_test_strip import MaterialStripSession
from film_foundry.app.control import CancelFlag,WorkflowCancelled
from film_foundry.core.material_family import ViewingConditions
from film_foundry.tests.test_material_windows import isolate_material_catalog
from film_foundry.tests.test_desktop_usability import wait


def trial_config(source):
    return dict(material=asdict(source.material_type()),seed=31,operations={},operation=None,
                view=asdict(ViewingConditions()),scan={},body=0)


@pytest.mark.parametrize('kind',['peel','integral','plate','paper','wet','cyanotype','platinum'])
def test_observation_strip_forms_once_and_is_exactly_equal(kind,monkeypatch):
    source=MaterialLabSession(kind);source.image=np.linspace(.02,.7,8*12*3,dtype=np.float32).reshape(8,12,3)
    base=trial_config(source);variants=[]
    for ev in (-1,-.5,0,.5,1):
        config=deepcopy(base);config['view']['exposure_ev']=ev;variants.append((ev,config))
    actual=[];expected=[];calls=[];original=MaterialLabSession.run_standard
    def counted(self,*a,**kw):calls.append(self.kind);return original(self,*a,**kw)
    monkeypatch.setattr(MaterialLabSession,'run_standard',counted)
    cached=MaterialStripSession(source,long_edge=32);cached.run(variants,CancelFlag(),lambda i,v,p:actual.append(p))
    assert len(calls)==1 and cached._formed is None and cached._source is None
    uncached=MaterialStripSession(source,long_edge=32,reuse=False);uncached.run(variants,CancelFlag(),lambda i,v,p:expected.append(p))
    assert len(calls)==6
    for a,b in zip(actual,expected):np.testing.assert_array_equal(a,b)
    assert source.state is None and source.group is None
    # Formation inputs invalidate the cache, while changing viewing never does.
    cached.render(base);changed=deepcopy(base);changed['seed']=32;result=cached.render(changed)
    np.testing.assert_array_equal(result,uncached.render(changed));assert len(calls)==9
    cached.clear();uncached.clear()


def test_material_trial_cancel_stops_between_steps_and_releases_cache(monkeypatch):
    source=MaterialLabSession('plate');source.image=np.full((8,12,3),.2,np.float32)
    runner=MaterialStripSession(source,long_edge=32);cancel=CancelFlag();operations=[]
    original=MaterialLabSession.process
    def process(self,operation,*a,**kw):
        result=original(self,operation,*a,**kw);operations.append(operation);cancel.cancel();return result
    monkeypatch.setattr(MaterialLabSession,'process',process)
    with pytest.raises(WorkflowCancelled):runner.run([(0,trial_config(source))],cancel,lambda *a:pytest.fail('Cancelled result published'))
    assert operations==['prepare'] and source.state is None and runner._formed is None and runner._source is None


def test_material_preview_coalesces_discards_stale_and_defers_hidden(monkeypatch):
    from film_foundry.ui.material_lab_window import create_material_window
    from film_foundry.ui.qt import _application
    w=create_material_window('paper');w.show();entered=Event();release=Event();calls=[];shown=[]
    try:
        w.session.image=np.full((8,12,3),.2,np.float32);w.session.process('expose');w._sync();w.preview_timer.stop()
        def preview(view,index=0):
            calls.append(view.exposure_ev)
            if len(calls)==1:entered.set();release.wait(5)
            return np.full((8,12,3),view.exposure_ev/10,np.float32)
        monkeypatch.setattr(w.session,'preview',preview)
        monkeypatch.setattr(w.viewer,'show_array',lambda image:shown.append(float(image[0,0,0])))
        w.refresh_preview();wait(entered.is_set)
        for value in (1,2,3):w.view_ev.setValue(value)
        release.set();wait(lambda:len(shown)==1 and not w.busy)
        assert calls==[0,3] and shown==[pytest.approx(.3)]
        w.refresh_preview();assert not w.busy and calls==[0,3]
        w.hide();w.view_ev.setValue(4);assert not w.preview_timer.isActive()
        w.show();wait(lambda:len(shown)==2 and not w.busy)
        assert calls==[0,3,4] and shown[-1]==pytest.approx(.4)
    finally:
        release.set();wait(lambda:not w.busy);w.preview_timer.stop();w.close();_application().processEvents()


def test_histogram_layer_cache_tracks_geometry_options_and_mutated_bins(tmp_path,monkeypatch):
    from film_foundry.ui.qt import _application
    from film_foundry.ui import histogram as module
    monkeypatch.setenv('FILM_FOUNDRY_UI_SETTINGS',str(tmp_path/'ui.json'))
    _application();w=module.Histogram();w.resize(600,200);w.settings.update(fill_style='solid')
    w.bins=np.full((4,256),10,np.int64);calls=[];original=module.solid_histogram_layer
    def counted(*a,**kw):calls.append(1);return original(*a,**kw)
    monkeypatch.setattr(module,'solid_histogram_layer',counted)
    try:
        first=w.grab().toImage()
        for _ in range(5):assert w.grab().toImage()==first
        assert len(calls)==1
        w.bins[1,30]=80;changed=w.grab().toImage();assert len(calls)==2 and changed!=first
        w._solid_cache_key=None;assert w.grab().toImage()==changed;assert len(calls)==3
        w.settings.update(kind='bars');w.grab();assert len(calls)==4
        w.resize(620,200);w.grab();assert len(calls)==5
        w.bins=None;assert w._solid_cache is None
    finally:w.close()

def test_hidden_editor_preview_drops_queued_render():
    from film_foundry.app.preview_budget import preview_budget,Priority
    from film_foundry.ui.material_preview import MaterialPreview
    from film_foundry.ui.qt import _application
    calls=[]
    class Renderer:
        def render_target(self,kind,config):
            calls.append(config['value'])
            frame=np.zeros((12,14,3),np.float32)
            return frame,frame,False
    app=_application()
    value={'value':1}
    widget=MaterialPreview(lambda:value,renderer=Renderer())
    try:
        widget.show();app.processEvents()
        with preview_budget.slot(Priority.INTERACTIVE):
            widget.request()
            widget.hide();app.processEvents()
        wait(lambda:not widget.busy)
        assert not calls and not widget.timer.isActive()
        value['value']=2
        widget.show();wait(lambda:widget.frames is not None and not widget.busy)
        assert calls==[2]
    finally:
        widget.close();widget.deleteLater()

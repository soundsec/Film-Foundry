"""Causal reduced models: pools, polarity, freezing, route and storage boundaries."""
from dataclasses import replace
import json
import numpy as np
import pytest
from film_foundry.core import photographic_paper as paper, wet_plate as wet, cyanotype as cyan, platinum_palladium as noble
from film_foundry.core.material_family import ViewingConditions
from film_foundry.app import material_families as api
from film_foundry.app.material_route import run_material_route
from film_foundry.app.material_lab_session import MaterialLabSession


def ramp():
    return np.broadcast_to(np.linspace(0,2,32,dtype=np.float32)[None,:,None],(12,32,3)).copy()


def finish(module,state):
    operations=module.default_operations(state.material)
    for operation,parameters in operations:
        if operation not in ('expose','prepare','sensitize'):
            state=api.family(module.FAMILY).process(state,operation,**parameters)
    return state


def silver_total(state):
    return sum(state.fields[key] for key in ('halide','silver','removed_silver'))


@pytest.mark.parametrize('kind',['graded','variable_contrast','direct_positive','color'])
def test_paper_polarity_and_silver_accounting(kind):
    state=paper.expose(ramp(),paper.PaperMaterial(kind=kind,texture=0),group_id='paper')
    original=state.fields['halide'].copy();formed=finish(paper,state)
    np.testing.assert_allclose(silver_total(formed),original,atol=3e-7)
    np.testing.assert_array_equal(state.fields['halide'],original)
    image=paper.observe(paper.products(formed).bodies[0])
    assert (image[0,0].mean()<image[0,-1].mean()) == (kind=='direct_positive')
    assert formed.fields['silver'].max()<.003 if kind=='color' else formed.fields['silver'].max()>.5
    assert not formed.fields['silver'].flags.writeable


def test_variable_contrast_uses_spectral_emulsion_speeds():
    doses=np.geomspace(.001,100,400).astype(np.float32)
    def curve(channel):
        image=np.zeros((1,len(doses),3),np.float32);image[...,channel]=doses
        return paper.latent_response(paper.expose(image,group_id='vc')).mean(-1)[0]
    def latitude(curve):
        return np.log10(doses[np.searchsorted(curve,.8)]/doses[np.searchsorted(curve,.2)])
    assert latitude(curve(1))>latitude(curve(2))*1.5
    assert curve(0).max()<1e-20


def test_paper_developer_capability_and_blix_remove_actual_silver():
    exposed=paper.expose(ramp(),paper.PaperMaterial(kind='color'),group_id='color')
    color=paper.develop(exposed,developer='color');bw=paper.develop(exposed,developer='bw')
    np.testing.assert_array_equal(color.fields['silver'],bw.fields['silver'])
    assert color.fields['dye'].max()>.5 and bw.fields['dye'].max()==0
    cleared=paper.bleach_fix(color)
    np.testing.assert_array_equal(cleared.fields['dye'],color.fields['dye'])
    assert cleared.fields['silver'].max()<color.fields['silver'].max()*.003
    no_bleach=paper.bleach_fix(color,bleach_activity=0)
    np.testing.assert_array_equal(no_bleach.fields['silver'],color.fields['silver'])
    assert no_bleach.fields['halide'].sum()<color.fields['halide'].sum()
    with pytest.raises(ValueError):paper.develop(paper.fix(exposed))


@pytest.mark.parametrize('support',['glass_negative','ambrotype','tintype'])
def test_wet_support_and_drying(support):
    material=wet.WetPlateMaterial(support=support,coating_variation=0,edge_loss=0,texture=0)
    state=wet.expose(ramp(),material,group_id='wet');formed=finish(wet,state)
    np.testing.assert_allclose(silver_total(formed),silver_total(state),atol=2e-7)
    body=wet.products(formed).bodies[0];image=wet.observe(body)
    assert body.usable_negative==(support=='glass_negative')
    assert (image[0,0].mean()>image[0,-1].mean())==(support=='glass_negative')
    delayed=finish(wet,wet.expose(ramp(),replace(material,exposure_delay_s=1800),group_id='dry'))
    assert delayed.fields['silver'].sum()<formed.fields['silver'].sum()*.1
    with pytest.raises(ValueError):wet.varnish(wet.dry(formed,seconds=1))
    varnished=wet.varnish(wet.dry(formed,seconds=1800))
    np.testing.assert_array_equal(varnished.fields['silver'],formed.fields['silver'])
    assert not np.array_equal(wet.preview(varnished),image)


def cyan_iron(state):return sum(state.fields[k] for k in ('ferric','ferrous','iron_removed','reduced_pigment','blue_pigment'))


def test_cyanotype_wash_oxidation_alkali_and_coating():
    initial=cyan.expose(ramp(),group_id='cyan');washed=cyan.wash(initial);formed=cyan.oxidize(washed)
    for state in (washed,formed):np.testing.assert_allclose(cyan_iron(state),initial.fields['coating'],atol=2e-7)
    assert initial.fields['blue_pigment'].max()==washed.fields['blue_pigment'].max()==0
    assert formed.fields['blue_pigment'].max()>.6
    image=cyan.preview(formed);assert image[:,-1,2].mean()>image[:,-1,0].mean()*3
    damaged=cyan.alkali_contact(formed,seconds=600)
    assert damaged.fields['blue_pigment'].sum()<formed.fields['blue_pigment'].sum()*.001
    np.testing.assert_allclose(cyan_iron(damaged),cyan_iron(initial),atol=3e-7)
    unchanged=cyan.wash(initial,water_changes=0)
    np.testing.assert_array_equal(unchanged.fields['ferrous'],initial.fields['ferrous'])
    with pytest.raises(ValueError):cyan.oxidize(initial)
    with pytest.raises(ValueError):cyan.light_leak(formed)


def test_platinum_palladium_separate_iron_metal_and_clearing():
    initial=noble.expose(ramp(),group_id='noble');developed=noble.develop(initial);cleared=noble.clear(developed,seconds=360)
    for state in (initial,developed,cleared,noble.wash(cleared)):
        np.testing.assert_allclose(sum(state.fields[k] for k in ('ferric','ferrous','spent_ferric','iron_removed')),2*initial.fields['coating'],atol=5e-7)
        np.testing.assert_allclose(sum(state.fields[k] for k in ('platinum_salt','palladium_salt','platinum','palladium','metal_removed')),initial.fields['coating'],atol=3e-7)
    for key in ('platinum','palladium'):np.testing.assert_array_equal(developed.fields[key],cleared.fields[key])
    assert cleared.fields['spent_ferric'].sum()<developed.fields['spent_ferric'].sum()*.02
    for ratio in (0.,1.):
        state=finish(noble,noble.expose(ramp(),noble.PlatinumMaterial(platinum_fraction=ratio),group_id='ratio'))
        assert state.fields['platinum' if ratio==0 else 'palladium'].max()==0
    dark=finish(noble,noble.expose(np.zeros((4,4,3),np.float32),group_id='dark'))
    assert dark.fields['platinum'].max()<1e-20 and dark.fields['palladium'].max()<1e-20
    continuous=noble.develop(initial,seconds=30);segmented=noble.develop(noble.develop(initial,seconds=10),seconds=20)
    for key in continuous.fields:np.testing.assert_allclose(continuous.fields[key],segmented.fields[key],atol=2e-7)


@pytest.mark.parametrize('module,material,kind',[(paper,paper.PaperMaterial(),'paper'),(wet,wet.WetPlateMaterial(),'wet'),(cyan,cyan.CyanotypeMaterial(),'cyanotype'),(noble,noble.PlatinumMaterial(),'platinum')])
def test_family_state_resume_storage_observation_and_session(tmp_path,module,material,kind):
    state=module.expose(ramp(),material,group_id=kind,seed=7)
    path=tmp_path/(kind+'.ffmaterial.npz');api.save(path,state);restored=api.load(path)
    assert api.state_family(restored)==module.FAMILY
    expected=finish(module,state);actual=finish(module,restored)
    for key in expected.fields:np.testing.assert_array_equal(expected.fields[key],actual.fields[key])
    group=module.products(actual);api.save(path,group);loaded=api.load(path)
    before={k:v.copy() for k,v in loaded.bodies[0].fields.items()}
    assert api.observe(loaded.bodies[0],ViewingConditions(exposure_ev=1)).shape==ramp().shape
    for k,v in before.items():np.testing.assert_array_equal(loaded.bodies[0].fields[k],v)
    session=MaterialLabSession(kind);session.load(path);assert session.group is not None
    with pytest.raises(ValueError):replace(state,schema_version=99)
    bad=dict(state.fields);bad['coating']=np.ones_like(bad['coating'])*2
    with pytest.raises(ValueError):replace(state,fields=bad)


def test_wet_negative_to_paper_and_paper_to_cyanotype_chain(tmp_path):
    negative=wet.products(finish(wet,wet.expose(ramp(),group_id='negative')))
    steps=[{'operation':op,**params} for op,params in paper.default_operations(paper.PaperMaterial()) if op!='expose']
    printed=run_material_route(negative,receiver=paper.FAMILY,material=paper.PaperMaterial(),operations=steps,stop='image')
    assert printed.value[:,0].mean()<printed.value[:,-1].mean()
    paper_path=tmp_path/'paper.ffmaterial.npz';api.save(paper_path,printed.medium)
    session=MaterialLabSession('cyanotype');session.open_medium(paper_path,relay={'mode':'reflection'},long_edge=0)
    for op,params in cyan.default_operations(cyan.CyanotypeMaterial()):session.process(op,params)
    trace=json.loads(session.state.process_json)['optical_cascade']
    assert len(trace['steps'])==2
    assert trace['steps'][-1]['relay']['mode']=='reflection'
    assert session.group.bodies[0].family==cyan.FAMILY


def test_cli_new_family_can_pause_resume_and_keep_formed_medium(tmp_path):
    from PIL import Image
    from film_foundry.app.cli import main
    image=tmp_path/'source.png';Image.fromarray(np.uint8(np.clip(ramp(),0,1)*255)).save(image)
    state=tmp_path/'cyan.ffmaterial.npz';output=tmp_path/'cyan.png'
    assert main(['route',str(image),str(state),'--start','image','--stop','state','--receiver',cyan.FAMILY])==0
    operations=tmp_path/'steps.json';operations.write_text(json.dumps([{'operation':'wash'},{'operation':'oxidize'}]),encoding='utf-8')
    assert main(['route',str(state),str(output),'--start','state','--stop','image','--operations',str(operations)])==0
    assert api.load(output.with_name('cyan.formed.ffmaterial.npz')).bodies[0].family==cyan.FAMILY
    assert api.load(state).stage=='exposed'

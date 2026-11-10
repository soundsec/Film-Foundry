from copy import deepcopy
from dataclasses import asdict
import numpy as np
import pytest

from film_foundry.model.config import DarkroomConfig
from film_foundry.contracts.formation_profiles import CURRENT_REDUCED_PROFILE, DETAILED_REDUCED_PROFILE, CONTACT_REDUCED_PROFILE, LEGACY_REDUCED_PROFILE
from film_foundry.core.implementation_plan import resolve_implementation_plan


def test_profile_compatibility_and_independent_formation_budgets():
    current=DarkroomConfig();current.fast_mode=True
    assert resolve_implementation_plan(current).profile_id==CURRENT_REDUCED_PROFILE
    assert resolve_implementation_plan(current).basic_formation_only
    assert resolve_implementation_plan(current).bath_contact_long_edge is None
    current.processing.reduced_profile=DETAILED_REDUCED_PROFILE
    for tier,edge in (('draft',256),('standard',512),('high',768)):
        current.processing.quality_mode=tier
        assert resolve_implementation_plan(current).bath_contact_long_edge==edge
        assert resolve_implementation_plan(current).coupled_intervals=={'draft':1,'standard':2,'high':4}[tier]
        assert not resolve_implementation_plan(current).spatial_chemistry_enabled
    saved=asdict(current)
    assert DarkroomConfig.from_dict(saved).processing.reduced_profile==DETAILED_REDUCED_PROFILE
    del saved['processing']['reduced_profile']
    legacy=DarkroomConfig.from_dict(saved)
    assert legacy.processing.reduced_profile==LEGACY_REDUCED_PROFILE
    assert resolve_implementation_plan(legacy).bath_contact_long_edge is None
    current.fast_mode=False;current.processing.execution_mode='scaled_fast'
    assert resolve_implementation_plan(current).bath_contact_long_edge is None
    assert resolve_implementation_plan(current).profile_id=='canonical_full_v1'
    current.processing.reduced_profile='unknown'
    with pytest.raises(ValueError,match='reduced_profile'):resolve_implementation_plan(current)


def test_sampled_contact_preserves_motion_windows_edges_and_tile_identity(monkeypatch):
    from film_foundry.core.contact_sampling import ContactTimeFieldCache
    from film_foundry.core.rotary_contact import contact_duration
    from film_foundry.tests.test_edge_wetting import event
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cpu')
    shape=(311,467);extent=(36.,24.);e=event();duration=120.
    for side in ('bottom','top'):
        ev={**e,'contact_edge':side};cache=ContactTimeFieldCache(shape,extent,96)
        full=cache.sample(0,ev,duration,None,shape,0,shape[0])
        tiled=np.concatenate([cache.sample(0,ev,duration,None,shape,a,min(a+19,shape[0])) for a in range(0,shape[0],19)])
        np.testing.assert_array_equal(full,tiled)
        assert len(cache.fields)==1 and max(cache.grid)==96
        ordered=full if side=='bottom' else full[::-1]
        assert np.min(np.diff(ordered,axis=0))>=-2e-5
        parts=sum(cache.sample(0,ev,duration,(i/7,(i+1)/7),shape,0,shape[0]) for i in range(7))
        np.testing.assert_allclose(parts,full,atol=4e-5,rtol=0)
        exact=contact_duration(shape,0,shape[0],duration,ev,extent=extent)
        assert np.mean(np.abs(exact-full))/duration<.002
        assert full.min()>=0 and full.max()<=duration
    for fraction in (0.,1.):
        ev=event(immersion_fraction=fraction)
        assert np.all(cache.sample(1,ev,duration,(.2,.6),shape,0,shape[0])==np.float32(48.*fraction))


def test_contact_field_is_shared_by_provisional_and_actual_rows(monkeypatch):
    from film_foundry.core.process_operations import BathContactRate,solution_hardening
    from film_foundry.contracts.process_events import validate_process_response
    from film_foundry.tests.test_rotary_instability import config
    from film_foundry.tests.test_edge_wetting import event
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cpu')
    c=config();c.fast_mode=True;c.processing.quality_mode='draft'
    c.processing.reduced_profile=DETAILED_REDUCED_PROFILE
    c.chemistry.process_events={'model':'film_process_events_v15','events':[event()]}
    field=BathContactRate(c,(280,420),prepare_surface=False)
    provisional=field.at_shape((80,120));tile=field.slice_rows(29,71)
    assert provisional._contact_fields is tile._contact_fields is field._contact_fields
    step=field.program.steps[0]
    provisional.contact_for_step(0,step)
    count=len(field._contact_fields.fields)
    actual=field.contact_for_step(0,step)
    np.testing.assert_array_equal(actual[29:71],tile.contact_for_step(0,step))
    assert len(field._contact_fields.fields)==count
    wet=field._contact_fields.sample(0,event(),120.,None,(280,420),0,280)
    response=validate_process_response(None)
    hard=solution_hardening(c,(280,420),response,.4,{'bath':1,'duration_s':120.,'concentration_g_l':2.})
    np.testing.assert_allclose(hard,1.-.6*np.exp(-response['hardening_rate_l_g_s']*2.*wet),atol=1e-7,rtol=0)
    legacy=deepcopy(c);legacy.processing.reduced_profile=LEGACY_REDUCED_PROFILE
    assert BathContactRate(legacy,(280,420),prepare_surface=False)._contact_fields is None


@pytest.mark.parametrize('spectral',[False,True])
def test_scanner_observes_same_medium_independent_of_formation_mode(monkeypatch,spectral):
    from film_foundry.core.engine import develop_negative,scan_negative
    from film_foundry.tests.test_spectral_grain import configuration
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cpu')
    c=configuration() if spectral else DarkroomConfig()
    c.enable_grain=False
    image=np.linspace(.01,.95,17*29*3,dtype=np.float32).reshape(17,29,3)
    medium=develop_negative(image,c,rng=np.random.default_rng(17))
    before=medium.optical_density_rgb.copy();medium.optical_density_rgb.setflags(write=False)
    expected=scan_negative(medium,c).output_srgb
    for profile,tier in ((LEGACY_REDUCED_PROFILE,'draft'),(CONTACT_REDUCED_PROFILE,'standard'),(CURRENT_REDUCED_PROFILE,'standard'),(CURRENT_REDUCED_PROFILE,'high')):
        candidate=deepcopy(c);candidate.fast_mode=True
        candidate.processing.execution_mode='reduced_fast'
        candidate.processing.reduced_profile=profile;candidate.processing.quality_mode=tier
        np.testing.assert_array_equal(scan_negative(medium,candidate).output_srgb,expected)
    np.testing.assert_array_equal(medium.optical_density_rgb,before)


def test_old_preparation_profile_default_is_normalized_without_reforming():
    from film_foundry.core.film_process.preparation import SpatialPreparation,pack_preparation,unpack_preparation
    c=DarkroomConfig();c.fast_mode=True;c.processing.reduced_profile=LEGACY_REDUCED_PROFILE
    p=SpatialPreparation();rng=np.random.default_rng(19)
    p.lookup(c,(17,23),'color_negative',rng)
    p.capture(rng,None,None,{},None,{})
    arrays={};record=pack_preparation(p.snapshot(),arrays)
    del record['binding']['config']['processing']['reduced_profile']
    restored=unpack_preparation(record,arrays)
    assert restored['binding']['config']['processing']['reduced_profile']==LEGACY_REDUCED_PROFILE
    actual=SpatialPreparation(restored)
    assert actual.lookup(c,(17,23),'color_negative',np.random.default_rng(19)) is not None


@pytest.mark.parametrize('legacy',[False,True])
def test_reduced_bath_resume_keeps_inventory_and_old_checkpoint_meaning(tmp_path,monkeypatch,legacy):
    from film_foundry.core.contact_sampling import ContactTimeFieldCache
    from film_foundry.tests.test_film_process_work import source,same_medium
    from film_foundry.tests.test_rotary_instability import config
    from film_foundry.tests.test_edge_wetting import event
    from film_foundry.app.film_process_work import start_work,continue_work,describe_work
    from film_foundry.core.film_process.checkpoint_io import load_checkpoint,save_checkpoint
    from film_foundry.core.negative_io import load_developed_negative_npz
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cpu')
    # Exercise an actually reduced contact grid on the small continuation fixture.
    original=ContactTimeFieldCache.__init__
    def bounded(self,shape,extent,edge):original(self,shape,extent,min(edge,16))
    monkeypatch.setattr(ContactTimeFieldCache,'__init__',bounded)
    c=config();c.fast_mode=True
    c.processing.reduced_profile=LEGACY_REDUCED_PROFILE if legacy else DETAILED_REDUCED_PROFILE
    c.chemistry.process_events={'model':'film_process_events_v15','events':[event()]}
    initial=tmp_path/'initial.ffprocess.npz';start_work(source(tmp_path,c),initial,c)
    if not legacy:
        capabilities=describe_work(initial)['capabilities']
        assert not capabilities['formation']['local_response']
        assert 'grain_moments_omitted' in capabilities['formation']['limits']
        assert capabilities['execution_implementation']['profile_id']==DETAILED_REDUCED_PROFILE
    mid=tmp_path/'mid.ffprocess.npz';continue_work(initial,mid,stop_after=3)
    if legacy:
        meta,latent,state=load_checkpoint(mid)
        del meta['config']['processing']['reduced_profile']
        del meta['spatial_preparation']['binding']['config']['processing']['reduced_profile']
        old=tmp_path/'old.ffprocess.npz';save_checkpoint(old,meta,latent,state);mid=old
    direct=continue_work(initial,tmp_path/'direct.npz');resumed=continue_work(mid,tmp_path/'resumed.npz')
    a=load_developed_negative_npz(direct.output_path);b=load_developed_negative_npz(resumed.output_path)
    same_medium(a,b)
    assert np.mean(a.process_surface['soluble_residue_mg_m2'])>0


def test_fast_coupled_program_preserves_bath_clock_and_drops_secondary_detail(monkeypatch):
    from film_foundry.tests.test_rotary_instability import config
    from film_foundry.core.process_operations import BathContactRate
    from film_foundry.core.engine import develop_negative
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cpu')
    c=config();c.fast_mode=True;c.enable_grain=True
    c.processing.reduced_profile=DETAILED_REDUCED_PROFILE
    c.film.color_process='color';c.chemistry.program_key='color_negative'
    c.chemistry.process_steps=(*c.chemistry.process_steps,{'action':'develop_color','time_min':1.})
    c.chemistry.development_adjacency_strength=.5
    c.film.dir_material_response_matrix=((.1,.1,.1),(.1,.1,.1),(.1,.1,.1))
    before=asdict(c);provider=BathContactRate(c,(23,37))
    assert len(provider.program.steps)==7
    for action in ('develop_silver','fix_halide'):
        assert sum(s.reaction_window[1]-s.reaction_window[0] for s in provider.program.steps if s.action.value==action)==1.
    image=np.linspace(.01,.9,23*37*3,dtype=np.float32).reshape(23,37,3)
    medium=develop_negative(image,c,rng=np.random.default_rng(19))
    model=medium.metadata['film_process_model']
    assert model['execution_implementation']['component_models']['spatial_chemistry']=='omitted_v1'
    assert model['development_adjacency']['skip_reason']=='reduced_profile_omission'
    assert model['dir_material_response']['skip_reason']=='reduced_profile_omission'
    assert model['formation_history']['grain_resolution']=='first_developer_global_fast_v1'
    assert 'local_fields' not in model['formation_history']
    assert medium.optical_density_rgb.shape==image.shape
    assert asdict(c)==before


def test_omitting_grain_moments_keeps_real_inventory_and_chemical_fog():
    from film_foundry.tests.test_rotary_instability import config
    from film_foundry.core.film_process.integration import develop_bw_negative_reduced
    c=config();image=np.linspace(.001,.8,11*17*3,dtype=np.float32).reshape(11,17,3)
    complete=develop_bw_negative_reduced(image,c.film,c.chemistry,coupled_intervals=2)
    reduced=develop_bw_negative_reduced(image,c.film,c.chemistry,coupled_intervals=2,retain_grain_moments=False)
    for name in ('halide','metallic_silver','developability','bleached_halide'):
        np.testing.assert_array_equal(getattr(complete.process_result.state,name),getattr(reduced.process_result.state,name))
    a=complete.process_result.state.formation_history;b=reduced.process_result.state.formation_history
    assert b and all(k.endswith('_fog') for k in b) and len(b)<len(a)
    for key,value in b.items():np.testing.assert_array_equal(value,a[key])
    np.testing.assert_array_equal(complete.optical_density_rgb,reduced.optical_density_rgb)


def test_basic_profile_skips_rotary_and_secondary_preserving_request(monkeypatch):
    from film_foundry.tests.test_rotary_instability import config
    from film_foundry.core.engine import develop_negative
    from film_foundry.core.process_operations import BathContactRate
    from film_foundry.core import rotary_contact
    c=config();c.fast_mode=True;c.chemistry.chemical_stain=.35
    before=asdict(c)
    def forbidden(*args,**kwargs):raise AssertionError('Basic formation must not integrate rotary contact')
    monkeypatch.setattr(rotary_contact,'contact_duration',forbidden)
    provider=BathContactRate(c,(23,37))
    assert not provider.entries and len(provider.program.steps)==1
    image=np.linspace(.01,.8,23*37*3,dtype=np.float32).reshape(23,37,3)
    a=develop_negative(image,c,rng=np.random.default_rng(19))
    changed=deepcopy(c);changed.chemistry.process_events['events'][0]['rotation_seed']=321
    changed.chemistry.process_events['events'][0]['immersion_fraction']=.1
    b=develop_negative(image,changed,rng=np.random.default_rng(19))
    np.testing.assert_array_equal(a.optical_density_rgb,b.optical_density_rgb)
    omissions=a.metadata['film_process_model']['execution_implementation']['omissions']
    assert omissions['omitted_event_kinds']==['rotary_contact'] and omissions['omitted_secondary_baths']==[1]
    assert omissions['chemical_stain']==.35
    assert a.metadata['runtime_config'].chemistry.process_events==c.chemistry.process_events
    assert asdict(c)==before


def test_basic_profile_continues_without_reviving_omitted_operations(tmp_path):
    from film_foundry.tests.test_film_process_work import source,same_medium
    from film_foundry.tests.test_rotary_instability import config
    from film_foundry.app.film_process_work import start_work,continue_work,describe_work
    from film_foundry.core.negative_io import load_developed_negative_npz
    c=config();c.fast_mode=True;c.chemistry.chemical_stain=.2
    c.chemistry.process_steps=(*c.chemistry.process_steps,{'action':'fix_halide','time_min':1.,'temperature_c':43.})
    initial=tmp_path/'basic.ffprocess.npz';start_work(source(tmp_path,c),initial,c)
    info=describe_work(initial)
    assert 'process_events_omitted' in info['capabilities']['formation']['limits']
    assert info['total_steps']==2
    mid=tmp_path/'mid.ffprocess.npz';continue_work(initial,mid,stop_after=1)
    direct=continue_work(initial,tmp_path/'direct.npz');resumed=continue_work(mid,tmp_path/'resumed.npz')
    a=load_developed_negative_npz(direct.output_path);b=load_developed_negative_npz(resumed.output_path)
    same_medium(a,b)
    assert not a.process_surface

"""Shared passive RGB optics for deposited silver, with read-only observation.

This is a reduced extinction/backscatter partition, not measured nanoparticle
optics, a spectral BRDF, or a thin-film interference simulation. The material
owns the coefficients; a viewer changes illumination and directions only.
"""
import math
import numpy as np
from film_foundry.contracts.process_events import RESPONSE_MODEL_V4,validate_process_response
from film_foundry.core.material_family import ViewingConditions

MODEL='deposited_silver_rgb_scattering_v1'


def extinction_coefficients(response):
    """Base-10 optical density per mg/m², also used in the formation master."""
    absorption=float(response['silver_density_per_mg_m2'])
    scattering=np.asarray(response.get('surface_silver_scattering_rgb',(0.,0.,0.)),dtype=np.float32)
    return absorption+scattering


def silver_transfer(mass,response,path_length=1.):
    """Return T and backscatter R with T+R<=1 independently in each channel."""
    extinction=extinction_coefficients(response)
    scattering=np.asarray(response.get('surface_silver_scattering_rgb',(0.,0.,0.)),dtype=np.float32)
    optical=np.asarray(mass,dtype=np.float32)[...,None]*extinction*path_length
    transmission=np.exp(-optical*np.float32(math.log(10.)))
    share=np.divide(scattering,extinction,out=np.zeros(3,np.float32),where=extinction>0)
    reflected=(-np.expm1(-optical*np.float32(math.log(10.))))*share
    return transmission,reflected


def surface_response(medium):
    audit=medium.metadata.get('film_process_model',{}).get('process_surface')
    if not audit or audit.get('observation')!=MODEL:
        raise ValueError('This medium has no recorded reflective silver optics; enable surface silver optics in the material before exposure')
    response=validate_process_response(audit.get('response'))
    if response['model']!=RESPONSE_MODEL_V4:raise ValueError('Reflective silver requires a frozen v4 response')
    if medium.process_surface is None:raise ValueError('Surface fields are not loaded for reflection observation')
    return response


def _vector(value,*,direction=False):
    v=np.asarray(value,dtype=np.float64)
    if v.shape!=(3,) or not np.isfinite(v).all():raise ValueError('Invalid viewing vector')
    if direction:
        length=np.linalg.norm(v)
        if not math.isfinite(length) or length==0:raise ValueError('Invalid viewing direction')
        return v/length
    if np.any(v<0) or np.any(v>1e6):raise ValueError('Invalid viewing radiance')
    return v


def observe_surface(medium,view=ViewingConditions()):
    """Front illumination plus a dim transmitted background; returns linear RGB.

    Angles affect a bounded specular/diffuse lobe, not the stored grain or
    chemistry. No final-image tint and no re-running development are involved.
    """
    response=surface_response(medium)
    if medium.optical_density_rgb is None:raise ValueError('Reflection requires an authoritative optical master')
    light=_vector(view.illuminant_rgb);background=_vector(view.background_rgb)
    l=_vector(view.light_direction,direction=True);v=_vector(view.view_direction,direction=True)
    if v[2]<.05:raise ValueError('Viewing direction must face the emulsion within 87 degrees of normal')
    if not math.isfinite(view.exposure_ev) or not -20<=view.exposure_ev<=20:raise ValueError('Invalid viewing exposure')
    reflected_direction=np.array([-l[0],-l[1],l[2]])
    alignment=float(np.clip(reflected_direction@v,0.,1.))
    rough=response['surface_silver_roughness'];directionality=response['surface_silver_directionality']
    exponent=max(0.,2/(rough*rough)-2)
    angular=max(l[2],0.)*((1-directionality)+directionality*alignment**exponent)
    from film_foundry.core.process_operations import sample_control_rows
    mass=medium.process_surface['surface_silver_mg_m2'];optical=medium.optical_density_rgb
    h,w=optical.shape[:2];result=np.empty_like(optical,dtype=np.float32)
    rows=max(1,500000//w);path_length=1/v[2]
    for start in range(0,h,rows):
        stop=min(h,start+rows)
        sampled=sample_control_rows(mass,(h,w),start,stop)
        _,reflected=silver_transfer(sampled,response,path_length)
        if 'backing_front_mg_m2' in medium.process_surface:
            audit=medium.metadata['film_process_model']['process_surface']
            front=sample_control_rows(medium.process_surface['backing_front_mg_m2'],(h,w),start,stop)
            # v3 treats redeposited carbon as an outer absorbing deposit.
            # Light traverses it both before and after silver reflection.
            reflected*=np.power(10.,-front[...,None]*audit['carbon_backing']['density_per_mg_m2']*(path_length+1/max(l[2],.05)))
        transmitted=np.power(10.,-np.maximum(optical[start:stop],0.)*path_length)
        # Old structure-density proxies can be locally negative. The assembled
        # observer still cannot reflect more light than the stack extinguishes.
        np.minimum(reflected,1-transmitted,out=reflected)
        result[start:stop]=(reflected*(light*angular)+transmitted*background)*(2**view.exposure_ev)
    return result

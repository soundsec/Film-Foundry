"""Read-only material diagnostics for workspaces; never an export authority."""
import numpy as np
from film_foundry.core.preview import resize_to_long_edge


def fields_for(session):
    if session.state is not None:return session.state.fields
    if session.group is not None:return session.group.bodies[0].fields
    return {}


def metrics(session):
    fields=fields_for(session)
    if not fields:return ()
    sampled={key:value[::max(1,value.shape[0]//96),::max(1,value.shape[1]//96)] for key,value in fields.items()}
    def mean(key):return float(sampled[key].mean()) if key in sampled else 0.
    cover=max(mean('coating'),1e-8)
    if session.kind=='wet':
        silver=(mean('silver')+mean('external_deposit')+mean('enhancement_deposit'))/cover
        return (('moisture',mean('moisture')),('silver',silver),('halide',mean('halide')/cover)) if 'moisture' in fields else (('silver',silver),('halide',mean('halide')/cover))
    if session.kind=='cyanotype':return (('blue',mean('blue_pigment')/cover),('soluble_iron',(mean('ferric')+mean('ferrous'))/cover),('unoxidized',mean('reduced_pigment')/cover))
    if session.kind=='platinum':return (('metal',(mean('platinum')+mean('palladium'))/cover),('residual_iron',(mean('ferric')+mean('ferrous')+mean('spent_ferric'))/(2*cover)),('unreduced_metal',(mean('platinum_salt')+mean('palladium_salt'))/cover))
    return (('silver',mean('silver')/cover),('halide',mean('halide')/cover),('dye',mean('dye')/cover))


INSPECTIONS={'wet':('coating','moisture','collodion_access','flow','external_silver','external_deposit','enhancement_silver','enhancement_deposit'),'cyanotype':('coating','blue_pigment','ferric','binding'),
             'platinum':('fibers','platinum','palladium','spent_ferric','clear_access'),'paper':('coating','silver','dye')}


def options(session):return tuple(key for key in INSPECTIONS[session.kind] if key in fields_for(session)) + (('paper_moisture',) if 'paper_moisture' in fields_for(session) else ())


def preview(session,key):
    fields=fields_for(session)
    if key not in options(session):return None
    value=resize_to_long_edge(fields[key],1600)
    if value.ndim==3:value=value.mean(-1)
    if key=='spent_ferric':value=value*.5
    return np.repeat(np.clip(value,0,1)[...,None],3,axis=2)

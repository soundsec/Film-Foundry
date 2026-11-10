"""Compile film-family event positions into the existing material-pool program.

This is the film's own sequence, not a universal executor for other families.
Bath indices continue to refer to original bath rows, before expansion.
"""
from dataclasses import replace
from film_foundry.core.film_process.operators import FilmProcessAction, FilmProcessStep


def inject_events(program, events):
    if not program.steps:
        raise ValueError('Ordered process events require material-pool processing')
    last=max(s.bath_index if s.bath_index is not None else i for i,s in enumerate(program.steps))
    for event in events:
        if max(event.get('before_bath',0),event.get('after_bath',0))>last+1:
            raise ValueError('Operation refers to a nonexistent bath')

    def operations(rows, source):
        for identity,event in rows:
            kind=event['kind']
            common=dict(bath_index=source,bath_entry=False,process_event_index=identity)
            if kind=='emulsion_wear':
                yield FilmProcessStep(FilmProcessAction.REMOVE_EMULSION,label=f'emulsion_wear_{identity+1}',**common)
            elif kind=='emulsion_shear':
                yield FilmProcessStep(FilmProcessAction.SHEAR_EMULSION,label=f'emulsion_shear_{identity+1}',**common)
            elif kind=='fixer_contact':
                yield FilmProcessStep(FilmProcessAction.FIX_HALIDE,label=f'fixer_contact_{identity+1}',**common)
            elif kind=='dye_bath' and event['concentration_mmol_l']>0 and event['duration_s']>0:
                yield FilmProcessStep(FilmProcessAction.DESTROY_DYE,label=f'chemical_dye_attack_{identity+1}',
                    dye_destruction_basis='chemical',chemical_contact=(event['concentration_mmol_l'],event['duration_s'],event['temperature_c']),
                    chemical_placement=(event['center_x'],event['center_y'],event['radius_mm']) if event['radius_mm']>0 else None,**common)
    result=[];previous=None
    for i,step in enumerate(program.steps):
        source=step.bath_index if step.bath_index is not None else i
        if source!=previous:
            if previous is not None:
                result.extend(operations([(j,e) for j,e in enumerate(events) if e.get('after_bath',0)==previous+1],previous))
            result.extend(operations([(j,e) for j,e in enumerate(events) if e.get('before_bath')==source+1],source))
        # Stable, nonempty names also identify the matching provisional DIR /
        # adjacency step when custom bath rows omit a user-facing label.
        result.append(replace(step,bath_index=source,label=step.label or f'bath_{source+1}:{step.action.value}'));previous=source
    result.extend(operations([(j,e) for j,e in enumerate(events) if e.get('after_bath',0)==last+1],last))
    result.extend(operations([(j,e) for j,e in enumerate(events) if not e.get('before_bath',0) and not e.get('after_bath',0)],last))
    return replace(program,steps=tuple(result))

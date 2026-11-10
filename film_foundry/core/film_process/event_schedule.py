"""Film-family before/after anchors shared by carried and surface inventories."""


def event_schedule(program,all_events,timeline):
    steps=program.steps;regular={};injected={}
    identities={id(event):i for i,event in enumerate(all_events)}
    for i,step in enumerate(steps):
        if step.process_event_index is not None:
            injected[step.process_event_index]=i
        elif step.chemical_contact is not None and step.label.startswith('chemical_dye_attack_'):
            injected[int(step.label.rsplit('_',1)[1])-1]=i
        elif step.time_history is None:
            regular.setdefault(i if step.bath_index is None else step.bath_index,[]).append(i)
    last_regular=max((i for indices in regular.values() for i in indices),default=-1)
    entries={b:min(indices) for b,indices in regular.items()}
    for identity,i in injected.items():
        before=all_events[identity].get('before_bath',0)
        if before:entries[before-1]=min(entries.get(before-1,i),i)
    floor=-1;schedule=[]
    for event in timeline:
        identity=identities.get(id(event));anchor=injected.get(identity)
        if anchor is not None:position=2*anchor
        elif event['kind']=='surface_reaction':
            position=2*next(i for i,s in enumerate(steps) if s==event['step'])+1
        elif event['kind'] in ('bath_temperature','solution_hardening'):
            position=2*entries[event['bath']-1]
        elif event.get('before_bath',0):position=2*entries[event['before_bath']-1]
        elif event.get('after_bath',0):position=2*max(regular[event['after_bath']-1])+1
        else:position=2*last_regular+1
        position=max(floor,position);schedule.append(position)
        floor=position if anchor is None else max(position,2*anchor+1)
    return schedule

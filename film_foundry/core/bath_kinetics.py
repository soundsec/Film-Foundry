"""One explicit reaction-rate resolver for bath programs and local contact.

Legacy recipes deliberately do not enter this model. Material selectivity and
pool bounds remain in the existing operators; this resolves solution access.
"""
import math
from film_foundry.contracts.bath_solution import validate_solution

def dye_attack_fraction(response,concentration,duration,temperature):
    """Generic reduced dye erosion; this is not a named chemical formulation."""
    return -math.expm1(-response*concentration*duration*2**((temperature-20.)/20.))

def solution_rate_per_second(solution,recipe):
    solution=validate_solution(solution,solution['action'])
    duration=float(recipe.time_min)*60
    agitated=duration if recipe.agitation_time_s is None else min(duration,max(0.,float(recipe.agitation_time_s)))
    duty=agitated/duration if duration else 0.
    transfer=.2+.8*min(max(float(recipe.agitation),0.),1.5)*duty
    return (solution['rate_per_minute']/60*solution['activity_remaining']*float(recipe.concentration)
        *solution['q10']**((float(recipe.temperature_c)-solution['reference_temperature_c'])/10)*transfer)

def solution_completion(solution,recipe):
    return -math.expm1(-solution_rate_per_second(solution,recipe)*float(recipe.time_min)*60)


COUPLED_INTERVALS=8

def coupled_reaction_plan(solution,recipe,*,intervals=COUPLED_INTERVALS):
    """Bounded symmetric operator splitting on a single shared bath clock.

    Each entry is (action, start fraction, end fraction, completion). This is
    an explicit reduced competition model, not a sequence of full baths.
    """
    if isinstance(intervals,bool) or not isinstance(intervals,int) or not 1<=intervals<=COUPLED_INTERVALS:
        raise ValueError('Coupled reaction intervals must be an integer from 1 to 8')
    from film_foundry.contracts.bath_solution import reaction_capability
    actions=(solution['action'],solution['secondary']['action'])
    rates={action:solution_rate_per_second(reaction_capability(solution,action),recipe) for action in actions}
    live=[action for action in actions if rates[action]>0]
    duration=float(recipe.time_min)*60
    if len(live)<2:
        return [(action,0.,1.,-math.expm1(-rates[action]*duration)) for action in live]
    result=[]
    for i in range(intervals):
        start=i/intervals;end=(i+1)/intervals;mid=(start+end)/2
        for action,a,b in ((actions[0],start,mid),(actions[1],start,end),(actions[0],mid,end)):
            result.append((action,a,b,-math.expm1(-rates[action]*duration*(b-a))))
    return result

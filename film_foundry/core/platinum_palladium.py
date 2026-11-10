"""Platinum/palladium family entry; independent noble-metal pools."""
from film_foundry.core.iron_print import (NOBLE_FAMILY as FAMILY,PlatinumMaterial,PlatinumState,
    PlatinumFamily,products,preview,observe,validate_body,noble_develop as develop,clear,noble_wash as wash,light_leak,handling,dry)
expose=PlatinumFamily.expose
OPERATION_FUNCTIONS={'develop':develop,'clear':clear,'wash':wash,'light_leak':light_leak,'handling':handling,'dry':dry}
FINAL_STAGES=('washed',)
def available_operations(state):
    if state is not None and state.schema_version==3 and state.stage in ('cleared','washed'):return ('clear','wash','dry','handling')
    return {None:('expose',),'exposed':('develop','clear','light_leak','handling'),'developed':('develop','clear','handling'),
        'cleared':('clear','wash','handling'),'washed':('clear','wash','handling')}[state.stage if state else None]
def default_operations(material):return [('expose',{}),('develop',{}),('clear',{}),('wash',{})]

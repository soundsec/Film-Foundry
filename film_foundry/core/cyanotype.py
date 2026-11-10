"""Cyanotype family entry; iron chemistry is implemented in iron_print."""
from film_foundry.core.iron_print import (CYAN_FAMILY as FAMILY,CyanotypeMaterial,CyanotypeState,
    CyanotypeFamily,products,preview,observe,validate_body,cyan_wash as wash,oxidize,alkali_contact,light_leak,handling,dry)
expose=CyanotypeFamily.expose
OPERATION_FUNCTIONS={'wash':wash,'oxidize':oxidize,'alkali_contact':alkali_contact,'light_leak':light_leak,'handling':handling,'dry':dry}
FINAL_STAGES=('oxidized',)
def available_operations(state):
    if state is not None and state.schema_version==3 and state.stage in ('washed','oxidized'):return ('wash','oxidize','dry','alkali_contact','handling')
    return {None:('expose',),'exposed':('wash','light_leak','handling'),'washed':('wash','oxidize','alkali_contact','handling'),
        'oxidized':('wash','oxidize','alkali_contact','handling')}[state.stage if state else None]
def default_operations(material):return [('expose',{}),('wash',{}),('oxidize',{})]

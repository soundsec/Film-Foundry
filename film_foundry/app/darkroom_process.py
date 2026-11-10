"""Application aliases for the shared control policy."""
from film_foundry.core.process_controls import condition_driven_config,RAW_CONDITIONS,RAW_CHEMISTRY,BATH_ACTIONS


def editable_bath_rows(config, program):
    """Retain authored stage identity when presenting a native sequence as a draft.

    Custom sequences recalculate kinetics from their per-bath conditions. This
    is a starting template, not a serialization of every native operator.
    """
    from copy import deepcopy
    from film_foundry.core.process_operations import source_bath_rows
    from film_foundry.core.film_process.recipe import _base_program_from_develop_recipe
    rows = deepcopy(source_bath_rows(config, program))
    if config.chemistry.process_steps is not None:
        return rows
    native = _base_program_from_develop_recipe(config.chemistry, mode=config.mode,
                                              material_process=config.film.medium_process)
    if len(rows) != len(native.steps) or any(row['action'] != step.action.value for row, step in zip(rows, native.steps)):
        raise ValueError('Native bath sequence cannot be mapped to an editable template')
    for row, step in zip(rows, native.steps):
        row.setdefault('label', step.label)
        if step.action.value == 'destroy_dye':
            row['dye_destruction_basis'] = step.dye_destruction_basis
            if step.dye_destruction_basis == 'explicit':
                row['strength'] = step.strength
    return rows

"""Fresh-work material choices over existing definitions, not new recipes.

Only existing categorical material switches are offered here. Numerical defaults
remain owned by the respective material family; film stocks use the preset bank.
"""
from dataclasses import asdict, replace


VARIANTS = {
    'peel': (('dye', {'transfer': 'dye', 'donor_mode': 'residue'}),
             ('silver', {'transfer': 'silver_compound', 'donor_mode': 'silver_negative'})),
    'integral': (('dye', {'transfer': 'dye'}), ('silver', {'transfer': 'silver_compound'})),
    'plate': (('default', {}),),
    'paper': tuple((key, {'kind': key}) for key in ('variable_contrast', 'graded', 'direct_positive', 'color')),
    'wet': tuple((key, {'support': key}) for key in ('glass_negative', 'ambrotype', 'tintype')),
    'cyanotype': (('default', {}),),
    'platinum': (('default', {}),),
}


def material_choices(family, *, include_variants=False):
    if family == 'film':
        from film_foundry.app import workflow
        return [dict(record, family=family, origin='custom' if workflow.film_preset_path(record['key']).resolve().is_relative_to(
            workflow.USER_FILM_PRESET_DIR.resolve()) else 'builtin')
                for record in workflow.film_preset_catalog(include_variants=include_variants)]
    from film_foundry.app.material_lab_session import FAMILIES
    definition = FAMILIES[family][1]()
    return [{'family': family, 'key': key, 'material': asdict(replace(definition, **values))}
            for key, values in VARIANTS[family]]


def validate_selection(selection):
    """Resolve before replacing a workspace; never partially apply a bad choice."""
    family = selection['family']
    if family == 'film':
        from film_foundry.app.workflow import film_preset_path
        from film_foundry.model.config import DarkroomConfig
        key = selection['key']
        if not key:raise ValueError('Choose a film material')
        DarkroomConfig.from_json(film_preset_path(key))
        return {'family': family, 'key': key}
    from film_foundry.app.material_lab_session import FAMILIES
    definition = FAMILIES[family][1](**selection['material'])
    return {'family': family, 'material': asdict(definition), 'key': selection.get('key', 'custom')}

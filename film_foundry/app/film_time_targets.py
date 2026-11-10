"""Draft-only time target API shared by editors and headless callers."""
from film_foundry.core.media import is_monochrome_material
from film_foundry.core.film_process.time_intent import describe_time_target, resolve_time_segment


def describe_targets(config, kind, *, conditions=None):
    report = describe_time_target(config.film.time_response, kind, conditions=conditions,
                                  layers=1 if is_monochrome_material(config) else 3)
    if config.chemistry.program_key == 'legacy_density':
        report.update(available=False, reason='material_pool_program_required')
    return report


def resolve_target(config, kind, target, *, conditions=None):
    if config.chemistry.program_key == 'legacy_density':
        raise ValueError('Film time targets require a material-pool program')
    return resolve_time_segment(config.film.time_response, kind, target, conditions=conditions,
                                layers=1 if is_monochrome_material(config) else 3)

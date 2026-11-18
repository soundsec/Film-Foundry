"""Reference step sequences shared by diagnostics and material frontends.

Durations are reference starting points, not guarantees of completion. All
chemistry, operation validation and defaults remain with each material family.
"""


def reference_operations(kind, module, material):
    if kind == 'plate':
        return [('prepare', {}), ('polish', {}), ('sensitize', {}), ('expose', {}),
                ('develop', {'seconds': material.reaction_seconds * 4}),
                ('fix', {'seconds': material.fixing_seconds * 12})]
    if kind in ('peel', 'integral'):
        steps = [('expose', {})]
        if kind == 'integral':steps.append(('eject', {}))
        steps.extend([('spread', {}), ('advance', {'seconds': material.stabilization_seconds * 2
                      if kind == 'integral' else material.reaction_seconds * 3})])
        steps.append(('stabilize' if kind == 'integral' else 'peel', {}))
        return steps
    return module.default_operations(material)

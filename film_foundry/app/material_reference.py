"""Reference step sequences shared by diagnostics and material frontends.

Durations are reference starting points, not guarantees of completion. All
chemistry, operation validation and defaults remain with each material family.
"""

import math
from dataclasses import replace

from film_foundry.core.material_family import ViewingConditions
from film_foundry.core.silver_plate import FAMILY as SILVER_PLATE

PLATE_VIEW_ANGLE = 25.


def reference_view(family, **overrides):
    view = ViewingConditions()
    if family in ('plate', SILVER_PLATE):
        angle = math.radians(PLATE_VIEW_ANGLE)
        view = replace(view, view_direction=(math.sin(angle), 0., math.cos(angle)))
    return replace(view, **overrides) if overrides else view


def example_exposure(image, kind, material):
    """Use a fixed reference negative for the catalog photograph of a printing paper."""
    paper_kind = material.get('kind', 'variable_contrast')
    if kind not in ('cyanotype','platinum','paper') or kind == 'paper' and paper_kind == 'direct_positive':
        return image
    import numpy as np
    from film_foundry.core.color import luminance
    from film_foundry.core.optical_relay import relay_dose, transmission_radiance
    from film_foundry.contracts.optical_cascade import OpticalRelay

    colour = kind == 'paper' and paper_kind == 'color'
    exposure = image if colour else luminance(image)[...,None]
    # Fixed sensitometric target, independent of the photograph's histogram.
    span, toe = (2.50, 24.) if kind == 'platinum' else (1.85, 24.)
    if colour:
        # A lower negative gamma keeps flesh and midtone chroma printable.
        span, toe = 1.55, 300.
    density = .08 + span * np.log1p(toe * exposure) / math.log1p(toe)
    if not colour:density = np.broadcast_to(density, image.shape)
    relay = OpticalRelay(exposure_seconds=2., filter_density_rgb=(0.,.025,0.) if colour else (0.,0.,0.))
    return relay_dose(transmission_radiance(density, relay.illuminant_rgb), relay)


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

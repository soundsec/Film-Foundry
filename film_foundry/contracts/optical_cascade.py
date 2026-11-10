"""Portable optical-relay conditions; relative RGB dose, not calibrated lux·s."""
from dataclasses import dataclass, asdict
from copy import deepcopy
import json
import math
from film_foundry.contracts.data_kernel import OPTICAL_CASCADE_KEY as CASCADE_KEY, OPTICAL_CASCADE_MODEL as CASCADE_MODEL


@dataclass(frozen=True)
class OpticalRelay:
    model: str = 'relative_rgb_optical_relay_v1'
    mode: str = 'transmission'
    exposure_seconds: float = 1.
    exposure_ev: float = 0.
    illuminant_rgb: tuple[float, float, float] = (1., 1., 1.)
    filter_density_rgb: tuple[float, float, float] = (0., 0., 0.)
    light_angle: float = 30.
    view_angle: float = 0.
    background_rgb: tuple[float, float, float] = (0., 0., 0.)
    output_shape: tuple[int, int] | None = None
    flip_horizontal: bool = False

    def __post_init__(self):
        if self.model != 'relative_rgb_optical_relay_v1' or self.mode not in ('transmission', 'reflection'):
            raise ValueError('Unsupported optical relay model or mode')
        for key, low, high in (('exposure_seconds', 0., 86400.), ('exposure_ev', -20., 20.),
                               ('light_angle', -80., 80.), ('view_angle', -80., 80.)):
            value = getattr(self, key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f'Invalid relay {key}')
        for key, high in (('illuminant_rgb', 100.), ('filter_density_rgb', 10.), ('background_rgb', 100.)):
            value = getattr(self, key)
            if not isinstance(value, (tuple, list)) or len(value) != 3 or any(
                    isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= high for v in value):
                raise ValueError(f'Invalid relay {key}')
            object.__setattr__(self, key, tuple(float(v) for v in value))
        if self.output_shape is not None:
            shape = self.output_shape
            if not isinstance(shape, (tuple, list)) or len(shape) != 2 or any(type(v) is not int or v <= 0 for v in shape):
                raise ValueError('Relay output_shape requires positive integer height and width')
            if shape[0] * shape[1] > 100_000_000:
                raise ValueError('Relay output exceeds 100 million pixels')
            object.__setattr__(self, 'output_shape', tuple(shape))
        if type(self.flip_horizontal) is not bool:
            raise ValueError('Relay flip_horizontal must be boolean')

    def as_dict(self):
        return asdict(self)

    @classmethod
    def from_value(cls, value):
        if isinstance(value, cls):
            return value
        if value is None:
            return cls()
        if not isinstance(value, dict):
            raise ValueError('Optical relay must be an object')
        try:
            return cls(**value)
        except TypeError as exc:
            raise ValueError('Unknown optical relay fields') from exc


def validate_cascade(value):
    """Keep a bounded, append-only chain of provenance; no pixel hashes."""
    if not isinstance(value, dict) or set(value) != {'model', 'steps'} or value['model'] != CASCADE_MODEL:
        raise ValueError('Invalid optical cascade provenance')
    steps = value['steps']
    if not isinstance(steps, list) or not 1 <= len(steps) <= 16:
        raise ValueError('Optical cascade supports 1 to 16 stages')
    ids = set()
    for step in steps:
        if not isinstance(step, dict) or not isinstance(step.get('id'), str) or not step['id'] or step['id'] in ids:
            raise ValueError('Cascade stages require distinct identities')
        ids.add(step['id'])
        if not isinstance(step.get('source'), dict) or not isinstance(step.get('target'), dict):
            raise ValueError('Cascade source and target are required')
        OpticalRelay.from_value(step.get('relay'))
    json.dumps(value, allow_nan=False)
    return deepcopy(value)

"""Frozen-program reaction boundaries; no observer, UI or scheduling ownership.

Spatial preparation belongs to the enclosing pipeline. New boundaries carry
its reusable derived fields separately from actual surface and inhibitor state;
legacy boundaries keep their original rebuilding scope. Never infer pools from
final density.
"""
from dataclasses import asdict, dataclass
import json
from typing import Callable

from film_foundry.core.film_process.model import FilmProcessState

MODEL = 'film_reaction_boundary_v1'


def binding_for(material, program, compatibility):
    # Compare the small complete execution description, including kinetics and
    # time histories. Display/audit program payloads intentionally omit fields.
    return json.loads(json.dumps({'model': MODEL, 'material': asdict(material),
        'program': asdict(program), 'compatibility': asdict(compatibility)}, allow_nan=False))


@dataclass(frozen=True)
class FilmProcessBoundary:
    state: FilmProcessState
    next_step: int
    trace: tuple
    binding: dict
    surface: dict | None = None
    inhibitor: dict | None = None
    preparation: dict | None = None


class FilmProcessPaused(Exception):
    def __init__(self, boundary, reason):
        super().__init__(reason)
        self.boundary = boundary
        self.reason = reason


@dataclass(frozen=True)
class FilmProcessRun:
    resume: FilmProcessBoundary | None = None
    stop_after: int | None = None
    pause_requested: Callable[[int, int], bool] | None = None
    include_surface: bool = False
    include_inhibitor: bool = False
    preparation: object | None = None
    track_wet_history: bool = False

    def bind(self, material, program, compatibility, shape):
        if self.stop_after is not None and (type(self.stop_after) is not int or not 0 <= self.stop_after <= len(program.steps)):
            raise ValueError('Reaction boundary must be between 0 and the total step count')
        binding = binding_for(material, program, compatibility)
        if self.resume is not None:
            b = self.resume
            if b.binding != binding:
                raise ValueError('The frozen material or reaction program differs from its checkpoint')
            if type(b.next_step) is not int or not 0 <= b.next_step <= len(program.steps) or b.state.halide.shape != shape:
                raise ValueError('Invalid reaction checkpoint cursor or dimensions')
            if self.stop_after is not None and self.stop_after < b.next_step:
                raise ValueError('A reaction checkpoint cannot run backwards')
        return binding

    def at_boundary(self, state, index, reports, binding, *, validated=False, surface=None, inhibitor=None):
        paused = bool(self.pause_requested and self.pause_requested(index, len(binding['program']['steps'])))
        if paused or self.stop_after == index:
            if not validated:
                state.validate()
            raise FilmProcessPaused(FilmProcessBoundary(state, index, tuple(reports), binding,
                                    surface.snapshot() if surface is not None else None,
                                    inhibitor.snapshot() if inhibitor is not None else None,
                                    self.preparation.snapshot() if self.preparation is not None else None),
                                    'cancelled' if paused else 'requested_boundary')

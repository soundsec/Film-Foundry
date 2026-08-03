"""Developer-only selection between equivalent execution topologies.

The reference topology is not a quality mode and must never change model
parameters, process programs, random streams, image dimensions, or numerical
precision.  It keeps allocation-heavy expressions available for same-version
A/B audits when an ownership or scratch-reuse optimization needs to be
isolated or rolled back.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator


OPTIMIZED_TOPOLOGY = "optimized"
REFERENCE_TOPOLOGY = "reference"
EXECUTION_TOPOLOGIES = (OPTIMIZED_TOPOLOGY, REFERENCE_TOPOLOGY)
REFERENCE_TOPOLOGY_VERSION = 5

_EXECUTION_TOPOLOGY: ContextVar[str] = ContextVar(
    "film_foundry_execution_topology",
    default=OPTIMIZED_TOPOLOGY,
)


def current_execution_topology() -> str:
    """Return the task-local developer execution topology."""
    return str(_EXECUTION_TOPOLOGY.get())


def reference_execution_enabled() -> bool:
    """Return true only inside an explicit developer reference context."""
    return current_execution_topology() == REFERENCE_TOPOLOGY


@contextmanager
def execution_topology(topology: str) -> Iterator[None]:
    """Temporarily select an equivalent developer execution topology.

    ``ContextVar`` keeps nested calls, worker tasks, and exception unwinding
    isolated.  The token is always reset, so a reference audit cannot leak
    into a later user operation on the same process.
    """
    normalized = str(topology).strip().lower()
    if normalized not in EXECUTION_TOPOLOGIES:
        raise ValueError(
            f"Unknown execution topology {topology!r}; expected one of "
            f"{EXECUTION_TOPOLOGIES}"
        )
    token = _EXECUTION_TOPOLOGY.set(normalized)
    try:
        yield
    finally:
        _EXECUTION_TOPOLOGY.reset(token)

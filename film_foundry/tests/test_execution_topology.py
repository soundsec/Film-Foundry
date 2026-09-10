import pytest

from film_foundry.core.execution_topology import (
    OPTIMIZED_TOPOLOGY,
    REFERENCE_TOPOLOGY,
    current_execution_topology,
    execution_topology,
    reference_execution_enabled,
)


def test_reference_execution_context_is_nested_and_exception_safe():
    assert current_execution_topology() == OPTIMIZED_TOPOLOGY
    assert reference_execution_enabled() is False

    with execution_topology(REFERENCE_TOPOLOGY):
        assert current_execution_topology() == REFERENCE_TOPOLOGY
        assert reference_execution_enabled() is True
        with execution_topology(OPTIMIZED_TOPOLOGY):
            assert current_execution_topology() == OPTIMIZED_TOPOLOGY
            assert reference_execution_enabled() is False
        assert current_execution_topology() == REFERENCE_TOPOLOGY

    assert current_execution_topology() == OPTIMIZED_TOPOLOGY
    with pytest.raises(RuntimeError, match="audit failure"):
        with execution_topology(REFERENCE_TOPOLOGY):
            raise RuntimeError("audit failure")
    assert current_execution_topology() == OPTIMIZED_TOPOLOGY


def test_unknown_execution_topology_is_rejected_without_state_change():
    with pytest.raises(ValueError, match="Unknown execution topology"):
        with execution_topology("quick"):
            pass
    assert current_execution_topology() == OPTIMIZED_TOPOLOGY

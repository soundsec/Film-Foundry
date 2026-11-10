import numpy as np
import pytest
from film_foundry.core.material_latent_grid import MaterialLatentSource
from film_foundry.core.execution_topology import execution_topology
from film_foundry.model.config import DarkroomConfig


@pytest.mark.parametrize('mono',[False,True])
def test_cached_exposure_rows_preserve_pool_area_integrals_and_reduce_work(monkeypatch,mono):
    c=DarkroomConfig()
    if mono:
        from pathlib import Path
        c=DarkroomConfig.from_json(Path(__file__).resolve().parents[1]/'presets/film/monochrome_fine_negative.json')
    image=np.random.default_rng(37).uniform(0.,1.5,(83,127,3)).astype(np.float32)
    counts=[];results=[]
    monkeypatch.setattr(MaterialLatentSource,'ROW_PIXELS',127*13)
    for topology in ('reference','optimized'):
        source=MaterialLatentSource(c.film,image_linear=image,exposure_duration_seconds=.1)
        calls=[];rows=source._rows
        def tracked(a,b):calls.append((a,b));return rows(a,b)
        source._rows=tracked
        with execution_topology(topology):state,grid=source.working_state(47)
        counts.append(len(calls));results.append(state)
        assert source.working_state(47)[0] is state
    assert counts[1]<counts[0]/2
    for name in MaterialLatentSource.POOLS:
        a,b=(getattr(s,name) for s in results)
        if a is None:assert b is None
        else:np.testing.assert_array_equal(a,b)

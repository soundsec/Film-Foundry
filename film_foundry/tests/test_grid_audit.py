import json

import pytest

from film_foundry.tools.audit_grids import main, run_grid_audit


def test_grid_audit_checks_multiple_boundaries_without_mutating_source():
    report = run_grid_audit(
        width=43,
        height=31,
        tile_rows=(1, 5, 13),
        seed=87,
    )

    assert report["source_image_unchanged"] is True
    assert report["runtime"]["tile_rows"] == [1, 5, 13]
    for program in report["contracts"]["material_programs"].values():
        for tiled in program["tiles"].values():
            assert all(
                comparison["exact_equal"]
                for comparison in tiled["comparisons"].values()
            )
    for polarity in report["contracts"]["scan_tiling"].values():
        for mode in polarity.values():
            for tiled in mode["tiles"].values():
                assert tiled["comparison"]["max_abs_error"] <= 1e-7


def test_grid_audit_rejects_invalid_dimensions_and_tile_rows():
    with pytest.raises(ValueError, match="greater than three"):
        run_grid_audit(width=3, height=20)
    with pytest.raises(ValueError, match="positive"):
        run_grid_audit(width=20, height=20, tile_rows=())
    with pytest.raises(ValueError, match="unique"):
        run_grid_audit(width=20, height=20, tile_rows=(3, 3))


def test_grid_audit_cli_writes_atomic_json(tmp_path, capsys):
    destination = tmp_path / "grid-audit.json"
    assert main(
        [
            "--width",
            "29",
            "--height",
            "23",
            "--tile-rows",
            "4",
            "--tile-rows",
            "9",
            "--json-output",
            str(destination),
        ]
    ) == 0
    printed = json.loads(capsys.readouterr().out)
    saved = json.loads(destination.read_text(encoding="utf-8"))
    assert printed == saved
    assert saved["source_image_unchanged"] is True

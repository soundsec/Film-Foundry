"""Thin module wrapper for `python -m half_frame_darkroom.app.main`."""

from half_frame_darkroom.app.cli import main


if __name__ == "__main__":
    raise SystemExit(main())


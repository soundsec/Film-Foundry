"""Thin module wrapper for `python -m film_foundry.app.main`."""

from film_foundry.app.cli import main


if __name__ == "__main__":
    raise SystemExit(main())


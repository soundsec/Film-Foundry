"""Dedicated positive-transparency scanner preset editor entry point."""

from film_foundry.tools.run_scanner_render_editor import PositiveScannerEditor


def main() -> None:
    PositiveScannerEditor().run()


if __name__ == "__main__":
    main()

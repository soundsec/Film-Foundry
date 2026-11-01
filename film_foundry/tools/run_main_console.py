"""Canonical module name for the main desktop console."""

from film_foundry.tools.run_darkroom_gui import *  # noqa: F401,F403
from film_foundry.tools.run_darkroom_gui import DarkroomPanel, MainConsole

if __name__ == "__main__":
    from film_foundry.tools.run_desktop_gui import main
    main()

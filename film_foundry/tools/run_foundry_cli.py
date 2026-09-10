"""Film Foundry command-line launcher.

Windows/Anaconda friendly usage:

    ~\python.exe -m film_foundry.app.cli full input_images outputs --film-preset clear_modern_negative --develop-preset standard_color_negative --scanner-preset neutral_scan
    ~\python.exe -m film_foundry.app.cli develop input_images outputs\negatives --film-preset clear_modern_negative --develop-preset monobath_clean --layer-pack
    ~\python.exe -m film_foundry.app.cli scan outputs\negatives outputs\rescans
"""

from pathlib import Path
import sys

SOURCE_ROOT = Path(__file__).resolve().parents[2]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from film_foundry.app.cli import main


if __name__ == "__main__":
    raise SystemExit(main())

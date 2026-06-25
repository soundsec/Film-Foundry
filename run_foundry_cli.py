"""Film Foundry command-line launcher.

Windows/Anaconda friendly usage:

    ~\python.exe run_foundry_cli.py full input_images outputs --film-preset clear_modern_negative --develop-preset standard_color_negative --scanner-preset neutral_scan
    ~\python.exe run_foundry_cli.py develop input_images outputs\negatives --film-preset clear_modern_negative --develop-preset monobath_clean --layer-pack
    ~\python.exe run_foundry_cli.py scan outputs\negatives outputs\rescans
"""

from half_frame_darkroom.app.cli import main


if __name__ == "__main__":
    raise SystemExit(main())

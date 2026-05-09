"""Film Foundry command-line launcher.

Windows/Anaconda friendly usage:

    ~\python.exe run_foundry_cli.py full input_images outputs
    ~\python.exe run_foundry_cli.py develop input_images outputs\negatives --layer-pack
    ~\python.exe run_foundry_cli.py scan outputs\negatives outputs\rescans
"""

from half_frame_darkroom.app.cli import main


if __name__ == "__main__":
    raise SystemExit(main())

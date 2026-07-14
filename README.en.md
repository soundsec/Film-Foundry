# Film Foundry / Electronic Negative Factory

Film Foundry is a physics-inspired virtual darkroom and electronic film-medium generator. It first forms a developed negative or reversal-positive medium from a chosen material and process, then creates a digital image through a separate transmission-scanning workflow.

The project is currently an early alpha. It is not a strict photochemical simulator and does not aim to clone a particular commercial film stock or scanner. Its focus is interpretable material behavior, darkroom controls, and creative flexibility.

[中文说明](README.zh-CN.md)

[v0.3.0 alpha release notes](https://github.com/soundsec/Film-Foundry/blob/main/docs/RELEASE_NOTES_0.3.md)

## Features

- Color negative, black-and-white negative, color reversal, and black-and-white reversal workflows.
- Bleach bypass, cross processing, push/pull, retained silver, accidental silver plating, light leaks, chemistry exhaustion, uneven development, and material ageing controls.
- Separate film-material, development-process, and scanner presets.
- Independent Develop and Scan stages: a saved medium can be rescanned with different interpretations without changing the developed medium.
- Negative base removal and inversion, positive light-table viewing, and optional channel compensation.
- Density-domain grain, frame-size behavior, halation, emulsion sharpness, and scan color controls.
- Transparent-medium, physical-transmission TIFF, scanner/light-table raw, plate-layer, and Layer Pack exports.

## Installation

Python 3.10 or newer is recommended. The supplied developing environment uses Python 3.11.

With pip:

```bash
pip install -r requirements.txt
```

With conda:

```bash
conda env create -f environment.yml
conda activate film-foundry
```

## Starting the GUI

The launcher is the recommended entry point:

```bash
python run_film_foundry_launcher.py
```

The main workflow GUI can also be opened directly:

```bash
python -m film_foundry.tools.run_darkroom_gui
```

The main GUI provides three workflows:

- **Full**: input image → developed negative or reversal positive → scanned output.
- **Develop**: create a reusable negative or positive medium.
- **Scan**: load an existing medium and scan it as a negative or positive.

Material selection first distinguishes negative and positive stocks, then lists matching presets with clear color or black-and-white labels. Diagnostic, accident, and experimental presets are hidden from the normal list but remain available through editors and the CLI.

## Processing Modes and Large Images

The CLI and runtime configuration provide three modes:

- `quality`: uses the requested final dimensions and is intended for final output.
- `scaled_fast`: processes a smaller preview-sized image for fast visual tuning.
- `reduced_fast`: preserves requested output dimensions while reducing selected internal detail calculations.

In the main GUI, **Fast mode** alone selects `reduced_fast`. **Use preview long edge** selects `scaled_fast` and has higher priority. When both are enabled, the effective mode is `scaled_fast`. Both are currently enabled by default and the preview long edge defaults to 1600 px, so pressing Process with the initial settings resizes first. Disable **Use preview long edge** to keep the requested dimensions with reduced internal detail; disable **Fast mode** as well to use `quality`.

The current comfortable working range is up to about 30 megapixels. Larger images remain available on a best-effort basis; actual success depends on resolution, enabled exports, and available memory. Film Foundry does not resize an image automatically unless a scaled mode or long-edge limit is selected.

## Command Line

The CLI is intended for batch processing, automation, and replaying GUI sessions.

Show help:

```bash
python -m half_frame_darkroom.app.cli --help
```

Full workflow:

```bash
python -m half_frame_darkroom.app.cli full input_images outputs --film-preset clear_modern_negative --develop-preset standard_color_negative --scanner-preset neutral_scan
```

Develop reusable media:

```bash
python -m half_frame_darkroom.app.cli develop input_images outputs/negatives --film-preset clear_modern_negative --develop-preset standard_color_negative
```

Rescan existing media:

```bash
python -m half_frame_darkroom.app.cli scan outputs/negatives outputs/rescans --scanner-preset rich_color_scan
```

Use scaled-fast processing:

```bash
python -m half_frame_darkroom.app.cli full input_images outputs --processing-mode scaled_fast --preview-long-edge 1600
```

Replay a GUI session:

```bash
python -m half_frame_darkroom.app.cli full input_images outputs --session user_presets/film_foundry_session.json
```

## Presets and Editors

Built-in presets are separated by purpose:

```text
half_frame_darkroom/presets/film/      film materials
half_frame_darkroom/presets/develop/   chemistry and process programs
half_frame_darkroom/presets/scanner/   scanning and viewing interpretations
```

User presets are stored under `user_presets/` and take priority over built-in presets with the same name.

Standalone editors:

- `python -m film_foundry.tools.run_film_material_editor` — silver-halide material editor.
- `python -m film_foundry.tools.run_develop_process_editor` — process and chemistry editor.
- `python -m film_foundry.tools.run_scanner_render_editor` — negative/positive transmission scanner editor.

See the [Preset Guide](https://github.com/soundsec/Film-Foundry/blob/main/docs/PRESET_GUIDE.md) for details.

## Output Files

The Full workflow normally creates a final PNG, JPEG, or TIFF and an optional sidecar.

Develop writes media according to the final polarity:

```text
outputs/negatives/*.darkroom_negative.npz
outputs/positives/*.darkroom_positive.npz
```

Depending on export settings, Film Foundry can also create:

- A negative visual preview or positive viewing preview.
- A 16-bit scanner-raw or light-table-raw TIFF.
- Transparent PNG, transparent 16-bit TIFF, and physical-transmission TIFF.
- CMY/density, grain, and halation plate helpers.
- A Layer Pack containing the medium, previews, available raw, transparency, plate layers, and manifest.

Layer Pack is the complete archive option and already contains transparency and plate products; it is not a third duplicate layer system.

## Windows Portable Build

When the Releases page provides `FilmFoundry-portable-win64.zip`, extract and keep the complete FilmFoundry folder. Do not copy only the executable.

Current portable builds are unsigned alpha packages and may trigger a Windows SmartScreen “unknown publisher” warning. If you do not want to bypass the warning, inspect the source and run the project from Python instead.

## Current Limitations

- JPG, JPEG, PNG, TIFF, BMP, and WebP are supported; RAW files are not read directly.
- Typical source images have already passed through a camera ISP and tone mapping, so exposure and linearization controls are visual approximations.
- High resolution, multiple auxiliary exports, and Layer Packs can require substantial memory, processing time, and disk space.
- Built-in presets are named by material or visual behavior and are not official simulations of manufacturer products.
- On some Windows systems, moving the dense Tk GUI window may feel less responsive than a native application window; this does not affect processing or saved results.

## License

Film Foundry is licensed under **GNU General Public License version 3 (GPL-3.0-or-later)**.

You may use, study, modify, and distribute this project under GPLv3. Distribution of modified versions or derivative works must follow the applicable GPLv3 obligations.

For closed-source commercial products, proprietary redistribution, or commercial SDK/plugin use that cannot comply with GPLv3, contact the project author.

## Code Provenance Notice

AI agents have assisted with code organization, refactoring, documentation, and debugging during development. If you believe that specific code, comments, documentation, naming, or structure infringes your rights, contact the author with the relevant file, location, and reason so it can be reviewed.

## Notice

Film Foundry is not affiliated with, endorsed by, sponsored by, or officially authorized by any film manufacturer, scanner manufacturer, camera manufacturer, or commercial film-emulation software vendor.

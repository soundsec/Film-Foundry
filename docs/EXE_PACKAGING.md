# Early Windows EXE Build

This project uses a simple PyInstaller onedir build for early non-programmer testing.
It is meant to produce a portable folder, not a formal installer.

## Recommended Build Environment

- Windows
- Python 3.11 or 3.12
- Project dependencies installed
- PyInstaller installed in the same environment

Example:

```powershell
conda activate film-foundry
python -m pip install -r requirements.txt
python -m pip install pyinstaller
```

## Build

From the project root:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_windows_exe.ps1
```

If you need to point at a specific Python executable:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_windows_exe.ps1 -Python D:\Anaconda3\envs\film-foundry\python.exe
```

The output folder is:

```text
dist\FilmFoundry
```

Send testers the whole `dist\FilmFoundry` folder. Do not send only `FilmFoundry.exe`, because the onedir build also includes runtime libraries and bundled preset data.

Do not run or distribute anything from the `build` folder. It is PyInstaller's temporary work area, and the executable there is not the portable release.

## Portable Folder Layout

```text
FilmFoundry/
  FilmFoundry.exe
  README.txt
  input_images/
  user_presets/
  outputs/
    negatives/
  _internal/
```

Testers should put input images in `input_images` and collect rendered results from `outputs`.
Double-clicking `FilmFoundry.exe` opens the launcher. From there testers can open
the main console, the film material editor, the develop process editor, or the
scanner/render editor.

## Notes

- The GUI loads presets from bundled app resources.
- Custom presets are saved beside the exe under `user_presets`.
- User-visible input and output folders are created beside the exe.
- First launch can be slow.
- The folder can be zipped for distribution.

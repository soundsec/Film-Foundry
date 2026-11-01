# Windows native dependency inventory

Generated from the actual PyInstaller binary table after restricting PATH to the selected Python environment and Windows. Relative packaged paths only; no hashes or private build paths.

`native-files.json` records 144 binaries and 9 matching Conda package records. `conda/` retains original notices from those exact cached package builds. Wheel-level notices remain in `../runtime/dependencies/`. The Qt system `icuuc.dll` is deliberately not bundled.

Package metadata can cover more tools than the DLLs actually included; retain the original notices rather than inferring that every package executable is shipped. Source availability and rebuild instructions must accompany binary publication where required; this inventory is not a source archive.

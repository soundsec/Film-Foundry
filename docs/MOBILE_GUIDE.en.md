# Film Foundry Mobile

[中文](MOBILE_GUIDE.md) · [Illustrated overview](MOBILE_SHOWCASE.en.md) · [Project home](../README.en.md)

[Installation](#android-test-version) · [Camera](#camera-controls) · [Development](#baths-and-recipes) · [Scanning](#viewing-and-scanning) · [Library](#rolls-and-library) · [Export](#saving-and-export)

> **Version note**: This guide reflects the current Android development source. Several UI and touch refinements (such as the resizable scan drawer, four color themes, and swipeable process categories) are newer than the published 0.1.0 test APK. See the [development snapshot](DEVELOPMENT_SNAPSHOT.en.md) for details.

## Android test version

The current **Android 0.1.0 test version** requires **Android 8.0 or later**. There is no iOS version. APK filenames end in `arm64-v8a`, `armeabi-v7a`, `x86_64`, `x86`, or `universal` (containing all four ABIs); each package installs independently. Device testing currently focuses on **ARM64 phones**. [Source and build instructions](../android/README.md)

- **Permissions & backups**: Camera permission is used for capture and live transmission viewing. Background processing uses a foreground service and notifications; older Android versions may request storage permission for system-gallery export. App-private rolls and film are excluded from Android auto-backup, so export important resources before uninstalling or switching devices.
- **Supported capture formats**:
  - **JPEG**: Standard capture path, linearized via the inverse sRGB transfer function.
  - **RAW (DNG)**: Supports uncompressed 8/16-bit Bayer DNG and interleaved three-channel LinearRaw DNG (Bayer is demosaiced by active-area phase; LinearRaw reads three channels directly), calibrated into linear working RGB.
  - **Ultra HDR**: Requires Android 14+, a compatible sRGB base image, and a real gain map; availability depends on the device. Compressed DNG and wide-gamut P3 are not fully supported, and 16-bit scanned outputs remain SDR images.

## From roll to photograph

1. **Load**: Choose stock, format (135 / half-frame / 120), and capacity on Camera, then load the roll (naming, pairings, capture precision, and rewind options live under *More loading settings*).
2. **Shoot**: Use the landscape viewfinder to tap-to-focus/meter, pinch-to-zoom, and adjust EV. You can leave an unfinished roll at any time and resume later.
3. **Seal**: Seal the roll from the camera menu to move it to Rolls for development.
4. **Develop**: Open the roll in Rolls, pick a process preset, or arrange custom baths in **Free processing**.
5. **View and scan**: Open a developed frame on the light table or in the scanner, refine style, light, color, and curves, then save to Library.

## Camera controls

| Control | Use |
| --- | --- |
| Manual advance | The shutter locks after each shot; tap **Advance** to move to the next frame |
| Multiple exposure | Tap **Multi** to re-cock the shutter on the current frame |
| Auto advance | Enable in camera settings to advance automatically after saving (a quick toggle stays on the capture screen) |
| EV and zoom | Hold briefly on the control, then slide vertically on the side rail or horizontally on the bottom rail; double-tap to reset |
| 135 panoramic gate | Adds unexposed top/bottom bands on 135 frames; toggle before advancing, with each exposure retaining its own state |
| Lenses and flash | Switch among lenses, zoom levels, and flash modes supported by your device |
| Capture format | Tap JPEG / RAW / HDR on the bottom viewfinder row to set the format for the next exposure once the camera is ready |

### Home layout & roll loading

- The Camera home screen separates **Loaded roll**, **Camera & capture settings**, and **Next-roll defaults**. The loaded roll displays its frozen stock, format, capacity, and progress. **ISO & shutter** controls subsequent sensor exposures without altering the film material. Next-roll defaults (grain, ageing, MTF softness, halation, sprockets, and factory edge lettering) freeze into a roll only when loaded.
- The roll-loading dialog summarizes grain, ageing, and film-edge settings; **Adjust grain / ageing / film edges…** updates that summary without losing your chosen stock, format, or length.
- Tap **?** in the landscape finder at any time to reopen the interactive control guide highlighting each available button in place.

### Camera effects & text imprinting

- **Camera effects**: Organized under **Light leaks / Gate dirt / Imprint** tabs with a fixed sample preview and active-effects summary. Combine up to 8 independent light-leak components (each with shape, direction, and strength; setting strength to zero preserves placement). Dust, debris, hair, insects, and imprinting can be combined freely. Saving applies only to future exposures.
- **Text imprinting**: Accepts up to **24 uppercase/lowercase English letters, digits, spaces, and ASCII punctuation** (full-width ASCII is normalized; CJK and emoji are unsupported; blank disables imprinting). Date-only strings use classic numeric glyphs; adding letters or other punctuation switches the string to a 5×7 dot-matrix font. Brightness spans 0–200% (100% baseline), with four corner presets plus horizontal/vertical sliders.
- **Grain, MTF, and halation**: Choose strength before loading (*Natural* keeps stock defaults; *Grain Off* disables both dye and silver grain). New rolls generate fine layer texture plus coarser partially correlated dye clouds, still modulated by bath conditions.

## Baths and recipes

### Preparation & recipe selection

- Development opens on **Prepare**, summarizing loaded stock, resolved process, expected film polarity, and step order before you tap **Start** (unfinished tasks are automatically detected for resume).
- Under **Choose a process**, scroll the category strip or swipe horizontally across the recipe list to switch among Recommended, Processes, Bath styles, and My recipes; tap a row to apply.
- Top tabs jump directly to **Bath conditions**, **Processing incidents**, **Additional base bleaching**, and **Scan & save**. Prepare, Free processing, and individual bath editors all show a live **synthetic process chart** reflecting your draft settings.
- Color stocks processed in B&W baths are tagged as silver images with their native base retained. When pairing a dye-free B&W stock with a full color process, the app warns that silver bleaching can clear the image and offers a one-tap switch to color-negative with retained silver.

### Free processing & processing incidents

- **Free processing**: Starts with the base bath sequence and provides top tabs for **Shared chemistry / Agitation / Reactions / Adjacency & DIR**. Select any bath to view inherited conditions or override them locally (*Shared bath duration* applies to baths without a local duration, not the total process time). **Done** inside a bath updates the workbench draft; **Apply to this development** commits the draft back to Prepare, with a confirmation prompt if you leave with pending edits.
- **Gyroscope agitation**: Record physical phone agitation with the gyroscope or enter manual values. Each development creates an independent batch so one roll can be processed multiple times.
- **Incidents & base bleaching**: Managed separately. Uneven development, chemical stains, and reticulation each support **Inherit / Off / Set**, with 24-step in-session undo/redo (continuous slider drags count as one step). *Clean process* disables only those three incidents while keeping grain and material settings.
- **Recipes & FFC2**: Save material, bath, and scan definitions independently under Settings, and share or import **FFC2 recipe codes**. Loaded rolls and existing tasks keep their frozen recipes.

## Viewing and scanning

| Mode | View |
| --- | --- |
| Light table | Preserves the film's actual polarity; tap **Light** for brightness and color temperature |
| Scene transmission | Overlays transparent film onto the live camera feed with zoom, drag, and reset |
| Positive projection | Interprets the film directly as a positive image |
| Scan | Adjusts style, base correction/inversion, light, color, and curves before saving to Library |

> **Read-only guarantee**: Viewing and scanning only observe the developed film and never alter it. Development completion and batch screens provide direct shortcuts to scan a single frame or the entire batch.

### Scan drawer, tools, and touch controls

- **Preview-first drawer**: Scanning opens with a large preview. Tap or swipe up the **Scan controls** bar to expand tools, and drag its handle to any preferred height (at least 1/5 of the workspace always stays open for the photo).
- **Tool order**: **Film (base correction / inversion) → Light (exposure / temperature) → Tone (gain / contrast) → Normalize → Color → Curves → Output → More**. Swipe horizontally across the tool body to switch tabs, or vertically to scroll.
- **Base correction & inversion**: Initial switches match the developed film's actual polarity and colored base (both on for negatives, both off for neutral positives, base correction only for strongly masked positives). Override manually at any time or tap **Restore automatic matching**; switching styles on the same frame keeps your switches. Under **Film**, you can also pin the frame's **Process sample** chart across tools.
- **Precision touch**: Tap or drag sliders to jump directly; hold until **Fine** appears to drag in fine increments (wide-range controls step by one minimal unit). Tap numeric buttons for exact entry; double-tap Gain or a ruler to restore its inherited value, or double-tap an HSL ruler to zero that channel.
- **Light EV & normalization**: The current phone scanner calibrates base and tonal endpoints at neutral light exposure before applying Light EV (with inversion on, lower EV brightens; with inversion off, higher EV brightens), while Tone Gain always brightens in the same direction. Default negative scans apply a `sigmoid` shoulder before normalization so lowering normalization strength does not mix unbounded readings back in.
- **Color HSL & curves**: In **Color**, swipe the hue strip to edit 8-band HSL directly under the preview. In **Curves**, tap the luma/R/G/B dots to switch channels, double-tap to add points, drag to adjust, or tap coordinates for numeric input. Editing style parameters appends `· Custom` to the style label (hold to inspect modified controls).

### Sessions, undo history, and histogram

- **Transmission reuse & comparison**: Lighting edits reuse the prepared unilluminated transmission of the same frame, and returning from the background preserves completed previews and save receipts. Hold the preview image to compare against the initial preview and histogram from this visit; release to return.
- **Top bar & sessions**: The top toolbar provides Back, rotation, corner histogram toggle, Undo/Redo, and Save (up to 8 parameter-only sessions × 24 undo steps in memory). From a saved photo's menu, **Continue editing this photo** restores its frozen parameters and original scanner implementation, whereas **Scan from source film** opens a fresh film session.
- **Saving & gallery export**: Tapping **Save** writes to the app Library and keeps the editor open so you can view, keep tweaking, or jump to the next frame in the batch (identical parameter sets prompt reuse without overwriting earlier versions). Batch scanning and simultaneous system-gallery export live under **Output**.

### Compute & GPU assistance

- Enable or disable **GPU assistance (automatic selection)** under **Settings → Processing → Compute & acceleration** (enabled by default for new setups; takes effect on the next job). Eligible linearization, transmission, spectral integration, lightweight continuous-bath development, and combined scan output steps run on OpenGL ES 3.1 when qualification and transfer-inclusive timing pass, falling back to CPU on failure.
- Latent and optical archives use fast lossless compression. **Recent execution records** summarize cumulative stage timings and actual backends (the hardware self-test measures synthetic blocks only and is not a whole-photo timing guarantee).

## Rolls and Library

The four bottom tabs are **Camera / Rolls / Library / Settings**:

| Resource layer | Management | Reuse |
| --- | --- | --- |
| Exposure rolls (originals & latents) | Whole-roll | Develop repeatedly with different recipes |
| Developed film | Individual or batch | View, rescan, or export optical NPZ |
| Scanned photos | Individual or batch | Browse, rate/favorite, export, and trace source |

- **Independent layers**: Deleting one layer never deletes the other two (deleting a roll removes captures/latents while keeping film and photos; deleting film removes rescanning while keeping the roll and photos).
- **Views, filtering, and display cache**: Cycle Library layouts among 3-column grid, 5-column grid, and detailed list; sort by creation time, file size, or roll name; filter by roll, favorites, and rating comparisons; choose paged or continuous scrolling (pan past a zoomed photo's horizontal edge to advance). Zooming into developed film loads high-resolution optical rows asynchronously within memory limits, and rendered light-table/projection views use a separate disk cache clearable under **Processing → Display cache**.
- **Reset buttons (↺)**: Tap **↺** beside any parameter to restore its preset/inherited value; group resets affect only that group; *Preset curves* restores style curves while channel **↺** resets to linear; light-table lamp resets to `0 EV / 5500 K`.

## Saving and export

### Formats & precision boundaries

- **JPEG**: 8-bit lossy (Low / Medium / High = 40 / 70 / 100); exports to system gallery or folder (unrotated JPEGs copy directly).
- **PNG**: 8-bit or 16-bit lossless; supports system gallery and folder export.
- **TIFF**: 16-bit lossless; folder export (SAF) only.
- Re-exporting an existing lower-precision image cannot recover higher precision; rescan the source film instead.

| Resource | Image export | Reusable material export |
| --- | --- | --- |
| Scanned photo | Compatible JPEG, PNG, or TIFF | Follow Source back to the film and rescan |
| Developed film | Light-table view (preserving polarity) or positive projection (JPEG / PNG / TIFF) | Optical NPZ for continued desktop viewing & scanning |
| Exposure roll | Retained camera JPEG / DNG originals to a folder | Whole-roll latent NPZ files for desktop development |

Mobile exposes into effective color or silver records. On desktop, develop an exported roll using those saved records. To try a different emulsion stack, export the original JPEG or DNG and start a new desktop exposure. [Desktop layer controls](DESKTOP_GUIDE.en.md#coatings)

### Metadata, provenance, and appearance

- **Information & frozen recipes**: Tiles and viewer show dimensions, polarity/stock type, and timestamps. Open **••• → Information** to inspect frozen recipes and records (missing fields show *not recorded* or *File time* rather than guessing).
- **Source provenance**: Tap **Source** in the viewer to inspect and jump to the source roll, roll frame position (`Frame 01`), development batch, and scan record using stable short IDs that never renumber when other items are deleted (full IDs appear under **Information → Resource IDs**).
- **Batch export**: Long-press items in Library or Rolls and tap **Export selected**. Negative exports always render from full optical data rather than small previews.
- Under **Settings → Appearance & language**, choose System, Chinese, English, or **Import language pack…** to install a community JSON file. The same page offers four themes (*Black & amber*, *Ivory*, *Slate*, *Silver*). Tap **?** for the current page’s offline guide. [Creating and installing language packs](../android/TRANSLATING.md)

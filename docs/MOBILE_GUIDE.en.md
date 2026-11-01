# Film Foundry Mobile

[中文](MOBILE_GUIDE.md) · [Illustrated overview](MOBILE_SHOWCASE.en.md) · [Project home](../README.en.md)

[Installation](#android-test-version) · [Camera](#camera-controls) · [Development](#baths-and-recipes) · [Scanning](#viewing-and-scanning) · [Library](#rolls-and-library) · [Export](#saving-and-export)

## Android test version

The current **Android 0.1.0 test version** requires Android 8.0 or later. APK filenames end in `arm64-v8a`, `armeabi-v7a`, `x86_64`, `x86` or `universal`; each installs independently, and the latter contains all four architectures. Device testing currently focuses on **ARM64 phones**; other architectures have not been device-tested. There is no iOS version. [Source and build instructions](../android/README.md)

Camera permission is used for capture and live transmission. Background processing uses a foreground service and notifications; older Android versions may require storage permission for system-gallery export. Private rolls and film are separate from system-gallery files. System auto-backup is disabled, so export resources you need before uninstalling.

JPEG is the standard capture format. Minimum RAW support primarily covers uncompressed 8/16-bit Bayer DNG; Ultra HDR requires Android 14+, a compatible sRGB base and a real gain map. Available formats depend on the device. Compressed DNG and wide-gamut P3 are not fully supported; a 16-bit scan is still not HDR output.

## From roll to photograph

1. **Load**: choose stock, 135/half-frame/120 format, capacity and a roll name on Camera. Set camera effects and load the roll. The first level shows stock, format and capacity; More loading settings holds naming, pairings, capture precision and rewind, with a collapsed summary.
2. **Shoot**: use the landscape finder, tap to focus and meter, pinch to zoom and adjust EV. Leave an unfinished roll and resume later.
3. **Seal**: seal from the camera menu, then open the roll in Rolls.
4. **Develop**: choose a process or open Free processing to arrange baths.
5. **View and scan**: open a developed frame, view it on the light table or scan it. Choose a style, brightness, color and curves, then tap Scan and save.

## Camera controls

| Control | Use |
| --- | --- |
| Manual advance | The shutter locks after capture; Advance moves to the next frame |
| Multiple exposure | Multi re-cocks the current frame for another exposure |
| Auto advance | Enable in camera settings to advance after saving; a capture-page shortcut remains available |
| EV and zoom | Hold briefly, then slide vertically on the side rail or horizontally on the bottom rail; double-tap to reset |
| Panoramic gate | Adds unexposed bands above and below a 135 frame; switch before advancing, with each exposure retaining its own state |
| Lenses and flash | Choose the lenses, zoom and flash options offered by the device |

Choose body leaks and gate dirt in camera settings before loading. Film settings contain ageing, sprockets and factory edge lettering. Text imprinting can be adjusted while shooting. Sprockets and edge lettering sit outside the image; 120 uses a border without holes.

Text imprinting accepts up to **24 uppercase/lowercase English letters, digits, spaces and ASCII punctuation**. Full-width ASCII is converted automatically; CJK and emoji are unsupported. Blank text disables printing. Date-only text keeps the original numeric glyphs; letters or other punctuation select a 5×7 dot font for the whole string. Brightness ranges from 0–200%: 100% retains the original exposure strength and 0% adds no exposure. Choose a corner, then adjust the horizontal and vertical sliders for custom placement. During landscape capture, the sample stays on the left; Content and Placement panels switch on the right. Saving affects future exposures only.

Choose grain, MTF softness and halation before loading. Natural retains the stock's own strength; other levels reduce or increase it. Grain Off disables both dye and silver grain. These settings apply to new rolls; loaded rolls and existing developed film keep their original settings. New rolls use fine layer texture and coarser dye clouds, with partly shared patterns across the color layers. Baths still affect grain strength, scale and clumping.

## Baths and recipes

Automatic development uses a selected preset. **Free processing** lets you arrange baths and adjust temperature, duration, concentration, agitation time and strength. Try reversal, cross processing, silver retention and bath accidents.

Processing opens in Prepare: choose a recipe, review the loaded material, resolved process, expected film and execution order, then start. Recipe categories are Recommended, Processes, Bath styles and My recipes. Free processing remains a primary entry. Bath conditions, Processing incidents, Additional base bleaching and Scan & save open as child pages from preparation. Back returns to preparation first. The header shows whether this run saves film alone or also creates Library photos; one Start button stays at the bottom. Resume unfinished work through More, or let Start identify an existing task first.

Preparation summarizes bath overrides and enabled incidents. Completion shows retained film/photo counts and entries to view, scan or export the batch.

Free processing displays the current process's base bath sequence immediately. Select a bath horizontally to inspect or edit it. Browsing, canceling or accepting an unchanged bath keeps the original process; a real bath edit creates a custom sequence. Enable Custom bath sequence to add, remove or reorder baths, or replace the draft with a template. Base baths and additional processing settings together form the full development.

The workbench starts with the sequence. Open shared chemistry and conditions, shared agitation, shared reactions, or Adjacency & DIR from that page; Back returns to the sequence first. Select a bath, then open its chemistry, conditions, agitation or reactions. Each bath inherits shared conditions or overrides them. Shared bath duration applies to baths without a local duration; it is not the total process time. Drag a slider or tap its value for precise input. Done in a bath updates the workbench draft; Apply to this development returns it to preparation. Cancel discards the outer draft. Standalone recipe editors retain Apply draft. Automatic program order and expected results come from the actual process resolver.

Phone agitation recording uses the gyroscope; manual values are also available. Each development creates a separate batch, so one roll can be processed with several recipes.

Scan & save controls whether development also scans photos into the app Library. Turn it off to retain developed film for scanning later. Invalid scan settings skip automatic scanning for that run; development still produces retained negatives. Correct the settings and scan from those negatives later.

Base bleaching shows the active process setting; manually turning it off explicitly disables it. Use process base bleaching restores the preset. Scan defaults follow the final film: negatives use base correction and inversion; neutral clear positives use neither; positives with a strong colored base use base correction without inversion. The system sets both switches from the final film. Override either manually, or tap Restore automatic matching. Selecting another frame starts with automatic matching; switching or resetting a style on the same frame keeps both switches. Old interpretation flags stored in a style are not applied as style parameters. Scan base correction does not bleach or alter the film.

Color stocks processed in black-and-white baths are tagged as silver images; their base and any retained halide remain. Cross-process polarity follows the ordered bath response.

Stock names distinguish color/B&W and negative/reversal. Process names describe the baths; Standard matches the loaded film automatically. The development page shows stock, process and expected result. With the current B&W stocks that lack dye-forming layers, a complete color process can bleach away the silver image. A notice offers a shortcut to color-negative processing with retained silver. Experimental choices remain available.

Save material, bath and scan configurations separately in Settings. Copy or import **FFC2 recipe share codes**. Loaded rolls and existing tasks retain their saved configurations.

Camera effects use Light leaks / Gate dirt / Imprint tabs with a fixed sample preview and a summary of configured effects. Combine up to eight leak components, each with its own shape, direction and strength. Overall strength controls the group; setting a component to zero keeps its position while disabling it. Dust, debris, hair, insects and imprint can coexist. Switching tools keeps the draft; Save applies to future exposures, while Cancel discards this edit.

Processing incidents and Additional base bleaching are separate branches; resetting one keeps the other. Uneven development, chemical stains and reticulation each offer Inherit, Off or Set, and can be combined. Inherit all keeps effects arising from the recipe and bath conditions; Clean process disables these three incidents while retaining grain and other material settings. Incident adjustments have up to 24 in-session undo/redo steps, with a continuous slider gesture treated as one step. History does not modify completed development or base bleaching.

## Viewing and scanning

| Mode | View |
| --- | --- |
| Light table | Preserves film polarity; Light controls brightness and temperature |
| Scene transmission | Overlays transparent film on the live camera, with zoom, drag and reset |
| Positive projection | Interprets the film as a positive image |
| Scan | Selects a finish and adjusts brightness, color and tone/RGB curves before saving |

Use Adjust scan settings in a development batch to scan one frame or the batch. A saved photo can also lead back to its source negative for rescanning.

Lighting adjustments reuse the same film transmission while preserving device response, clipping and color processing. The first film read still takes time. Slow updates show a waiting indicator; fast ones do not flash a spinner.

Returning from the background keeps a completed preview and its save settings. Pending previews resume if automatic refresh is enabled; otherwise, tap Refresh before saving.

Save and batch scan on the scan page use the settings from the current successful preview. After editing or refreshing, wait for the new preview before saving. Hold ◧ at the top right of the preview to compare with the first successful preview from this visit; release to return to the current effect. Comparison changes no settings and does not recompute the scan.

The scan page keeps the preview above the current tool, with a fixed Style selector underneath. Choose a preset first, then refine its parameters. The toolbar order is Film (base correction/inversion), Light (exposure/temperature), Tone (gain/contrast), Normalize, Color, Curves, Output and More. Style is the parameter starting point, not a later processing stage. Selecting or resetting a style keeps independent base correction, inversion and output settings. The new phone scanner calibrates base and tonal ranges at neutral light exposure, then applies Light EV. With inversion on, lower EV brightens the scan; with inversion off, higher EV brightens it. Temperature remains part of transmission capture, while Gain always brightens in the same direction. Saved-photo editing and frozen tasks retain their recorded implementation; new scans opened from film use the new one. Drag sliders or tap a value for precise input and preset reset. Normalize offers Strength, Black and White controls for matching tonal endpoints; its effect can be small when the image already fits the target range. Scanning hides the brand header and global navigation. The compact top bar holds Back, the frame name, Undo/Redo and Save. Tap the expand icon on the preview to expand the photo. Output contains Refresh, Scan batch and the system-gallery option; batch execution still confirms first. More contains saved configurations and device definitions. The Recipes page groups material, bath and scanner presets; storage and cache controls are under Processing.

Continue editing this photo restores the photo's frozen settings and recorded scanner implementation. Scan from source film opens a separate film session. Missing sources or unreadable settings are reported instead of substituting the current draft. Up to eight parameter-only sessions are retained in memory, each with 24 undo steps. After restarting, start again from a saved photo's settings.

A slider/curve gesture or group reset is one undo step. The style panel allows repeated auditions, with the main preview and thumbnails of this film. Done accepts the audition; Cancel restores the opening state and preserves redo. Style and stock selectors include favorites and recent choices. Stock thumbnails are synthetic standard-process examples; the detailed pairing preview remains available. Thumbnails never become film or saved scan input.

Save writes a single photo to the app Library and keeps the editor open. View the result, keep editing, or continue to the next frame when available in this batch. Identical existing versions are identified and reused; earlier versions remain. Enable export to the system gallery under Output; writing starts after any required permission is granted.

The histogram button at the top right of the preview toggles a corner overlay. Color contains Saturation; Tone contains independent Contrast without rewriting curves. New default negative scans use a sigmoid shoulder before normalization so lower normalization does not mix unbounded readings back in. Saved photo settings and custom recipes are not rewritten. The curve graph also shows the selected channel distribution. Statistics describe the entire displayed sRGB preview after adjustments, rather than RAW, curve input or full-resolution film data. Pending edits mark the histogram as needing refresh; holding the initial-preview comparison shows its matching histogram.

Edit curves below the preview. Black/white, red, green and blue dots select the channel. Double tap to add a point, drag to adjust, or tap coordinates for precise input. Output shows negative and expected saved-photo dimensions. The selector shows the preset name, adding Custom when gain, color, curves or other style parameters differ. Hold it for the affected controls. Base correction and inversion switches do not mark the style as custom.

Processing → Compute & acceleration offers **GPU assistance (automatic selection)**. It defaults on for new preferences, preserves existing choices and applies to the next job. Eligible operations include input linearization, density-to-transmission, spectral integration and combined scan output. Selection includes transfer time, with CPU fallback on qualification or execution failure. Global statistics, development and previews remain on CPU. JPEG uses full reference decoding with subsequent memory-based processing blocks. Spectral mode retains all wavelengths. The hardware self-test uses synthetic data, does not write to Library or change the acceleration switch, and reports block timings rather than whole-photo development time. Recent execution records show the actual backend.

Latent and optical archives use fast lossless compression. Pixel and material data stay unchanged; files may be slightly larger. Recent execution records show totals for the executed portion of the latest job: setup, spatial preparation, material computation, scratch writing, and compression/commit. Computation includes row reads; these are not single-shot shutter timings.

## Rolls and Library

The four bottom pages are Camera, Rolls, Library and Settings. Rolls groups development batches by roll. Library offers photo/negative grids, swipe browsing and zoom. While zoomed in, pan to either horizontal edge and keep swiping to change photos. Hold an item to enter batch selection. Detailed photo decoding follows the available memory budget and retains native dimensions when they fit.

The layout button cycles through three columns, five columns and a list. Grids prioritize image size; the list shows details. Filter & sort supports creation time/file size/roll name, ascending or descending order, a selected roll, favorites and rating comparisons (>, <, ≥, ≤; 0 means unrated), plus pages or continuous scrolling. The viewer's five stars and heart control rating and favorite independently. Hold a development batch on its roll page to manage it. Thumbnails transition to detailed images while retaining the available preview during loading.

| Resource | Management | Reuse |
| --- | --- | --- |
| Exposures | Whole rolls | Develop again |
| Developed film | Individual or batch | View and rescan |
| Scanned photos | Individual or batch | Browse and export |

The three layers are stored independently. Deleting one keeps the others; missing sources are shown when following a result upstream.

Negative previews use averaging to reduce sampling aliasing. Zooming loads detail from optical rows in the background within the memory budget; browsing remains available. Optical material stays unchanged. Rendered light-table and projection views have a separate disk cache for reopening. Changed lighting produces a new view. The first uncached view of large film still needs the full material read. Clear display caches under Processing without removing film or photos.

Use **↺** beside a parameter to restore its preset or inherited value. A scan group reset affects that group while keeping the style and other adjustments. Preset curves restores the style's tone and RGB curves; the existing channel reset still makes that channel linear. Whole-draft resets in material/scanner definitions and Free processing return to the opening draft without changing saved materials or tasks. The light-table lamp resets to 0 EV / 5500 K. The camera retains its existing double-tap resets.

## Saving and export

Save is available at the top of the scan page. Output holds format, quality, size, system-gallery export and batch scanning, using the current successful preview settings. Review execution details before development or batch execution. Development needs automatic scanning enabled to export finished photos; development alone retains film for scanning later.

JPEG is 8-bit lossy with Low/Medium/High quality (40/70/100); PNG supports 8/16-bit, and TIFF is 16-bit lossless. Automatic scanning uses the output choices in Scan & save. Export requires a format and destination: the system gallery accepts JPEG/PNG, while TIFF goes to a folder. Existing unrotated JPEGs are copied unchanged; conversion or rotation re-encodes, capped at the original JPEG's recorded quality. Export cannot restore higher precision from an existing lower-precision image; rescan the film instead.

| Resource | Image export | Processing material |
| --- | --- | --- |
| Saved scan | Compatible JPEG, PNG or TIFF based on source format/precision; TIFF uses file export | Follow its source and rescan to select a different precision |
| Developed film | Light-table view preserving polarity or positive projection; JPEG/PNG/TIFF, with TIFF saved to files | Optical NPZ for continued desktop viewing and scanning |
| Exposure roll | Retained camera JPEG / DNG originals to a folder | All latent NPZ files from a sealed roll for desktop development |

Library tiles and the viewer show dimensions, type and time. Photos identify their source film polarity; rolls identify their stock type, with planned dimensions marked Planned. Open ••• → Information for saved photo/film recipes and records. Roll details also offer Information for the loaded material, capture and default processing settings. Developed film uses its actual recorded polarity. File time is labeled when an older film has no creation timestamp; missing facts are marked unrecorded, never inferred from a roll name or current settings.

The viewer's **Source** button shows the source roll, frame, development batch and scan record, with links to available resources. Frame numbers identify exposure positions on a roll, rather than the order of pictures in Library. Development batches and scans have fixed short identifiers; deleting other results does not renumber them. Full identifiers are available under **Information → Resource IDs**.

Source history distinguishes available resources, removed or unrecorded entries, and missing files. Missing sources do not prevent viewing or exporting a saved photo, and another result from the same frame is never substituted. New photos retain the source film's actual material and development settings. Older photos with neither a source nor a saved record show “not recorded.”

Hold to select items in Library or Rolls, then Export selected. Negative images are rendered from complete optical resources, not small previews. Materials and camera originals export as files. Latents not formed yet are prepared during export and may take time. App resources remain intact. Export files you need before uninstalling or changing phones.

Settings offers Chinese / English and Dark / Light themes. The page's ? opens the relevant illustrated guide, with optional detail and a return to the same screen.

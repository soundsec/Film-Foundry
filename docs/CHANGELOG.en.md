# Changelog

[0.4.0 Release Summary](#v040) · [Android 0.1.0](#android010) · [Desktop 0.4.0 Highlights](#v040-desktop) · [0.3.1 & 0.3 Series](#v031)

[中文](CHANGELOG.md) · [Project home](../README.en.md)

> Historical release notes reflect the capabilities and limits of each respective version. For current usage, see the [Desktop Guide](DESKTOP_GUIDE.en.md) and [Mobile Guide](MOBILE_GUIDE.en.md).

---

## 2026-11-28 · Desktop coating controls and runtime translations

- The workbench owns layered exposure and independent grain. Material presets store the coating structure; saved latents retain their exposure records. The guide and sample plates show the two controls on one stock. [Workbench](DESKTOP_GUIDE.en.md#coatings) · [Comparison](SHOWCASE.en.md#coatings)
- Desktop and Android can load a community translation from JSON at runtime. [Desktop](TRANSLATING.en.md) · [Android](../android/TRANSLATING.md)

## 2026-11-24 · Development Documentation & Sample Plates

- Added CLI examples for saved recipes on the bilingual homepages, and organized folder batching, staged rescanning, material recipe replay, and Python/IDE script instructions in the desktop guide ([CLI](DESKTOP_GUIDE.en.md#cli) · [Scripts](DESKTOP_GUIDE.en.md#scripts)).
- Unified showcase comparisons and CLI examples around our own red-fruit photograph, demonstrating panchromatic/orthochromatic/blue-sensitive silver stocks, six lens filters on color negative and reversal film, and instant film, plates, and printing papers ([Film stocks](SHOWCASE.en.md#film-stocks) · [Lens filters](SHOWCASE.en.md#lens-filters) · [Instant film, plates & prints](SHOWCASE.en.md#materials)).

## 2026-11-19 · Source Snapshot

- **Android**: Added preview-first resizable scan drawer, direct/fine-tuning sliders, hold-to-compare preview, four color themes, and community translation template/packaging tools.
- **Desktop**: Added automatic sample refresh in onboarding, manual re-measurement in characteristic curves, and pinned probe markers on the material canvas. See [Development Source Snapshot](DEVELOPMENT_SNAPSHOT.en.md).

## 2026-11-18 · Source Snapshot

- **Desktop**: Added 2D test-strip matrices, density & exposure probes, measured characteristic curves, 100% full-scale local preview, experimental-material process checkpoints/branching, pinned parameters, per-frame batch exposure, scan crop/straighten and base eyedropper, dodge/burn masking, automatic crash recovery, and viewport-aware gallery thumbnail caching.
- **Android**: Updated lightweight film formation, presets, and qualified GLES 3.1 continuous-bath acceleration; added hidden Settings one-shot camera for the seven experimental material families.
- Source-only checkpoint; display versions remain Desktop 0.4.0 Alpha and Android 0.1.0 test. See [Development Source Snapshot](DEVELOPMENT_SNAPSHOT.en.md).

## 2026-11-10 · Source Checkpoint

> Intermediate source checkpoint after 0.4.0 with no new binary installer or version bump. See [Development Source Snapshot](DEVELOPMENT_SNAPSHOT.en.md) for full details.

### Desktop workbench & preset management

- Introduced the gallery-first single-window workbench with single/dual sidebars, read-only high-resolution browsing, stacked origin/medium/scan lookup, and execution-time output selection.
- Added side-by-side, split, and hold-for-original comparisons, rotation, undo/redo, histograms, and 8-band HSL.
- Added unified Preset Manager with selective `FFC2:` collection sharing and independent *Keep* / *Apply* selection on import (processes embed bath solutions).

### Materials, input codecs & compute

- Unified Normal / Expected-effect / Principle controls on the film pipeline with expanded bath kinetics, edge-anchored rotary wetting, contamination/wash/hardening, surface silver/dye damage, time history, electronic rolls, and staged development checkpoints.
- Integrated seven experimental material families (integral instant, peel-apart, daguerreotype, photographic paper, wet plate, cyanotype, platinum/palladium), shared scan output, and creative printmaking/plate separation.
- Added desktop DNG (`rawpy` / LibRaw) and Ultra HDR JPEG gain-map input, field-level storage precision, and optional CUDA / OpenCL acceleration with CPU fallbacks.
- Updated Android source with lightweight film formation, tone-dependent grain, simplified accidents, neutral auto-scan, and 8-band HSL (existing rolls keep frozen versions).

---

<a id="v040"></a>

## 0.4.0 Alpha Release Summary / Android 0.1.0 Test

Prepared 2026-11-02. Desktop display version is **0.4.0**; Android uses an independent version **0.1.0** (`versionCode 34`).

### Desktop maintenance

- Scan tiling shares immutable whole-image calibration and output curves, running normalization, sRGB encoding, and luma curves contiguously per tile while keeping CPU parity.
- Replaced 8-bit output antibanding with explicitly attributed `lowbias32`, preserving determinism and tile consistency.
- Restricted Windows portable DLL search paths to the bundled Python environment and system directories, with complete native dependency/license manifests.

<a id="android010"></a>

### Android Test · 0.1.0

- **Capture & rolls**: Full roll lifecycle (12 / 24 / 36 frames), manual/auto advance, multiple exposure, 135 panorama gate, landscape text imprinting, sealing, and three independent resource layers (originals/latents, developed film, scanned photos); supports JPEG, uncompressed 8/16-bit Bayer DNG, and compatible Ultra HDR.
- **Development & scanning**: Preparation screen summarizing process and bath order, Free processing workbench, film-only or auto-scan runs, bounded background concurrency, and qualified OpenGL ES compute with CPU fallback.
- **Library & export**: 3-column / 5-column / list layouts, independent ratings/favorites with filtering and sorting; JPEG (Low/Medium/High), 8/16-bit PNG, and folder-exported 16-bit TIFF.
- **Installation & signing**: Requires Android 8.0+, ships `arm64-v8a`, `armeabi-v7a`, `x86`, `x86_64`, and `universal` APKs (tested on ARM64); signed with an independent release certificate and bundles complete third-party license notices in-app.

[Mobile overview](MOBILE_SHOWCASE.en.md) · [Mobile guide](MOBILE_GUIDE.en.md) · [Android build & architecture](../android/README.md) · [Third-party notices](../THIRD_PARTY_NOTICES.md)

<a id="v040-desktop"></a>

### 0.4.0 Alpha · Desktop Highlights (2026-10-26)

- **Workbench & editors**: Unified six-page Qt workbench with common controls up front, collapsible advanced sections, bilingual UI, dark/light themes, and standalone material/process/scanner editors.
- **Materials & creation**: Five workflows (`full`, `expose`, `develop_latent`, `develop`, `scan`), unified negative/reversal/cross-processing operators, experimental mask bleaching, Smits 7-basis spectral exposure, and spectral material/observer editors.
- **Files & compatibility**: Canonical `film_foundry` package entry point, backward-compatible media/sessions/FFC2, optional PyAV frame-by-frame material movie masters, and cross-platform medium interchange with Android.

[Desktop guide](DESKTOP_GUIDE.en.md#workbench) · [Mobile guide](MOBILE_GUIDE.en.md) · [Acknowledgements](REFERENCE.en.md#acknowledgements)

---

<a id="v031"></a>

## 0.3.1 Alpha and 0.3 Series

v0.3.1 was a stability, compatibility, and general-performance maintenance release on top of v0.3.0 that established the unified silver-halide model and read-only scanning architecture:

- **Unified silver-halide formation & read-only scanning**: Color/B&W negative and reversal films share conserved material pools and ordered operators so polarity emerges from process order; scanning reads `optical_density_rgb` with independent base-removal and inversion controls and never mutates developed media.
- **Tile consistency & common optimizations**: Material and scan tiling share frozen whole-image calibration to eliminate seams, alongside tighter array lifetimes, buffer reuse, and atomic multi-file commits.
- **Outputs & robustness**: Added `.darkroom_negative.npz` / `.darkroom_positive.npz`, 16-bit scanner raw TIFF, transparent media, Layer Packs, EXIF orientation normalization, run manifests, and fail-closed validation.
- **Compatibility notes**: Removed the legacy `half_frame_darkroom` package name and CLI; split `film/`, `develop/`, and `scanner/` presets are recommended while legacy full JSON files remain readable; `legacy_density` remains available only for explicit regression comparisons.

# Materials, Processes, and Parameter Reference

[中文](REFERENCE.md) · [Desktop guide](DESKTOP_GUIDE.en.md) · [Examples & recipes](SHOWCASE.en.md) · [Project home](../README.en.md)

This reference documents Film Foundry's unified material formation model, read-only scanning boundary, spectral extensions, parameter dictionary, and performance strategy. For interactive controls and CLI usage, see the [Desktop User Guide](DESKTOP_GUIDE.en.md).

> **Model scope & boundaries**: Film Foundry is an interpretable reduced-order creative model, not a quantitative chemical simulator or a calibrated replica of commercial film stocks, chemistries, or scanners. Published process literature guides physical/chemical directionality; numerical coefficients remain adjustable engineering parameters.

## Table of Contents

- [Unified Material and Development Model](#model): latent state, conserved silver-halide pools, operator order, polarity, and cross processing
- [Spectral Exposure, Materials, and Observation](#spectral): exposure spectra, final component spectra, and read-only viewing
- [Glossary and Parameter Dictionary](#parameters): stage roles, material, development, scanner, look controls, and troubleshooting
- [Process Programs and Scanner Interpretation Fields](#process-fields): program keys, compatibility, base removal, and inversion
- [Diagnostic and Accident Recipes](#diagnostic): sensitivity benchmarks, stain, uneven development, and silver retention
- [8-Band HSL Color Mixer](#color-mixer): output-stage per-hue hue, saturation, and lightness control
- [Performance and Compute Strategy](#performance): precomputation, stage reuse, tiling, memory, and GPU acceleration
- [Acknowledgements and References](#acknowledgements)
- [Source Scope and Release Status](#source)

---

<a id="model"></a>

## Unified Material and Development Model

Rather than implementing C-41, ECN-2, E-6, B&W negative, and B&W reversal as disconnected image filters, Film Foundry routes silver-halide stocks through one causal pipeline:

```text
Material -> Exposure -> Latent State -> Ordered Process Operators
         -> Final Medium -> Read-only Optical Observation -> Digital Image
```

What is unified is the **material state language and formation entry point**—not the materials themselves. Negative and reversal stocks keep distinct base densities, sensitivity curves, dye absorption, auxiliary layers, and process compatibility profiles.

At runtime, `auto` selects the appropriate formal operator program from the stock's native identity and mode. The legacy H-D density path runs only when `legacy_density` is explicitly requested for regression comparisons.

### 1. Continuous latent state and single conserved halide pool

Conceptually, each emulsion layer `i` partitions its silver halide into developable $H_i^E$ and inactive $H_i^U$ shares:

```text
H_i^E = H_i * e_i
H_i^U = H_i * (1 - e_i),  e_i in [0, 1]
```

In code, only two authoritative arrays are stored—`halide` ($H_i$) and `developability` ($e_i$)—and both shares are derived on demand so conservation holds strictly across multi-step processing:

```text
developable_halide + inactive_halide == halide
```

### 2. Emulsion layers and medium-level components

```text
L_i = {halide, developability, metallic_silver, bleached_halide?, coupler?, dye?}
M   = {L_1 ... L_n, base, auxiliary layers}
```

- Monochrome materials omit `coupler` and `dye`; color materials define per-layer capacities and dye absorption matrices.
- Base, orange mask, rem-jet, antihalation backing, and protective layers live at the medium level rather than being duplicated per emulsion layer.
- **Material identity vs. process program**: Processing a color negative in `bw_negative` or `bw_reversal` preserves its three-layer spectral sensitivity, dye matrix, and colored base; the B&W program simply forms no dye and renders the resulting metallic silver as broad-spectrum neutral density.
- **Latent material binding**: New latent archives freeze the exposure-time material parameters. Subsequent development can freely swap baths, conditions, and accidents, but cannot swap the exposed base or dye stock. Both desktop and mobile preserve the original latent on disk and save each development as an independent medium.

### 3. Selective process operators

Every process is an ordered sequence of operators governed by action, strength, layer selectivity, and material–process compatibility:

- **Selective silver development**: Reduces developable halide to metallic silver.
- **Color coupling development**: Forms dye alongside silver development, bounded by silver formed, remaining coupler capacity, and compatibility.
- **Remaining halide activation**: Raises developability of still-inactive halide for reversal.
- **Silver bleach**: Converts metallic silver into fixable `bleached_halide`.
- **Fixing**: Removes remaining undeveloped halide and `bleached_halide` without touching unbleached metallic silver.
- **First silver removal**: Directly clears the first negative silver image in B&W reversal.
- **Dye destruction & auxiliary removal**: Applied in color tail steps and non-standard processing.

Material `cross_process_*` compatibility fields take effect only when the program does not match the stock's native class; native pairings stay neutral so cross-process tuning never perturbs standard development.

### 4. Polarity emerges from operator order

- **B&W negative**: Keeps first-developed metallic silver and fixes remaining halide.
- **Color negative**: Color development → silver bleach → fix, leaving dye (partial bleach retains metallic silver for bleach bypass).
- **B&W reversal**: `first dev -> remove first silver -> activate remaining halide -> second silver dev -> fix`.
- **Color reversal**: `first silver dev -> activate remaining halide -> color dev -> bleach silver from both developments -> fix`.

Both reversal paths produce a positive medium via finite halide competition rather than mathematically inverting a finished negative.

### 5. Cross processing and colored masks

Cross processing applies a non-native program to a stock so look differences emerge from real pool and compatibility changes:

- `color-negative stock + color_reversal` → positive transparency on its native colored base;
- `color-reversal stock + color_negative` → negative medium on a clear base;
- `monochrome stock + color_negative / color_reversal` → zero coupler capacity (no invented dye); standard silver bleach clears the silver, whereas bleach bypass retains it.

**Orange mask & experimental mask bleaching**:
- The color-negative orange mask combines unwanted dye side-absorptions with uniform base masking density using a three-band RGB approximation. Scanner base removal subtracts only the frozen uniform base anchor while preserving the base's broadband interaction with dyes.
- Reversing a color negative retains its native orange mask by default (yielding a strong amber/salmon cast); the scanner never silently strips it. To obtain a clear-base positive, explicitly enable the experimental **mask/dye bleach (`BLEACH_MASK_DYE`)**, which is strictly separate from silver bleach and induces material-defined image-dye loss.

### 6. Final medium and read-only observation

The developed medium stores metallic silver, dye, residual halide, bleached halide, base, and auxiliary layers alongside frozen observation coefficients, deriving the read-only authoritative **`optical_density_rgb`**. Scanning observes only that optical master:

```text
final medium optical density + illuminant / sensor / interpretation = digital image
```

- **Strict read-only boundary**: Scanner presets never modify material pools, `optical_density_rgb`, compatibility arrays, or process metadata.
- **One shared transmission scanner**: Negatives and positives pass through the same illuminant and sensor capture primitive. **`remove_base_mask`** and **`invert_transmission`** are independent user toggles—the medium contract provides a recommendation (both on for negatives, both off for positives), while any combination remains allowed (e.g., photographing a negative on a light table).
- **Clear-base reference border**: Extends the observation canvas after `optical_density_rgb` is finalized using the known clear-base density under the same lamp and sensor; never written back to the optical master.
- **Independent bleach & fix states**: Incomplete bleach leaves neutral metallic silver (higher contrast, lower saturation); incomplete fixing leaves halide salts tinted by the stock's optical coefficients (typically blue-dense/yellowish haze on color negatives, neutral haze on B&W).

### 7. Kinetics, phenomena, and stage ownership

- **Bath kinetics**: Time, temperature, concentration, agitation, push/pull, developer exhaustion, and compensation compile into effective activity, completion, contrast, fog, D-max, toe/shoulder, and grain factors. Fixer exhaustion reduces only fixing completion without altering already-formed silver or dye. Developer types (standard, fine-grain, compensating, high-acutance, high-contrast, monobath) and fixer types (rapid, hardening, monobath) remain distinct from exhaustion and push.
- **Causal stage placement**:

```text
Dust, debris, hair, insects, imprint flash, light leaks, halation -> Pre-latent exposure
Uneven development, adjacency effect, DIR interimage              -> Material-pool conversion
Emulsion/dye grain & independent metallic silver grain            -> Formed layer density
Chemical stain, surface silver plating, emulsion reticulation     -> Post-formation optical layer
Explicit dye damage                                               -> Dye-pool operator
Scanning & grading                                                -> Read-only observation
```

- **Exposure-side optics & marks**: Dust/debris vary per frame; hair/insects share stable gate identity across a batch. Light leaks add non-negative pre-latent exposure (supporting secondary shapes, external masks, and manual placement). Support light piping (off by default) decays inward solely from declared frame edges. Halation supports legacy warm RGB return or experimental three-scale layer-selective return. Desktop and mobile store the developed rebate in the optical medium. The plate fixes the stock name, barcode, frame number and advance symbol in their slots. Loading offset moves the whole strip in units of one frame width; zero aligns it to the gate. Code 128 C encodes a four-digit batch and a four-digit frame number. A split code can be read across adjacent frames in feed order. Custom factory edge patterns encode as `FFEM1:<W>x<H>:<hex>` (up to 128×32, row-major MSB-first), with independent top and bottom slots under Plate design. Set the start and end of the text, frame number, direction and code as percentages of frame width. Custom edge text accepts English letters and spaces and prints in uppercase. Text and numbers use square grid, rounded sans or light proportional lettering. Desktop and mobile offer a factory lamp colour and exposure control (−4 to +4 EV). Camera leaks and factory light both expose the emulsion on the rebate. The scan shows the transmitted scanner lamp through each perforation, including its brightness and colour. A custom stencil occupies the text slot. Mask printing and focused exposure develop with the emulsion. The focused spot has adjustable spread. Laser etching removes emulsion at the mark and retains the support and surviving backing.
- **Ink printing and rebate light bleed**. Under Film edges & perforations, select Ink printing in the lettering tab to set how much red, green and blue light passes through the ink. Rebate light bleed in the format tab starts at 0%. Raising it adds a small warm exposure along the outer edge and around perforations. Laser-exposed strokes also scatter light nearby. Ink edges stay sharp.
- **In-picture printing** offers the original laser dots, optical projection and lab printing. “Use original laser dots” selects the original face and lamp. Dot-matrix and seven-segment faces can also be paired with lamp colours. The narrow-gap face follows the original stroke proportions, with a fine seam at each joint. A wide-gap face is also available. New imprints start at +1.25 EV. Saved exposures retain their original settings. Highlight return follows the stock and lamp dose.
- **Grain, adjacency, DIR, and post-formation effects**: Emulsion grain scales with formed density and tapers toward D-max; optional independent metallic silver grain acts only on retained silver. First-development adjacency and color DIR interimage (both off by default) estimate local reaction rates read-only before running a single formal development pass. Chemical stain, surface silver plating, reticulation (never auto-triggered by single-bath temperature), and explicit dye damage do not retroactively alter already-generated grain.
- **Material degradation**: Interpolates speed loss, base fog, layer imbalance, and up to ~+1/3 baseline grain as a unified material property (zero is an exact no-op).

### 8. Core architectural constraints

```text
Film Formation = Material State + Latent Activation + Ordered Selective Operators
Polarity       = A Property of the Final Medium
Cross Process  = Program Applied to a Non-native Material
Scan           = Read-only Optical Observation
```

Integral instant, peel-apart, daguerreotype, photographic paper, wet plate, cyanotype, and platinum/palladium each use their own dedicated material state and process models rather than being forced into the silver-halide film pool.

---

<a id="spectral"></a>

## Spectral Exposure, Materials, and Observation

Built-in stocks default to empirical RGB models. Spectral workflows activate when explicit profiles are loaded—supporting clean analytic creative spectra without requiring laboratory stock calibration.

Desktop exposes three independent spectral slots under *Frame Geometry & Spectral Profiles*:

1. **Exposure spectrum (`film.spectral_exposure_profile`)**: Defines RGB / Smits 7-basis (W/C/M/Y/R/G/B) illuminant bases and emulsion layer sensitivities, pre-integrated into compact matrices before pixel loops while retaining HDR headroom.
2. **Final material absorption spectrum (`film.spectral_material_profile`)**: Defines wavelength-dependent absorbance for nine normalized final pools (C/M/Y dye, metallic silver, residual halide, bleached halide, clear support, masking coupler, auxiliary layer), frozen at exposure.
3. **Observer light & response spectrum (`scanner.spectral_observation_profile`)**: Defines transmission illuminant and RGB readout curves for read-only Beer–Lambert integration during scanning.

### Spectral grain, post-process layers, and storage cost

- **Conservative spectral grain**: Emulsion and silver grain redistribute dye/silver pool amounts locally while conserving whole-frame totals; salts, support, mask, auxiliary layers, and clear-base anchors remain untouched.
- **Post-process optical overlay**: Stain, silver plating, and reticulation store a signed density overlay interpolated piecewise across 450 / 550 / 650 nm reference points (clamped outside the endpoints).
- **Overhead & interchange**: Spectral media add 9 float32 component channels (~**36 bytes/pixel**) plus ~**12 bytes/pixel** when a post-process overlay is active, with no per-pixel wavelength tensor. Desktop and mobile exchange the same spectral NPZ files while using independent runtimes.

---

<a id="parameters"></a>

## Glossary and Parameter Dictionary

[Material](#material-parameters) · [Development](#bath-parameters) · [Scanner](#scan-parameters) · [Look](#look-parameters) · [Troubleshooting](#troubleshooting)

**Rule of thumb**: If changing a parameter alters the saved `.npz` medium or `optical_density_rgb`, it belongs to formation (`film` / `develop`); if it only alters how the same medium is viewed, it belongs to observation (`scanner`).

### Stage glossary

| Term | Meaning | Effect of changes |
| --- | --- | --- |
| Input image | Display-encoded JPG/PNG/TIFF or decoded linear DNG/Ultra HDR | Ordinary SDR images are not scene-referred radiometry |
| Approximate linear space | Inverse-sRGB float working space | Improves physical plausibility of optical/exposure math |
| Film stock | H-D curve, base, dye matrix, grain baseline, halation, MTF | Alters the formed medium; not for scan-only tweaks |
| Chemistry / develop | Program, time, temperature, concentration, agitation, push, exhaustion | Changes density, fog, contrast, grain, and retained silver |
| Developed electronic medium | `.npz` holding material contract, layer masters, and `optical_density_rgb` | `optical_density_rgb` is authoritative for scanning |
| Transmission / scanner raw | 16-bit linear transmitted capture (with optional clear-base border) | Suitable for external grading or fast scan-only loops |
| Scan / observation | Read-only base removal, inversion, tone/color interpretation | Never mutates the developed medium |
| Output | File format, dimensions, bit depth, quality, sidecars | Delivery only; no photographic chemistry effect |

### Sidecars and randomness strategies

Output files can carry `.json` sidecars recording paths, timestamps, seed strategy, configuration snapshots, and exact exported clear-border pixel widths. Randomness strategies control repeatability for grain, leaks, and variations:

- `random`: Resamples every run;
- `fixed`: Reproducible for identical seeds and settings;
- `path`: Deterministic per input path while varying across different files.

> *Process condition random variation* controls random drift in time, temperature, concentration, agitation, and exhaustion—separate from the seed strategy above.

### Configuration separation

Split presets into `film/` (stock), `develop/` (process & bath conditions), and `scanner/` (observation & tone); full root JSON files serve only as complete single-file examples.

<a id="material-parameters"></a>

### Film material parameters (`film`)

| Field | Scope | Meaning | Typical effect when increased |
| --- | --- | --- | --- |
| `halation_strength` | `film` | Halation coupling strength | Stronger warm glow around bright highlights |
| `halation_return_model` | `film` | Return model (`compatibility_rgb` / `layer_selective`) | Switches between legacy warm RGB return and direct per-layer return |
| `halation_layer_return_weights` / `spread_scale_weights` | `film` | Per-layer and compact/main/wide spread weights | Active under `layer_selective`; shapes multi-scale halation |
| `halation_threshold` / `softness` | `film` | Highlight trigger threshold and transition width | Higher threshold limits glow to brightest peaks; wider softness smooths onset |
| `halation_core_radius` / `exponential_radius` | `film` | Short-range scatter and long-range base reflection radii | Thicker core and farther outer halation falloff |
| `light_piping_strength` / `depth` / `edge_mode` / `layer_weights` | `film` | Edge-injected base light piping (off by default) | Adds pre-latent fog decaying inward from declared frame edges |
| `extreme_exposure_reversal_strength` / `start_loge` / `width` | `film` | Experimental extreme-exposure latent roll-back (default 0) | Lowers developability beyond threshold (latent-side only) |
| `film_base_density_rgb` / `clear_support_density_rgb` | `film` | Total base/orange-mask density and unbleachable clear support floor | Deeper orange mask; clear floor prevents unphysical zero-density base |
| `base_dye_interaction_strength` / `matrix` | `film` | Broadband base–dye interaction strength and overlap matrix | Retains spectral tinting effect of colored base after mask removal |
| `retained_halide_density_rgb` / `bleached_halide_density_*` | `film` | Optical density tendencies for unfixed and bleached silver salts | Governs tint/haze direction of incomplete fixing |
| `material_degradation` + `degradation_*` | `film` | Unified ageing strength (0–1) plus speed loss, fog, and layer balance | Reduces speed, raises fog, shifts balance, and increases baseline grain |
| `auxiliary_layer_amount` / `density_rgb` | `film` | Removable backing / rem-jet pool and RGB density | Enters final density when `auxiliary_removal` is incomplete |
| `layer_sensitivity_matrix` / `dye_absorption_matrix` | `film` | RGB-to-layer sensitivity and CMY-to-RGB absorption matrices | Controls color capture separation and dye crosstalk |
| `dir_material_response_matrix` / `dir_diffusion_radius` | `film` | Color DIR source→target inhibition matrix and diffusion radius | Inhibits target layers during color development (off for B&W) |
| `hd_gamma` / `density_min` / `density_max` | `film` | Characteristic slope, base fog floor, and maximum density | Adjusts negative contrast, shadow floor, and highlight headroom |
| `log_exposure_toe` / `log_exposure_shoulder` | `film` | Shadow toe and highlight shoulder exposure coordinates | Shifts shadow lift and highlight compression knees |
| `granularity_sigma` / `grain_density_correlation_radius` | `film` | Density-domain grain RMS baseline and correlation radius | Stronger and coarser emulsion grain (further modulated by bath factors) |
| `silver_grain_strength` / `radius` / `clump_mix` | `film` | Independent neutral metallic-silver grain (default 0) | Adds neutral grain solely on retained metallic silver |
| `emulsion_mtf_strength` / `digital_artifact_suppression` | `film` | Optical resolution softness and ISP sharpening-ringing suppression | Softens harsh digital edges; excessive values blur fine detail |

#### Saved NPZ array semantics

| Array | Semantics | Authoritative for scanning? |
| --- | --- | --- |
| `optical_density_rgb` | Read-only total RGB optical density derived from final pools | **Yes** (primary scanner & light-table input) |
| `density_cmy` | Legacy pre-grain/pre-stain 3-channel layer proxy | No (backward compatibility & plate exports) |
| `density_grain` | Legacy composite proxy including grain & post-process deltas | No (not a pure grain map) |

Legacy fields (`color_matrix`, `base_fog`, `contrast`, `toe_strength`, `shoulder_strength`, `shoulder_point`, `saturation`, `grain_strength`, `grain_scales`, `grain_scale_weights`, `grain_midtone_mu`, `grain_midtone_sigma`) remain for old file compatibility only and are unused by the main pipeline.

<a id="bath-parameters"></a>

### Development parameters (`chemistry` / `develop`)

| Field | Scope | Meaning | Typical effect when increased |
| --- | --- | --- | --- |
| `developer_type` / `fixer_type` | `develop` | Developer profile (standard/fine/compensating/high-acutance/high-contrast/monobath) and fixer profile | Shapes kinetics, grain character, clearing efficiency, and adjacency |
| `frame_size` | `develop` | Format / enlargement proxy | Smaller formats show more prominent grain |
| `time_min` / `temperature_c` / `concentration` / `agitation` | `develop` | Bath duration, temperature, dilution, and agitation | Raises activity, contrast, fog, and grain |
| `process_mode` / `compensation` | `develop` | Execution mode and highlight compensation tendency | Higher compensation reins in highlight density growth earlier |
| `push_stops` | `chemistry` | Push / pull stops | Positive increases activity, contrast, fog, and grain; negative reduces |
| `developer_exhaustion` / `fixer_exhaustion` | `chemistry` | Developer and fixer exhaustion | Developer lowers activity/D-max and adds fog; fixer reduces salt clearing only |
| `development_adjacency_strength` / `radius` | `chemistry` | First-development edge adjacency strength and relative radius | Enhances local edge contrast via reaction rate (not scanner sharpening) |
| `dir_process_effectiveness` / `adjacency_work_long_edge` | `chemistry` / `processing` | Color-bath DIR multiplier and internal adjacency grid cap | Gates material DIR (`1` for color neg, `0` for standard reversal) |
| `silver_retention` / `silver_plating` | `chemistry` | Bleach-bypass strength vs. post-process surface silver deposit | Former keeps image silver; latter deposits patchy neutral surface silver |
| `mask_bleach_completion` / `auxiliary_removal` | `develop` / `chemistry` | Experimental mask bleach and rem-jet/backing clearing completion | Reduces orange mask (with dye risk) or clears backing layer |
| `reticulation_strength` / `dye_damage_strength` | `chemistry` | Explicit emulsion reticulation and color dye loss (default 0) | Adds near-zero-mean texture or reduces formed color dye |
| `light_leak_*` / `exposure_occlusion_*` | `chemistry` | Pre-latent light leaks (templates, secondary, mask, placement, seed) and dust/debris/hair/insects | Adds light or blocks light prior to latent formation |
| `film_border.imprint_*` | `film_border` | Laser dot-matrix / optical amber / lab edge imprints | Strong gate illumination added after MTF into latent exposure |

<a id="scan-parameters"></a>

### Scanner and observation parameters (`scanner`)

| Field | Scope | Meaning | Typical effect when increased |
| --- | --- | --- | --- |
| `transmission_light_ev` / `temperature_k` | `scanner` | Shared transmission lamp EV and color temperature | Adjusts capture headroom and illuminant tint (legacy `negative_backlight_*` / `light_table_*` still supported) |
| `remove_base_mask` / `invert_transmission` | `scanner` | Independent base-removal and negative-inversion toggles | Controls how transmitted capture is interpreted without mutating media |
| `include_clear_base_border` | `scanner` | Append derived clear-base reference border | Expands observation canvas only |
| `scanner_response_matrix` / `scan_base_percentile` | `scanner` | Capture sensor matrix and external-raw fallback percentile | Shapes raw sensor capture; fallback used only when no base/border exists |
| `negative_channel_matrix` / `gamma` | `scanner` | Post-inversion density-domain channel reconstruction | Gently corrects residual scanner crosstalk |
| `negative_channel_compensation_enabled` / `strength` | `scanner` | Bounded scanner-side blue/green compensation (default off, 0.35) | Acts solely on composed RGB scene density without inverting dye matrices |
| `print_reference_density` / `print_gamma` / `print_mapping_mode` | `scanner` | Midgray reference density, contrast slope, and curve (`printlike` / `sigmoid`) | Sets tonal placement, contrast, and shoulder character |
| `print_color_shift` / `print_color_bias` / `highlight_color_bias` | `scanner` | Log-domain filter shift, RGB gain, and threshold-gated highlight gain | Color balance (prefer log `print_color_shift` over multiplicative bias) |
| `projection_black_adaptation` | `scanner` | Light-table deep-shadow viewing adaptation | Monotonically lifts near-black detail while preserving hue ratios |
| `scan_saturation` | `scanner` | Final scanned color saturation | Adjusts output vividness only |
| `scan_normalize` / `strength` / `mode` / `*_percentile` | `scanner` | Auto black/white normalization (`luma` vs `rgb`), blend, and endpoint percentiles | `luma` preserves color cast; `rgb` acts like per-channel auto white balance |

<a id="look-parameters"></a>

### GUI / CLI quick look controls (`look`)

- **Formation-side**: `exposure_ev`, `negative_contrast`, `saturation_multiplier` (dye selectivity), `halation_multiplier`, `grain_multiplier`, `grain_size_multiplier`, and `look_strength` (global convenience scaler; keep at `1.0` when authoring serious presets).
- **Scan-side**: `print_contrast`, `print_exposure_ev` (never alter the developed medium).

<a id="troubleshooting"></a>

### Common tuning diagnostics

| Symptom | Check first | Suggested fix |
| --- | --- | --- |
| Flat, gray negative scans | `scan_normalize`, `scan_normalize_strength`, `print_contrast` | Enable `luma` normalization and raise strength slightly before boosting `print_contrast` |
| Exposure EV barely changes brightness | `scan_normalize` | Lower normalization strength or disable it to see true exposure shifts |
| Overly clean / auto-white-balanced look | `scan_normalize_mode`, `strength` | Switch from `rgb` back to `luma` and moderate strength |
| Grain looks pasted on top | `emulsion_mtf_strength`, `grain_size_multiplier`, `digital_artifact_suppression` | Increase MTF/artifact suppression modestly and adjust grain scale |
| Highlights tint yellow/green or skies go gray | Scanner blue curve, `highlight_color_bias` | Avoid early blue-channel flattening; tune `highlight_color_bias` near `1.00` |

**Recommended workflow**: Disable/lower auto-normalization → dial in `film` + `develop` and save `.npz` → rescan `.npz` while tuning `scanner` → finalize output size and format.

---

<a id="process-fields"></a>

## Process Programs and Scanner Interpretation Fields

### Process program fields (`develop`)

```json
{
  "develop": {
    "program_key": "color_negative",
    "reversal_activation": 1.0,
    "first_silver_removal": 1.0,
    "silver_bleach_completion": 1.0,
    "halide_fixing_completion": 1.0,
    "dye_coupling_efficiency": 1.0,
    "auxiliary_removal": 1.0,
    "process_layer_balance": [1.0, 1.0, 1.0]
  }
}
```

- `program_key` supports `auto`, `bw_negative`, `color_negative`, `color_negative_bleach_bypass`, `bw_reversal`, `color_reversal`, and regression-only `legacy_density`.
- `first_silver_removal` applies only to B&W reversal; `experimental_color_negative_clear_reversal` appends `BLEACH_MASK_DYE` controlled by `mask_bleach_completion` (always `0` in standard processes).
- Material `cross_process_*` fields multiply with program balance only on material/program mismatch.

### Scanner interpretation fields (`scanner`)

```json
{
  "scanner": {
    "interpreter_key": "negative_scan",
    "target_medium_process": "negative",
    "input_polarity": "negative",
    "output_polarity": "positive"
  }
}
```

New sessions save `interpretation_mode = "manual"` with explicit `remove_base_mask` and `invert_transmission` switches; legacy presets migrate cleanly while preserving historical behavior. On modern `.npz` files, frozen `optical_observation` always takes precedence over any `film` fields present in a scanner JSON.

### Positive material vs. light-table viewing controls

- **Material formation** (changes electronic positive density): `positive_highlight_shoulder` / `width`, `positive_midtone_density`, `positive_shadow_toe` / `width`.
- **Light-table viewing** (read-only display adaptation): `projection_white_softness`, `projection_black_adaptation`.

---

<a id="diagnostic"></a>

## Diagnostic and Accident Recipes

### Sensitivity benchmark: `film/diagnostic_develop_sensitive` + `scanner/diagnostic_flat_scan`

Designed to verify that development parameters measurably alter the medium and scan: steep narrow H-D curve, minimal grain/MTF/halation, and scanner auto-normalization disabled so density shifts are not flattened.

```bash
python -m film_foundry.app.cli full input_images outputs/diagnostic_develop \
  --film-preset diagnostic_develop_sensitive \
  --develop-preset standard_color_negative \
  --scanner-preset diagnostic_flat_scan \
  --fast
```

### Bounded accident example: `develop/accident_kelp_light_leak`

Combines `light_leak_strength` (pre-latent directional edge exposure), `chemical_stain` (post-formation CMY deposit), `uneven_development` (low-frequency local developer activity), `silver_retention` (bleach bypass), and `silver_plating` (neutral surface deposit).

---

<a id="color-mixer"></a>

## 8-Band HSL Color Mixer

Desktop (**Observation & Grading → 8-Band HSL**) and Mobile (**Scan → Color → HSL**) adjust Hue (±30°), Saturation (±100), and Lightness (±100) across **Red, Orange, Yellow, Green, Cyan, Blue, Purple, and Magenta** bands.

- Stored under `scanner.color_mixer` as 8 `[H, S, L]` rows; travels in JSON and `FFC2:` codes, never modifies developed media, and is bypassed identically when absent or all zeros.
- Model `output_hsl_bands_v1` runs after display encoding and curves using smoothstep circular interpolation across band centers (0° / 30° / 60° / 120° / 180° / 240° / 270° / 300°), scaling saturation by `1 + S/100` and shifting lightness by `0.0025 * L * min(chroma / 0.05, 1)` while leaving pure neutral grays untouched.

---

<a id="performance"></a>

## Performance and Compute Strategy

Film Foundry separates user-controlled approximation axes from bit-exact common engineering optimizations:

- **Three orthogonal user axes**: **Edge scaling**, **Quality tier** (`draft` / `standard` / `high`), and **Reduced fast mode** (explicitly omits costly secondary mechanisms).
- **Hardware acceleration with CPU fallback**: Desktop source accelerates selected formation, transfer, observation, and encoding operators via optional **CUDA / OpenCL** while defaulting to CPU and falling back cleanly on failure (Vulkan not implemented). Mobile uses qualified **OpenGL ES 3.1** acceleration for eligible steps with CPU fallback. See [Development Source Snapshot](DEVELOPMENT_SNAPSHOT.en.md).

### Common execution optimizations

| Strategy | Implementation |
| --- | --- |
| Precomputed tables | Compact lookup tables for RGB / Smits bases, layer responses, and observer weights |
| Stage reuse | Scan-only edits reuse the formed medium without re-exposing or re-developing |
| Coalesced edits | Rapid slider drags cancel stale jobs and keep the UI responsive |
| Global calibration, tiled execution | Freeze base, exposure, and black/white bounds once per image before row tiling |
| Memory peak control | Row-wise spectral integration, private scratch reuse, timely history release |
| Fast lossless archives | Fast lossless NPZ compression (or uncompressed option) preserving exact arrays and dtypes |

**Design reference**: [Fotufilm's `SpectralModel.swift`](https://github.com/DhaliwalX/fotufilm-engine/blob/c30bf7176bdff85cc3bb8a34453311ebba359d57/Sources/FotufilmCore/SpectralModel.swift) inspired config-driven spectral table preparation and bounded caching.

---

<a id="acknowledgements"></a>

## Acknowledgements and References

| Project / Reference | Contribution / Inspiration |
| --- | --- |
| [spektrafilm](https://github.com/andreavolpato/spektrafilm) · Andrea Volpato | Staged spectral film, paper, and viewing research |
| [Fotufilm](https://github.com/DhaliwalX/fotufilm-engine) | Config-driven spectral tables, bounded caching, and preparation coordination |
| Brian Smits ([RGB → Spectrum](https://doi.org/10.1080/10867651.1999.10487511)) / [PBRT](https://www.pbr-book.org/3ed-2018/Color_and_Radiometry/The_SampledSpectrum_Class) | Seven-basis color decomposition concept (implemented with original analytic bases) |

All material models, process operators, and compute pipelines in Film Foundry are independently implemented. Thanks also to the authors of Python, NumPy, OpenCV, Pillow, Qt / PySide6, LittleCMS, python-lcms2, LibRaw / rawpy, tifffile, imagecodecs, and PyAV. AI tools assisted development.

---

<a id="source"></a>

## Source Scope and Release Status

The public source repository includes the desktop Python application, shared data contracts, presets, test suite, and assets, alongside all five Android modules (`:interchange`, `:kernel`, `:workflow`, `:platform`, `:app`), Gradle wrapper, synthetic fixtures, and device-check tools. Signing keys, local SDK paths, private photos, and internal dev notes are excluded.

- **Display versions**: Desktop **0.4.0 Alpha**, Android **0.1.0 test** ([Changelog](CHANGELOG.en.md#v040) · [Source Snapshot](DEVELOPMENT_SNAPSHOT.en.md)).
- **Entry point & parity**: Canonical Python package is `film_foundry`. Desktop defaults to CPU with optional CUDA/OpenCL; mobile uses CPU with optional GLES assistance; both share interchange contracts without promising pixel-identical cross-platform output.

### Basic verification

```bash
python run_film_foundry_launcher.py
python run_film_foundry_launcher.py --self-check
python -m pytest
```

### Licensing and redistribution

See [Third-Party Notices](../THIRD_PARTY_NOTICES.md) and [third_party_licenses/README.md](../third_party_licenses/README.md) for algorithm attributions and upstream dependency licenses. 8-bit antibanding uses attributed `lowbias32`, and Android bundles runtime dependency notices inside the APK. Any binary redistribution must provide matching project source and applicable dependency license materials.

## Film edge formation

Choose a mode in Film edges & perforations or in the phone’s roll-loading settings.

| Mode | Image area | Rebate |
| --- | --- | --- |
| Attached (default) | Adds two strips parallel to film travel | Factory exposure follows the stock and baths. Gate-edge leaks continue into the rebate. |
| Whole strip | Places the source inside a larger strip | The scene and factory marks develop together with grain and spatial defects. |
| 35 mm in 120 | Keeps the source dimensions and framing | The scene exposes the material between and around the perforations. |

Full-frame 135 travels along the 36 mm edge. Half frame travels along the 18 mm edge, with four perforations on each short side. A landscape half frame has vertical edges. One factory plate spans eight perforations. Exposures 23 and 24 take the first and second four-perforation windows of factory frame 12. Stock lettering and barcodes can cross the join. Exposure through the perforations is available with full-frame 135 stock in a wider gate. Ordinary 120 stock is unperforated.

New rolls size lettering relative to the film format. Full- and half-frame 35 mm share the same stock height. Lettering occupies a smaller fraction of a 120 frame. Desktop also offers the legacy sizing option.

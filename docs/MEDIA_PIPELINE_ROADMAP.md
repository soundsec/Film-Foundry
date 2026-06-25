# Film Foundry Media Pipeline Roadmap

This document records the longer-term design direction for Film Foundry as it
grows beyond color negative film into slide film, reversal processing, instant
materials, peel-apart processes, direct-positive paper, plate processes, and
other playful or experimental image-making workflows.

The short version:

```text
choose material
-> choose process
-> choose viewing / output interpretation
```

The current codebase already follows this shape in a smaller form:

```text
film preset
-> develop preset
-> scanner preset
```

The goal is to keep that separation intact as more media types are added.

## Simulation Philosophy

Film Foundry is a serious material-inspired toy, not a rigorous physics or
chemistry simulator.

The project should be more structured and more explainable than a normal film
filter, but its models are intentionally practical approximations:

- physics-inspired, chemistry-inspired, and workflow-inspired;
- built from equivalent controls, reduced-order models, and bounded heuristics;
- allowed to use direct CV operations when they produce a useful, controllable
  material behavior;
- designed for single-image experimentation, not laboratory prediction;
- expected to preserve stage boundaries and parameter meaning even when the
  internal math is approximate.

The important promise is not scientific exactness. The important promise is
that controls have understandable consequences, intermediate states are
inspectable, and the pipeline remains reusable as a creative virtual darkroom.

## User-Facing Entry Model

Future UI should guide users through three decisions.

### 1. Material

The material describes the image-bearing medium itself:

- color negative film
- black-and-white negative film
- color reversal / slide film
- black-and-white reversal film
- instant integral film
- peel-apart instant film
- direct-positive paper
- silver plate / daguerreotype-inspired plate
- experimental or fantasy materials

Material presets should own physical response:

- H-D / characteristic curve
- native polarity
- color or monochrome behavior
- dye or silver density model
- base density / mask / reflective substrate
- halation or optical spread
- MTF / softness
- grain or plate texture baseline

### 2. Process

The process describes what is done to the material:

- standard negative processing
- push / pull
- compensating development
- bleach bypass / retained silver
- reversal processing
- cross processing
- monobath
- bad fixer / silvering
- dirty chemistry / stain
- light leaks and other accidents
- instant diffusion development
- plate exposure and development

Process presets should not permanently redefine the material. They should
produce a specific developed state from one material instance.

### 3. Viewing / Output Interpretation

The output interpretation describes how the developed material is viewed:

- scan negative
- scan positive transparency
- reflective scan
- contact print
- instant print view
- plate view
- separations / material export

Scanner or render presets should not go back and alter material formation.
They should interpret an already developed medium.

## Where Reversal Processing Belongs

Reversal processing belongs in the process stage, not the scanner stage.

For example:

```text
material: color negative stock
process: reversal / cross-process positive
developed medium polarity: positive
render: positive transparency or reflective interpretation
```

The scanner can decide how to view that positive material, but the fact that the
material became positive is a develop/process outcome.

This matters because reversal processing changes the developed medium itself:
the negative or positive state should be stored in the reusable intermediate
artifact, not faked only during final rendering.

## Current Reusable Components

Most current modules can be reused if each future medium gets an explicit
pipeline branch instead of overloading one color-negative path.

Reusable as-is or with small adapters:

- `core/color.py`: color space and luminance utilities.
- `core/mtf.py`: input-side or emulsion-side softness / artifact suppression.
- `core/halation.py`: optical spread for transparent or translucent media.
- `core/development.py`: process kinetics and derived process state.
- `core/sensitometry.py`: H-D density response for silver/dye density media.
- `core/density_grain.py`: density-domain grain and residue texture.
- `core/accidents.py`: bounded darkroom accidents.
- `core/scanner.py`: negative/positive interpretation primitives.
- `core/media_registry.py`: dispatch point for future media pipelines.
- `core/states.py`: pattern for medium state objects.

Likely future additions:

- `core/reversal.py`: reversal and cross-process state transitions.
- `core/instant.py`: diffusion-development / integral-print behavior.
- `core/plate.py`: direct-positive plate and reflective metal rendering.
- `core/paper.py`: reflective paper / contact-print response.
- New state classes for positive transparencies, instant prints, paper prints,
  and plate images.

## Grain and Chemistry: Can We Model More Detail?

Yes. The current implementation already has a lightweight path for chemistry to
affect grain:

```text
DevelopRecipeConfig
-> build_effective_development()
-> grain_factor / grain_radius_factor
-> density_grain.py
-> density-domain noise strength and correlation radius
```

This is not yet a microscopic silver-halide crystal simulator. It is a bounded,
artist-friendly proxy. But it is capable of growing into a more expressive
model.

Current grain controls:

- `FilmStockConfig.granularity_sigma`: material baseline grain strength.
- `FilmStockConfig.grain_density_correlation_radius`: material baseline grain
  scale.
- `DeveloperProfile.grain_bias`: developer/process effect on grain strength.
- `DeveloperProfile.grain_radius_bias`: developer/process effect on apparent
  grain size.
- `DevelopRecipeConfig.frame_size`: visible enlargement proxy.
- `push_stops`, `temperature_c`, `developer_exhaustion`, `fixer_exhaustion`,
  `silver_retention`, and `chemical_stain`: process effects that currently
  influence grain strength or residue texture.

Future grain model extensions can add:

- `solvent_effect`: fine-grain developer solvent action, reducing apparent
  grain radius and edge granularity.
- `acutance_effect`: high-acutance developer edge adjacency behavior.
- `clumping`: exhausted or hot chemistry causing larger, less even clusters.
- `crystal_growth_bias`: process tendency toward larger apparent grain.
- `grain_spectral_bias`: different grain visibility per color layer.
- `residue_texture_scale`: stain/silvering residue pattern size.
- `reticulation_strength`: high-temperature emulsion damage and wrinkling.

These should be derived from developer/process profiles first, with sliders as
expert overrides only when needed.

Suggested profile shape:

```text
DeveloperProfile
  activity_rate
  gamma_bias
  fog_bias
  dmax_bias
  grain_bias
  grain_radius_bias
  solvent_effect
  acutance_effect
  clumping
  stain_bias
  residue_bias
```

Then recipe sliders such as time, temperature, concentration, agitation, and
exhaustion can modulate those profile tendencies.

## Preset Naming Direction

Long-term presets should be organized conceptually like this:

```text
materials/
  color_negative/
  bw_negative/
  color_reversal/
  bw_reversal/
  instant/
  paper/
  plate/

processes/
  negative/
  reversal/
  cross_process/
  instant/
  direct_positive/
  plate/
  accidents/

renders/
  negative_scan/
  positive_scan/
  reflective_scan/
  contact_print/
  instant_print/
  plate_view/
```

The current directories can remain for now:

```text
presets/film
presets/develop
presets/scanner
```

But new UI language should increasingly describe them as:

```text
material preset
process preset
render preset
```

## Implementation Roadmap

### Phase 1: Clarify Names Without Breaking Existing Presets

- Keep `film/develop/scanner` directories working.
- Add UI labels that explain them as material/process/render.
- Keep `merge_config_presets()` as the compatibility composition point.
- Document which parameters belong to material, process, and render.

### Phase 2: Add Medium-Aware State Objects

Add explicit states beyond `DevelopedNegative`:

- `DevelopedTransparency`
- `DevelopedPositive`
- `InstantPrint`
- `ReflectivePrint`
- `PlateImage`

Each state should carry:

- data arrays
- native polarity
- medium family
- process metadata
- render assumptions

### Phase 3: Add Reversal Prototype

Start with black-and-white reversal, because it is simpler than color reversal.

Prototype shape:

```text
input linear image
-> first development negative silver image proxy
-> bleach / clear negative silver proxy
-> re-exposure or chemical fog
-> second development positive density
-> positive transparency state
-> positive scan/render
```

After that, consider color reversal / slide behavior with separate material
presets rather than reusing color-negative assumptions too aggressively.

### Phase 4: Add Instant / Peel-Apart / Plate Families

Instant and plate processes should be new media pipelines, not scanner presets.
They can still reuse H-D response, accidents, grain, optical spread, and render
curves where appropriate.

## Design Rule

If a control changes the reusable developed medium, it belongs to material or
process.

If a control only changes how an existing developed medium is viewed, it belongs
to render/scanner.

This rule should remain the main guardrail as the project grows.

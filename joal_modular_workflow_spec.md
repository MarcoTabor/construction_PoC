# JOAL Modular Workflow Specification

## Purpose
Create a vector-first, modular pipeline for JOAL corridor geometry extraction and measurement that is reusable inside a larger workflow.

The pipeline must:
- Start from JOAL vectors and a filled shell mask
- Keep the accepted centerline method (clip endcaps, then extend to mask)
- Derive inner/outer from shell geometry
- Remove endpoint-row artifacts
- Smooth all three curves consistently
- Measure lengths/area
- Produce QA visuals including transparent plan overlays

## Design Principles
- Vector-first geometry, raster only for shell/visualization support
- One module per responsibility
- Explicit data contracts between modules
- Reproducible outputs with provenance
- Replaceable modules without breaking the full pipeline

## Module Structure

### 1. IO Module
Suggested file: `scripts/modules/io.py`

Responsibilities:
- Load/save JSON payloads
- Load/save PNG masks and overlays
- Render PDF page into raster at configured pixels-per-point

Inputs:
- File paths, page index, pixels-per-point

Outputs:
- Numpy arrays and typed dictionaries

### 2. Calibration Module
Suggested file: `scripts/modules/calibration.py`

Responsibilities:
- Resolve canonical calibration (meters_per_point and meters_per_pixel)
- Track calibration provenance and method

Inputs:
- `outputs/scale_detection/scale_detection.json`

Outputs:
- Calibration payload

Core fields:
- `meters_per_point`
- `meters_per_pixel`
- `pixels_per_point`
- `source_file`
- `method`

### 3. Shell Module
Suggested file: `scripts/modules/shell.py`

Responsibilities:
- Build/validate filled JOAL shell mask from vectors
- Emit shell QA artifacts

Inputs:
- Vector payload, page geometry, pixels-per-point

Outputs:
- Binary shell mask

### 4. Centerline Module
Suggested file: `scripts/modules/centerline.py`

Responsibilities:
- Extract accepted centerline from shell
- Clip endpoint ambiguity regions
- Extend endpoints back to shell boundary

Inputs:
- Shell mask, centerline extraction config

Outputs:
- Centerline polyline and metadata

### 5. Boundaries Module
Suggested file: `scripts/modules/boundaries.py`

Responsibilities:
- Derive inner/outer rail lines from shell contour geometry
- Split shell contour into two arcs between centerline endpoints
- Classify arc with smaller bend-center radius as inner

Inputs:
- Shell mask and centerline endpoints/context

Outputs:
- Inner and outer polylines

### 6. Filters Module
Suggested file: `scripts/modules/filters.py`

Responsibilities:
- Remove endpoint-row artifacts (same/near start-end Y rows)
- Apply same smoothing policy to all three curves

Inputs:
- Centerline, inner, outer curves

Outputs:
- Cleaned and smoothed curve set

### 7. Metrics Module
Suggested file: `scripts/modules/metrics.py`

Responsibilities:
- Compute lengths (px and m)
- Compute corridor area between inner and outer
- Support raw/filtered/smoothed metric variants

Inputs:
- Curves, shell mask, calibration

Outputs:
- Structured metrics payload

### 8. Visualization Module
Suggested file: `scripts/modules/visualization.py`

Responsibilities:
- Generate QA overlays on mask
- Generate transparent line-only layer (RGBA)
- Generate transparent-plan composite overlays

Inputs:
- Plan raster, mask, curves, style config

Outputs:
- PNG artifacts and a visualization manifest

### 9. Orchestration Module
Suggested file: `scripts/pipeline_joal_geometry.py`

Responsibilities:
- Run all modules step-by-step
- Validate intermediate outputs
- Write unified run summary

Inputs:
- One pipeline config JSON

Outputs:
- All stage outputs + one run summary manifest

## Data Contracts

### Calibration Contract
- `meters_per_point`
- `meters_per_pixel`
- `pixels_per_point`
- `source_file`
- `method`

### Curves Contract
- `centerline_yx`
- `inner_line_yx`
- `outer_line_yx`
- `stage`
- `filters_applied`
- `smoothing_window`

### Metrics Contract
- `lengths_px`
- `lengths_m`
- `area_px2`
- `area_m2`
- `calibration_ref`
- `measurement_policy`

### Visual Manifest Contract
- `overlay_mask`
- `overlay_plan`
- `transparent_layer`
- `line_styles`
- `notes`

## Step-by-Step Execution Order
1. Load canonical calibration
2. Build shell mask from vectors
3. Extract centerline (clip + extend)
4. Extract inner/outer from shell contour split
5. Apply endpoint-row exclusion policy
6. Smooth all three curves with identical kernel
7. Compute final lengths and area
8. Generate visuals:
   - mask overlay
   - transparent line-only layer
   - transparent-plan composite
9. Write run summary manifest

## Output Expectations
Required final outputs:
- Curve JSON (centerline, inner, outer)
- Metrics JSON (lengths + area, raw and smoothed variants)
- QA images (mask overlays)
- Transparent line layer
- Transparent-plan composite
- Run summary manifest

## Notes for Integration
- Keep module APIs pure and typed where possible
- Keep all constants in config, not hard-coded in orchestrator
- Include calibration source in all downstream metric outputs
- Record exact smoothing and filtering policy in metadata for reproducibility

# Footpath Extraction Pipeline Write-Up

## Purpose

This document describes the current footpath workflow in this repository, from the original drawing PDF through legend color detection, raster mask generation, centerline reconstruction, width estimation, and final area calculation.

The focus is the `PROPOSED FOOTPATH` on page 1 of `examples/Joal 502.pdf`.

## High-Level Flow

The pipeline currently has four logical layers:

1. **Legend and scale detection**
   - Find the legend entry for footpath.
   - Extract its swatch color.
   - Detect the drawing scale and derive meters per pixel.

2. **Pixel footpath isolation**
   - Render the PDF page to an image.
   - Build a raw color mask from the legend color.
   - Remove page furniture and overlaps.
   - Reconstruct a continuous corridor for the footpath.

3. **Centerline and width modelling**
   - Place seeds inside the corridor.
   - Connect them into a path graph.
   - Measure local widths.
   - Refine the centerline to better match the actual path center.

4. **Area estimation and uncertainty**
   - Rasterize a final thick path.
   - Build sigma-based width confidence maps.
   - Estimate area from width along the centerline.
   - Estimate an uncertainty envelope.

## Inputs

### Source PDF

- `examples/Joal 502.pdf`

### Key derived metadata

- `outputs/legend_colors/legend_colors.json`
  - Contains the detected legend entry and swatch color.
  - For `PROPOSED FOOTPATH`, the swatch color is gray: `#CCCCCC` / RGB `(204, 204, 204)`.

- `outputs/scale_detection/scale_detection.json`
  - Detects the scale label and scale bar.
  - On the current drawing this identified `A3 Scale 1:500` and the nearby tick labels.
  - This is later used to convert pixel lengths and areas into meters and square meters.

## Stage 0: Legend Color Detection

Script:

- `scripts/find_legend_colors.py`

What it does:

1. Reads the PDF page as vector/text content.
2. Finds the legend block.
3. Searches for legend entries containing labels such as `PROPOSED FOOTPATH`.
4. Links each label to a nearby swatch rectangle.
5. Extracts the swatch fill color.
6. Produces a JSON summary and highlight images.

Why it matters:

- This stage tells the rest of the pipeline what pixel color to search for in the full page render.
- In this case the footpath is represented by a light gray fill.

Primary outputs:

- `outputs/legend_colors/legend_colors.json`
- `outputs/legend_colors/visualizations/page_legend_highlight_proposed_footpath.png`
- `outputs/legend_colors/visualizations/page_legend_regions_proposed_footpath.png`

## Stage 0B: Scale Detection

Script:

- `scripts/find_scale_bar.py`

What it does:

1. Finds the scale text block, such as `A3 Scale 1: 500`.
2. Finds the nearby bar graphics and tick labels.
3. Stores the geometry needed for calibration.

Why it matters:

- Area is first computed in pixel space.
- This stage provides the conversion from pixels to meters.

Primary outputs:

- `outputs/scale_detection/scale_detection.json`
- visualization images in `outputs/scale_detection/visualizations/`

## Stage 1: Render the Original Page

Script:

- `scripts/footpath_pixel_pipeline.py`

Step label in script:

- `STEP 02 | Rendering PDF page`

What it does:

1. Opens the source PDF.
2. Renders the requested page to a raster image.
3. Saves that page image for all downstream pixel-based steps.

Created image:

- `outputs/footpath_pixel_pipeline/visualizations/stage_01_page.png`

Meaning:

- This is the clean raw page image.
- It contains the drawing exactly as rendered from the PDF.
- It should be treated as the clean base for probing and HSV/color analysis.

## Stage 2: Raw Footpath Color Mask

Script:

- `scripts/footpath_pixel_pipeline.py`

Step label in script:

- `STEP 03 | Building raw RGB-distance mask`

What it does:

1. Reads the footpath legend color from `legend_colors.json`.
2. Measures RGB distance between every page pixel and the target footpath color.
3. Marks matching pixels as white and all others as black.

Created image:

- `outputs/footpath_pixel_pipeline/visualizations/stage_02_raw_mask.png`

Meaning:

- White pixels are candidate footpath pixels.
- Black pixels are background.
- This is the first coarse segmentation and still contains false positives such as legend swatches and title-strip matches.

## Stage 3: Clean the Raw Mask

Script:

- `scripts/footpath_pixel_pipeline.py`

Step label in script:

- `STEP 04 | Removing page furniture regions`

What it does:

1. Removes the legend block using the legend bounding box.
2. Removes the bottom title strip where many unrelated color matches occur.
3. Applies light morphology to remove small speckles.

Created image:

- `outputs/footpath_pixel_pipeline/visualizations/stage_03_clean_mask.png`

Meaning:

- This is a cleaner binary representation of the footpath pixels.
- It is the main mask used for later reconstruction and width estimation.

## Stage 4: Component Analysis and Selection

Script:

- `scripts/footpath_pixel_pipeline.py`

Step labels in script:

- `STEP 05 | Connected-components analysis`
- `STEP 05B | Seed mask mode`

What it does:

1. Finds connected components in the cleaned mask.
2. Computes size, fill ratio, and span statistics.
3. Rejects obvious noise blobs or irrelevant components.
4. Chooses either the cleaned raw mask or the selected components as the downstream seed mask.

Created images:

- `outputs/footpath_pixel_pipeline/visualizations/stage_04_components_bbox.png`
- `outputs/footpath_pixel_pipeline/visualizations/stage_05_selected_mask.png`
- `outputs/footpath_pixel_pipeline/visualizations/stage_05b_seed_mask.png`

Meaning:

- `stage_04_components_bbox.png`: the page with bounding boxes around connected components.
- `stage_05_selected_mask.png`: only the components kept after filtering.
- `stage_05b_seed_mask.png`: the actual mask used for downstream processing.

## Stage 5: Baseline Overlay

Script:

- `scripts/footpath_pixel_pipeline.py`

Step label in script:

- `STEP 06 | Rendering baseline magenta overlay`

What it does:

- Blends the current seed mask onto the page as a visual sanity check.

Created image:

- `outputs/footpath_pixel_pipeline/visualizations/stage_06_overlay_footpath.png`

Meaning:

- This is a quick diagnostic view to check whether the mask is roughly aligned with the visible path in the drawing.

## Stage 6: Remove Overlapping Non-Surface Graphics

Script:

- `scripts/footpath_pixel_pipeline.py`

Step labels in script:

- `STEP 07 | Building non-surface overlap mask`
- `STEP 08 | Resolving overlaps and healing strip continuity`

What it does:

1. Detects dark text and contour annotations.
2. Detects green utility graphics.
3. Detects blue annotations.
4. Combines these into a non-surface mask.
5. Removes those overlaps from the candidate footpath mask.
6. Heals small cuts using morphological closing.

Created images:

- `outputs/footpath_pixel_pipeline/visualizations/stage_07_non_surface_mask.png`
- `outputs/footpath_pixel_pipeline/visualizations/stage_08_overlap_resolved_mask.png`

Meaning:

- `stage_07_non_surface_mask.png`: text, annotation, and utility overlays to ignore.
- `stage_08_overlap_resolved_mask.png`: footpath mask after those overlaps have been removed and continuity has been repaired.

## Stage 7: Corridor Reconstruction and Centerline Recovery

Script:

- `scripts/footpath_pixel_pipeline.py`

Step labels in script:

- `STEP 08C | Width-aware path reconstruction from resolved mask`
- `STEP 08D | Centerline best-fit reconstruction from raw support`
- `STEP 08G | Geodesic linking of broken centerline components`
- `STEP 08B | Corridor continuity enforcement`

What it does:

This is the main recovery stage when the footpath is interrupted by labels, linework, and drawing clutter.

Sub-steps:

1. Estimate an approximate half-width from a distance transform.
2. Dilate the resolved mask to reconstruct a continuous corridor.
3. Extract a centerline candidate from local maxima of the distance transform.
4. Link broken centerline pieces geodesically across likely interruption zones.
5. Reconstruct a path strip from that centerline.
6. Combine reconstructions and enforce corridor continuity.

Created images:

- `outputs/footpath_pixel_pipeline/visualizations/stage_08c_reconstructed_mask.png`
- `outputs/footpath_pixel_pipeline/visualizations/stage_08d_centerline_mask.png`
- `outputs/footpath_pixel_pipeline/visualizations/stage_08g_centerline_geodesic_mask.png`
- `outputs/footpath_pixel_pipeline/visualizations/stage_08e_centerline_reconstructed_mask.png`
- `outputs/footpath_pixel_pipeline/visualizations/stage_08f_combined_reconstruction.png`
- `outputs/footpath_pixel_pipeline/visualizations/stage_08b_corridor_mask.png`

Meaning:

- `stage_08c_reconstructed_mask.png`: width-aware strip reconstructed from the resolved mask.
- `stage_08d_centerline_mask.png`: centerline candidates extracted from the support region.
- `stage_08g_centerline_geodesic_mask.png`: broken centerline fragments linked across interruptions.
- `stage_08e_centerline_reconstructed_mask.png`: thickened strip reconstructed from the centerline.
- `stage_08f_combined_reconstruction.png`: union of the direct and centerline-based reconstructions.
- `stage_08b_corridor_mask.png`: final corridor retained after continuity filtering.

## Stage 8: Polygon Preview

Script:

- `scripts/footpath_pixel_pipeline.py`

Step label in script:

- `STEP 09 | Polygon preview from resolved mask`

What it does:

1. Extracts outer contours from the corridor mask.
2. Fills them to produce a polygon preview.

Created image:

- `outputs/footpath_pixel_pipeline/visualizations/stage_09_polygon_preview.png`

Meaning:

- This is a footprint-style preview of the final recovered footpath area.

## Stage 9: Pixel Pipeline Summary JSON

Script:

- `scripts/footpath_pixel_pipeline.py`

Step label in script:

- `STEP 10 | Writing diagnostics JSON`

Created file:

- `outputs/footpath_pixel_pipeline/footpath_pixel_pipeline.json`

Meaning:

- This contains parameters, component counts, pixel counts, and the full list of generated stage artifacts.

## Stage 10: Seed Placement on the Mask

Script:

- `scripts/path_seeds.py`

What it does:

1. Reads the raw mask and the raw page.
2. Excludes the legend region.
3. Places seeds greedily inside white mask pixels.
4. Keeps seeds away from the edge by optionally eroding the mask first.

Created images:

- `outputs/footpath_pixel_pipeline/visualizations/seeds_01_on_mask.png`
- `outputs/footpath_pixel_pipeline/visualizations/seeds_01_on_page.png`

Meaning:

- These show the initial seed points that will later be connected into a path graph.

## Stage 11: Connect Seeds into a Path Graph

Script:

- `scripts/path_connect_seeds.py`

What it does:

1. Builds candidate edges between nearby seeds.
2. Penalizes edges that leave the white mask.
3. Greedily builds a degree-constrained graph.
4. Selects a main connected component and identifies start and end nodes.

Created artifacts:

- `outputs/footpath_pixel_pipeline/visualizations/seeds_02_connected_on_mask.png`
- `outputs/footpath_pixel_pipeline/visualizations/seeds_02_connected_on_page.png`
- `outputs/footpath_pixel_pipeline/visualizations/seeds_02_graph.json`

Meaning:

- This is the first explicit centerline-like graph through the footpath region.

## Stage 12: Width Estimation from the Seed Graph

Script:

- `scripts/path_width_from_graph.py`

What it does:

1. Reuses the seed graph from stage 11.
2. Transfers each seed to the clean mask.
3. Probes along the normal direction on both sides.
4. Measures local width per seed.
5. Rejects width outliers robustly using MAD-based filtering.

Created artifacts:

- `outputs/footpath_pixel_pipeline/visualizations/seeds_03_width_on_clean_mask.png`
- `outputs/footpath_pixel_pipeline/visualizations/seeds_03_width_on_page.png`
- `outputs/footpath_pixel_pipeline/visualizations/seeds_03_width_stats.json`

Meaning:

- This is the first quantitative width model along the path.
- The JSON stores raw samples and summary statistics such as mean, median, and percentiles.

## Stage 13: Refine the Centerline

Script:

- `scripts/path_refine_centerline.py`

What it does:

1. Keeps the graph connectivity fixed.
2. Moves active seed coordinates toward the center of the path.
3. Uses a distance-transform objective.
4. Uses midpoint regularization to keep the line smooth.
5. Adds a curvature penalty to discourage sharp turns.

Created artifacts:

- `outputs/footpath_pixel_pipeline/visualizations/seeds_04_refined_graph.json`
- `outputs/footpath_pixel_pipeline/visualizations/seeds_04_refined_on_clean_mask.png`
- `outputs/footpath_pixel_pipeline/visualizations/seeds_04_refined_on_page.png`
- `outputs/footpath_pixel_pipeline/visualizations/seeds_04_refine_stats.json`

Meaning:

- This is the optimized centerline used as the basis for later area calculations and visual overlays.
- `seeds_04_refined_on_page.png` is the image with the optimized centerline drawn on the map.

## Stage 14: Rasterize the Final Thick Path

Script:

- `scripts/path_final_rasterize.py`

What it does:

1. Reads the refined centerline.
2. Reads width estimates from stage 12.
3. Assigns either global or local width to each segment.
4. Draws a thick raster representation of the footpath.

Created artifacts:

- `outputs/footpath_pixel_pipeline/visualizations/seeds_05_final_path_raster.png`
- `outputs/footpath_pixel_pipeline/visualizations/seeds_05_final_path_on_page.png`
- `outputs/footpath_pixel_pipeline/visualizations/seeds_05_final_path.json`

Meaning:

- This produces a filled final path model from the refined centerline and width estimates.

## Stage 15: Sigma-Based Width Confidence Map

Script:

- `scripts/path_width_confidence_map.py`

What it does:

1. Reads the refined centerline and measured widths.
2. Computes mean and standard deviation of width.
3. Colors each segment by sigma distance from the mean.

Current class definition:

- Green: `|w - mean| <= 1 sigma`
- Yellow: `1 sigma < |w - mean| <= 2 sigma`
- Red: `|w - mean| > 2 sigma` or missing

Created artifacts:

- `outputs/footpath_pixel_pipeline/visualizations/seeds_06_width_confidence_on_clean_mask.png`
- `outputs/footpath_pixel_pipeline/visualizations/seeds_06_width_confidence_on_page.png`
- `outputs/footpath_pixel_pipeline/visualizations/seeds_06_width_confidence.json`

Meaning:

- This is the primary visual quality map for how stable or unusual each local width measurement is.

## Stage 16: Error-Aware Area from the Refined Centerline

Script:

- `scripts/path_area_error_aware.py`

What it does:

1. Integrates width along the refined path.
2. Produces a nominal area.
3. Produces uncertainty-aware area models based on sigma classes.
4. Converts pixel area into square meters using the detected scale.

Created artifact:

- `outputs/footpath_pixel_pipeline/visualizations/seeds_07_area_error_aware.json`

Meaning:

- This is the first uncertainty-aware area summary built directly from the centerline plus width model.

## Stage 17: First-Hit-Black Area Model

Script:

- `scripts/path_area_first_hit_black.py`

What it does:

This is the current alternative area model used in the later iterations of the workflow.

Core idea:

1. Start from the optimized centerline in `seeds_04_refined_graph.json`.
2. Sample dense points along the centerline.
3. At each sample, cast a normal probe left and right.
4. Stop when a black boundary is reached in the clean probe image.
5. Use the measured width to integrate area with the trapezoid rule.

Important implementation details:

- Uses `stage_01_page.png` as the clean probing image.
- Uses `seeds_04_refined_on_page.png` only as the visualization base.
- Uses `stage_02_raw_mask.png` as corridor support.
- Snaps sample centers back onto support if needed.
- Tries to rescue unrealistically short widths.
- Rejects very wide text-hit outliers statistically.
- Produces sigma-colored thick bars on top of the centerline image.

Created artifacts:

- `outputs/footpath_pixel_pipeline/visualizations/seeds_08_first_hit_area.json`
- `outputs/footpath_pixel_pipeline/visualizations/seeds_08_first_hit_width_overlay.png`

Meaning:

- `seeds_08_first_hit_width_overlay.png` is the current diagnostic image showing:
  - the refined centerline,
  - faint wiggle-width variations,
  - thick sigma-colored median width bars.

- `seeds_08_first_hit_area.json` is the current area output, including:
  - nominal area,
  - width statistics,
  - sigma-class bar counts,
  - min/nominal/max area envelope.

## Current Image/Artifact Inventory by Stage

### Legend and scale

- `outputs/legend_colors/legend_colors.json`
- `outputs/legend_colors/visualizations/...`
- `outputs/scale_detection/scale_detection.json`
- `outputs/scale_detection/visualizations/...`

### Footpath pixel pipeline

- `stage_01_page.png`
- `stage_02_raw_mask.png`
- `stage_03_clean_mask.png`
- `stage_04_components_bbox.png`
- `stage_05_selected_mask.png`
- `stage_05b_seed_mask.png`
- `stage_06_overlay_footpath.png`
- `stage_07_non_surface_mask.png`
- `stage_08_overlap_resolved_mask.png`
- `stage_08c_reconstructed_mask.png`
- `stage_08d_centerline_mask.png`
- `stage_08g_centerline_geodesic_mask.png`
- `stage_08e_centerline_reconstructed_mask.png`
- `stage_08f_combined_reconstruction.png`
- `stage_08b_corridor_mask.png`
- `stage_09_polygon_preview.png`
- `footpath_pixel_pipeline.json`

### Seed / centerline / width / area pipeline

- `seeds_01_on_mask.png`
- `seeds_01_on_page.png`
- `seeds_02_connected_on_mask.png`
- `seeds_02_connected_on_page.png`
- `seeds_02_graph.json`
- `seeds_03_width_on_clean_mask.png`
- `seeds_03_width_on_page.png`
- `seeds_03_width_stats.json`
- `seeds_04_refined_graph.json`
- `seeds_04_refined_on_clean_mask.png`
- `seeds_04_refined_on_page.png`
- `seeds_04_refine_stats.json`
- `seeds_05_final_path_raster.png`
- `seeds_05_final_path_on_page.png`
- `seeds_05_final_path.json`
- `seeds_06_width_confidence_on_clean_mask.png`
- `seeds_06_width_confidence_on_page.png`
- `seeds_06_width_confidence.json`
- `seeds_07_area_error_aware.json`
- `seeds_08_first_hit_area.json`
- `seeds_08_first_hit_width_overlay.png`

## Current Interpretation of the Workflow

The process is best understood as:

1. **Identify the footpath symbolically** using the legend.
2. **Find the footpath pixels** on the page using that legend color.
3. **Remove clutter and reconstruct continuity** where the path is obscured by text and annotations.
4. **Infer a reliable centerline** through the recovered corridor.
5. **Measure width along that centerline** rather than trying to trust a single filled region blindly.
6. **Convert width plus length into area** using integration.
7. **Attach uncertainty** using sigma-based classes.

This is why the workflow ends up with both a reconstructed area mask and a centerline-based width model: the area mask is useful for visual recovery, but the centerline-based formulation is more auditable for quantity calculations.

## Current Best Summary Output

At the moment, the most decision-useful outputs are:

- `outputs/legend_colors/legend_colors.json`
- `outputs/scale_detection/scale_detection.json`
- `outputs/footpath_pixel_pipeline/footpath_pixel_pipeline.json`
- `outputs/footpath_pixel_pipeline/visualizations/seeds_04_refined_on_page.png`
- `outputs/footpath_pixel_pipeline/visualizations/seeds_06_width_confidence_on_page.png`
- `outputs/footpath_pixel_pipeline/visualizations/seeds_08_first_hit_width_overlay.png`
- `outputs/footpath_pixel_pipeline/visualizations/seeds_08_first_hit_area.json`

These together show:

- how the footpath was identified,
- what geometry was reconstructed,
- what centerline was used,
- how widths vary,
- and what final area and uncertainty were derived.
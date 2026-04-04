# Drawing Data Extraction Spec

## Goal
Extract reliable data from construction drawing PDFs so the following quantities can be calculated:

1. Total area of 150 mm concrete pavement
2. Volume of 150 mm thick GAP65
3. Subsoil drain length
4. Flush nib length
5. Footpath area

## Documents in Scope
- `examples/Joal 502-General Plan.pdf`
- `examples/Joal 502 -Longitudinal Section.pdf`
- `examples/Joal 502 Typical Cross section pavement.pdf`
- `examples/Joal 502.pdf`

## What Must Be Extracted
Use a structured, auditable model instead of plain OCR text.

### 1) Feature Geometry
- Closed polygons for area-based features:
  - concrete pavement zones
  - footpath zones
  - GAP65 zones (where represented as areas)
- Polylines for length-based features:
  - subsoil drain
  - flush nib

### 2) Feature Classification
Each geometry must be classified as one of:
- Concrete pavement (with thickness attribute)
- GAP65 (with thickness attribute)
- Subsoil drain
- Flush nib
- Footpath

Classification signals:
- Nearby labels/callouts
- Legend mappings
- Line style/symbol patterns
- Notes and standard abbreviations

### 3) Dimensions and Units
- Confirm drawing units (typically meters)
- Capture or infer scale if geometry is rasterized
- Attach thickness values to the correct material/system

### 4) Scope and De-duplication
- Restrict to project boundary / relevant extents
- Use latest revision only
- Prevent double counting across plan/detail/section sheets

## Minimum Data Schema
Each extracted item should include:
- Item ID
- Source sheet
- Feature type
- Geometry type (`polygon` or `polyline`)
- Geometry coordinates
- Measured area or length
- Unit
- Thickness (if applicable)
- Confidence score
- Evidence (matched text, legend key, style match)

## Quantity Formulas

### 1) Concrete Pavement Area
- Sum area of all polygons where:
  - feature type = concrete pavement
  - thickness = 150 mm

### 2) GAP65 Volume (150 mm)
- `Volume = Total GAP65 Area x 0.15 m`
- If area is not explicit and only length/width exists:
  - `Area = Length x Width`
  - `Volume = Area x 0.15 m`

### 3) Subsoil Drain Length
- Sum polyline lengths where feature type = subsoil drain

### 4) Flush Nib Length
- Sum polyline lengths where feature type = flush nib

### 5) Footpath Area
- Sum polygon areas where feature type = footpath
- If represented as boundary lines only, construct closed polygons first

## Preferred Extraction Pipeline (Reliability First)

### Step 1: Vector-First Parsing
- Parse vector entities from PDF (paths/lines/text)
- Use drawing coordinates directly when available
- Prefer this over OCR-only methods

### Step 2: Text + Annotation Parsing
- Extract all text with coordinates
- Detect relevant keywords:
  - `150 mm`, `concrete`, `conc`, `GAP65`, `subsoil drain`, `flush nib`, `footpath`

### Step 3: Legend/Symbology Mapping
- Build mapping per sheet:
  - symbol/line style -> feature type
- Apply this mapping to unlabeled geometry

### Step 4: Spatial Linking Rules
- Link labels to nearest candidate geometry within thresholds
- Resolve conflicts using scoring:
  - label proximity
  - style/legend match
  - keyword confidence

### Step 5: Validation and QA
- Unit sanity checks
- Cross-sheet consistency checks
- Outlier detection
- Manual review queue for low-confidence items

## Reliability Risks and Mitigations

### Risk: Wrong Sheet Used for Quantity Takeoff
- Mitigation: define sheet priority rules (plans for quantities, sections/details for attributes)

### Risk: Double Counting Across Sheets
- Mitigation: deduplicate by geometry identity and source hierarchy

### Risk: Raster Content in PDF
- Mitigation: detect raster pages, apply calibrated OCR/line detection, lower confidence

### Risk: Ambiguous Abbreviations
- Mitigation: build project-specific dictionary from legend + general notes

### Risk: Thickness Not Properly Linked
- Mitigation: require explicit linkage via callout/note/section reference

## Workflow Before Implementation
1. Define quantity dictionary (what is included/excluded for each item).
2. Finalize extraction schema fields and confidence rules.
3. Define sheet priority and de-duplication rules.
4. Set acceptance criteria (e.g., within 2% of manual check).
5. Run pilot extraction on one sheet and compare against manual measurements.

## Suggested Next Deliverables
- Keyword dictionary + regex patterns
- Geometry classification rule set
- Confidence scoring formula
- First-pass implementation plan for current PDFs

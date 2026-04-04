# Extraction Implementation Plan

## Best Extraction Strategy (Most Reliable First)
Use a layered pipeline instead of single-shot OCR.

### 1. Vector-first extraction
- Parse PDF vector primitives: lines, polylines, paths, and text objects.
- Keep geometry in drawing coordinates.
- Treat this as the primary source because it is significantly more reliable than OCR for engineering drawings.

### 2. Text and annotation parsing
- Extract all text with coordinates.
- Match keywords and patterns near geometry:
  - 150 mm
  - conc
  - concrete
  - GAP65
  - subsoil drain
  - flush nib
  - footpath

### 3. Legend and symbology mapping
- Build a per-sheet legend map:
  - line style, color, or symbol to feature type
- Use this mapping to classify unlabeled geometry on the same sheet.

### 4. Rule-based spatial linking
- Link labels to nearest candidate geometry within configurable thresholds.
- Resolve conflicts with weighted scoring:
  - distance
  - style or legend match
  - keyword strength

### 5. Validation layer
- Unit sanity checks.
- Cross-sheet consistency checks.
- Outlier detection for unexpectedly large or small segments.
- Manual review queue for low-confidence items.

## How To Proceed: Phased Delivery Plan

## Phase 0: Scope Lock (0.5 to 1 day)
Output:
- Quantity dictionary with include and exclude rules for all five quantities.
- Sheet priority rules (which sheets are authoritative for quantities vs attributes).
- Acceptance criteria (for example, plus or minus 2 percent from manual check).

Tasks:
1. Freeze definitions for concrete pavement, GAP65, subsoil drain, flush nib, and footpath.
2. Confirm units and revision handling.
3. Define what to do when thickness is missing or ambiguous.

## Phase 1: Subset Pilot (recommended start) (2 to 4 days)
Start with one sheet and two quantity types:
- Subsoil drain length
- Flush nib length

Reason:
- Line-based quantities are simpler than polygon closure.
- They validate vector parsing, text extraction, and label-linking quickly.

Deliverables:
1. PDF parser that exports:
   - vector entities
   - text entities with coordinates
2. First rule engine for line feature classification.
3. CSV or JSON output with length totals plus evidence and confidence.
4. Small manual comparison report against one marked-up sheet.

Exit criteria:
- At least 90 percent precision on detected target lines.
- Length error within agreed tolerance on pilot sheet.

## Phase 2: Area Features (2 to 5 days)
Add polygon-capable quantities:
- Footpath area
- Concrete pavement area (150 mm)

Tasks:
1. Detect and close boundary loops into polygons.
2. Classify polygons via labels and legend style matching.
3. Add overlap and duplicate handling.

Deliverables:
- Area totals per feature type.
- Per-polygon confidence and evidence.
- Manual review list for unclosed or ambiguous boundaries.

Exit criteria:
- Area deviation within tolerance on pilot scope.
- Stable handling of common geometry edge cases.

## Phase 3: Derived Volume for GAP65 (1 to 2 days)
Tasks:
1. Detect GAP65 areas where explicit.
2. Apply thickness conversion:
   - volume equals area multiplied by 0.15
3. If only length and width are available, derive area first.

Deliverables:
- GAP65 volume result with transparent formula trace.

Exit criteria:
- All volume values trace back to source geometry and thickness rule.

## Phase 4: Multi-sheet Integration and QA (2 to 4 days)
Tasks:
1. Add cross-sheet de-duplication logic.
2. Add consistency checks across plan, section, and notes.
3. Add outlier flags and confidence thresholds for manual review.

Deliverables:
- Project-level totals for all five quantities.
- QA report with flagged items and reasons.

Exit criteria:
- End-to-end run without manual edits except flagged queue.

## Phase 5: Production Hardening (optional) (2 to 5 days)
Tasks:
1. Package as repeatable command-line workflow.
2. Add regression test set with known expected outputs.
3. Add baseline performance metrics and failure alerts.

Deliverables:
- Repeatable runbook.
- Test harness and sample fixtures.

## Suggested Technical Shape
- Parser layer: vector and text extraction from PDF.
- Normalization layer: coordinate, unit, and entity normalization.
- Classification layer: rules plus confidence scoring.
- Quantification layer: area, length, volume calculations.
- QA layer: validations, outliers, and review queue generation.
- Output layer: machine-readable JSON and human-readable summary table.

## Data Outputs To Implement Early
1. entities.json
   - raw vectors and text by sheet
2. features.json
   - classified geometry with confidence and evidence
3. quantities.json
   - totals for the five requested metrics
4. review_queue.json
   - low-confidence and conflicting items

## Practical First Sprint Plan
Day 1:
- Build PDF entity extraction for one target sheet.
- Export vectors and text with coordinates.

Day 2:
- Implement keyword matching and nearest-label linking for line features.
- Compute subsoil drain and flush nib totals.

Day 3:
- Add confidence scoring and manual review queue.
- Validate against manual measurements.

Day 4:
- Tune thresholds and style-matching rules.
- Freeze pilot rules and prepare for polygon phase.

## Decision Gates
1. After Phase 1 pilot:
- If accuracy is strong, move to polygon features.
- If weak, improve legend mapping and label-linking before expanding scope.

2. After Phase 2 polygon rollout:
- If polygon closure is unreliable, add assisted review tooling rather than forcing full automation.

3. Before production:
- Require stable results on at least three drawing sets.

## Immediate Next Actions
1. Select one pilot PDF sheet from the examples folder.
2. Confirm which two line features are easiest to validate manually.
3. Lock acceptance thresholds for pilot sign-off.
4. Start Phase 1 implementation.

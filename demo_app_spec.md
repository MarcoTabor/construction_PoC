# Proof of Concept (PoC) Demo App: Task-Driven Geometry Workspace

## 1. Vision & Core Concept
The overarching goal of this application is to demonstrate trust, auditability, and engineering logic. While the backend mathematically computes geometry and quantities (via the modular extraction pipeline), the frontend product story must prove to the user that the system *understands* the drawings, *connects* multiple sheets, and keeps the human *in control*. 

**Core Product Mantra:** "I upload messy drawings → the system understands them → suggests what to do → shows me exactly what it found → I confirm → I get quantities with full traceability."

---

## 2. Main UI Layout & Wireframe

The workspace is divided into four main regions to separate document management, visual evidence, task execution, and auditability.

```text
+----------------+-------------------------------------------------+-------------------------+
|                |                                                 |                         |
|  DOCUMENTS     |                 DRAWING WORKSPACE               |  TASK & INSIGHT PANEL   |
|  (Left Panel)  |                 (Centre - Primary)              |  (Right Panel)          |
|                |                                                 |                         |
|  [ ST3-P07  ]  |   +---------------------------------------+     |  Current Task:          |
|  [   Plan   ]  |   |                                       |     |  Confirm Scope          |
|                |   |       [ Interactive Canvas area ]     |     |                         |
|  [ ST3-L06  ]  |   |       - Zoom / Pan controls           |     |  Suggested Actions:     |
|  [Longitud. ]  |   |       - Highlighted Geometry          |     |  [ ✅ Confirm Joal   ] |
|                |   |       - Opacity / Layer Toggles       |     |  [ ✅ Confirm Path   ] |
|  [  DE04    ]  |   |                                       |     |                         |
|  [ Section  ]  |   |                                       |     |                         |
|                |   +---------------------------------------+     |  Confidence: High       |
|                |                                                 |                         |
+----------------+-------------------------------------------------+-------------------------+
|                                    AUDIT TIMELINE (Bottom Panel - Expandable)              |
|                                                                                            |
|  [Step 1: Upload] -> [Step 2: Scope] -> [Step 3: Geometry] -> [Step 4: Quantities]         |
|   (Clicking a step shows the image, explanation, and extracted data for that step)         |
+--------------------------------------------------------------------------------------------+
```

---

## 3. End-to-End Demo Workflow

The demo follows a strong progressive disclosure flow. The system suggests, the user confirms, and the system continues.

### Step 1: Upload & Auto-Analysis
*   **UI:** User uploads a PDF set. 
*   **System Action:** System responds with "Detected construction drawing set."
*   **Visual:** Populates the Left Panel with thumbnails classified into Plan (`ST3-P07`), Longitudinal (`ST3-L06`), and Section (`DE04`).

### Step 2: Scope Selection
*   **UI:** The Right Panel shows "Suggested Analysis Scope: Stage 3 extent identified."
*   **Visual:** Centre canvas displays `ST3-P07`. The target region is highlighted, and the rest of the plan is dimmed out.
*   **User Action:** User clicks **✅ Confirm scope** (saves state: `scope.active = "ST3-P07_stage3_region"`).

### Step 3: Engineering Context & Decision Fork (Slope Check)
*   **UI:** Right Panel detects vertical profile. Shows the system considering multiple approaches:
    *   *(Auto-selected)* **Plan-based take-off:** Fast, suitable for this case.
    *   *(Skipped)* **Full alignment reconstruction:** More detailed, not required here.
*   **Visual:** Centre canvas switches to `ST3-L06` (Longitudinal), highlighting the elevation profile.
*   **System Meaning:** "Slope assessed → impact negligible → plan-based method selected." (Shows system intelligence rather than blindly executing).
*   **User Action:** User clicks **✅ Accept approach** to proceed.

### Step 4: Geometry Detection (Plan)
*   **UI:** Right Panel text: "Matched legend style to plan geometry." Shows a micro-progression: *Legend item → Visual pattern → Matched region*. 
    *   *Friction Moment:* System flags a minor ambiguity: "Multiple candidate footpath regions detected near intersection — selecting most likely contiguous path." (Proves the system handles real-world messiness).
*   **Visual:** Canvas switches back to `ST3-P07`. Carriageway and footpath are highlighted.
*   **User Action:** User clicks **✅ Confirm Joal** and **✅ Confirm Footpath**.

### Step 5: Cross-Section Understanding (Meaning Linking)
*   **UI:** Right panel explains: "Mapped plan geometry to construction intent." Explicitly displays associations:
    *   *Plan (Joal Polygon) ↔ Section (Concrete Layer) ↔ Meaning (Carriageway surface)*
    *   *Plan (Boundary) ↔ Section (Flush nib) ↔ Meaning (Edge detail)*
    *   *Plan (Inner edge) ↔ Section (Subsoil drain) ↔ Meaning (Drainage element)*
*   **Visual:** Canvas shows `DE04` (Cross-section) highlighting concrete layers, GAP65, flush nib, and drain.
*   **User Action:** User confirms the property mapping (e.g., width 5.5m, thickness 150mm).

### Step 6: Derived Geometry
*   **UI:** Right panel lists derived lines: 
    *   Centerline (Derived from polygon)
    *   Flush Nib (Plan boundary)
    *   Subsoil Drain (Interpreted: Derived from typical section position and aligned to inner carriageway edge).
*   **Visual:** Back on `ST3-P07`, these 3 lines are drawn over the plan in distinct colors. For Subsoil Drain, a mini Section view highlights the drain while the Plan view shows the inferred line.
*   **User Action:** User confirms all derived reference lines.

### Step 7: Quantity Calculation Tasks ("The Money Moment")
The system walks the user through individual quantity tasks visually. These are presented sequentially as compelling visual cards to prove understanding.
*   **Concrete Pavement Area:** Visual card shows highlighted plan geometry + linked section snippet + formula + numeric area. User confirms.
*   **GAP65 Volume:** Visual card shows Centreline + `DE04` spec + calculation (Length × Width × Thickness). 
    *   *Intelligence Reuse Bubble:* "Reusing previously confirmed centerline for GAP65 multiple quantities." (Signals efficiency).
*   **Footpath Area:** Visual card shows footpath polygon -> Output: Numeric Area.
*   **Flush Nib Length:** Visual card shows boundary line (Plan) + formula + numeric length.
*   **Subsoil Drain Length:** Visual card shows inner line -> System reiterates: "Interpreted from Section + aligned to Plan". User confirms sum.

### Step 8: Results Dashboard (Final Validated Output)
*   **UI:** A polished summary panel. The top 2 results (e.g., Concrete Area, GAP65 Volume) are displayed as large hero cards. Below that, a clean table shows Item, Value, Confidence, and Source.
*   **Visual:** Each row features a mini-thumbnail snippet or a visual geometric indicator (e.g., a line icon for lengths, a polygon icon for areas).
*   **Interaction:** Every row is clickable, transitioning gracefully into the Audit Trail.

### Step 9: Audit Trail (Critical Feature - Causality)
*   **UI:** Expanding a result from the dashboard (or bottom timeline) shows the full evidence chain with explicit causality ("*This* → *therefore* → *this*").
*   **Example (GAP65 Volume):**
    1.  *Identified Joal centerline (ST3-P07)* → Shows cropped canvas image with highlight.
    2.  *Therefore used to derive length for GAP65 volume.*
    3.  *Extracted GAP65 spec (DE04)* → Shows cropped section image with 150mm depth and 6.0m width highlighted.
    4.  *Applied calculation* → Length × width × depth = Volume.
    5.  *User confirmed assumptions* → Shows the audit timestamp.

---

## 4. UX Principles to Enforce

1.  **Show, don’t tell:** If the UI makes a claim, the centre canvas *must* highlight the geometric evidence.
2.  **Progressive Disclosure:** Do not dump all quantities at once. The step-by-step confirmation builds the habit of trust.
3.  **Confidence Labelling:** Use terms like "Detected" (measured strictly from plan), "Derived" (calculated from multiple detected points), and "Inferred" (projected based on assumptions). 
4.  **Engineering Logic over Math Redundancy:** Even if 5 quantities can be determined mathematically from a single centerline, UI must anchor each result back to its source drawing (Plan, Section, Profile) to prove *understanding*.

---

## 5. Technical Implementation Plan (For the PoC)

To build this quickly as a front-end prototype connected to our existing Python modules:

### Phase 1: Interactive UI Shell (Structured Data)
*   **Tech Stack:** React/Vue (Frontend), Vite, TailwindCSS for layout.
*   **Canvas Tech:** OpenLayers, Leaflet, or a simple HTML5 Canvas library to display the raster PDFs and draw vector overlays (GeoJSON/Canvas API).
*   **Goal:** Build the UI layout (Left, Centre, Right panels). Utilize precomputed structured outputs (static JSON payloads derived from our pipeline) for the demo flow. Do not overcomplicate the backend for this proof of concept. Stick exclusively to the Joal 502 / Stage 3 extent.

### Phase 2: Python Backend / Bridge
*   **Tech Stack:** FastAPI or Flask.
*   **Functionality:** Expose the `scripts/pipeline_joal_geometry.py` modules as REST endpoints (or run them offline to dump localized JSONs for the UI).
*   **Data Contracts:** Ensure the frontend consumes the standard outputs:
    *   `curves contract` (`centerline_yx`, `inner_line_yx`, `outer_line_yx`)
    *   `metrics contract` (`lengths`, `area`, `measurement_policy`)
    *   `visual manifest` (for rendering coordinates correctly over the plan images)

### Phase 3: State Management & Timeline Integration
*   **Functionality:** Implement the "Timeline" and "Audit Trail". 
*   **Mechanics:** Every time a user clicks "Confirm" in the Right Panel, a snapshot (bounding box + coordinates + context) is pushed to an audit log state array, which is later rendered in Steps 8 & 9.

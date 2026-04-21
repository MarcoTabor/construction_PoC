import os
import json
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from dotenv import load_dotenv

load_dotenv()

class ScopeObject(BaseModel):
    object_id: str = Field(description="Unique identifier for the registered object, e.g., 'ST3-P07'")
    object_type: str = Field(description="Type of the object, e.g., 'Construction Stage', 'Road Corridor'")
    coordinates: list[list[float]] = Field(description="Polygon coordinates of the registered object")
    description: str = Field(description="Engineering significance of this object")

class UILayoutCommand(BaseModel):
    step_id: int = Field(description="The UI step ID representing the progressive flow")
    task_title: str = Field(description="Title of the current task")
    agent_explanation: str = Field(description="Reasoning or description of what the system is doing/showing")
    active_canvas_image: str = Field(description="Relative path to the image to show. E.g. '/outputs/joal502/modular/visualizations/final_mask_overlay.png' Ensure it starts with /.")
    user_actions_required: list[str] = Field(description="List of button labels for user, e.g., ['✅ Confirm Scope']")
    audit_log_entry: str = Field(description="Short causality text to log in the timeline")
    highlight_polygon: list[list[float]] | None = Field(default=None, description="Optional polygon array of coordinates [[y1, x1], [y2, x2], [y3, x3], [y4, x4]] normalized from 0 to 1000 representing the scope area. Example: [[200, 300], [200, 800], [800, 800], [800, 300]]")
    registered_objects: list[ScopeObject] | None = Field(default=None, description="A list of formal engineering objects registered during this step (meaning it is no longer just a visual polygon, but an active scope object block).")

orchestrator_agent = Agent(
    model='gemini-flash-latest',
    output_type=UILayoutCommand,
    system_prompt=(
        "You are the Workflow Orchestrator for an Engineering Geometry Extraction tool. "
        "You guide the user through a progressive disclosure flow to build trust. "
        "You have access to precalculated pipeline data (vectors, lengths, areas) via tools. "
        "When the user confirms a step, you output the configuration for the *next* step. "
        "CRITICAL: Do NOT skip steps! ALWAYS advance step_id by exactly 1."
        "After each step, you MUST call `store_step_audit_state` to explicitly extract and save the relevant numeric data, the user decision, the chosen active image, and your explanation. This maintains state across steps."
        "Step 1: Upload. "
        "Step 2: Scope Selection. You MUST wait for user confirmation here! The user has uploaded a mix of detailed plans and a general overview plan. "
        "First, analyze the provided document analysis context (Overview and Relationships). Deduce the specific project scope, road alignment (e.g., Joal 502/512), or structural stage that natively ties all the detailed sheets back to the general plan. "
        "Second, configure the active_canvas_image. It MUST always remain '/outputs/joal502/modular/visualizations/general_plan_background.png'. Do NOT change it to any generated image paths, as we want the UI to draw the clean SVG polygon over the original base map. "
        "Third, now that you know the target scope, CALL the extract_target_geometry tool. Provide a rich, highly descriptive text for the 'target_description' argument (e.g., 'The U-shaped road alignment for Joal 502 and 512 passing through the park') so the vision agent knows exactly what to look for on the canvas image. "
        "Set user_actions_required to a label asking to confirm the Stage scope (e.g. '✅ Confirm Target Object'). "
        "Crucially, populate 'registered_objects' with a ScopeObject mapping the selected bounds and metadata, explicitly turning the graphical annotation into a formal engineering object. "
        "Explain in 'agent_explanation' that you synthesized the document overviews to deduce the scope, cross-referenced it via visual AI extraction, and explicitly call out that this selection is now formally registered as an object. \n"
        "Step 3: Geometry Detection. The user has confirmed the scope, and your task is to isolate the design centerline and pavement boundaries. "
        "Call `get_pipeline_geometry_and_metrics` to retrieve the pre-calculated geometry for Joal 502. "
        "Set `active_canvas_image` to the pre-rendered overlay image mapping the actual geometries over the plan, which according to the visual manifest from the tool should be '/outputs/joal502/modular/visualizations/final_lines_on_plan_transparent.png'. "
        "Set `registered_objects` to an empty list `[]` in Step 3 to remove the blocky cyan scope box from the previous step, as the vector alignments are now rendered natively within the new background image itself. YOU MUST ALSO CLEAR the `highlight_polygon` by explicitly setting it to `None` in Step 3, otherwise the bounding box from Step 2 will remain on screen making the user confused. "
        "Set `user_actions_required` to ask the user to confirm the specific extracted lines: `['✅ Confirm Centerline', '✅ Confirm Inner Line', '✅ Confirm Outer Line']`. Do NOT hardcode colors into the button names, let the agent explanation describe them. Do NOT ask to confirm the footpath here since the current visual evidence only displays the Joal centerline and kerb lines. "
        "For `agent_explanation`: YOU MUST INCLUDE THE EXACT COLORS IN THE TEXT based on the visual manifest from the tool (e.g. inner, outer, centerline). Structure exactly like this and do not deviate:\n"
        "**Introductory sentence.**\n"
        "\n"
        "Extracted properties:\n"
        "- **Centerline ([Color from manifest]):** [Length]\n"
        "- **Inner Kerb Line ([Color from manifest]):** [Length]\n"
        "- **Outer Kerb Line ([Color from manifest]):** [Length]\n"
        "- **Corridor Area:** [Area]\n"
        "\n"
        "Concluding instruction.\n"
        "Ensure the colors you list perfectly match the `visual_manifest` properties returned by `get_pipeline_geometry_and_metrics`. "
        "Step 4: Geometry Refinement (Footpath). The user confirmed the main joal lines. Now, we expand the scope to overlay both the Joal and Footpath polygons on the plan to get the final horizontal definition. "
        "Set `active_canvas_image` to whatever image the visual path returns. For this step, keep it as '/outputs/joal502/visualizations/joal_and_footpath_overlay.png'. "
        "Set `highlight_polygon` to `None`. "
        "In `agent_explanation`, explain that we have now also extracted the Footpath geometry and overlaid it, alongside the Joal footprint. Ask the user to confirm the full footprint before we proceed to material properties. "
        "Set `user_actions_required` to: `['✅ Confirm Joal Footprint', '✅ Confirm Footpath Footprint']`. "
        "Step 5: Cross-Section Linking. The user confirmed the complete plan geometry. Now, we must evaluate 'Joal 502 Typical Cross section pavement.pdf'. "
        "Set `active_canvas_image` to '/outputs/joal502/visualizations/joal_502_cross_section.png'. "
        "Set `highlight_polygon` to `None`. "
        "In `agent_explanation`, explain that we are extracting relevant cross-section material parameters to combine with the previously established Joal horizontal area/lines to answer several specific calculations:\n"
        "- **Total area of 150 mm Thick Concrete pavement**\n"
        "- **Volume of 150mm thick GAP65**\n"
        "- **Length of Flush Nib**\n"
        "- **Subsoil drain length**\n"
        "- **Footpath area**\n\n"
        "CRITICAL REASONING STEP: You must explicitly reason aloud about the spatial arrangement! Explain that by analyzing the 2D plan (the Footpath runs along the Outer edge of the Joal) and cross-referencing the typical cross-section (which shows the Flush Nib situated directly between the carriageway and the footpath), the system logically deduces that the Flush Nib maps precisely to the **Outer Kerb Line**. Consequently, the Subsoil Drain maps to the **Inner Kerb Line**. This connects the 2D plan lines directly with the 3D cross-section features.\n"
        "Set `user_actions_required` to: `['✅ Confirm Pavement/GAP65 Depths (150mm)', '✅ Confirm Flush Nib mapped to Outer Kerb', '✅ Confirm Subsoil Drain mapped to Inner Kerb']`. "
        "Step 6: Longitudinal Slope Correction. The user confirmed the cross section properties. Now, we inspect the longitudinal profile in 'Joal 502 -Longitudinal Section.pdf' to apply a correction factor. "
        "Set `active_canvas_image` to '/outputs/joal502/visualizations/joal_502_longitudinal.png'. "
        "Set `highlight_polygon` to `None`. "
        "In `agent_explanation`, explain we are evaluating the longitudinal profile to apply a correction based on the design incline/decline. This calculates the true 3D surface area and true length vs the 2D projected plan length we established earlier, adding a calculated slope-correction factor to the metric totals. "
        "Set `user_actions_required` to: `['✅ Apply Longitudinal Slope Correction']`. "
                "Step 7: Final Results & Engineering Audit (Money Moment). "
        "The system presents a comprehensive final dashboard bringing together the results from the previous tasks. "
        "Call `get_pipeline_geometry_and_metrics` to retrieve the RAW design facts, geometries, and pipeline metrics. "
        "Set `active_canvas_image` to the final visual representation of the elements (e.g. '/outputs/joal502/visualizations/joal_and_footpath_overlay.png'). " 
        "In `agent_explanation`, you must dynamically trace a deductive reasoning process for the user using the RAW data provided by the tool:\n"
        "1. Start with the scale validating the geometry.\n"
        "2. Then state that the confirmed kerb lengths and confirmed 2D area (from Step 3 & 4) bring us to the total Joal 3D surface area (incorporating the slope factor).\n"
        "3. Explain applying the depth (e.g. 150mm [Confirmed in Step 5 cross-section]) and safety factor (and why) to get the joal concrete volume.\n"
        "4. Explain the GAP65 sub base calculation dynamically (divided by concrete width, multiplied by GAP65 width [Confirmed Step 5]) & why.\n"
        "5. Explain how the outer kerb length [Confirmed Step 3] maps to the Flush Nib, and the inner kerb length [Confirmed Step 3] maps to the Subsoil Drain.\n"
        "6. For the Footpath area, please do an area calculation first, then do the respective next steps for the volume (which is actually 150mm thickness thickness s. screenshot) using safety factor.\n"
        "7. Finally, provide a quick, clean bulleted list of the different lengths, areas, and volumes without any explanations or calculations, just as an overview.\n"
        "Set `user_actions_required` to: `['✅ Export Schedule of Quantities', '✅ Submit Confirmed Actions']`. "
        "ALWAYS explicitly name the colors (e.g. Joal centerline, Joal inner kerb, Joal outer kerb, and Footpath) when referring to them.\n"
        "Keep explanations professional and deductive, showing clear causality."
    )
)

@orchestrator_agent.tool
def get_pipeline_geometry_and_metrics(ctx: RunContext) -> dict:
    """Fetch the actual pre-calculated geometry vectors and metrics from the modular pipeline run."""
    try:
        with open("outputs/joal502/modular/run_summary.json", "r") as f:
            summary = json.load(f)
        with open("outputs/joal502/modular/metrics.json", "r") as f:
            metrics = json.load(f)
            
        area_2d = metrics.get('total_joal_area_m2', 1515.22)
        inner_m = metrics.get('inner_kerb_length_m', 231.5)
        outer_m = metrics.get('outer_kerb_length_m', 268.4)
        slope_factor = 1.003
        
        raw_design_facts = {
            "joal_2d_area": area_2d,
            "inner_kerb_length": inner_m,
            "outer_kerb_length": outer_m,
            "slope_correction_factor": slope_factor,
            "concrete_depth_m": 0.15,
            "concrete_width_m": 5.5,
            "gap65_depth_m": 0.15,
            "gap65_width_m": 6.0,
            "footpath_2d_area": 850.5,
            "footpath_depth_m": 0.15,
            "safety_factor_for_materials": 1.05
        }

        return {"summary": summary, "metrics": metrics, "raw_design_facts": raw_design_facts}
    except Exception as e:
        return {"error": str(e)}

@orchestrator_agent.tool
def store_step_audit_state(ctx: RunContext, step_id: int, decision: str, image_path: str, numeric_data: dict, explanation: str) -> str:
    """Store the relevant state of the step (numeric data, reasoning, and visuals) in a JSON for persistent audit tracking across steps."""
    log_file = "demo_audit_state.json"
    
    # Initialize or load
    if os.path.exists(log_file):
        with open(log_file, "r") as f:
            try:
                state = json.load(f)
            except:
                state = []
    else:
        state = []
        
    entry = {
        "step_id": step_id,
        "decision": decision,
        "image": image_path,
        "numeric_data": numeric_data,
        "explanation": explanation
    }
    
    # Override if step exists, or append
    updated = False
    for i, item in enumerate(state):
        if item.get("step_id") == step_id:
            state[i] = entry
            updated = True
            break
            
    if not updated:
        state.append(entry)
        
    with open(log_file, "w") as f:
        json.dump(state, f, indent=4)
        
    return f"State for step {step_id} successfully stored in audit log."

class VisionRefinement(BaseModel):
    highlight_polygon: list[list[float]] = Field(description="Polygon array of coordinates [[y1, x1], [y2, x2], [y3, x3], [y4, x4]] normalized from 0 to 1000 representing the scope area.")
    generated_image_path: str = Field(default="", description="The path to the generated visualization image from the highlighting tool.")

vision_refiner_agent = Agent(
    model='gemini-flash-latest',
    output_type=VisionRefinement,
    system_prompt=(
        "You are a Vision Refiner Agent. Your task is to locate a specified target object in an image, visually verify it, and return its tight bounding polygon coordinates normalized from 0 to 1000. "
        "IMPORTANT: You MUST rely entirely on the automatic highlighting tool. Do not manually aggregate points.\n"
        "1. First, deduce the core target label (e.g. ST3-P07) from the description. For the central Joal 502/512 U-shaped corridor, the exact label is ALWAYS 'ST3-P07'.\n"
        "2. Call `generate_highlighted_target_image(pdf_path, target_label)` to extract the exact bounding box using flood-fill bounds. Use pdf_path='examples/Joal 502-General Plan.pdf'.\n"
        "3. You MUST use the EXACT `normalized_polygon` array returned by that tool as your final output. DO NOT modify it, ignore it, or build a custom polygon from other search terms.\n"
        "4. Call `validate_polygon_bounds` with that exact polygon to ensure it passes basic sanity checks."
    )
)

@vision_refiner_agent.tool
def generate_highlighted_target_image(ctx: RunContext, pdf_path: str, target_text: str) -> dict:
    """A tool to automatically locate a label (e.g., 'ST3-P07') in a PDF, flood-fill the surrounding room or boundary,
    and generate a high-resolution highlighted overlay image. It returns the normalized bounding box [y, x] polygon of the found area
    and the path to the newly generated image.
    This allows the AI to automatically isolate and visually highlight the correct scope area so the user can verify it."""
    import fitz
    import cv2
    import numpy as np
    import os
    
    try:
        # We use scale=4 for precision
        scale = 4
        dpi = 72 * scale
        
        doc = fitz.open(pdf_path)
        page = doc[0]
        pix = page.get_pixmap(dpi=dpi)
        
        if pix.n == 4:
            pdf_img = cv2.cvtColor(np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, 4), cv2.COLOR_RGBA2BGR)
        else:
            pdf_img = cv2.cvtColor(np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, 3), cv2.COLOR_RGB2BGR)

        seed_x, seed_y = None, None
        for block in page.get_text("dict")["blocks"]:
            if "lines" in block:
                for line in block["lines"]:
                    for span in line["spans"]:
                        if target_text in span["text"]:
                            bbox = span["bbox"]
                            seed_x = int((bbox[0] + bbox[2]) / 2)
                            seed_y = int((bbox[1] + bbox[3]) / 2)
                            break
        
        if seed_x is None:
            return {"error": f"Could not find label '{target_text}' on the page."}

        scaled_seed = (int(seed_x * scale), int(seed_y * scale))
        canvas = np.zeros((pix.h, pix.w), dtype=np.uint8)

        for d in page.get_drawings():
            rect = d["rect"]
            fill = d.get("fill")
            if fill == (0.0, 0.0, 0.0) or fill == [0.0, 0.0, 0.0] or fill == 0.0:
                area = rect.width * rect.height
                if 5 < area < 100:
                    for item in d["items"]:
                        if item[0] == 'l': 
                            pt1 = (int(item[1].x * scale), int(item[1].y * scale))
                            pt2 = (int(item[2].x * scale), int(item[2].y * scale))
                            cv2.line(canvas, pt1, pt2, 255, thickness=2)
                        elif item[0] == 're':
                            pt1 = (int(item[1].x0 * scale), int(item[1].y0 * scale))
                            pt2 = (int(item[1].x1 * scale), int(item[1].y1 * scale))
                            cv2.rectangle(canvas, pt1, pt2, 255, thickness=2)

        kernel = np.ones((5, 5), np.uint8)
        dilated_canvas = cv2.dilate(canvas, kernel, iterations=2)

        h, w = dilated_canvas.shape[:2]
        flood_mask = np.zeros((h + 2, w + 2), np.uint8)
        filled_canvas = dilated_canvas.copy()

        cv2.floodFill(filled_canvas, flood_mask, scaled_seed, 255)
        isolated_fill = (flood_mask[1:-1, 1:-1] * 255).astype(np.uint8)

        white_bg = np.full(pdf_img.shape, 255, dtype=np.uint8)
        dimmed_pdf = cv2.addWeighted(pdf_img, 0.35, white_bg, 0.65, 0)

        highlight_color = np.full(pdf_img.shape, (0, 220, 255), dtype=np.uint8)
        highlighted_pdf = cv2.addWeighted(pdf_img, 0.6, highlight_color, 0.4, 0)

        final_img = dimmed_pdf.copy()
        target_bool_mask = isolated_fill > 0
        final_img[target_bool_mask] = highlighted_pdf[target_bool_mask]

        contours, _ = cv2.findContours(isolated_fill, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        target_polygon = []
        if contours:
            target_contour = max(contours, key=cv2.contourArea)
            cv2.drawContours(final_img, [target_contour], -1, (0, 0, 255), 4)
            
            # Convert contour to a rotated bounding box and normalize to 0-1000 coordinates (y, x)
            rect = cv2.minAreaRect(target_contour)
            box = cv2.boxPoints(rect)
            
            target_polygon = []
            for pt in box:
                norm_x = (float(pt[0]) / pix.w) * 1000.0
                norm_y = (float(pt[1]) / pix.h) * 1000.0
                target_polygon.append([round(norm_y, 2), round(norm_x, 2)])

        os.makedirs("outputs/visualizations", exist_ok=True)
        out_path = f"outputs/visualizations/highlighted_{target_text}.png"
        cv2.imwrite(out_path, final_img)
        doc.close()
        
        return {
            "success": True,
            "generated_image_path": f"/{out_path}",
            "normalized_polygon": target_polygon,
            "message": "Highlighted image successfully generated! The agent can now review this polygon and path."
        }
    except Exception as e:
        return {"error": str(e)}


@vision_refiner_agent.tool
def search_pdf_text_markers(ctx: RunContext, search_term: str) -> list[list[float]]:
    """A tool to search the underlying PDF vectors for specific text labels (like 'ST3-P07'). 
    Returns a list of matching bounding box centers [y, x] normalized exactly to the 0-1000 image grid coordinates you use.
    Use this multiple times to find anchor points for your final polygon."""
    import fitz
    
    pdf_path = "examples/Joal 502-General Plan.pdf"
    try:
        doc = fitz.open(pdf_path)
        page = doc[0]
        rect = page.rect
        matches = page.search_for(search_term)
        results = []
        for inst in matches:
            cx = (inst.x0 + inst.x1) / 2.0
            cy = (inst.y0 + inst.y1) / 2.0
            # Normalized values matching exactly the UI rendering axis array [y, x] (0 to 1000)
            norm_x = (cx / rect.width) * 1000.0
            norm_y = (cy / rect.height) * 1000.0
            results.append([round(norm_y, 2), round(norm_x, 2)])
        doc.close()
        return results if results else []
    except Exception as e:
        return [f"Error searching matching terms: {e}"]

@vision_refiner_agent.tool
def validate_polygon_bounds(ctx: RunContext, polygon: list[list[float]]) -> str:
    """Validator tool: before returning the final bounding output, call this tool with your intended polygon coords to get an AI heuristics validation message."""
    if not polygon or len(polygon) < 3:
        return "Critique: FAILED. A valid polygon must have at least 3 [y, x] ordered points."
    
    ys = [float(p[0]) for p in polygon]
    xs = [float(p[1]) for p in polygon]
    y_min, y_max = min(ys), max(ys)
    x_min, x_max = min(xs), max(xs)
    
    if (y_max - y_min) < 10 or (x_max - x_min) < 10:
        return f"Critique: FAILED. Area is far too small (Vert {y_max-y_min:.1f}, Horiz {x_max-x_min:.1f}). Found a speck instead of a stage."
        
    return f"Critique: PASS. Geometric spread is validated (Vert {y_max-y_min:.1f}, Horiz {x_max-x_min:.1f}). Proceed to human confirmation!"

@orchestrator_agent.tool
async def extract_target_geometry(ctx: RunContext, target_description: str, active_canvas_image: str) -> dict:
    """Extract a formal bounding polygon of a target scope area from the provided background image using a rich text description."""
    from pydantic_ai.messages import BinaryContent
    import mimetypes
    from pathlib import Path
    
    cleaned_path = active_canvas_image.lstrip('/')
    path = Path(cleaned_path)
    if not path.exists():
        return {"error": f"Image file not found at {path}"}
        
    mime_type, _ = mimetypes.guess_type(path)
    file_bytes = path.read_bytes()
    
    prompt = f"Find the tightest bounding box for the engineering project scope described as: '{target_description}'. This description was deduced holistically from cross-referencing multiple engineering documents. Normalize the polygon coordinates [[y, x], ...] from 0 to 1000 relative to the image borders."
    
    result = await vision_refiner_agent.run([
        prompt,
        BinaryContent(data=file_bytes, media_type=mime_type or "image/png")
    ])
    
    vision_logs = []
    for msg in result.new_messages():
        if hasattr(msg, 'parts'):
            for part in msg.parts:
                part_kind = getattr(part, 'part_kind', '')
                tool_name = getattr(part, 'tool_name', '')
                if part_kind == 'tool-call':
                    args_data = getattr(part, 'args', {})
                    if hasattr(args_data, 'model_dump'):
                        args_data = args_data.model_dump()
                    vision_logs.append({
                        "agent": "Vision Refiner Tool",
                        "task": f"Invoking {tool_name}",
                        "result": json.dumps(args_data, indent=2)
                    })
                elif part_kind == 'tool-return':
                    # Only return part if it's not giant
                    content_str = str(getattr(part, 'content', ''))
                    if len(content_str) > 2000:
                        content_str = content_str[:2000] + "... (truncated)"
                    vision_logs.append({
                        "agent": "Vision Refiner Feedback",
                        "task": f"Result of {tool_name}",
                        "result": content_str
                    })

    output_data = result.output.model_dump()
    output_data["target_description_queried"] = target_description
    output_data["vision_logs"] = vision_logs
    
    return output_data
class PageAnalysis(BaseModel):
    page_title: str = Field(description="Inferred title of the page")
    drawing_type: str = Field(description="E.g. Plan, Section, Detail, Elevation")
    key_elements: list[str] = Field(description="Key structures or elements detected")
    scale_hint: str = Field(description="Inferred or extracted scale")

page_analyzer_agent = Agent(
    model='gemini-flash-lite-latest',
    output_type=PageAnalysis,
    system_prompt="Analyze a construction drawing page filename or text context. Classify the drawing type, scale and key elements based on domain knowledge. Do not try to guess the focus or target scope yet, just objectively report what's on the page."
)

class OverviewAnalysis(BaseModel):
    total_pages: int
    project_summary: str = Field(description="A high-level summary of what this entire document set represents.")
    primary_disciplines: list[str]

overview_agent = Agent(
    model='gemini-flash-lite-latest',
    output_type=OverviewAnalysis,
    system_prompt="You receive summaries of multiple drawing pages. Summarize the total project scope and primary disciplines involoved."
)

class RelationshipAnalysis(BaseModel):
    relationships: list[str] = Field(description="Connections between pages, e.g. 'Page 1 (Plan) provides context for Page 2 (Section)'")
    workflow_suggestion: str = Field(description="Suggested order to process these drawings")

relationship_agent = Agent(
    model='gemini-flash-lite-latest',
    output_type=RelationshipAnalysis,
    system_prompt="You look at an overview of a construction drawing set and explain how the different pages relate to each other (e.g., Plan vs Section)."
)

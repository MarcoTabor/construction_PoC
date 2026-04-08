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
        "Step 1: Upload. "
        "Step 2: Scope Selection. You MUST wait for user confirmation here! The user has uploaded a mix of detailed plans and a general overview plan. "
        "First, analyze the provided document analysis context (Overview and Relationships). Deduce the specific project scope, road alignment (e.g., Joal 502/512), or structural stage that natively ties all the detailed sheets back to the general plan. "
        "Second, configure the active_canvas_image. It MUST always remain '/outputs/joal502/modular/visualizations/general_plan_background.png'. Do NOT change it to any generated image paths, as we want the UI to draw the clean SVG polygon over the original base map. "
        "Third, now that you know the target scope, CALL the extract_target_geometry tool. Provide a rich, highly descriptive text for the 'target_description' argument (e.g., 'The U-shaped road alignment for Joal 502 and 512 passing through the park') so the vision agent knows exactly what to look for on the canvas image. "
        "Set user_actions_required to a label asking to confirm the Stage scope (e.g. '✅ Confirm Target Object'). "
        "Crucially, populate 'registered_objects' with a ScopeObject mapping the selected bounds and metadata, explicitly turning the graphical annotation into a formal engineering object. "
        "Explain in 'agent_explanation' that you synthesized the document overviews to deduce the scope, cross-referenced it via visual AI extraction, and explicitly call out that this selection is now formally registered as an object. "
        "Step 3: Geometry Detection. "
        "Step 4: Cross-Section Linking. Step 5: Derived Geometry. Step 6: Quantities (Money Moment). "
        "Keep explanations concise, showing clear causality."
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
        return {"summary": summary, "metrics": metrics}
    except Exception as e:
        return {"error": str(e)}

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

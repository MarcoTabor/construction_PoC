import json
from pathlib import Path
import shutil

from fastapi import FastAPI, UploadFile, File, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
from pydantic import BaseModel
from agents import orchestrator_agent, page_analyzer_agent, overview_agent, relationship_agent
from pydantic_ai.messages import BinaryContent

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5174", "http://127.0.0.1:5173", "http://localhost:5175"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/outputs", StaticFiles(directory="outputs"), name="outputs")

@app.exception_handler(Exception)
async def custom_exception_handler(request: Request, exc: Exception):
    import traceback
    traceback.print_exc()
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error", "error": str(exc)},
        headers={"Access-Control-Allow-Origin": "*"}
    )

@app.post("/api/upload")
async def upload_files(files: list[UploadFile]):
    upload_dir = Path("uploads")
    upload_dir.mkdir(exist_ok=True)
    saved = []
    for f in files:
        if not f.filename:
            continue
        try:
            target = upload_dir / f.filename
            with target.open("wb") as buf:
                shutil.copyfileobj(f.file, buf)
            saved.append(f.filename)
        except Exception as e:
            pass
    return {"status": "ok", "processed": saved}

@app.get("/api/results")
def get_results():
    modular_dir = Path("outputs/joal502/modular")
    summary_path = modular_dir / "run_summary.json"
    metrics_path = modular_dir / "metrics.json"
    if not summary_path.exists():
        return {"error": "none"}
    summary = json.loads(summary_path.read_text())
    
    def normalize_paths(obj):
        if isinstance(obj, dict):
            return {k: normalize_paths(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [normalize_paths(v) for v in obj]
        elif isinstance(obj, str):
            return obj.replace('\\\\', '/')
        return obj
        
    normalized_summary = normalize_paths(summary)
    metrics = None
    if metrics_path.exists():
        metrics = normalize_paths(json.loads(metrics_path.read_text()))
    return {"summary": normalized_summary, "metrics": metrics}

class OrchRequest(BaseModel):
    current_step_id: int
    user_action: str
    analysis_context: dict | None = None

@app.post("/api/orchestrator/next-step")
async def get_next_step(req: OrchRequest):
    context_str = f" Context from document analysis: {json.dumps(req.analysis_context)}" if req.analysis_context else ""
    prompt = f"The user was just on Step {req.current_step_id}. They performed the action: '{req.user_action}'.{context_str} Advance to the strictly next progressive engineering step as per the demo spec. Make sure to fix the active_canvas_image to start with / if it doesn't already. If moving from Step 1 to Step 2 (confirming scope target), use the boundary polygon from the context into the highlight_polygon field."
    
    import asyncio
    max_retries = 3
    result = None
    last_err = None
    for attempt in range(max_retries):
        try:
            result = await orchestrator_agent.run(prompt)
            break
        except Exception as e:
            last_err = e
            await asyncio.sleep(1 + attempt)
    
    if not result:
        raise last_err

    sub_agent_logs = []
    for msg in result.new_messages():
        if hasattr(msg, 'parts'):
            for part in msg.parts:
                if getattr(part, 'tool_name', '') == 'extract_target_geometry':
                    # Sometimes the result is in content, sometimes in return_value
                    ret_val = getattr(part, 'content', None) or getattr(part, 'return_value', None)
                    if ret_val:
                        if isinstance(ret_val, dict) and "vision_logs" in ret_val:
                            # 1. Surface the inner vision refiner tool trace (search pdf markers, validate!)
                            sub_agent_logs.extend(ret_val.pop("vision_logs"))
                            
                        # 2. Finally surface the overarching sub-agent polygon output itself
                        sub_agent_logs.append({
                            "agent": "Vision Refiner (Sub-Agent Result)",
                            "task": "Locate target bounds with multimodal vision",
                            "result": ret_val
                        })

    output = result.output.model_dump()
    if output["active_canvas_image"] and not output["active_canvas_image"].startswith("/"):
        output["active_canvas_image"] = "/" + output["active_canvas_image"]
        
    output["sub_agent_logs"] = sub_agent_logs
    return output

class MultiFileAnalysisRequest(BaseModel):
    filenames: list[str]

@app.post("/api/analyze-drawings")
async def analyze_drawings(req: MultiFileAnalysisRequest):
    logs = []
    page_summaries = []
    
    # 1. Page analyzer
    for filename in req.filenames:
        target_path = Path("uploads") / filename
        
        try:
            if target_path.exists():
                logs.append({
                    "agent": "System (Deterministic)",
                    "task": "File Verification",
                    "result": f"SUCCESS: Found '{filename}' in uploads/. Sending multimodal binary bytes to Agent."
                })
                
                file_bytes = target_path.read_bytes()
                media_type = "application/pdf"
                if filename.lower().endswith(".png"):
                    media_type = "image/png"
                elif filename.lower().endswith(".jpg") or filename.lower().endswith(".jpeg"):
                    media_type = "image/jpeg"
                
                # Create a BinaryContent object and send it alongside the prompt
                prompt_input = [
                      f"Extract drawing details from this file: '{filename}'. Please review the binary contents provided and extract the exact bounding box of the sub-scope area (such as the U-shaped sub-stage).",
                      BinaryContent(data=file_bytes, media_type=media_type)
                ]
            else:
                  logs.append({
                      "agent": "System (Deterministic)",
                      "task": "File Verification",
                      "result": f"WARNING: File '{filename}' NOT FOUND in 'uploads/' directory. Falling back to text-only filename guess."
                  })

                  prompt_input = f"Extract drawing details from this filename / simulated OCR text: '{filename}'. In a real run, this would be the OCR content of the PDF. Please extract the sub-scope bounding box if possible."
            
            result = await page_analyzer_agent.run(prompt_input)

            page_summaries.append(result.output.model_dump())
            logs.append({
                "agent": "Page Analyzer",
                "task": f"Process page: {filename}",
                "result": json.dumps(result.output.model_dump(), indent=2)
            })
        except Exception as e:
            logs.append({
                "agent": "Page Analyzer",
                "task": f"Failed to process page: {filename}",
                "result": str(e)
            })
    
    # Let's provide a fallback summary if the array is empty
    summary_text = json.dumps(page_summaries) if page_summaries else "No specific pages provided."
    
    # 2. Overview Agent
    prompt_overview = f"Here are the summaries of all pages: {summary_text}. Calculate total pages and project summary."
    overview_res = await overview_agent.run(prompt_overview)
    overview_data = overview_res.output.model_dump()
    logs.append({
        "agent": "Overview Manager",
        "task": "Summarize entire document drop",
        "result": json.dumps(overview_data, indent=2)
    })
    
    # 3. Relationship Agent
    prompt_relation = f"Based on this overview: {json.dumps(overview_data)}. Explain how the plan pages relate to the section pages (if any)."
    relation_res = await relationship_agent.run(prompt_relation)
    relation_data = relation_res.output.model_dump()
    logs.append({
        "agent": "Relationship Strategist",
        "task": "Map dependencies between pages",
        "result": json.dumps(relation_data, indent=2)
    })

    return {
        "status": "ok",
        "logs": logs,
        "page_summaries": page_summaries,
        "overview": overview_data,
        "relationship": relation_data
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)

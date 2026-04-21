import json
with open('agents.py', 'r', encoding='utf-8') as f:
    text = f.read()

import re

# 1. Clean math_derivations prompt residual
text = re.sub(r'\s*"Crucially, YOU must dynamically calculate the final values to populate the `math_derivations` array.*?\.', '', text)
text = re.sub(r' Calculate them accurately using the design facts payload\.\\n"', '', text, flags=re.DOTALL)

# 2. Add color naming rule explicitly
text = text.replace(
    '"Keep explanations professional and deductive, showing clear causality."',
    '"ALWAYS explicitly name the colors (e.g. Joal centerline, Joal inner kerb, Joal outer kerb, and Footpath) when referring to them.\\n"\n        "Keep explanations professional and deductive, showing clear causality."'
)

# 3. Add usage of the save state tool
text = text.replace(
    '"CRITICAL: Do NOT skip steps! ALWAYS advance step_id by exactly 1."',
    '"CRITICAL: Do NOT skip steps! ALWAYS advance step_id by exactly 1."\n        "After each step, you MUST call `store_step_audit_state` to explicitly extract and save the relevant numeric data, the user decision, the chosen active image, and your explanation. This maintains state across steps."'
)

# 4. Inject the new tool definition
new_tool_code = """
@orchestrator_agent.tool
def store_step_audit_state(ctx: RunContext, step_id: int, decision: str, image_path: str, numeric_data: dict, explanation: str) -> str:
    \"\"\"Store the relevant state of the step (numeric data, reasoning, and visuals) in a JSON for persistent audit tracking across steps.\"\"\"
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
"""

# Insert after get_pipeline_geometry_and_metrics
if "def store_step_audit_state" not in text:
    target_str = "    except Exception as e:\n        return {\"error\": str(e)}\n"
    text = text.replace(target_str, target_str + new_tool_code)


with open('agents.py', 'w', encoding='utf-8') as f:
    f.write(text)

print("Patch applied.")

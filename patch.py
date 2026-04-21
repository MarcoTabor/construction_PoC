import sys

with open('agents.py', 'r', encoding='utf-8') as f:
    text = f.read()

old_1 = 'In gent_explanation, present a beautiful high-level summary narrative. Mention the correction factor of 1.003 and how spatial alignment confirmed the kerb mapping.'
new_1 = 'In gent_explanation, present a beautiful high-level summary narrative tracing the deductive process. Start with the scale validating the geometry. Then explain that confirmed kerb lengths and confirmed 2D area [Confirmed Steps 3 & 4] bring us to the total Joal 3D surface area (incorporating the 1.003 slope factor). Explain applying the 150mm depth [Confirmed in Step 5 cross-section] and a 5% safety factor [Industry standard for waste/spillage] to get the concrete volume. Explain the GAP65 calculation: we divide the plan area by 5.5m (concrete width) and multiply by 6.0m (GAP65 width) [Confirmed in Step 5 cross-section] because the sub-base extends wider than the pavement. Next, explain the outer kerb length [Confirmed Step 3] maps to the Flush Nib, and the inner kerb length [Confirmed Step 3] maps to the Subsoil Drain. For the Footpath, first state the area calculation, then apply the 150mm thickness [Confirmed in Step 5 screenshot] and 5% safety factor for the final volume.\\n"\\n        "At the very end of gent_explanation, provide a quick list of the different lengths, areas, and volumes without any explanations or calculations, just as a clean overview.'

old_2 = 'CRITICAL: We must provide EXACT reasoning for GAP65 Sub-base width. The GAP65 is 6m wide (from DE04 section), whereas the joal concrete surface is 5.5m wide! "\n        "Therefore, the GAP65 plan area CANNOT be the same as the concrete pavement! We estimate the GAP65 plan area by multiplying the main concrete plan area by (6.0/5.5).\\n'
new_2 = ''

text = text.replace(old_1, new_1)
text = text.replace(old_2, new_2)

# Update Python calculations in get_pipeline_geometry_and_metrics
old_math = '''        vol_concrete = round(area_3d * 0.15, 2)
        area_gap65 = round(area_3d * (6.0 / 5.5), 2)
        vol_gap65 = round(area_gap65 * 0.15, 2)
        flush_nib_3d = round(outer_m * slope_factor, 2)
        subsoil_3d = round(inner_m * slope_factor, 2)

        step_7_math = [
            {"item": "Concrete Pavement (150mm)", "reasoning": "Plan area mapped directly with 3D slope correction.", "formula": f"{area_2d:.2f} m² * {slope_factor}", "result": f"{area_3d:.2f} m² (Volume: {vol_concrete:.2f} m³)"},       
            {"item": "GAP65 Sub-base (150mm)", "reasoning": "GAP65 is 6m wide vs concrete 5.5m wide (scale 6.0/5.5).", "formula": f"({area_3d:.2f} m² * 6.0 / 5.5) * 0.15m", "result": f"{vol_gap65:.2f} m³"},
            {"item": "Flush Nib", "reasoning": "Mapped to Outer Kerb line based on plan/RC cross-section correlation via Step 5.", "formula": f"{outer_m:.2f}m * {slope_factor}", "result": f"{flush_nib_3d:.2f} m"},
            {"item": "Subsoil Drain", "reasoning": "Mapped to Inner Kerb line based on plan/RC cross-section correlation.", "formula": f"{inner_m:.2f}m * {slope_factor}", "result": f"{subsoil_3d:.2f} m"},
            {"item": "Footpath Concrete (100mm)", "reasoning": "Mapped to dynamic layout width.", "formula": "850.5 m² * 1.003 * 0.10m", "result": "85.3 m³"} 
        ]'''

new_math = '''        safety_factor = 1.05
        vol_concrete = round(area_3d * 0.15 * safety_factor, 2)
        area_gap65 = round(area_3d * (6.0 / 5.5), 2)
        vol_gap65 = round(area_gap65 * 0.15 * safety_factor, 2)
        flush_nib_3d = round(outer_m * slope_factor, 2)
        subsoil_3d = round(inner_m * slope_factor, 2)
        footpath_area_2d = 850.5
        footpath_vol = round(footpath_area_2d * slope_factor * 0.15 * safety_factor, 2)

        step_7_math = [
            {"item": "Concrete Pavement (150mm)", "reasoning": "Plan area mapped directly with 3D slope correction + 5% safety factor.", "formula": f"{area_2d:.2f} m² * {slope_factor} * 0.15m * {safety_factor}", "result": f"{area_3d:.2f} m² (Volume: {vol_concrete:.2f} m³)"},       
            {"item": "GAP65 Sub-base (150mm)", "reasoning": "GAP65 is 6m wide vs concrete 5.5m wide. Includes 5% safety factor.", "formula": f"({area_3d:.2f} m² * 6.0 / 5.5) * 0.15m * {safety_factor}", "result": f"Area: {area_gap65:.2f} m² (Volume: {vol_gap65:.2f} m³)"},
            {"item": "Flush Nib", "reasoning": "Mapped to Outer Kerb line based on plan/RC cross-section correlation.", "formula": f"{outer_m:.2f}m * {slope_factor}", "result": f"{flush_nib_3d:.2f} m"},
            {"item": "Subsoil Drain", "reasoning": "Mapped to Inner Kerb line based on plan/RC cross-section correlation.", "formula": f"{inner_m:.2f}m * {slope_factor}", "result": f"{subsoil_3d:.2f} m"},
            {"item": "Footpath Concrete (150mm)", "reasoning": "Area multiplied by 150mm depth + 5% safety factor.", "formula": f"{footpath_area_2d:.1f} m² * {slope_factor} * 0.15m * {safety_factor}", "result": f"{footpath_vol:.2f} m³"} 
        ]'''

text = text.replace(old_math, new_math)

with open('agents.py', 'w', encoding='utf-8') as f:
    f.write(text)


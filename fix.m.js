import fs from 'fs';

let content = fs.readFileSync('agents.py', 'utf8');

// Use simple string replacement with split/join to avoid regex syntax issues
let finalContent = content.split('        "Step 7: Final Results & Engineering Audit (Money Moment). "').join(`        "Step 7: Final Results & Engineering Audit (Money Moment). "
        "The system presents a comprehensive final dashboard bringing together the results from the previous tasks. "
        "Call \`get_pipeline_geometry_and_metrics\` to retrieve the pre-calculated metrics. Ensure you multiply appropriately using exact math calculation internally. "
        "Set \`active_canvas_image\` to the final visual representation of the elements. "
        "In \`agent_explanation\`, present a beautiful high-level summary narrative. Mention the correction factor of 1.003 and how spatial alignment confirmed the kerb mapping.\\n"
        "CRITICAL: We must provide EXACT reasoning for GAP65 Sub-base width. The GAP65 is 6m wide (from DE04 section), whereas the joal concrete surface is 5.5m wide! "
        "Therefore, the GAP65 plan area CANNOT be the same as the concrete pavement! We estimate the GAP65 plan area by multiplying the main concrete plan area by (6.0/5.5).\\n"
        "ALSO CRITICAL: You must populate the \`math_derivations\` array with beautiful individual objects for each calculation so the UI can render them centrally! Each object should have:\\n"
        "- \`item\` (e.g., 'Concrete Pavement (150mm)')\\n"
        "- \`reasoning\` (e.g., 'Plan area mapped directly with 3D slope correction.')\\n"
        "- \`formula\` (e.g., '1655.65 m² * 1.003')\\n"
        "- \`result\` (e.g., '1660.62 m²')\\n"
        "Include derivations for 1. Concrete Pavement, 2. GAP65 Sub-base (using the 6/5.5 width scale reasoning!), 3. Flush Nib, 4. Subsoil Drain, 5. Footpath.\\n"
        "Set \`user_actions_required\` to: ['✅ Export Schedule of Quantities', '✅ Submit Confirmed Actions']."`);

// Trim everything after the Step 7 replacement if necessary, but actually we can just manually splice it
let beforeStep7 = content.split('        "Step 7: Final Results')[0];
let afterStep7 = `        "Step 7: Final Results & Engineering Audit (Money Moment). "
        "The system presents a comprehensive final dashboard bringing together the results from the previous tasks. "
        "Call \`get_pipeline_geometry_and_metrics\` to retrieve the pre-calculated metrics. Ensure you multiply appropriately using exact math calculation internally. "
        "Set \`active_canvas_image\` to the final visual representation of the elements (e.g. '/outputs/joal502/visualizations/joal_and_footpath_overlay.png'). "
        "In \`agent_explanation\`, present a beautiful high-level summary narrative. Mention the correction factor of 1.003 and how spatial alignment confirmed the kerb mapping.\\n"
        "CRITICAL: We must provide EXACT reasoning for GAP65 Sub-base width. The GAP65 is 6m wide (from DE04 section), whereas the joal concrete surface is 5.5m wide! "
        "Therefore, the GAP65 plan area CANNOT be the same as the concrete pavement! We estimate the GAP65 plan area by multiplying the main concrete plan area by (6.0/5.5).\\n"
        "ALSO CRITICAL: You must populate the \`math_derivations\` array with beautiful individual objects for each calculation so the UI can render them centrally! Each object should have:\\n"
        "- \`item\` (e.g., 'Concrete Pavement (150mm)')\\n"
        "- \`reasoning\` (e.g., 'Plan area mapped directly with 3D slope correction.')\\n"
        "- \`formula\` (e.g., '1655.65 m² * 1.003')\\n"
        "- \`result\` (e.g., '1660.62 m²')\\n"
        "Include derivations for 1. Concrete Pavement, 2. GAP65 Sub-base (using the 6/5.5 width scale reasoning!), 3. Flush Nib, 4. Subsoil Drain, 5. Footpath.\\n"
        "Set \`user_actions_required\` to: ['✅ Export Schedule of Quantities', '✅ Submit Confirmed Actions']."
    )
)

@orchestrator_agent.tool`;

let finalFile = beforeStep7 + afterStep7 + content.split('@orchestrator_agent.tool')[1];

fs.writeFileSync('agents.py', finalFile, 'utf8');
console.log('agents.py rewrote successfully!');

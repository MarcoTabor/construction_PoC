import fitz
import cv2
import numpy as np

pdf_path = "examples/Joal 502-General Plan.pdf"
doc = fitz.open(pdf_path)
page = doc[0]

# --- 1. FIND THE TARGET LABEL ---
target_text = "ST3-P07"
seed_x, seed_y = None, None

for block in page.get_text("dict")["blocks"]:
    if "lines" in block:
        for line in block["lines"]:
            for span in line["spans"]:
                if target_text in span["text"]:
                    bbox = span["bbox"]
                    # Calculate center point for the flood fill seed
                    seed_x = int((bbox[0] + bbox[2]) / 2)
                    seed_y = int((bbox[1] + bbox[3]) / 2)
                    break

if seed_x is None:
    raise ValueError(f"Could not find label '{target_text}' on the page.")

print(f"[{target_text}] Found at seed point: ({seed_x}, {seed_y})")

# --- 2. CREATE A BLANK CANVAS & DRAW EXTRACTED DASHES ---
# We use a canvas matching the size of the PDF page, scaling by a factor for better resolution
scale = 4  # scale up to not lose tiny gaps
width = int(page.rect.width * scale)
height = int(page.rect.height * scale)

# Create highly black canvas (H, W, Channels)
canvas = np.zeros((height, width), dtype=np.uint8)

# Extract and draw dashes to the canvas
for d in page.get_drawings():
    rect = d["rect"]
    fill = d.get("fill")
    # Matching the same black dash filtering from step16
    if fill == (0.0, 0.0, 0.0) or fill == [0.0, 0.0, 0.0] or fill == 0.0:
        area = rect.width * rect.height
        if 5 < area < 100:
            for item in d["items"]:
                # Draw lines directly onto our OpenCV canvas (remembering to scale coordinates)
                if item[0] == 'l': 
                    pt1 = (int(item[1].x * scale), int(item[1].y * scale))
                    pt2 = (int(item[2].x * scale), int(item[2].y * scale))
                    cv2.line(canvas, pt1, pt2, 255, thickness=2)
                elif item[0] == 're':
                    pt1 = (int(item[1].x0 * scale), int(item[1].y0 * scale))
                    pt2 = (int(item[1].x1 * scale), int(item[1].y1 * scale))
                    cv2.rectangle(canvas, pt1, pt2, 255, thickness=2)

# --- 3. CLOSE GAPS (MORPHOLOGICAL DILATION) ---
# Expand the white lines slightly so dashed lines merge into solid boundaries
kernel = np.ones((5, 5), np.uint8)
dilated_canvas = cv2.dilate(canvas, kernel, iterations=2)

# Save intermediate mask to see the closed boundaries
cv2.imwrite("outputs/step17_mask_boundaries.png", dilated_canvas)

# --- 4. FLOOD FILL ---
# The seed point must be scaled to match our canvas size
scaled_seed = (int(seed_x * scale), int(seed_y * scale))

h, w = dilated_canvas.shape[:2]
flood_mask = np.zeros((h + 2, w + 2), np.uint8)

# Output image to hold just our filled result
filled_canvas = dilated_canvas.copy()

print(f"Flooding at scaled seed: {scaled_seed}")
cv2.floodFill(filled_canvas, flood_mask, scaled_seed, 255)

# Notice floodFill fills the shape on top of the borders.
# To ONLY get the fill (and not the rest of the drawing), we can extract just the filled portion.
# The `flood_mask` contains 1s where the fill happened.
isolated_fill = (flood_mask[1:-1, 1:-1] * 255).astype(np.uint8)

cv2.imwrite("outputs/step17_isolated_fill.png", isolated_fill)

# --- 5. EXTRACT CONTOUR ---
# Find the exact boundary of our isolated rectangle blob
contours, _ = cv2.findContours(isolated_fill, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

if contours:
    # Get the largest contour (ignoring tiny noise if any)
    target_contour = max(contours, key=cv2.contourArea)
    
    # We can draw it back down on a fresh visual
    display = cv2.cvtColor(canvas, cv2.COLOR_GRAY2BGR)
    cv2.drawContours(display, [target_contour], -1, (0, 0, 255), 4) # Draw red
    
    cv2.imwrite("outputs/step17_final_target_rectangle.png", display)
    print("Success! Target rectangle isolated and saved.")
else:
    print("Warning: Could not extract contour from the filled region.")
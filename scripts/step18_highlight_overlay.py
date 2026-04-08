import fitz
import cv2
import numpy as np

pdf_path = "examples/Joal 502-General Plan.pdf"
doc = fitz.open(pdf_path)
page = doc[0]

print("Rendering high-res PDF background...")
# Use exactly scale=4 (DPI 288) to perfectly match Step 17's math and prevent flood-fill leaks
scale = 4
dpi = 72 * scale
pix = page.get_pixmap(dpi=dpi)

# Convert from PyMuPDF Pixmap to OpenCV BGR image
if pix.n == 4:
    pdf_img = cv2.cvtColor(np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, 4), cv2.COLOR_RGBA2BGR)
else:
    pdf_img = cv2.cvtColor(np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, 3), cv2.COLOR_RGB2BGR)

print("Finding label coordinates...")
# --- 1. FIND THE TARGET LABEL ---
target_text = "ST3-P07"
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
    raise ValueError(f"Could not find label '{target_text}' on the page.")

scaled_seed = (int(seed_x * scale), int(seed_y * scale))

print("Extracting vectors and generating mask...")
# --- 2. CREATE A BLANK CANVAS & DRAW EXTRACTED DASHES ---
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

# --- 3. CLOSE GAPS (MORPHOLOGICAL DILATION) ---
kernel = np.ones((5, 5), np.uint8)
dilated_canvas = cv2.dilate(canvas, kernel, iterations=2)

# --- 4. FLOOD FILL ---
h, w = dilated_canvas.shape[:2]
flood_mask = np.zeros((h + 2, w + 2), np.uint8)
filled_canvas = dilated_canvas.copy()

cv2.floodFill(filled_canvas, flood_mask, scaled_seed, 255)
isolated_fill = (flood_mask[1:-1, 1:-1] * 255).astype(np.uint8)

print("Compositing final image...")
# --- 5. COMPOSITE IMAGE (DARK HIGHLIGHT ON FADED BG) ---

# Dim the entire original PDF by blending it heavily with white (transparent faded background)
white_bg = np.full(pdf_img.shape, 255, dtype=np.uint8)
dimmed_pdf = cv2.addWeighted(pdf_img, 0.35, white_bg, 0.65, 0)

# Create a highlighted colored version of the original PDF
# Using a yellow-ish tint (BGR: 0, 220, 255)
highlight_color = np.full(pdf_img.shape, (0, 220, 255), dtype=np.uint8)
highlighted_pdf = cv2.addWeighted(pdf_img, 0.6, highlight_color, 0.4, 0)

# Create the final image as the dimmed version
final_img = dimmed_pdf.copy()

# Replace the pixels inside the target area with the brightly highlighted version
target_bool_mask = isolated_fill > 0
final_img[target_bool_mask] = highlighted_pdf[target_bool_mask]

# --- 6. DRAW A SOLID BORDER ---
contours, _ = cv2.findContours(isolated_fill, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
if contours:
    target_contour = max(contours, key=cv2.contourArea)
    cv2.drawContours(final_img, [target_contour], -1, (0, 0, 255), 4)

out_path = "outputs/step18_final_overlay.png"
cv2.imwrite(out_path, final_img)
print(f"Success! Final highlight saved to {out_path}")

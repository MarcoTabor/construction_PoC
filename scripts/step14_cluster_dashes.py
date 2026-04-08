import fitz
import sys

try:
    import cv2
    import numpy as np
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

print(f"Has OpenCV: {HAS_CV2}")

doc = fitz.open("examples/Joal 502-General Plan.pdf")
page = doc[0]

# 1. Get text center for ST3-P07
term = "ST3-P07"
cx, cy = 0, 0
for b in page.get_text("dict")["blocks"]:
    if "lines" in b:
        for l in b["lines"]:
            for s in l["spans"]:
                if term in s["text"]:
                    bbox = s["bbox"]
                    cx = (bbox[0] + bbox[2]) / 2
                    cy = (bbox[1] + bbox[3]) / 2

pt = fitz.Point(cx, cy)

# 2. Draw only our dashed elements, but with a FAT stroke so they connect mathematically
doc_mask = fitz.open()
mask_page = doc_mask.new_page(width=page.rect.width, height=page.rect.height)
shape = mask_page.new_shape()

drawings = page.get_drawings()
for d in drawings:
    rect = d["rect"]
    fill = d.get("fill")
    if fill == (0.0, 0.0, 0.0) or fill == [0.0, 0.0, 0.0] or fill == 0.0:
        area = rect.width * rect.height
        if 5 < area < 100:
            shape.draw_rect(rect)
            # Make them 15 points thick so the dashes bleed together into a solid boundary line
            shape.finish(color=(0, 0, 0), fill=(0, 0, 0), width=15)

shape.commit()

mask_path = "outputs/step14_fat_dashes_mask.png"
mask_page.get_pixmap(dpi=150).save(mask_path)
print("Saved fat connected dashed lines mask to", mask_path)

if HAS_CV2:
    # 3. Use OpenCV to find the enclosed polygon
    img = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
    # The mask has black lines on white background. Invert for contour finding
    # Actually wait, PyMuPDF background is white by default? No, it might be transparent.
    # Let's read pixels properly.
    img_color = cv2.imread(mask_path)
    gray = cv2.cvtColor(img_color, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 127, 255, cv2.THRESH_BINARY_INV)
    
    # Find contours
    contours, hierarchy = cv2.findContours(thresh, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    
    # Scale point to 150 DPI (150 / 72.0)
    scale = 150.0 / 72.0
    pt_cv = (int(cx * scale), int(cy * scale))
    
    best_contour = None
    min_area = float('inf')
    
    for cnt in contours:
        # Check if point is inside
        # pointPolygonTest returns +1 if inside, 0 if on contour, -1 if outside
        if cv2.pointPolygonTest(cnt, pt_cv, False) > 0:
            area = cv2.contourArea(cnt)
            # We want the tightest valid contour containing the point
            if 10000 < area < min_area:
                min_area = area
                best_contour = cnt
                
    if best_contour is not None:
        print(f"Found enclosing contour! Area: {min_area}")
        # Draw it
        result_img = img_color.copy()
        cv2.drawContours(result_img, [best_contour], -1, (0, 0, 255), 5)
        cv2.circle(result_img, pt_cv, 10, (0, 255, 0), -1)
        cv2.imwrite("outputs/step14_solved_boundary.png", result_img)
        print("Saved solved boundary to outputs/step14_solved_boundary.png")
    else:
        print("Could not find a closed contour containing the center point.")


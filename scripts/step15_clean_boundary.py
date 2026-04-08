import fitz
import cv2
import numpy as np
import time

doc = fitz.open("examples/Joal 502-General Plan.pdf")
page = doc[0]

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

scale = 300.0 / 72.0

doc_mask = fitz.open()
mask_page = doc_mask.new_page(width=page.rect.width, height=page.rect.height)
shape = mask_page.new_shape()

for d in page.get_drawings():
    rect = d["rect"]
    fill = d.get("fill")
    if fill == (0.0, 0.0, 0.0) or fill == [0.0, 0.0, 0.0] or fill == 0.0:
        area = rect.width * rect.height
        if 5 < area < 100:
            shape.draw_rect(rect)

shape.finish(color=(0, 0, 0), fill=(0, 0, 0), width=8)
shape.commit()

mask_path = "outputs/step15_mask.png"
mask_page.get_pixmap(dpi=300).save(mask_path)
img = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)

_, thresh = cv2.threshold(img, 127, 255, cv2.THRESH_BINARY_INV)
contours, _ = cv2.findContours(thresh, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
pt_cv = (int(cx * scale), int(cy * scale))

best_cnt = None
min_area = float('inf')

for cnt in contours:
    if cv2.pointPolygonTest(cnt, pt_cv, False) > 0:
        area = cv2.contourArea(cnt)
        if 10000 < area < min_area:
            min_area = area
            best_cnt = cnt

if best_cnt is not None:
    shape_final = page.new_shape()
    
    epsilon = 0.005 * cv2.arcLength(best_cnt, True)
    approx = cv2.approxPolyDP(best_cnt, epsilon, True)
    
    # Map back to PDF points
    pts = [fitz.Point(p[0][0] / scale, p[0][1] / scale) for p in approx]
    
    for i in range(len(pts)):
        p1 = pts[i]
        p2 = pts[(i + 1) % len(pts)]
        shape_final.draw_line(p1, p2)
        
    # Draw transparent green over the area, and thick green border
    shape_final.finish(color=(0, 1, 0), fill=(0, 1, 0), fill_opacity=0.3, width=3)
    shape_final.commit()
    
    out_path = "outputs/step15_final_polygon.png"
    page.get_pixmap(dpi=300).save(out_path)
    print(f"MAPPED PERFECTLY! Found {len(approx)} vertices for boundary. Saved to {out_path}.")
else:
    print("Could not solve.")

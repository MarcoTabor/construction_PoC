import fitz
import os
import math

doc = fitz.open("examples/Joal 502-General Plan.pdf")
page = doc[0]
term = "ST3-P07"

texts = page.get_text("dict")["blocks"]
cx, cy = 0, 0

for b in texts:
    if "lines" in b:
        for l in b["lines"]:
            for s in l["spans"]:
                if term in s["text"]:
                    bbox = s["bbox"]
                    cx = (bbox[0] + bbox[2]) / 2
                    cy = (bbox[1] + bbox[3]) / 2

print(f"Center coordinates: ({cx}, {cy})")

radius = 50
search_rect = fitz.Rect(cx - radius, cy - radius, cx + radius, cy + radius)

# Draw the search bounds (circle and box) for context
shape = page.new_shape()
shape.draw_circle(fitz.Point(cx, cy), radius=radius)
shape.finish(color=(0, 1, 0), width=1, dashes="[5 5] 0") # green dashed circle

shape.draw_rect(search_rect)
shape.finish(color=(0, 0, 1), width=0.5) # blue box

# Get drawings and highlight those overlapping the search rect
drawings = page.get_drawings()
found_count = 0

for d in drawings:
    if d["rect"].intersects(search_rect):
        found_count += 1
        # highlight the drawing
        for item in d["items"]:
            if item[0] == "l": # line
                shape.draw_line(item[1], item[2])
            elif item[0] == "c": # curve
                shape.draw_bezier(item[1], item[2], item[3], item[4])
            elif item[0] == "re": # rect
                shape.draw_rect(item[1])
        shape.finish(color=(1, 0, 1), width=2) # magenta
        
shape.commit()

out_path = "outputs/step2_radial_search.png"
page.get_pixmap(dpi=150, clip=fitz.Rect(cx - 200, cy - 200, cx + 200, cy + 200)).save(out_path)
print(f"Saved region image to {out_path} showing {found_count} elements.")
doc.close()

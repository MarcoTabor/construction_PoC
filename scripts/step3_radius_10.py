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

radius = 10

def pt_in_circle(pt, ctx, cty, r):
    return math.hypot(pt.x - ctx, pt.y - cty) <= r

def item_in_circle(item, ctx, cty, r):
    if item[0] == "l":
        p1, p2 = item[1], item[2]
        for i in range(11):
            t = i / 10.0
            px = p1.x + t * (p2.x - p1.x)
            py = p1.y + t * (p2.y - p1.y)
            if math.hypot(px - ctx, py - cty) <= r:
                return True
        return False
    elif item[0] == "c":
        p1, p2, p3, p4 = item[1], item[2], item[3], item[4]
        for i in range(11):
            t = i / 10.0
            mt = 1 - t
            px = (mt**3)*p1.x + 3*(mt**2)*t*p2.x + 3*mt*(t**2)*p3.x + (t**3)*p4.x
            py = (mt**3)*p1.y + 3*(mt**2)*t*p2.y + 3*mt*(t**2)*p3.y + (t**3)*p4.y
            if math.hypot(px - ctx, py - cty) <= r:
                return True
        return False
    elif item[0] == "re":
        rect = item[1]
        pts = [rect.tl, rect.tr, rect.br, rect.bl]
        for p in pts:
            if pt_in_circle(p, ctx, cty, r): return True
        if rect.contains(fitz.Point(ctx, cty)): return True
        return False
    return False

search_rect = fitz.Rect(cx - radius, cy - radius, cx + radius, cy + radius)
shape = page.new_shape()

shape.draw_circle(fitz.Point(cx, cy), radius=radius)
shape.finish(color=(0, 1, 0), width=0.5, dashes="[2 2] 0") # green dashed circle

drawings = page.get_drawings()
found_count = 0

for d in drawings:
    # A single drawing object can contain thousands of lines. 
    # Its overall bounding box might intersect our search area, 
    # even if the lines themselves don't.
    if not d["rect"].intersects(search_rect):
        continue
        
    valid_items = []
    for item in d["items"]:
        # Only keep individual segments/curves that physically enter our 10pt radius
        if item_in_circle(item, cx, cy, radius):
            valid_items.append(item)
            
    if valid_items:
        found_count += len(valid_items)
        for item in valid_items:
            if item[0] == "l": 
                shape.draw_line(item[1], item[2])
            elif item[0] == "c": 
                shape.draw_bezier(item[1], item[2], item[3], item[4])
            elif item[0] == "re": 
                shape.draw_rect(item[1])
        shape.finish(color=(1, 0, 1), width=1.5)

shape.commit()

out_path = "outputs/step3_radius_10.png"
page.get_pixmap(dpi=300, clip=fitz.Rect(cx - 50, cy - 50, cx + 50, cy + 50)).save(out_path)
print(f"Saved region image to {out_path} showing {found_count} actual segments.")
doc.close()

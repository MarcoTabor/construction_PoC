import fitz
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

radius = 20

def distance_pt_to_segment(ptx, pty, p1x, p1y, p2x, p2y):
    dx = p2x - p1x
    dy = p2y - p1y
    if dx == 0 and dy == 0:
        return math.hypot(ptx - p1x, pty - p1y)
    t = ((ptx - p1x) * dx + (pty - p1y) * dy) / (dx*dx + dy*dy)
    t = max(0, min(1, t))
    closest_x = p1x + t * dx
    closest_y = p1y + t * dy
    return math.hypot(ptx - closest_x, pty - closest_y)

def item_intersects_circle(item, cx, cy, r):
    if item[0] == "l":
        return distance_pt_to_segment(cx, cy, item[1].x, item[1].y, item[2].x, item[2].y) <= r
    elif item[0] == "re":
        rect = item[1]
        x_closest = max(rect.x0, min(cx, rect.x1))
        y_closest = max(rect.y0, min(cy, rect.y1))
        return math.hypot(cx - x_closest, cy - y_closest) <= r
    elif item[0] == "c":
        p1, p2, p3, p4 = item[1], item[2], item[3], item[4]
        for i in range(101):
            t = i / 100.0
            mt = 1 - t
            px = (mt**3)*p1.x + 3*(mt**2)*t*p2.x + 3*mt*(t**2)*p3.x + (t**3)*p4.x
            py = (mt**3)*p1.y + 3*(mt**2)*t*p2.y + 3*mt*(t**2)*p3.y + (t**3)*p4.y
            if math.hypot(cx - px, cy - py) <= r:
                return True
    return False

search_rect = fitz.Rect(cx - radius, cy - radius, cx + radius, cy + radius)
shape = page.new_shape()

# Draw the boundary circle for reference
shape.draw_circle(fitz.Point(cx, cy), radius=radius)
shape.finish(color=(0, 1, 0), width=0.5, dashes="[2 2] 0")

drawings = page.get_drawings()
found_arrays = 0

for d in drawings:
    if not d["rect"].intersects(search_rect):
        continue
        
    # See if ANY segment in this drawing array explicitly hits the radius
    hits = 0
    for item in d["items"]:
        if item_intersects_circle(item, cx, cy, radius):
            hits += 1
            
    # If this drawing has elements inside the radius, mark the ENTIRE drawing
    if hits > 0:
        found_arrays += 1
        for item in d["items"]:
            if item[0] == "l": shape.draw_line(item[1], item[2])
            elif item[0] == "c": shape.draw_bezier(item[1], item[2], item[3], item[4])
            elif item[0] == "re": shape.draw_rect(item[1])
        shape.finish(color=(1, 0, 1), width=1.5)

shape.commit()

out_path = "outputs/step6_entire_arrays.png"
# Expand the clip slightly so we can see the contiguous extensions
page.get_pixmap(dpi=300, clip=fitz.Rect(cx - 250, cy - 250, cx + 250, cy + 250)).save(out_path)
print(f"Saved to {out_path} marking {found_arrays} entire drawing arrays.")
doc.close()

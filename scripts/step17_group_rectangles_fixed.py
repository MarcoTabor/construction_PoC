import fitz
import math
import random
import colorsys

doc = fitz.open("examples/Joal 502-General Plan.pdf")
page = doc[0]

# 1. Extract Dashes
dashes = []
for d in page.get_drawings():
    rect = d["rect"]
    fill = d.get("fill")
    if fill == (0.0, 0.0, 0.0) or fill == [0.0, 0.0, 0.0] or fill == 0.0:
        area = rect.width * rect.height
        if 5 < area < 100:
            points = []
            for item in d["items"]:
                if item[0] == 'l': points.extend([item[1], item[2]])
                elif item[0] == 're': points.extend([fitz.Point(item[1].x0, item[1].y0), fitz.Point(item[1].x1, item[1].y1)])
                elif item[0] == 'c': points.extend([item[1], item[4]])
            
            if not points: continue
            
            cx = sum(p.x for p in points) / len(points)
            cy = sum(p.y for p in points) / len(points)
            
            d_angle = 0
            max_len = 0
            for i in range(len(points)-1):
                for j in range(i+1, len(points)):
                    dx = points[j].x - points[i].x
                    dy = points[j].y - points[i].y
                    l = math.hypot(dx, dy)
                    if l > max_len:
                        max_len = l
                        d_angle = math.degrees(math.atan2(dy, dx)) % 180
            
            dashes.append({"cx": cx, "cy": cy, "angle": d_angle, "rect": rect, "d": d})

edges = []
for dash in dashes:
    matched = False
    for edge in edges:
        avg_a = sum(ds["angle"] for ds in edge) / len(edge)
        a_diff = min(abs(dash["angle"] - avg_a), 180 - abs(dash["angle"] - avg_a))
        
        if a_diff < 5:
            ag = math.radians(avg_a)
            calc_rho = dash["cx"] * math.sin(ag) - dash["cy"] * math.cos(ag)
            avg_rho = sum((ds["cx"] * math.sin(ag) - ds["cy"] * math.cos(ag)) for ds in edge) / len(edge)
            
            if abs(calc_rho - avg_rho) < 15: # Collinear tolerance
                min_dist = min(math.hypot(dash["cx"]-ds["cx"], dash["cy"]-ds["cy"]) for ds in edge)
                if min_dist < 40.0:
                    edge.append(dash)
                    matched = True
                    break
    
    if not matched:
        edges.append([dash])

clusters = [[i] for i in range(len(edges))]

def get_endpoints(edge):
    rad = math.radians(edge[0]["angle"])
    def proj(ds): return ds["cx"] * math.cos(rad) + ds["cy"] * math.sin(rad)
    sorted_dashes = sorted(edge, key=proj)
    return (sorted_dashes[0]["cx"], sorted_dashes[0]["cy"]), (sorted_dashes[-1]["cx"], sorted_dashes[-1]["cy"])

endpts = [get_endpoints(e) for e in edges]

for i in range(len(edges)):
    for j in range(i+1, len(edges)):
        e1_1, e1_2 = endpts[i]
        e2_1, e2_2 = endpts[j]
        d1 = math.hypot(e1_1[0]-e2_1[0], e1_1[1]-e2_1[1])
        d2 = math.hypot(e1_1[0]-e2_2[0], e1_1[1]-e2_2[1])
        d3 = math.hypot(e1_2[0]-e2_1[0], e1_2[1]-e2_1[1])
        d4 = math.hypot(e1_2[0]-e2_2[0], e1_2[1]-e2_2[1])
        
        # Cross intersection or Endpoint bridging tolerance
        if min(d1, d2, d3, d4) < 30.0:
            old_c = clusters[j]
            new_c = clusters[i]
            if old_c is not new_c:
                new_c.extend(old_c)
                for idx in old_c: clusters[idx] = new_c

unique_clusters = []
for c in clusters:
    if c not in unique_clusters:
        unique_clusters.append(c)

doc_out = fitz.open()
page_out = doc_out.new_page(width=page.rect.width, height=page.rect.height)
page_out.draw_rect(page_out.rect, color=(1,1,1), fill=(1,1,1))

# Generate distinct hues iteratively instead of fully random to guarantee visibility
num_valid = sum(1 for c in unique_clusters if len(c) >= 2)
hue_step = 1.0 / max(1, num_valid)
hue_current = 0.0

for c_group in unique_clusters:
    # Filter tiny standalone groups to remove visual noise
    if len(c_group) < 2: continue
    
    r, g, b = colorsys.hsv_to_rgb(hue_current, 1.0, 0.9)
    hue_current += hue_step
    
    shape = page_out.new_shape()
    has_dashes = False
    for e_idx in c_group:
        edge = edges[e_idx]
        for dash in edge:
            has_dashes = True
            d = dash["d"]
            for item in d["items"]:
                if item[0] == "l": shape.draw_line(item[1], item[2])
                elif item[0] == "re": shape.draw_rect(item[1])
                elif item[0] == "c": shape.draw_bezier(item[1], item[2], item[3], item[4])
    
    if has_dashes:
        shape.finish(color=(r,g,b), fill=(r,g,b), width=2)
        shape.commit()

out_path = "outputs/step17_colored_rectangles_fixed.png"
page_out.get_pixmap(dpi=200).save(out_path)
print(f"Grouped dashes into {num_valid} footprint rectangles. Saved to {out_path}.")

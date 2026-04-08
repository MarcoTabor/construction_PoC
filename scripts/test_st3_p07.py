import fitz
import os
import math

def trace_dashes():
    doc = fitz.open("examples/Joal 502-General Plan.pdf")
    page = doc[0]
    term = "ST3-P07"
    
    texts = page.get_text("dict")["blocks"]
    found_bboxes = []
    for b in texts:
        if "lines" in b:
            for l in b["lines"]:
                for s in l["spans"]:
                    if term in s["text"]:
                        found_bboxes.append(s["bbox"])
    
    print(f"--- TEXT BLOCKS containing {term} ---")
    print(f"Total {term} found: {len(found_bboxes)}")
    
    drawings = page.get_drawings()
    
    for bbox in found_bboxes:
        tx0, ty0, tx1, ty1 = bbox
        
        dash_candidates = []
        for i, d in enumerate(drawings):
            p_rect = d.get("rect")
            if not p_rect: continue
            px0, py0, px1, py1 = p_rect
            if tx0 - 80 < px0 and px1 < tx1 + 80 and ty0 - 80 < py0 and py1 < ty1 + 80:
                color = d.get("color")
                width = d.get("width")
                if color is None or sum(color) < 2.0:
                    if width and width >= 0.45:
                        dx = px1 - px0
                        dy = py1 - py0
                        if 1 < dx < 15 and 1 < dy < 15:
                            dash_candidates.append(i)
                            
        signatures = set()
        for i in dash_candidates:
            d = drawings[i]
            color = d.get("color") or (0, 0, 0)
            width = d.get("width")
            signatures.add((tuple(color), width))
            
        all_matching_dashes = []
        for i, d in enumerate(drawings):
            p_rect = d.get("rect")
            if not p_rect: continue
            px0, py0, px1, py1 = p_rect
            color = d.get("color") or (0, 0, 0)
            width = d.get("width")
            
            if (tuple(color), width) in signatures:
                dx = px1 - px0
                dy = py1 - py0
                if dx < 15 and dy < 15:
                    all_matching_dashes.append({
                        "id": i,
                        "rect": p_rect,
                        "cx": (px0 + px1) / 2,
                        "cy": (py0 + py1) / 2
                    })
                    
        id_to_dash = {d["id"]: d for d in all_matching_dashes}
        visited_ids = set(dash_candidates)
        front = [cid for cid in dash_candidates if cid in id_to_dash]
        connected_dashes = []
        
        while front:
            current_id = front.pop(0)
            if current_id not in id_to_dash:
                continue
            curr_dash = id_to_dash[current_id]
            if curr_dash not in connected_dashes:
                connected_dashes.append(curr_dash)
                
            for other_dash in all_matching_dashes:
                other_id = other_dash["id"]
                if other_id in visited_ids:
                    continue
                dist = math.hypot(curr_dash["cx"] - other_dash["cx"], curr_dash["cy"] - other_dash["cy"])
                if dist < 45:
                    visited_ids.add(other_id)
                    front.append(other_id)
                    
        print(f"Traversed and connected {len(connected_dashes)} dashes in the array!")
        
        # DRAW A SINGLE POLYGON RATHER THAN 7000 RECTANGLES TO AVOID PYMUPDF MEMORY CRASH
        points = [fitz.Point(d["cx"], d["cy"]) for d in connected_dashes]
        if points:
            # We don't necessarily have them in order, so rather than polygon, we just draw circles or tiny dots using a single shape
            shape = page.new_shape()
            for p in points:
                shape.draw_circle(p, radius=2)
            shape.finish(color=(1, 0, 0), width=1.5)
            shape.commit()
            
        page.draw_rect(bbox, color=(0, 0, 1), width=2.0)
        
    os.makedirs("outputs", exist_ok=True)
    out_path = "outputs/test_st3_p07_marked.png"
    pix = page.get_pixmap(dpi=150)
    pix.save(out_path)
    print("Done!")
    doc.close()

if __name__ == "__main__":
    trace_dashes()

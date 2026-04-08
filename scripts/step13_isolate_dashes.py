import fitz

doc = fitz.open("examples/Joal 502-General Plan.pdf")
page = doc[0]

doc_blank = fitz.open()
blank_page = doc_blank.new_page(width=page.rect.width, height=page.rect.height)
shape = blank_page.new_shape()

drawings = page.get_drawings()
dash_count = 0

for d in drawings:
    rect = d["rect"]
    fill = d.get("fill")
    color = d.get("color")
    items = d["items"]
    
    # Mathematical criteria based on empirical log:
    # 1. Fill is solid black
    # 2. No stroke color normally, but we can just rely on fill
    # 3. Shape area is bounded
    
    # Many of them we saw had 6 lines ('l', 'l', 'l', 'l', 'l', 'l') 
    # and area roughly 10 - 20 (we can use 5 to 50 to be safe)
    
    if fill == (0.0, 0.0, 0.0) or fill == [0.0, 0.0, 0.0] or fill == 0.0:
        area = rect.width * rect.height
        
        # Filter for the dashed rectangular shapes
        if 5 < area < 100:
            # We don't strictly assert exactly 6 lines because CAD can be weird,
            # but if it fits the small rectangle profile, we grab it.
            dash_count += 1
            
            # Since these are filled areas, we can just draw them as rectangles on our blank page
            # Or precisely replicate their path
            for item in items:
                if item[0] == "l": shape.draw_line(item[1], item[2])
                elif item[0] == "re": shape.draw_rect(item[1])
                elif item[0] == "c": shape.draw_bezier(item[1], item[2], item[3], item[4])
                
            shape.finish(color=(1, 0, 0), fill=(1, 0, 0), width=0.5)

shape.commit()

out_path = "outputs/step13_isolated_dashes.png"
blank_page.get_pixmap(dpi=300).save(out_path)
print(f"Isolated {dash_count} dash elements and saved to {out_path}.")

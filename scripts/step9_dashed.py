import fitz

doc = fitz.open("examples/Joal 502-General Plan.pdf")
page = doc[0]

drawings = page.get_drawings()
shape = page.new_shape()

found_dashed = 0
for d in drawings:
    dashes = d.get("dashes", None)
    
    # In fitz, dashes is either None, or a string like "[2] 0"
    if dashes and str(dashes) != "[] 0":
        # Let's draw the actual path this time, so we see what it is
        for item in d["items"]:
            if item[0] == "l":
                shape.draw_line(item[1], item[2])
            elif item[0] == "c":
                shape.draw_bezier(item[1], item[2], item[3], item[4])
        found_dashed += 1

shape.finish(color=(1, 0, 0), width=2)
shape.commit()

page.get_pixmap(dpi=300).save("outputs/step9_dashed_vectors.png")
print(f"Found {found_dashed} dashed vector paths.")

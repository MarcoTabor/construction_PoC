import fitz
import os

doc = fitz.open("examples/Joal 502-General Plan.pdf")
page = doc[0]
term = "ST3-P07"

texts = page.get_text("dict")["blocks"]

for b in texts:
    if "lines" in b:
        for l in b["lines"]:
            for s in l["spans"]:
                if term in s["text"]:
                    bbox = s["bbox"]
                    print(f"Found '{term}' at bbox: {bbox}")
                    
                    # Calculate the center of the text bounding box
                    cx = (bbox[0] + bbox[2]) / 2
                    cy = (bbox[1] + bbox[3]) / 2
                    print(f"Center coordinates calculated: ({cx}, {cy})")
                    
                    # Draw a clearly visible red dot at the center
                    shape = page.new_shape()
                    shape.draw_circle(fitz.Point(cx, cy), radius=3)
                    shape.finish(color=(1, 0, 0), fill=(1, 0, 0))
                    shape.commit()

os.makedirs("outputs", exist_ok=True)
out_path = "outputs/step1_center.png"
page.get_pixmap(dpi=150).save(out_path)
print(f"Saved image to {out_path}")
doc.close()

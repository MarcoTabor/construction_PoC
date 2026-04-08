import fitz

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

search_rect = fitz.Rect(cx - 50, cy - 50, cx + 50, cy + 50)

drawings = page.get_drawings()
thick_or_dashed = 0
black_lines = 0

print(f"Analyzing {len(drawings)} total drawings on page...")

for i, d in enumerate(drawings):
    rect = d["rect"]
    if not rect.intersects(search_rect):
        continue
    
    color = d.get("color")
    width = d.get("width")
    if width is None: width = 0
    dashes = d.get("dashes")

    is_black = False
    if color is not None and sum(color) < 0.1:
        is_black = True
        black_lines += 1

    # Print out any dashed lines we find in the area!
    if dashes:
        print(f"Found dashed drawing {i}, color {color}, width {width}, box {rect}")
        thick_or_dashed += 1
    elif width > 2:
        # It's a thick line, maybe a solid bar?
        if is_black:
            print(f"Found thick black drawing {i}, width {width}, box {rect}")
        thick_or_dashed += 1
        
print(f"Total drawings intersecting radius: {sum(1 for d in drawings if d['rect'].intersects(search_rect))}")
print(f"Thick or dashed vector lines near ST3-P07: {thick_or_dashed}")

doc.close()

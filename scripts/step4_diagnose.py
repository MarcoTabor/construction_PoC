import fitz

doc = fitz.open("examples/Joal 502-General Plan.pdf")
page = doc[0]

# 1. Look for ST3-P07
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
print(f"Center: {cx}, {cy}")

# 2. Check for images
images = page.get_images(full=True)
print(f"Total images on page: {len(images)}")
image_rects = []
for index, img in enumerate(images):
    rects = page.get_image_rects(img[0])
    for r in rects:
        if r.intersects(search_rect):
            print(f"Image {img[0]} intersects our search area! Box: {r}")
            image_rects.append(r)

# 3. Check for vector paths matching black color or thick dashes
drawings = page.get_drawings()
thick_or_dashed = 0
black_lines = 0
for d in drawings:
    rect = d["rect"]
    if not rect.intersects(search_rect):
        continue
    
    color = d.get("color")
    fill = d.get("fill")
    width = d.get("width", 0)
    dashes = d.get("dashes")

    # Many blacks in PDF are [0], [0,0,0], or None depending on colorspace
    is_black = False
    if color is not None and sum(color) < 0.1:
        is_black = True

    if is_black:
        black_lines += 1
    
    if dashes or width > 2:
        thick_or_dashed += 1

print(f"Vectors intersecting area: {sum(1 for d in drawings if d['rect'].intersects(search_rect))}")
print(f" - Of those, black colored: {black_lines}")
print(f" - Of those, thick (>2) or dashed: {thick_or_dashed}")

doc.close()

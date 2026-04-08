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

radius = 20
search_rect = fitz.Rect(cx - radius, cy - radius, cx + radius, cy + radius)

# We will draw semi-transparent orange rectangles over every raster image
# that touches our area to prove what is inside an image.
shape = page.new_shape()
shape.draw_circle(fitz.Point(cx, cy), radius=radius)
shape.finish(color=(0, 1, 0), width=0.5, dashes="[2 2] 0")

images = page.get_images(full=True)
found_images = 0

for img in images:
    xref = img[0]
    rects = page.get_image_rects(xref)
    for r in rects:
        # Check if the image overlaps our 20pt radius center bounding box
        if r.intersects(search_rect):
            found_images += 1
            shape.draw_rect(r)
            # Orange, somewhat thick to stand out
            shape.finish(color=(1, 0.5, 0), width=1.5)

shape.commit()

out_path = "outputs/step7_embedded_images.png"
# Expand the clip slightly to see the images
page.get_pixmap(dpi=300, clip=fitz.Rect(cx - 200, cy - 200, cx + 200, cy + 200)).save(out_path)
print(f"Saved to {out_path} marking {found_images} embedded raster images.")
doc.close()

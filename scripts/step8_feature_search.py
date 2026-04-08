import fitz

doc = fitz.open("examples/Joal 502-General Plan.pdf")
page = doc[0]

drawings = page.get_drawings()
shape_vectors = page.new_shape()
large_vector_count = 0

for d in drawings:
    rect = d["rect"]
    if rect.width > 200 and rect.height > 200:
        shape_vectors.draw_rect(rect)
        large_vector_count += 1

shape_vectors.finish(color=(1, 0, 0), width=1)
shape_vectors.commit()

page.get_pixmap(dpi=150).save("outputs/step8_large_vectors.png")
print(f"Found {large_vector_count} large vector bounding boxes.")

doc2 = fitz.open("examples/Joal 502-General Plan.pdf")
page2 = doc2[0]
shape_images = page2.new_shape()

images = page2.get_images(full=True)
img_count = 0
for img in images:
    xref = img[0]
    rects = page2.get_image_rects(xref)
    for r in rects:
        shape_images.draw_rect(r)
        img_count += 1

shape_images.finish(color=(0, 0, 1), width=1)
shape_images.commit()

page2.get_pixmap(dpi=150).save("outputs/step8_all_images.png")
print(f"Found {img_count} total raster image tiles on the page.")

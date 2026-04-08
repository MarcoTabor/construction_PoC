import fitz

doc = fitz.open("examples/Joal 502-General Plan.pdf")
page = doc[0]

doc_blank = fitz.open()
blank_page = doc_blank.new_page(width=page.rect.width, height=page.rect.height)
shape = blank_page.new_shape()

for d in page.get_drawings():
    for item in d["items"]:
        if item[0] == "l": shape.draw_line(item[1], item[2])
        elif item[0] == "c": shape.draw_bezier(item[1], item[2], item[3], item[4])
        elif item[0] == "re": shape.draw_rect(item[1])
        elif item[0] == "qu": shape.draw_quad(item[1])

    width = d.get("width")
    if width is None: width = 1.0
    fill = d.get("fill")
    shape.finish(color=(0, 0, 1), fill=fill, width=width)

shape.commit()

blank_page.get_pixmap(dpi=150).save("outputs/step10_vectors_only.png")
print("Saved outputs/step10_vectors_only.png")

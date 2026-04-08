import fitz

doc = fitz.open("examples/Joal 502-General Plan.pdf")
page = doc[0]

term_rect = fitz.Rect(400, 270, 500, 370)

drawings = page.get_drawings()

print("Inspecting drawings near ST3-P07:")
for d in drawings:
    rect = d["rect"]
    if rect.intersects(term_rect):
        area = rect.width * rect.height
        if area < 1000:
            types = [item[0] for item in d["items"]]
            fill = d.get("fill")
            color = d.get("color")
            print(f"Items: {types}, Fill: {fill}, Color: {color}, Rect: {rect.width:.1f}x{rect.height:.1f}, Area: {area:.1f}")

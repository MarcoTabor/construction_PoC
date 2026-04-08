import fitz

doc = fitz.open("examples/Joal 502-General Plan.pdf")
page = doc[0]

drawings = page.get_drawings()

black_rects = 0
for d in drawings:
    # Check if it's a completely filled black shape (often used for custom dashes)
    fill = d.get("fill")
    if fill == [0.0, 0.0, 0.0] or fill == (0.0, 0.0, 0.0) or fill == 0.0: # Black fill
        black_rects += 1

print(f"Found {black_rects} solid black filled vector paths.")

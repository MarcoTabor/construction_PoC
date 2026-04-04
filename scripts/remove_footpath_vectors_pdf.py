#!/usr/bin/env python3
"""Remove selected footpath vectors from a copied PDF via redaction rectangles.

Note:
- This removes content in each selected vector bounding box, not the exact path only.
- If objects overlap those boxes, they will also be removed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import fitz
import numpy as np


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Remove selected vectors from PDF and render a cleaner image")
    parser.add_argument("--pdf", default="examples/Joal 502.pdf", help="Input PDF")
    parser.add_argument("--sets-json", default="outputs/joal502/footpath_vector_sets.json", help="Input sets JSON")
    parser.add_argument("--set-name", choices=["structural", "visible", "hidden_or_occluded"], default="visible", help="Which set to remove")
    parser.add_argument("--out-pdf", default="outputs/joal502/Joal_502_cleaned.pdf", help="Output cleaned PDF")
    parser.add_argument("--out-png", default="outputs/joal502/visualizations/Joal_502_cleaned.png", help="Output rendered PNG from cleaned PDF")
    parser.add_argument("--page", type=int, default=1, help="1-based page")
    parser.add_argument("--dpi", type=int, default=300, help="Render DPI for output PNG")
    parser.add_argument("--fill-gray", type=float, default=1.0, help="Redaction fill gray in [0,1], 1=white")
    return parser.parse_args()


def rect_from_list(values: list[Any]) -> fitz.Rect:
    return fitz.Rect(float(values[0]), float(values[1]), float(values[2]), float(values[3]))


def clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def main() -> int:
    args = parse_args()

    pdf_path = Path(args.pdf)
    sets_path = Path(args.sets_json)
    out_pdf = Path(args.out_pdf)
    out_png = Path(args.out_png)

    if not pdf_path.exists():
        raise FileNotFoundError(f"Missing PDF: {pdf_path}")
    if not sets_path.exists():
        raise FileNotFoundError(f"Missing sets JSON: {sets_path}")

    sets_payload = json.loads(sets_path.read_text(encoding="utf-8"))
    vectors = sets_payload.get(args.set_name, [])

    doc = fitz.open(pdf_path)
    page_index = max(0, args.page - 1)
    if page_index >= doc.page_count:
        raise ValueError(f"Page out of range: {args.page}")
    page = doc[page_index]

    fill = clamp01(float(args.fill_gray))
    fill_rgb = (fill, fill, fill)

    redactions = 0
    for v in vectors:
        if int(v.get("page", 0)) != args.page:
            continue
        rect = v.get("rect")
        if not isinstance(rect, list) or len(rect) != 4:
            continue
        r = rect_from_list(rect)
        if r.is_empty or r.width <= 0 or r.height <= 0:
            continue
        page.add_redact_annot(r, fill=fill_rgb)
        redactions += 1

    if redactions > 0:
        page.apply_redactions()

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out_pdf, garbage=4, deflate=True)

    page_clean = doc[page_index]
    pix = page_clean.get_pixmap(dpi=args.dpi, alpha=False)
    rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]
    doc.close()

    out_png.parent.mkdir(parents=True, exist_ok=True)

    try:
        import cv2
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("opencv-python-headless is required for PNG export") from exc

    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    cv2.imwrite(str(out_png), bgr)

    print(f"Wrote cleaned PDF: {out_pdf}")
    print(f"Wrote cleaned PNG: {out_png}")
    print(f"Applied redactions: {redactions}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

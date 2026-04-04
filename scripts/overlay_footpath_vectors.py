#!/usr/bin/env python3
"""Render a transparent-base PNG and overlay detected footpath vectors.

Inputs:
- PDF page (rendered to base PNG in memory)
- Footpath vectors JSON from scripts/find_footpath_vectors.py

Output:
- Overlay PNG where the base page is faded and vectors are highlighted.

Usage:
    python scripts/overlay_footpath_vectors.py
    python scripts/overlay_footpath_vectors.py --dpi 300 --base-alpha 0.42
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import fitz
import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Overlay detected footpath vectors on a faded PDF render")
    parser.add_argument("--pdf", default="examples/Joal 502.pdf", help="Input PDF")
    parser.add_argument("--vectors-json", default="outputs/joal502/footpath_vectors.json", help="Input vectors JSON")
    parser.add_argument("--out", default="outputs/joal502/visualizations/footpath_vectors_overlay.png", help="Output PNG")
    parser.add_argument("--page", type=int, default=1, help="1-based PDF page")
    parser.add_argument("--dpi", type=int, default=300, help="Render DPI")
    parser.add_argument("--base-alpha", type=float, default=0.45, help="Base image opacity factor in [0,1]")
    parser.add_argument("--line-thickness", type=int, default=2, help="Overlay line thickness in pixels")
    parser.add_argument("--curve-steps", type=int, default=20, help="Bezier approximation segments")
    return parser.parse_args()


def ensure_cv2() -> None:
    if cv2 is None:
        raise RuntimeError("opencv-python-headless is required. Install from requirements.txt")


def bezier_point(p0: np.ndarray, c1: np.ndarray, c2: np.ndarray, p1: np.ndarray, t: float) -> np.ndarray:
    omt = 1.0 - t
    return (
        (omt ** 3) * p0
        + 3.0 * (omt ** 2) * t * c1
        + 3.0 * omt * (t ** 2) * c2
        + (t ** 3) * p1
    )


def pt_to_px(pt: list[float], sx: float, sy: float) -> tuple[int, int]:
    return (int(round(pt[0] * sx)), int(round(pt[1] * sy)))


def draw_path_items(
    canvas_bgr: np.ndarray,
    path_items: list[dict[str, Any]],
    sx: float,
    sy: float,
    color_bgr: tuple[int, int, int],
    thickness: int,
    curve_steps: int,
) -> None:
    for item in path_items:
        op = item.get("op")

        if op == "line":
            p0 = item.get("p0")
            p1 = item.get("p1")
            if p0 and p1:
                cv2.line(canvas_bgr, pt_to_px(p0, sx, sy), pt_to_px(p1, sx, sy), color_bgr, thickness, lineType=cv2.LINE_AA)

        elif op == "rect":
            rect = item.get("rect")
            if rect and len(rect) == 4:
                x0, y0 = pt_to_px([rect[0], rect[1]], sx, sy)
                x1, y1 = pt_to_px([rect[2], rect[3]], sx, sy)
                cv2.rectangle(canvas_bgr, (min(x0, x1), min(y0, y1)), (max(x0, x1), max(y0, y1)), color_bgr, thickness, lineType=cv2.LINE_AA)

        elif op == "curve":
            p0 = item.get("p0")
            c1 = item.get("c1")
            c2 = item.get("c2")
            p1 = item.get("p1")
            if not (p0 and c1 and c2 and p1):
                continue

            p0n = np.array(p0, dtype=np.float64)
            c1n = np.array(c1, dtype=np.float64)
            c2n = np.array(c2, dtype=np.float64)
            p1n = np.array(p1, dtype=np.float64)

            pts: list[tuple[int, int]] = []
            for i in range(max(2, curve_steps + 1)):
                t = i / max(1, curve_steps)
                p = bezier_point(p0n, c1n, c2n, p1n, t)
                pts.append(pt_to_px([float(p[0]), float(p[1])], sx, sy))

            for a, b in zip(pts, pts[1:]):
                cv2.line(canvas_bgr, a, b, color_bgr, thickness, lineType=cv2.LINE_AA)


def run(args: argparse.Namespace) -> Path:
    ensure_cv2()

    pdf_path = Path(args.pdf)
    vectors_path = Path(args.vectors_json)
    out_path = Path(args.out)

    if not pdf_path.exists():
        raise FileNotFoundError(f"Missing PDF: {pdf_path}")
    if not vectors_path.exists():
        raise FileNotFoundError(f"Missing vectors JSON: {vectors_path}")

    doc = fitz.open(pdf_path)
    page_index = max(0, args.page - 1)
    if page_index >= doc.page_count:
        raise ValueError(f"Page out of range: {args.page}")

    page = doc[page_index]
    pix = page.get_pixmap(dpi=args.dpi, alpha=False)
    page_width = float(page.rect.width)
    page_height = float(page.rect.height)
    doc.close()

    rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3].copy()
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

    alpha = min(1.0, max(0.0, float(args.base_alpha)))
    faded = cv2.addWeighted(bgr, alpha, np.full_like(bgr, 255), 1.0 - alpha, 0.0)

    sx = pix.width / page_width
    sy = pix.height / page_height

    payload = json.loads(vectors_path.read_text(encoding="utf-8"))
    vectors = payload.get("vectors", [])

    overlay_color = (25, 60, 235)  # red-ish in BGR
    for v in vectors:
        if int(v.get("page", 0)) != args.page:
            continue
        path_items = v.get("path_items") or []
        draw_path_items(
            canvas_bgr=faded,
            path_items=path_items,
            sx=sx,
            sy=sy,
            color_bgr=overlay_color,
            thickness=max(1, int(args.line_thickness)),
            curve_steps=max(4, int(args.curve_steps)),
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), faded)

    return out_path


def main() -> int:
    args = parse_args()
    out = run(args)
    print(f"Wrote overlay image to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

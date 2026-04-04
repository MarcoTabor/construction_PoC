#!/usr/bin/env python3
"""Classify footpath vectors into structural vs visible sets.

Structural set:
- All vectors from a precomputed footpath candidate JSON.

Visible set:
- Vectors whose bounding box contains enough rendered pixels near target footpath color.

This keeps PDF semantics (object exists) separate from rendered visibility.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import fitz
import numpy as np


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Classify footpath vectors by rendered visibility")
    parser.add_argument("--pdf", default="examples/Joal 502.pdf", help="Input PDF")
    parser.add_argument("--vectors-json", default="outputs/joal502/footpath_vectors_relaxed.json", help="Input vectors JSON")
    parser.add_argument("--out", default="outputs/joal502/footpath_vector_sets.json", help="Output JSON")
    parser.add_argument("--page", type=int, default=1, help="1-based page")
    parser.add_argument("--dpi", type=int, default=300, help="Render DPI")
    parser.add_argument("--pixel-threshold", type=float, default=0.06, help="RGB distance threshold on rendered pixels")
    parser.add_argument("--visible-ratio", type=float, default=0.03, help="Minimum near-color pixel ratio in vector bbox")
    parser.add_argument("--min-pixels", type=int, default=25, help="Minimum near-color pixel count in bbox")
    return parser.parse_args()


def clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def color_distance_image(rgb: np.ndarray, target: np.ndarray) -> np.ndarray:
    diff = rgb - target[None, None, :]
    return np.sqrt(np.sum(diff * diff, axis=2))


def main() -> int:
    args = parse_args()

    pdf_path = Path(args.pdf)
    vectors_path = Path(args.vectors_json)
    out_path = Path(args.out)

    if not pdf_path.exists():
        raise FileNotFoundError(f"Missing PDF: {pdf_path}")
    if not vectors_path.exists():
        raise FileNotFoundError(f"Missing vectors JSON: {vectors_path}")

    payload = json.loads(vectors_path.read_text(encoding="utf-8"))
    vectors = payload.get("vectors", [])
    summary = payload.get("summary", {})
    target = summary.get("target_color")
    if not isinstance(target, list) or len(target) < 3:
        raise RuntimeError("target_color not found in input vectors JSON summary")

    doc = fitz.open(pdf_path)
    page_index = max(0, args.page - 1)
    if page_index >= doc.page_count:
        raise ValueError(f"Page out of range: {args.page}")

    page = doc[page_index]
    pix = page.get_pixmap(dpi=args.dpi, alpha=False)
    page_width = float(page.rect.width)
    page_height = float(page.rect.height)
    doc.close()

    rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3].astype(np.float32)
    sx = pix.width / page_width
    sy = pix.height / page_height

    target_np = np.array([float(target[0]), float(target[1]), float(target[2])], dtype=np.float32)
    dist_map = color_distance_image(rgb / 255.0, target_np)
    near_mask = dist_map <= float(args.pixel_threshold)

    structural = []
    visible = []
    hidden = []

    for v in vectors:
        if int(v.get("page", 0)) != args.page:
            continue

        rect = v.get("rect")
        if not isinstance(rect, list) or len(rect) != 4:
            continue

        x0 = clamp(int(round(float(rect[0]) * sx)), 0, pix.width)
        y0 = clamp(int(round(float(rect[1]) * sy)), 0, pix.height)
        x1 = clamp(int(round(float(rect[2]) * sx)), 0, pix.width)
        y1 = clamp(int(round(float(rect[3]) * sy)), 0, pix.height)
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0

        w = max(0, x1 - x0)
        h = max(0, y1 - y0)
        area = w * h
        if area <= 0:
            continue

        roi = near_mask[y0:y1, x0:x1]
        near_count = int(np.count_nonzero(roi))
        ratio = float(near_count / area)

        rec = {
            "page": int(v.get("page", 0)),
            "index_on_page": int(v.get("index_on_page", -1)),
            "seqno": int(v.get("seqno", -1)),
            "type": v.get("type"),
            "rect": rect,
            "match_channel": v.get("match_channel"),
            "match_distance": v.get("match_distance"),
            "visible_ratio": ratio,
            "visible_pixels": near_count,
            "bbox_pixels": int(area),
        }

        structural.append(rec)
        if ratio >= float(args.visible_ratio) and near_count >= int(args.min_pixels):
            visible.append(rec)
        else:
            hidden.append(rec)

    result = {
        "summary": {
            "pdf": str(pdf_path),
            "page": args.page,
            "dpi": args.dpi,
            "target_color": target,
            "pixel_threshold": args.pixel_threshold,
            "visible_ratio_threshold": args.visible_ratio,
            "min_pixels": args.min_pixels,
            "structural_count": len(structural),
            "visible_count": len(visible),
            "hidden_or_occluded_count": len(hidden),
        },
        "structural": structural,
        "visible": visible,
        "hidden_or_occluded": hidden,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print(
        f"Wrote sets to {out_path} | structural={len(structural)} visible={len(visible)} hidden={len(hidden)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

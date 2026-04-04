#!/usr/bin/env python3
"""Step 1 – Place seeds inside the raw mask and visualise them.

Seeds are placed greedily with a minimum inter-seed distance so that every
seed sits on a white (footpath) pixel and no two seeds are closer than
    outputs/footpath_pixel_pipeline/visualizations/seeds_01_placement.png
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np
import json

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Seed placement in footpath raw mask")
    p.add_argument("--mask",  default="outputs/footpath_pixel_pipeline/visualizations/stage_02_raw_mask.png")
    p.add_argument("--page",  default="outputs/footpath_pixel_pipeline/visualizations/stage_01_page.png")
    p.add_argument("--outdir", default="outputs/footpath_pixel_pipeline/visualizations")
    p.add_argument("--legend-json", default="outputs/legend_colors/legend_colors.json",
                   help="Legend JSON containing legend_region_bbox in PDF points")
    p.add_argument("--pdf", default="examples/Joal 502.pdf",
                   help="Source PDF – used to get page rect for coordinate conversion")
    p.add_argument("--pdf-page", type=int, default=1)
    p.add_argument("--legend-pad", type=int, default=12,
                   help="Padding (px) around computed legend exclusion box")
    p.add_argument("--min-dist", type=int, default=30,
                   help="Minimum pixel distance between seeds")
    p.add_argument("--erode-px", type=int, default=4,
                   help="Erode mask by this many pixels before sampling (keeps seeds away from edges)")
    return p.parse_args()


def greedy_seeds(mask: np.ndarray, min_dist: int) -> list[tuple[int, int]]:
    """Return (x, y) seed list using greedy Poisson-disk on white pixels."""
    ys, xs = np.where(mask > 0)
    if len(xs) == 0:
        return []

    # Sort top-to-bottom, left-to-right for a predictable order
    order = np.lexsort((xs, ys))
    pts = list(zip(xs[order].tolist(), ys[order].tolist()))

    taken = np.zeros_like(mask, dtype=bool)
    seeds: list[tuple[int, int]] = []
    min_d2 = min_dist * min_dist

    for x, y in pts:
        if taken[y, x]:
            continue
        seeds.append((x, y))
        # Mark a circle of radius min_dist
        r = min_dist
        y0, y1 = max(0, y - r), min(mask.shape[0], y + r + 1)
        x0, x1 = max(0, x - r), min(mask.shape[1], x + r + 1)
        gy, gx = np.ogrid[y0:y1, x0:x1]
        circle = (gy - y) ** 2 + (gx - x) ** 2 <= min_d2
        taken[y0:y1, x0:x1][circle] = True

    return seeds


def main() -> None:
    args = parse_args()

    mask_bgr = cv2.imread(str(args.mask))
    page_bgr = cv2.imread(str(args.page))

    if mask_bgr is None:
        raise FileNotFoundError(f"Mask not found: {args.mask}")
    if page_bgr is None:
        raise FileNotFoundError(f"Page not found: {args.page}")

    # Binary mask: white pixels are footpath candidates
    gray = cv2.cvtColor(mask_bgr, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 127, 255, cv2.THRESH_BINARY)

    # Optional erosion to keep seeds off the thin edges
    if args.erode_px > 0:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE,
                                           (2 * args.erode_px + 1, 2 * args.erode_px + 1))
        binary = cv2.erode(binary, kernel)

    # --- Compute legend exclusion rect in pixel space ---
    legend_excl = None  # (px0, py0, px1, py1)
    legend_json_path = Path(args.legend_json)
    if legend_json_path.exists() and fitz is not None:
        legend_data = json.loads(legend_json_path.read_text(encoding="utf-8"))
        doc = fitz.open(str(args.pdf))
        page = doc.load_page(args.pdf_page - 1)
        mh, mw = gray.shape[:2]
        sx = mw / page.rect.width
        sy = mh / page.rect.height
        doc.close()

        # Prefer tight box from explicit legend entry bboxes.
        x0s: list[float] = []
        y0s: list[float] = []
        x1s: list[float] = []
        y1s: list[float] = []
        for entry in legend_data.get("legend_entries", []):
            lb = entry.get("label_bbox")
            if isinstance(lb, list) and len(lb) == 4:
                x0s.append(float(lb[0]))
                y0s.append(float(lb[1]))
                x1s.append(float(lb[2]))
                y1s.append(float(lb[3]))

            sw = entry.get("swatch") or {}
            sb = sw.get("bbox") if isinstance(sw, dict) else None
            if isinstance(sb, list) and len(sb) == 4:
                x0s.append(float(sb[0]))
                y0s.append(float(sb[1]))
                x1s.append(float(sb[2]))
                y1s.append(float(sb[3]))

        if x0s:
            bbox_pdf = [min(x0s), min(y0s), max(x1s), max(y1s)]
            source = "legend_entries"
        else:
            bbox_pdf = legend_data.get("legend_region_bbox")
            source = "legend_region_bbox"

        if isinstance(bbox_pdf, list) and len(bbox_pdf) == 4:
            pad = args.legend_pad
            lx0 = max(0, int(round(float(bbox_pdf[0]) * sx)) - pad)
            ly0 = max(0, int(round(float(bbox_pdf[1]) * sy)) - pad)
            lx1 = min(mw, int(round(float(bbox_pdf[2]) * sx)) + pad)
            ly1 = min(mh, int(round(float(bbox_pdf[3]) * sy)) + pad)
            legend_excl = (lx0, ly0, lx1, ly1)
            print(f"Legend exclusion source: {source}")
            print(f"Legend exclusion rect (px): x={lx0}-{lx1}, y={ly0}-{ly1}")
    elif fitz is None:
        print("Warning: PyMuPDF not available – legend exclusion skipped")

    all_seeds = greedy_seeds(binary, args.min_dist)

    # Remove seeds inside the legend box
    if legend_excl:
        lx0, ly0, lx1, ly1 = legend_excl
        seeds = [(x, y) for x, y in all_seeds if not (lx0 <= x <= lx1 and ly0 <= y <= ly1)]
        print(f"Seeds before legend filter: {len(all_seeds)}  after: {len(seeds)}")
    else:
        seeds = all_seeds

    print(f"Placed {len(seeds)} seeds  (min_dist={args.min_dist}px, erode={args.erode_px}px)")

    # --- Visualisation 1: seeds on the raw mask (to see placement quality) ---
    vis_mask = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    # Draw legend exclusion box in blue so it's visible
    if legend_excl:
        lx0, ly0, lx1, ly1 = legend_excl
        cv2.rectangle(vis_mask, (lx0, ly0), (lx1, ly1), (255, 100, 0), 2)
    for x, y in seeds:
        cv2.circle(vis_mask, (x, y), 4, (0, 0, 255), -1)   # red filled dot

    # --- Visualisation 2: seeds overlaid on the page image ---
    vis_page = page_bgr.copy()
    # Scale seeds if images have different sizes
    sh, sw = page_bgr.shape[:2]
    mh, mw = gray.shape[:2]
    sx = sw / mw
    sy = sh / mh
    for x, y in seeds:
        px = int(round(x * sx))
        py = int(round(y * sy))
        cv2.circle(vis_page, (px, py), 5, (0, 0, 255), -1)   # red dot
        cv2.circle(vis_page, (px, py), 5, (255, 255, 255), 1) # white border

    out = Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)

    p1 = out / "seeds_01_on_mask.png"
    p2 = out / "seeds_01_on_page.png"
    cv2.imwrite(str(p1), vis_mask)
    cv2.imwrite(str(p2), vis_page)
    print(f"Wrote: {p1}")
    print(f"Wrote: {p2}")


if __name__ == "__main__":
    main()

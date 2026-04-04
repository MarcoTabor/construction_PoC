#!/usr/bin/env python3
"""Step 6 - Visualize width confidence along the refined path.

Color encodes deviation from median width:
- Green: near median (+/- tolerance)
- Red: strong deviation or missing width sample

Outputs:
- outputs/footpath_pixel_pipeline/visualizations/seeds_06_width_confidence_on_page.png
- outputs/footpath_pixel_pipeline/visualizations/seeds_06_width_confidence_on_clean_mask.png
- outputs/footpath_pixel_pipeline/visualizations/seeds_06_width_confidence.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Render width confidence map along centerline")
    p.add_argument("--refined-graph", default="outputs/footpath_pixel_pipeline/visualizations/seeds_04_refined_graph.json")
    p.add_argument("--width-stats", default="outputs/footpath_pixel_pipeline/visualizations/seeds_03_width_stats.json")
    p.add_argument("--clean-mask", default="outputs/footpath_pixel_pipeline/visualizations/stage_03_clean_mask.png")
    p.add_argument("--page", default="outputs/footpath_pixel_pipeline/visualizations/stage_01_page.png")
    p.add_argument("--outdir", default="outputs/footpath_pixel_pipeline/visualizations")
    p.add_argument("--line-scale", type=float, default=1.0, help="Scale rendered line thickness")
    return p.parse_args()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_ordered_path(edges: list[dict], active_ids: set[int], start_end: list[int]) -> list[int]:
    adj: dict[int, list[int]] = {i: [] for i in active_ids}
    for e in edges:
        a = int(e["a"])
        b = int(e["b"])
        if a in active_ids and b in active_ids:
            adj[a].append(b)
            adj[b].append(a)

    endpoints = [i for i in active_ids if len(adj[i]) == 1]
    if start_end and int(start_end[0]) in active_ids:
        start = int(start_end[0])
    elif endpoints:
        start = endpoints[0]
    else:
        start = min(active_ids)

    order = [start]
    prev = -1
    cur = start
    seen = {start}
    while True:
        nxts = [n for n in adj[cur] if n != prev]
        if not nxts:
            break
        nxt = nxts[0]
        if nxt in seen:
            break
        order.append(nxt)
        seen.add(nxt)
        prev, cur = cur, nxt
    return order


def confidence_bucket(sigma_dist: float, valid: bool) -> tuple[str, tuple[int, int, int]]:
    """Return bucket label + BGR color."""
    if not valid:
        return "red", (47, 47, 211)

    if sigma_dist <= 1.0:
        return "green", (83, 200, 0)
    if sigma_dist <= 2.0:
        return "yellow", (79, 213, 255)
    return "red", (47, 47, 211)


def draw_legend(img: np.ndarray) -> None:
    labels = [
        ("green: |w-mean| <= 1 sigma", (83, 200, 0)),
        ("yellow: 1 sigma < |w-mean| <= 2 sigma", (79, 213, 255)),
        ("red: |w-mean| > 2 sigma or missing", (47, 47, 211)),
    ]

    x0, y0 = 24, 24
    box_w, row_h = 310, 24
    h = 12 + row_h * len(labels) + 10

    overlay = img.copy()
    cv2.rectangle(overlay, (x0 - 10, y0 - 10), (x0 - 10 + box_w, y0 - 10 + h), (25, 25, 25), -1)
    cv2.addWeighted(overlay, 0.65, img, 0.35, 0, img)

    for i, (text, color) in enumerate(labels):
        y = y0 + i * row_h
        cv2.rectangle(img, (x0, y), (x0 + 16, y + 16), color, -1)
        cv2.putText(img, text, (x0 + 24, y + 13), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (235, 235, 235), 1, cv2.LINE_AA)


def main() -> None:
    args = parse_args()

    graph = load_json(Path(args.refined_graph))
    widths = load_json(Path(args.width_stats))

    seeds = graph["seeds"]
    edges = graph["edges"]
    start_end = graph.get("start_end") or graph.get("endpoint_ids", [])
    active_ids = {int(s["id"]) for s in seeds if s.get("active", True)}
    order = build_ordered_path(edges, active_ids, start_end)

    id_to_xy = {int(s["id"]): (int(s["x"]), int(s["y"])) for s in seeds}

    id_to_width: dict[int, float] = {}
    for s in widths.get("samples", []):
        if s.get("valid") and s.get("width") is not None and float(s.get("width")) > 0:
            id_to_width[int(s["id"])] = float(s["width"])

    width_values = np.array(list(id_to_width.values()), dtype=np.float32)
    if width_values.size == 0:
        raise RuntimeError("No valid width samples available for sigma-based confidence mapping")
    mean_w = float(np.mean(width_values))
    std_w = float(np.std(width_values))
    std_w = max(std_w, 1e-6)
    median_w = float(widths.get("width_px", {}).get("median", mean_w))

    mask_bgr = cv2.imread(str(args.clean_mask))
    page_bgr = cv2.imread(str(args.page))
    if mask_bgr is None:
        raise FileNotFoundError(f"Clean mask not found: {args.clean_mask}")
    if page_bgr is None:
        raise FileNotFoundError(f"Page not found: {args.page}")

    gray = cv2.cvtColor(mask_bgr, cv2.COLOR_BGR2GRAY)
    vis_mask = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    vis_page = page_bgr.copy()

    mh, mw = gray.shape[:2]
    ph, pw = vis_page.shape[:2]
    sx = pw / float(mw)
    sy = ph / float(mh)

    bucket_counts = {"green": 0, "yellow": 0, "red": 0}

    for i in range(len(order) - 1):
        a = order[i]
        b = order[i + 1]
        if a not in id_to_xy or b not in id_to_xy:
            continue

        wa = id_to_width.get(a)
        wb = id_to_width.get(b)
        valid = wa is not None and wb is not None
        if valid:
            w_seg = 0.5 * (wa + wb)
            sigma_dist = abs(w_seg - mean_w) / std_w
        else:
            w_seg = median_w
            sigma_dist = 1e9

        bucket, color = confidence_bucket(sigma_dist, valid)
        bucket_counts[bucket] += 1

        x0, y0 = id_to_xy[a]
        x1, y1 = id_to_xy[b]

        thick_mask = max(2, int(round(w_seg * args.line_scale)))
        cv2.line(vis_mask, (x0, y0), (x1, y1), color, thick_mask)

        p0 = (int(round(x0 * sx)), int(round(y0 * sy)))
        p1 = (int(round(x1 * sx)), int(round(y1 * sy)))
        thick_page = max(2, int(round(w_seg * sx * args.line_scale)))
        cv2.line(vis_page, p0, p1, color, thick_page)

    # Draw seed points for context.
    for sid in order:
        x, y = id_to_xy[sid]
        cv2.circle(vis_mask, (x, y), 2, (0, 0, 255), -1)
        cv2.circle(vis_page, (int(round(x * sx)), int(round(y * sy))), 2, (0, 0, 255), -1)

    draw_legend(vis_mask)
    draw_legend(vis_page)

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    p1 = outdir / "seeds_06_width_confidence_on_clean_mask.png"
    p2 = outdir / "seeds_06_width_confidence_on_page.png"
    p3 = outdir / "seeds_06_width_confidence.json"

    cv2.imwrite(str(p1), vis_mask)
    cv2.imwrite(str(p2), vis_page)

    payload = {
        "mean_width_px": mean_w,
        "std_width_px": std_w,
        "median_width_px": median_w,
        "path_segment_count": max(0, len(order) - 1),
        "bucket_counts": bucket_counts,
        "scale_definition": {
            "green": "|w-mean| <= 1 sigma",
            "yellow": "1 sigma < |w-mean| <= 2 sigma",
            "red": "|w-mean| > 2 sigma or width sample unavailable",
        },
    }
    p3.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"Path segments: {max(0, len(order) - 1)}")
    print(f"Mean width: {mean_w:.2f} px")
    print(f"Std width: {std_w:.2f} px")
    print(f"Bucket counts: {bucket_counts}")
    print(f"Wrote: {p1}")
    print(f"Wrote: {p2}")
    print(f"Wrote: {p3}")


if __name__ == "__main__":
    main()

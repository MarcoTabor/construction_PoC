#!/usr/bin/env python3
"""Step 5 - Rasterize final thick path from refined centerline and width estimates.

Uses the refined graph (seeds_04_refined_graph.json) and width stats (seeds_03_width_stats.json)
to draw the final reconstructed footpath with adaptive local width.

Outputs:
- outputs/footpath_pixel_pipeline/visualizations/seeds_05_final_path_raster.png
- outputs/footpath_pixel_pipeline/visualizations/seeds_05_final_path_on_page.png
- outputs/footpath_pixel_pipeline/visualizations/seeds_05_final_path.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Rasterize final thick path from refined centerline")
    p.add_argument("--refined-graph", default="outputs/footpath_pixel_pipeline/visualizations/seeds_04_refined_graph.json")
    p.add_argument("--width-stats", default="outputs/footpath_pixel_pipeline/visualizations/seeds_03_width_stats.json")
    p.add_argument("--clean-mask", default="outputs/footpath_pixel_pipeline/visualizations/stage_03_clean_mask.png")
    p.add_argument("--page", default="outputs/footpath_pixel_pipeline/visualizations/stage_01_page.png")
    p.add_argument("--outdir", default="outputs/footpath_pixel_pipeline/visualizations")
    p.add_argument("--width-method", choices=["median", "local", "smooth"], default="smooth",
                   help="Use global median, per-seed local, or temporally smoothed widths")
    p.add_argument("--smooth-window", type=int, default=3, help="Moving window for width smoothing")
    p.add_argument("--width-scale", type=float, default=1.0, help="Multiply all widths by this factor")
    p.add_argument("--line-type", choices=["thick", "aa"], default="thick", help="Line drawing style")
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

    out = [start]
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
        out.append(nxt)
        seen.add(nxt)
        prev, cur = cur, nxt
    return out


def main() -> None:
    args = parse_args()

    refined = load_json(Path(args.refined_graph))
    width_stats = load_json(Path(args.width_stats))

    seeds = refined["seeds"]
    edges = refined["edges"]
    start_end = refined.get("start_end") or refined.get("endpoint_ids", [])

    active_ids = {int(s["id"]) for s in seeds if s.get("active", True)}
    order = build_ordered_path(edges, active_ids, start_end)

    id_to_xy = {int(s["id"]): (int(s["x"]), int(s["y"])) for s in seeds}

    mask_bgr = cv2.imread(str(args.clean_mask))
    page_bgr = cv2.imread(str(args.page))
    if mask_bgr is None:
        raise FileNotFoundError(f"Clean mask not found: {args.clean_mask}")
    if page_bgr is None:
        raise FileNotFoundError(f"Page not found: {args.page}")

    gray = cv2.cvtColor(mask_bgr, cv2.COLOR_BGR2GRAY)
    mh, mw = gray.shape[:2]

    samples = width_stats.get("samples", [])
    id_to_width = {}
    for s in samples:
        if s.get("valid"):
            sid = int(s["id"])
            w = float(s.get("width", 0.0))
            if w > 0:
                id_to_width[sid] = w

    median_width = float(width_stats.get("width_px", {}).get("median", 12.0))
    if args.width_method == "median":
        local_widths = {sid: median_width for sid in order}
    elif args.width_method == "local":
        local_widths = {}
        for sid in order:
            local_widths[sid] = id_to_width.get(sid, median_width)
    elif args.width_method == "smooth":
        raw_widths = [id_to_width.get(sid, median_width) for sid in order]
        window = int(args.smooth_window)
        if window > 1:
            smoothed = []
            for i in range(len(raw_widths)):
                i0 = max(0, i - window // 2)
                i1 = min(len(raw_widths), i + window // 2 + 1)
                smoothed.append(float(np.mean(raw_widths[i0:i1])))
            local_widths = {order[i]: w for i, w in enumerate(smoothed)}
        else:
            local_widths = {order[i]: w for i, w in enumerate(raw_widths)}

    for sid in local_widths:
        local_widths[sid] *= float(args.width_scale)

    path_raster = np.zeros((mh, mw), dtype=np.uint8)
    for i, sid in enumerate(order):
        if i == len(order) - 1:
            break
        next_sid = order[i + 1]
        if sid not in id_to_xy or next_sid not in id_to_xy:
            continue

        x0, y0 = id_to_xy[sid]
        x1, y1 = id_to_xy[next_sid]
        w0 = int(round(local_widths.get(sid, median_width)))
        w1 = int(round(local_widths.get(next_sid, median_width)))

        if args.line_type == "thick":
            thick = max(2, (w0 + w1) // 2)
            cv2.line(path_raster, (x0, y0), (x1, y1), 255, thick)
        else:
            thick = max(1, (w0 + w1) // 2)
            cv2.line(path_raster, (x0, y0), (x1, y1), 255, thick, cv2.LINE_AA)

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    p_raster = outdir / "seeds_05_final_path_raster.png"
    cv2.imwrite(str(p_raster), path_raster)

    vis_page = page_bgr.copy()
    ph, pw = vis_page.shape[:2]
    sx = pw / float(mw)
    sy = ph / float(mh)

    for i, sid in enumerate(order):
        if i == len(order) - 1:
            break
        next_sid = order[i + 1]
        if sid not in id_to_xy or next_sid not in id_to_xy:
            continue

        x0, y0 = id_to_xy[sid]
        x1, y1 = id_to_xy[next_sid]
        p0 = (int(round(x0 * sx)), int(round(y0 * sy)))
        p1 = (int(round(x1 * sx)), int(round(y1 * sy)))

        w0 = int(round(local_widths.get(sid, median_width)))
        w1 = int(round(local_widths.get(next_sid, median_width)))
        thick = max(2, int(round((w0 + w1) * sx / 2.0)))

        cv2.line(vis_page, p0, p1, (100, 200, 100), thick)

    for sid in order:
        x, y = id_to_xy[sid]
        px, py = (int(round(x * sx)), int(round(y * sy)))
        cv2.circle(vis_page, (px, py), 3, (0, 0, 255), -1)

    if start_end and len(start_end) >= 2:
        s_id = int(start_end[0])
        e_id = int(start_end[1])
        if s_id in id_to_xy:
            sp = (int(round(id_to_xy[s_id][0] * sx)), int(round(id_to_xy[s_id][1] * sy)))
            cv2.circle(vis_page, sp, 8, (255, 0, 255), 2)
            cv2.putText(vis_page, "S", (sp[0] + 5, sp[1] - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 255), 2, cv2.LINE_AA)
        if e_id in id_to_xy:
            ep = (int(round(id_to_xy[e_id][0] * sx)), int(round(id_to_xy[e_id][1] * sy)))
            cv2.circle(vis_page, ep, 8, (0, 255, 255), 2)
            cv2.putText(vis_page, "E", (ep[0] + 5, ep[1] - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2, cv2.LINE_AA)

    p_page = outdir / "seeds_05_final_path_on_page.png"
    cv2.imwrite(str(p_page), vis_page)

    payload = {
        "refined_graph": str(args.refined_graph),
        "width_stats": str(args.width_stats),
        "width_method": args.width_method,
        "width_scale": float(args.width_scale),
        "median_width_px": median_width,
        "path_len": len(order),
        "raster_size": [mw, mh],
        "per_seed_widths": {str(sid): float(local_widths[sid]) for sid in order},
    }

    p_json = outdir / "seeds_05_final_path.json"
    p_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"Path nodes: {len(order)}")
    print(f"Width method: {args.width_method}")
    print(f"Median width: {median_width:.2f} px (scaled: {median_width * float(args.width_scale):.2f})")
    print(f"Raster size: {mw}x{mh}")
    print(f"Raster non-zero pixels: {int(np.count_nonzero(path_raster))}")
    print(f"Wrote: {p_raster}")
    print(f"Wrote: {p_page}")
    print(f"Wrote: {p_json}")


if __name__ == "__main__":
    main()

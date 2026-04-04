#!/usr/bin/env python3
"""Step 3 - Estimate path width from an existing seed continuum.

This script reuses the exact seed graph from seeds_02_graph.json (built on raw mask),
then measures widths on a cleaner mask by probing along +/- normal direction at
seeds along the ordered path.

Outputs:
- outputs/footpath_pixel_pipeline/visualizations/seeds_03_width_on_clean_mask.png
- outputs/footpath_pixel_pipeline/visualizations/seeds_03_width_on_page.png
- outputs/footpath_pixel_pipeline/visualizations/seeds_03_width_stats.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Width probing on clean mask from transferred seed graph")
    p.add_argument("--graph-json", default="outputs/footpath_pixel_pipeline/visualizations/seeds_02_graph.json")
    p.add_argument("--clean-mask", default="outputs/footpath_pixel_pipeline/visualizations/stage_03_clean_mask.png")
    p.add_argument("--page", default="outputs/footpath_pixel_pipeline/visualizations/stage_01_page.png")
    p.add_argument("--outdir", default="outputs/footpath_pixel_pipeline/visualizations")
    p.add_argument("--max-probe", type=int, default=120, help="Max pixels searched on each side of normal")
    p.add_argument("--step", type=float, default=1.0, help="Probe increment in pixels")
    p.add_argument("--mad-k", type=float, default=2.5, help="Outlier reject threshold in MAD units")
    p.add_argument("--min-valid-ratio", type=float, default=0.6, help="Minimum valid width sample ratio")
    p.add_argument("--snap-radius", type=int, default=8,
                   help="Max radius to snap transferred seed center onto nearest white pixel in clean mask")
    return p.parse_args()


def load_graph(path: Path) -> tuple[list[dict], list[dict], list[int], list[int]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    seeds = data["seeds"]
    edges = data["edges"]
    start_end = data.get("start_end")
    if not start_end:
        start_end = data.get("endpoint_ids", [])
    active_ids = [s["id"] for s in seeds if s.get("active", True)]
    return seeds, edges, start_end, active_ids


def build_ordered_path(edges: list[dict], active_ids: list[int], start_end: list[int]) -> list[int]:
    active_set = set(active_ids)
    adj: dict[int, list[int]] = {i: [] for i in active_ids}
    for e in edges:
        a = int(e["a"])
        b = int(e["b"])
        if a in active_set and b in active_set:
            adj[a].append(b)
            adj[b].append(a)

    endpoints = [i for i in active_ids if len(adj[i]) == 1]
    if len(start_end) >= 1 and int(start_end[0]) in active_set:
        start = int(start_end[0])
    elif endpoints:
        start = endpoints[0]
    else:
        # Fallback for a loop; choose smallest id deterministically.
        start = min(active_ids)

    order = [start]
    prev = -1
    cur = start
    while True:
        nxts = [n for n in adj[cur] if n != prev]
        if not nxts:
            break
        nxt = nxts[0]
        order.append(nxt)
        prev, cur = cur, nxt

    return order


def ray_length(mask_bin: np.ndarray, x: float, y: float, dx: float, dy: float, max_probe: int, step: float) -> float | None:
    h, w = mask_bin.shape
    t = 0.0
    while t <= float(max_probe):
        px = int(round(x + dx * t))
        py = int(round(y + dy * t))
        if px < 0 or py < 0 or px >= w or py >= h:
            return t
        if mask_bin[py, px] == 0:
            return t
        t += step
    return None


def robust_keep(widths: list[float], k: float) -> tuple[list[float], dict]:
    if not widths:
        return [], {"median": None, "mad": None}
    arr = np.array(widths, dtype=np.float32)
    med = float(np.median(arr))
    mad = float(np.median(np.abs(arr - med)))
    if mad <= 1e-6:
        return widths[:], {"median": med, "mad": mad}
    z = np.abs(arr - med) / mad
    keep = arr[z <= float(k)]
    return keep.tolist(), {"median": med, "mad": mad}


def snap_to_white(mask_bin: np.ndarray, x: float, y: float, radius: int) -> tuple[float, float, bool]:
    px = int(round(x))
    py = int(round(y))
    h, w = mask_bin.shape
    if 0 <= px < w and 0 <= py < h and mask_bin[py, px] > 0:
        return x, y, False

    r = int(radius)
    x0, x1 = max(0, px - r), min(w, px + r + 1)
    y0, y1 = max(0, py - r), min(h, py + r + 1)
    if x1 <= x0 or y1 <= y0:
        return x, y, False

    roi = mask_bin[y0:y1, x0:x1]
    ys, xs = np.where(roi > 0)
    if len(xs) == 0:
        return x, y, False

    gx = xs + x0
    gy = ys + y0
    d2 = (gx - px) ** 2 + (gy - py) ** 2
    k = int(np.argmin(d2))
    return float(gx[k]), float(gy[k]), True


def main() -> None:
    args = parse_args()

    seeds, edges, start_end, active_ids = load_graph(Path(args.graph_json))

    mask_bgr = cv2.imread(str(args.clean_mask))
    page_bgr = cv2.imread(str(args.page))
    if mask_bgr is None:
        raise FileNotFoundError(f"Clean mask not found: {args.clean_mask}")
    if page_bgr is None:
        raise FileNotFoundError(f"Page image not found: {args.page}")

    gray = cv2.cvtColor(mask_bgr, cv2.COLOR_BGR2GRAY)
    _, mask_bin = cv2.threshold(gray, 127, 255, cv2.THRESH_BINARY)

    # Small close to reduce tiny holes/speckles that can under-estimate width.
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask_bin = cv2.morphologyEx(mask_bin, cv2.MORPH_CLOSE, kernel)

    order = build_ordered_path(edges, active_ids, start_end)
    if len(order) < 3:
        raise RuntimeError("Ordered path too short for width probing")

    id_to_xy = {int(s["id"]): (float(s["x"]), float(s["y"])) for s in seeds}
    snapped_count = 0
    for sid in active_ids:
        if sid not in id_to_xy:
            continue
        x, y = id_to_xy[sid]
        sx, sy, moved = snap_to_white(mask_bin, x, y, args.snap_radius)
        id_to_xy[sid] = (sx, sy)
        if moved:
            snapped_count += 1

    raw_samples: list[dict] = []
    valid_widths: list[float] = []

    for i, sid in enumerate(order):
        x, y = id_to_xy[sid]

        cx = int(round(x))
        cy = int(round(y))
        if cx < 0 or cy < 0 or cx >= mask_bin.shape[1] or cy >= mask_bin.shape[0] or mask_bin[cy, cx] == 0:
            raw_samples.append({"id": int(sid), "x": x, "y": y, "valid": False, "reason": "center_not_on_clean_mask"})
            continue

        if i == 0:
            nx_id = order[i + 1]
            x1, y1 = id_to_xy[nx_id]
            tx, ty = (x1 - x), (y1 - y)
        elif i == len(order) - 1:
            pv_id = order[i - 1]
            x0, y0 = id_to_xy[pv_id]
            tx, ty = (x - x0), (y - y0)
        else:
            pv_id = order[i - 1]
            nx_id = order[i + 1]
            x0, y0 = id_to_xy[pv_id]
            x1, y1 = id_to_xy[nx_id]
            tx, ty = (x1 - x0), (y1 - y0)

        norm = (tx * tx + ty * ty) ** 0.5
        if norm < 1e-6:
            raw_samples.append({"id": int(sid), "x": x, "y": y, "valid": False, "reason": "zero_tangent"})
            continue

        tx /= norm
        ty /= norm
        # Unit normals (+/-)
        nx, ny = -ty, tx

        l_pos = ray_length(mask_bin, x, y, +nx, +ny, args.max_probe, args.step)
        l_neg = ray_length(mask_bin, x, y, -nx, -ny, args.max_probe, args.step)

        valid = l_pos is not None and l_neg is not None
        if valid:
            width = float(l_pos + l_neg)
            if width <= 0.0:
                valid = False
                width = None
            else:
                valid_widths.append(width)
        else:
            width = None

        raw_samples.append({
            "id": int(sid), "x": x, "y": y,
            "nx": float(nx), "ny": float(ny),
            "d_pos": None if l_pos is None else float(l_pos),
            "d_neg": None if l_neg is None else float(l_neg),
            "width": width,
            "valid": bool(valid),
        })

    kept_widths, robust = robust_keep(valid_widths, args.mad_k)
    if len(valid_widths) == 0:
        raise RuntimeError("No valid width samples found")

    valid_ratio = len(valid_widths) / float(len(order))
    if valid_ratio < float(args.min_valid_ratio):
        raise RuntimeError(f"Too few valid width samples: {valid_ratio:.3f}")

    w_arr = np.array(kept_widths, dtype=np.float32)
    w_med = float(np.median(w_arr))
    w_mean = float(np.mean(w_arr))
    w_p10 = float(np.percentile(w_arr, 10))
    w_p90 = float(np.percentile(w_arr, 90))

    # Visualization on clean mask: transferred continuum + normal ticks.
    vis_mask = cv2.cvtColor(mask_bin, cv2.COLOR_GRAY2BGR)
    for e in edges:
        a = int(e["a"])
        b = int(e["b"])
        if a in id_to_xy and b in id_to_xy:
            p0 = tuple(int(round(v)) for v in id_to_xy[a])
            p1 = tuple(int(round(v)) for v in id_to_xy[b])
            cv2.line(vis_mask, p0, p1, (60, 220, 80), 2)

    for s in raw_samples:
        x = int(round(s["x"]))
        y = int(round(s["y"]))
        if not s.get("valid", False):
            cv2.circle(vis_mask, (x, y), 3, (0, 0, 255), -1)
            continue

        nx = float(s["nx"])
        ny = float(s["ny"])
        dp = float(s["d_pos"])
        dn = float(s["d_neg"])
        p1 = (int(round(x + nx * dp)), int(round(y + ny * dp)))
        p2 = (int(round(x - nx * dn)), int(round(y - ny * dn)))
        cv2.line(vis_mask, p1, p2, (0, 255, 255), 1)
        cv2.circle(vis_mask, (x, y), 3, (0, 0, 255), -1)

    # Visualization on page image.
    vis_page = page_bgr.copy()
    ph, pw = vis_page.shape[:2]
    mh, mw = mask_bin.shape
    sx = pw / float(mw)
    sy = ph / float(mh)

    for e in edges:
        a = int(e["a"])
        b = int(e["b"])
        if a in id_to_xy and b in id_to_xy:
            x0, y0 = id_to_xy[a]
            x1, y1 = id_to_xy[b]
            p0 = (int(round(x0 * sx)), int(round(y0 * sy)))
            p1 = (int(round(x1 * sx)), int(round(y1 * sy)))
            cv2.line(vis_page, p0, p1, (60, 220, 80), 2)

    out = Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)

    p_img1 = out / "seeds_03_width_on_clean_mask.png"
    p_img2 = out / "seeds_03_width_on_page.png"
    p_json = out / "seeds_03_width_stats.json"

    cv2.imwrite(str(p_img1), vis_mask)
    cv2.imwrite(str(p_img2), vis_page)

    payload = {
        "input_graph": str(args.graph_json),
        "input_clean_mask": str(args.clean_mask),
        "snap_radius": int(args.snap_radius),
        "snapped_seed_count": int(snapped_count),
        "ordered_path_len": len(order),
        "valid_samples": len(valid_widths),
        "valid_ratio": valid_ratio,
        "robust": robust,
        "kept_after_outlier_filter": len(kept_widths),
        "width_px": {
            "median": w_med,
            "mean": w_mean,
            "p10": w_p10,
            "p90": w_p90,
        },
        "samples": raw_samples,
    }
    p_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"Transferred path nodes: {len(order)}")
    print(f"Snapped seed centers onto clean white pixels: {snapped_count}")
    print(f"Valid width samples: {len(valid_widths)} / {len(order)} ({valid_ratio:.3f})")
    print(f"Width px median={w_med:.2f}, p10={w_p10:.2f}, p90={w_p90:.2f}")
    print(f"Wrote: {p_img1}")
    print(f"Wrote: {p_img2}")
    print(f"Wrote: {p_json}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Step 7 - Error-aware area estimation from centerline + sigma width classes.

Computes:
1) Nominal area from measured per-segment widths.
2) Conservative min/max area using sigma-class-aware width bounds:
   - green: within +/-1 sigma of mean
   - yellow: between 1 and 2 sigma from mean (side-aware)
   - red: outside 2 sigma (or missing) -> broad bounds
3) Mean-baseline + class-deviation model:
    - baseline uses mean width for all segments
    - uncertainty band depends on class (green/yellow/red)

Outputs:
- outputs/footpath_pixel_pipeline/visualizations/seeds_07_area_error_aware.json
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import cv2
import fitz
import numpy as np


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Error-aware area estimate from sigma confidence")
    p.add_argument("--refined-graph", default="outputs/footpath_pixel_pipeline/visualizations/seeds_04_refined_graph.json")
    p.add_argument("--width-stats", default="outputs/footpath_pixel_pipeline/visualizations/seeds_03_width_stats.json")
    p.add_argument("--page-image", default="outputs/footpath_pixel_pipeline/visualizations/stage_01_page.png")
    p.add_argument("--pdf", default="examples/Joal 502.pdf")
    p.add_argument("--pdf-page", type=int, default=1)
    p.add_argument("--scale-json", default="outputs/scale_detection/scale_detection.json")
    p.add_argument("--outdir", default="outputs/footpath_pixel_pipeline/visualizations")
    p.add_argument("--red-window", type=int, default=4, help="Half-window (in segments) for local red bounds")
    p.add_argument("--red-p-lo", type=float, default=20.0, help="Lower percentile for red local bounds")
    p.add_argument("--red-p-hi", type=float, default=80.0, help="Upper percentile for red local bounds")
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


def meters_per_pixel(args: argparse.Namespace) -> float:
    scale_path = Path(args.scale_json)
    if scale_path.exists():
        cal = load_json(scale_path).get("calibration", {})
        m_per_pt = cal.get("meters_per_point")
        if m_per_pt is not None:
            img = cv2.imread(str(args.page_image))
            if img is None:
                raise FileNotFoundError(f"Page image not found: {args.page_image}")
            h, w = img.shape[:2]
            doc = fitz.open(str(args.pdf))
            page = doc.load_page(args.pdf_page - 1)
            px_per_pt_x = w / float(page.rect.width)
            px_per_pt_y = h / float(page.rect.height)
            doc.close()
            mpx_x = float(m_per_pt) / px_per_pt_x
            mpx_y = float(m_per_pt) / px_per_pt_y
            return 0.5 * (mpx_x + mpx_y)

    raise RuntimeError("Could not derive meters_per_pixel from scale data")


def interpolate_missing(values: list[float | None]) -> tuple[list[float], int]:
    arr = np.array([np.nan if v is None else float(v) for v in values], dtype=np.float32)
    n = arr.size
    if n == 0:
        return [], 0
    missing = int(np.count_nonzero(np.isnan(arr)))
    if missing == 0:
        return arr.tolist(), 0

    idx = np.arange(n)
    valid = np.isfinite(arr)
    if not np.any(valid):
        raise RuntimeError("All widths are missing; cannot interpolate")

    arr[~valid] = np.interp(idx[~valid], idx[valid], arr[valid])
    return arr.tolist(), missing


def main() -> None:
    args = parse_args()

    graph = load_json(Path(args.refined_graph))
    widths = load_json(Path(args.width_stats))

    seeds = graph["seeds"]
    edges = graph["edges"]
    start_end = graph.get("start_end") or graph.get("endpoint_ids", [])
    active_ids = {int(s["id"]) for s in seeds if s.get("active", True)}
    order = build_ordered_path(edges, active_ids, start_end)

    id_to_xy = {int(s["id"]): (float(s["x"]), float(s["y"])) for s in seeds}

    id_to_width: dict[int, float] = {}
    for s in widths.get("samples", []):
        if s.get("valid") and s.get("width") is not None and float(s.get("width")) > 0:
            id_to_width[int(s["id"])] = float(s["width"])

    vals = np.array(list(id_to_width.values()), dtype=np.float32)
    if vals.size == 0:
        raise RuntimeError("No valid width samples available")

    mu = float(np.mean(vals))
    sigma = float(np.std(vals))
    sigma = max(sigma, 1e-6)
    w_min = float(np.min(vals))
    w_max = float(np.max(vals))

    total_len_px = 0.0
    area_nom_px2 = 0.0
    area_lo_px2 = 0.0
    area_hi_px2 = 0.0

    # Model 2: mean baseline with class-dependent deviations.
    area_mean_base_px2 = 0.0
    area_mean_lo_px2 = 0.0
    area_mean_hi_px2 = 0.0
    class_deviation_px2 = {
        "green": {"down": 0.0, "up": 0.0},
        "yellow": {"down": 0.0, "up": 0.0},
        "red": {"down": 0.0, "up": 0.0},
    }

    class_counts = {"green": 0, "yellow": 0, "red": 0}

    seg_rows: list[dict] = []

    for i in range(len(order) - 1):
        a = order[i]
        b = order[i + 1]
        if a not in id_to_xy or b not in id_to_xy:
            continue

        x0, y0 = id_to_xy[a]
        x1, y1 = id_to_xy[b]
        seg_len = math.hypot(x1 - x0, y1 - y0)
        total_len_px += seg_len

        wa = id_to_width.get(a)
        wb = id_to_width.get(b)
        valid = wa is not None and wb is not None

        if valid:
            w_seg = 0.5 * (wa + wb)
            z = abs(w_seg - mu) / sigma
            if z <= 1.0:
                cls = "green"
                w_lo = max(w_min, mu - sigma)
                w_hi = min(w_max, mu + sigma)
            elif z <= 2.0:
                cls = "yellow"
                if w_seg >= mu:
                    w_lo = max(w_min, mu + sigma)
                    w_hi = min(w_max, mu + 2.0 * sigma)
                else:
                    w_lo = max(w_min, mu - 2.0 * sigma)
                    w_hi = min(w_max, mu - sigma)
            else:
                cls = "red"
                if w_seg >= mu:
                    w_lo = max(w_min, mu + 2.0 * sigma)
                    w_hi = w_max
                else:
                    w_lo = w_min
                    w_hi = min(w_max, mu - 2.0 * sigma)

            # Keep nominal width in bounds for consistency
            w_nom = min(max(w_seg, w_lo), w_hi)
        else:
            cls = "red"
            z = None
            w_nom = mu
            w_lo = w_min
            w_hi = w_max

        class_counts[cls] += 1

        area_nom_px2 += seg_len * w_nom
        area_lo_px2 += seg_len * w_lo
        area_hi_px2 += seg_len * w_hi

        # --- Mean-baseline + class deviations ---
        base_w = mu
        if cls == "green":
            band_lo = max(w_min, mu - sigma)
            band_hi = min(w_max, mu + sigma)
        elif cls == "yellow":
            band_lo = max(w_min, mu - 2.0 * sigma)
            band_hi = min(w_max, mu + 2.0 * sigma)
        else:
            band_lo = w_min
            band_hi = w_max

        area_mean_base_px2 += seg_len * base_w
        area_mean_lo_px2 += seg_len * band_lo
        area_mean_hi_px2 += seg_len * band_hi

        class_deviation_px2[cls]["down"] += seg_len * max(0.0, base_w - band_lo)
        class_deviation_px2[cls]["up"] += seg_len * max(0.0, band_hi - base_w)

        seg_rows.append(
            {
                "i": i,
                "a": int(a),
                "b": int(b),
                "len_px": float(seg_len),
                "class": cls,
                "z": None if z is None else float(z),
                "w_nom_px": float(w_nom),
                "w_lo_px": float(w_lo),
                "w_hi_px": float(w_hi),
                "valid_width": bool(valid),
            }
        )

    # --- Model 3 (tightened): interpolate missing widths + local bounds for red ---
    seed_w_raw = [id_to_width.get(sid) for sid in order]
    seed_w_interp, missing_seed_count = interpolate_missing(seed_w_raw)

    seg_len_list: list[float] = []
    seg_w_list: list[float] = []
    for i in range(len(order) - 1):
        a = order[i]
        b = order[i + 1]
        if a not in id_to_xy or b not in id_to_xy:
            continue
        x0, y0 = id_to_xy[a]
        x1, y1 = id_to_xy[b]
        seg_len = math.hypot(x1 - x0, y1 - y0)
        seg_len_list.append(seg_len)
        seg_w_list.append(0.5 * (seed_w_interp[i] + seed_w_interp[i + 1]))

    seg_w_arr = np.array(seg_w_list, dtype=np.float32)
    mu_b = float(np.mean(seg_w_arr))
    sigma_b = float(np.std(seg_w_arr))
    sigma_b = max(sigma_b, 1e-6)

    w_floor = float(np.percentile(seg_w_arr, 5))
    w_ceil = float(np.percentile(seg_w_arr, 95))

    area_b_base_px2 = 0.0
    area_b_lo_px2 = 0.0
    area_b_hi_px2 = 0.0
    class_counts_b = {"green": 0, "yellow": 0, "red": 0}

    for i, (seg_len, w_nom) in enumerate(zip(seg_len_list, seg_w_list)):
        z = abs(w_nom - mu_b) / sigma_b
        if z <= 1.0:
            cls = "green"
            w_lo = max(w_floor, mu_b - sigma_b)
            w_hi = min(w_ceil, mu_b + sigma_b)
        elif z <= 2.0:
            cls = "yellow"
            w_lo = max(w_floor, mu_b - 2.0 * sigma_b)
            w_hi = min(w_ceil, mu_b + 2.0 * sigma_b)
        else:
            cls = "red"
            j0 = max(0, i - int(args.red_window))
            j1 = min(len(seg_w_list), i + int(args.red_window) + 1)
            local = np.array(seg_w_list[j0:j1], dtype=np.float32)
            w_lo = float(np.percentile(local, float(args.red_p_lo)))
            w_hi = float(np.percentile(local, float(args.red_p_hi)))
            w_lo = max(w_floor, w_lo)
            w_hi = min(w_ceil, w_hi)

        if w_lo > w_hi:
            w_lo, w_hi = w_hi, w_lo

        area_b_base_px2 += seg_len * mu_b
        area_b_lo_px2 += seg_len * w_lo
        area_b_hi_px2 += seg_len * w_hi
        class_counts_b[cls] += 1

    m_per_px = meters_per_pixel(args)
    m2_per_px2 = m_per_px * m_per_px

    total_len_m = total_len_px * m_per_px
    area_nom_m2 = area_nom_px2 * m2_per_px2
    area_lo_m2 = area_lo_px2 * m2_per_px2
    area_hi_m2 = area_hi_px2 * m2_per_px2
    area_b_base_m2 = area_b_base_px2 * m2_per_px2
    area_b_lo_m2 = area_b_lo_px2 * m2_per_px2
    area_b_hi_m2 = area_b_hi_px2 * m2_per_px2
    area_mean_base_m2 = area_mean_base_px2 * m2_per_px2
    area_mean_lo_m2 = area_mean_lo_px2 * m2_per_px2
    area_mean_hi_m2 = area_mean_hi_px2 * m2_per_px2

    out = {
        "length": {
            "px": float(total_len_px),
            "m": float(total_len_m),
        },
        "width_stats_px": {
            "mean": float(mu),
            "std": float(sigma),
            "min": float(w_min),
            "max": float(w_max),
        },
        "classification": {
            "green": "|w-mean| <= 1 sigma",
            "yellow": "1 sigma < |w-mean| <= 2 sigma (side-aware bounds)",
            "red": "|w-mean| > 2 sigma or missing width",
            "counts": class_counts,
        },
        "area": {
            "model_1_measured_segment_widths": {
                "nominal_px2": float(area_nom_px2),
                "min_px2": float(area_lo_px2),
                "max_px2": float(area_hi_px2),
                "nominal_m2": float(area_nom_m2),
                "min_m2": float(area_lo_m2),
                "max_m2": float(area_hi_m2),
            },
            "model_2_mean_baseline_plus_class_deviation": {
                "baseline_px2": float(area_mean_base_px2),
                "min_px2": float(area_mean_lo_px2),
                "max_px2": float(area_mean_hi_px2),
                "baseline_m2": float(area_mean_base_m2),
                "min_m2": float(area_mean_lo_m2),
                "max_m2": float(area_mean_hi_m2),
                "deviation_contribution_m2": {
                    "green": {
                        "down": float(class_deviation_px2["green"]["down"] * m2_per_px2),
                        "up": float(class_deviation_px2["green"]["up"] * m2_per_px2),
                    },
                    "yellow": {
                        "down": float(class_deviation_px2["yellow"]["down"] * m2_per_px2),
                        "up": float(class_deviation_px2["yellow"]["up"] * m2_per_px2),
                    },
                    "red": {
                        "down": float(class_deviation_px2["red"]["down"] * m2_per_px2),
                        "up": float(class_deviation_px2["red"]["up"] * m2_per_px2),
                    },
                },
            },
            "model_3_tightened_local_red_bounds": {
                "baseline_px2": float(area_b_base_px2),
                "min_px2": float(area_b_lo_px2),
                "max_px2": float(area_b_hi_px2),
                "baseline_m2": float(area_b_base_m2),
                "min_m2": float(area_b_lo_m2),
                "max_m2": float(area_b_hi_m2),
                "mean_width_px": float(mu_b),
                "std_width_px": float(sigma_b),
                "width_floor_p5_px": float(w_floor),
                "width_ceil_p95_px": float(w_ceil),
                "interpolated_missing_seed_widths": int(missing_seed_count),
                "class_counts": class_counts_b,
                "red_local_bound_params": {
                    "window_half_size_segments": int(args.red_window),
                    "percentile_lo": float(args.red_p_lo),
                    "percentile_hi": float(args.red_p_hi),
                },
            },
            "nominal_px2": float(area_nom_px2),
            "min_px2": float(area_lo_px2),
            "max_px2": float(area_hi_px2),
            "nominal_m2": float(area_nom_m2),
            "min_m2": float(area_lo_m2),
            "max_m2": float(area_hi_m2),
        },
        "conversion": {
            "meters_per_pixel": float(m_per_px),
            "m2_per_px2": float(m2_per_px2),
        },
        "formula": {
            "segment_area": "A_i = l_i * w_i",
            "total_area": "A = sum_i l_i * w_i",
            "length": "L = sum_i l_i",
        },
        "segments": seg_rows,
    }

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    out_path = outdir / "seeds_07_area_error_aware.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")

    print(f"Length: {total_len_px:.2f} px | {total_len_m:.2f} m")
    print(f"Width mean={mu:.2f}px std={sigma:.2f}px min={w_min:.2f}px max={w_max:.2f}px")
    print(f"Class counts: {class_counts}")
    print(f"Area model1 (measured widths) min/nom/max: {area_lo_m2:.2f} / {area_nom_m2:.2f} / {area_hi_m2:.2f} m2")
    print(f"Area model2 (mean baseline) min/base/max: {area_mean_lo_m2:.2f} / {area_mean_base_m2:.2f} / {area_mean_hi_m2:.2f} m2")
    print(f"Area model3 (tightened) min/base/max: {area_b_lo_m2:.2f} / {area_b_base_m2:.2f} / {area_b_hi_m2:.2f} m2")
    print(f"Model3 class counts: {class_counts_b}, interpolated missing seeds: {missing_seed_count}")
    print(f"Wrote: {out_path}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Alternative area model: centerline + first-hit-black normal probing.

Given a refined centerline, this estimates local width by casting normals until the
first black pixel on each side of the line. Area is integrated as A = integral w(s) ds.

Outputs:
- outputs/footpath_pixel_pipeline/visualizations/seeds_08_first_hit_area.json
- outputs/footpath_pixel_pipeline/visualizations/seeds_08_first_hit_width_overlay.png
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
    p = argparse.ArgumentParser(description="Area from first-hit-black width probing")
    p.add_argument("--refined-graph", default="outputs/footpath_pixel_pipeline/visualizations/seeds_04_refined_graph.json")
    p.add_argument("--probe-mask", default="outputs/footpath_pixel_pipeline/visualizations/stage_02_raw_mask.png",
                   help="Binary-like mask where white is path and black is outside")
    p.add_argument("--page", default="outputs/footpath_pixel_pipeline/visualizations/stage_01_page.png",
                   help="Clean raw page image used for HSV probing (no painted overlays)")
    p.add_argument("--viz-page", default="outputs/footpath_pixel_pipeline/visualizations/seeds_04_refined_on_page.png",
                   help="Image used as visualization base (can have overlays; not used for probing)")
    p.add_argument("--pdf", default="examples/Joal 502.pdf")
    p.add_argument("--pdf-page", type=int, default=1)
    p.add_argument("--scale-json", default="outputs/scale_detection/scale_detection.json")
    p.add_argument("--outdir", default="outputs/footpath_pixel_pipeline/visualizations")
    p.add_argument("--step-px", type=float, default=2.0, help="Sampling step along centerline")
    p.add_argument("--max-probe", type=int, default=25, help="Max probe distance each side (keep short to avoid hitting text labels)")
    p.add_argument("--probe-step", type=float, default=1.0)
    p.add_argument("--snap-radius", type=int, default=6, help="Snap sample center to nearest white pixel")
    p.add_argument("--black-run", type=int, default=1,
                   help="Require this many consecutive black probe hits before stopping")
    p.add_argument("--outside-run", type=int, default=1,
                   help="Require this many consecutive outside-support hits before stopping")
    p.add_argument("--outside-grace", type=float, default=80.0,
                   help="After first outside-support hit, continue this many px to look for black boundary (default matches max-probe so probe always seeks black first)")
    p.add_argument("--close-kernel", type=int, default=3,
                   help="Morphological close kernel size to bridge tiny black interruptions")
    p.add_argument("--black-v-thresh", type=int, default=45,
                   help="Value threshold in HSV to consider a pixel black")
    p.add_argument("--support-v-min", type=int, default=55,
                   help="Within local corridor, pixels with V >= this are treated as support")
    p.add_argument("--gray-s-max", type=int, default=20,
                   help="Max HSV saturation to consider a pixel gray")
    p.add_argument("--gray-v-min", type=int, default=90,
                   help="Min HSV value to consider a pixel gray")
    p.add_argument("--green-h-lo", type=int, default=35,
                   help="Lower HSV hue for green support pixels")
    p.add_argument("--green-h-hi", type=int, default=95,
                   help="Upper HSV hue for green support pixels")
    p.add_argument("--green-s-min", type=int, default=55,
                   help="Min HSV saturation for green support pixels")
    p.add_argument("--green-v-min", type=int, default=35,
                   help="Min HSV value for green support pixels")
    p.add_argument("--support-pad", type=float, default=2.0,
                   help="Max pixel distance from raw-mask support where gray/green support is allowed")
    p.add_argument("--rescue-low-sigma", type=float, default=0.5,
                   help="Rescue widths lower than median - this*std")
    p.add_argument("--rescue-extra-sigma", type=float, default=1.0,
                   help="Extra outward search window as multiple of std(px)")
    p.add_argument("--rescue-support-ratio", type=float, default=0.6,
                   help="Minimum support occupancy ratio in rescue window to accept extension")
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
    cal = load_json(Path(args.scale_json)).get("calibration", {})
    m_per_pt = cal.get("meters_per_point")
    if m_per_pt is None:
        raise RuntimeError("meters_per_point missing in scale json")

    img = cv2.imread(str(args.page))
    if img is None:
        raise FileNotFoundError(f"Page image not found: {args.page}")
    h, w = img.shape[:2]

    doc = fitz.open(str(args.pdf))
    page = doc.load_page(args.pdf_page - 1)
    px_per_pt_x = w / float(page.rect.width)
    px_per_pt_y = h / float(page.rect.height)
    doc.close()

    mpx_x = float(m_per_pt) / px_per_pt_x
    mpx_y = float(m_per_pt) / px_per_pt_y
    return 0.5 * (mpx_x + mpx_y)


def dense_polyline(points: list[tuple[float, float]], step_px: float) -> list[tuple[float, float]]:
    if len(points) < 2:
        return points

    out: list[tuple[float, float]] = [points[0]]
    for i in range(len(points) - 1):
        x0, y0 = points[i]
        x1, y1 = points[i + 1]
        dx = x1 - x0
        dy = y1 - y0
        seg_len = math.hypot(dx, dy)
        if seg_len < 1e-6:
            continue
        n = max(1, int(math.floor(seg_len / max(step_px, 1e-6))))
        for k in range(1, n + 1):
            t = min(1.0, k / n)
            out.append((x0 + dx * t, y0 + dy * t))
    return out


def snap_to_white(mask_bin: np.ndarray, x: float, y: float, radius: int) -> tuple[float, float, bool]:
    px = int(round(x))
    py = int(round(y))
    h, w = mask_bin.shape
    if 0 <= px < w and 0 <= py < h and mask_bin[py, px] > 0:
        return x, y, False

    r = int(radius)
    x0, x1 = max(0, px - r), min(w, px + r + 1)
    y0, y1 = max(0, py - r), min(h, py + r + 1)
    roi = mask_bin[y0:y1, x0:x1]
    ys, xs = np.where(roi > 0)
    if len(xs) == 0:
        return x, y, False

    gx = xs + x0
    gy = ys + y0
    d2 = (gx - px) ** 2 + (gy - py) ** 2
    k = int(np.argmin(d2))
    return float(gx[k]), float(gy[k]), True


def ray_to_black(
    support_mask: np.ndarray,
    black_mask: np.ndarray,
    x: float,
    y: float,
    dx: float,
    dy: float,
    max_probe: int,
    step: float,
    black_run: int,
    outside_run: int,
    outside_grace: float,
) -> float | None:
    h, w = support_mask.shape
    t = float(step)
    run_black = 0
    run_out = 0
    first_black_t = None
    first_out_t = None
    outside_trigger_t = None
    while t <= float(max_probe):
        px = int(round(x + dx * t))
        py = int(round(y + dy * t))
        if px < 0 or py < 0 or px >= w or py >= h:
            return t

        if black_mask[py, px] > 0:
            if first_black_t is None:
                first_black_t = t
            run_black += 1
            if run_black >= int(max(1, black_run)):
                return first_black_t
        else:
            run_black = 0
            first_black_t = None

        if support_mask[py, px] == 0:
            if first_out_t is None:
                first_out_t = t
            run_out += 1
            if run_out >= int(max(1, outside_run)):
                if outside_trigger_t is None:
                    outside_trigger_t = first_out_t
        else:
            run_out = 0
            first_out_t = None

        # If outside triggered and grace window passed, decide boundary.
        if outside_trigger_t is not None and t >= outside_trigger_t + float(outside_grace):
            if first_black_t is not None:
                return first_black_t
            return outside_trigger_t
        t += step

    if first_black_t is not None:
        return first_black_t
    if outside_trigger_t is not None:
        return outside_trigger_t
    return None


def support_ratio_in_window(
    support_mask: np.ndarray,
    black_mask: np.ndarray,
    x: float,
    y: float,
    dx: float,
    dy: float,
    t0: float,
    t1: float,
    step: float,
) -> float:
    h, w = support_mask.shape
    good = 0
    tot = 0
    t = max(float(step), float(t0))
    while t <= float(t1):
        px = int(round(x + dx * t))
        py = int(round(y + dy * t))
        if 0 <= px < w and 0 <= py < h:
            tot += 1
            if support_mask[py, px] > 0 and black_mask[py, px] == 0:
                good += 1
        t += step
    if tot == 0:
        return 0.0
    return good / float(tot)


def first_boundary_in_window(
    support_mask: np.ndarray,
    black_mask: np.ndarray,
    x: float,
    y: float,
    dx: float,
    dy: float,
    t0: float,
    t1: float,
    step: float,
    black_run: int,
    outside_run: int,
) -> float | None:
    h, w = support_mask.shape
    t = max(float(step), float(t0))
    run_black = 0
    run_out = 0
    first_black_t = None
    first_out_t = None
    while t <= float(t1):
        px = int(round(x + dx * t))
        py = int(round(y + dy * t))
        if px < 0 or py < 0 or px >= w or py >= h:
            return t

        if black_mask[py, px] > 0:
            if first_black_t is None:
                first_black_t = t
            run_black += 1
            if run_black >= int(max(1, black_run)):
                return first_black_t
        else:
            run_black = 0
            first_black_t = None

        if support_mask[py, px] == 0:
            if first_out_t is None:
                first_out_t = t
            run_out += 1
            if run_out >= int(max(1, outside_run)):
                return first_out_t
        else:
            run_out = 0
            first_out_t = None

        t += step
    return None


def main() -> None:
    args = parse_args()

    graph = load_json(Path(args.refined_graph))
    seeds = graph["seeds"]
    edges = graph["edges"]
    start_end = graph.get("start_end") or graph.get("endpoint_ids", [])

    active_ids = {int(s["id"]) for s in seeds if s.get("active", True)}
    order = build_ordered_path(edges, active_ids, start_end)
    id_to_xy = {int(s["id"]): (float(s["x"]), float(s["y"])) for s in seeds}

    poly = [id_to_xy[sid] for sid in order if sid in id_to_xy]
    dense = dense_polyline(poly, args.step_px)
    if len(dense) < 3:
        raise RuntimeError("Dense centerline too short")

    mask_bgr = cv2.imread(str(args.probe_mask))
    page_bgr = cv2.imread(str(args.page))
    viz_bgr = cv2.imread(str(args.viz_page))
    if mask_bgr is None:
        raise FileNotFoundError(f"Probe mask not found: {args.probe_mask}")
    if page_bgr is None:
        raise FileNotFoundError(f"Page not found: {args.page}")
    if viz_bgr is None:
        print(f"Warning: viz-page not found ({args.viz_page}), falling back to --page for visualization")
        viz_bgr = page_bgr

    gray = cv2.cvtColor(mask_bgr, cv2.COLOR_BGR2GRAY)
    _, mask_bin = cv2.threshold(gray, 127, 255, cv2.THRESH_BINARY)
    if int(args.close_kernel) > 1:
        k = int(args.close_kernel)
        if k % 2 == 0:
            k += 1
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
        mask_bin = cv2.morphologyEx(mask_bin, cv2.MORPH_CLOSE, kernel)

    # Build support mask from raw mask + local non-black corridor support.
    page_probe = page_bgr
    if page_probe.shape[:2] != mask_bin.shape[:2]:
        page_probe = cv2.resize(page_probe, (mask_bin.shape[1], mask_bin.shape[0]), interpolation=cv2.INTER_LINEAR)

    hsv = cv2.cvtColor(page_probe, cv2.COLOR_BGR2HSV)
    h = hsv[:, :, 0]
    s = hsv[:, :, 1]
    v = hsv[:, :, 2]

    is_gray = (s <= int(args.gray_s_max)) & (v >= int(args.gray_v_min))
    is_green = (
        (h >= int(args.green_h_lo))
        & (h <= int(args.green_h_hi))
        & (s >= int(args.green_s_min))
        & (v >= int(args.green_v_min))
    )
    is_non_black = v >= int(args.support_v_min)
    raw_support = (mask_bin > 0)
    # Distance (in px) from each pixel to nearest raw-support pixel.
    dist_to_raw = cv2.distanceTransform((~raw_support).astype(np.uint8), cv2.DIST_L2, 3)
    near_raw = dist_to_raw <= float(args.support_pad)

    # Corridor support: within near_raw band, accept any non-black pixel;
    # keep gray/green terms for diagnostics/compatibility but non-black drives robustness.
    support_mask = (raw_support | ((is_non_black | is_gray | is_green) & near_raw)).astype(np.uint8) * 255
    black_mask = (v <= int(args.black_v_thresh)).astype(np.uint8) * 255

    widths: list[float] = []
    samples: list[dict] = []
    snapped = 0

    for i in range(len(dense)):
        x, y = dense[i]
        x, y, moved = snap_to_white(support_mask, x, y, args.snap_radius)
        snapped += 1 if moved else 0

        cx = int(round(x))
        cy = int(round(y))
        if cx < 0 or cy < 0 or cx >= support_mask.shape[1] or cy >= support_mask.shape[0] or support_mask[cy, cx] == 0:
            samples.append({"i": i, "x": float(x), "y": float(y), "valid": False, "reason": "center_not_on_support"})
            continue

        if i == 0:
            x0, y0 = dense[i]
            x1, y1 = dense[i + 1]
            tx, ty = x1 - x0, y1 - y0
        elif i == len(dense) - 1:
            x0, y0 = dense[i - 1]
            x1, y1 = dense[i]
            tx, ty = x1 - x0, y1 - y0
        else:
            x0, y0 = dense[i - 1]
            x1, y1 = dense[i + 1]
            tx, ty = x1 - x0, y1 - y0

        nrm = math.hypot(tx, ty)
        if nrm < 1e-6:
            samples.append({"i": i, "x": x, "y": y, "valid": False, "reason": "zero_tangent"})
            continue

        tx /= nrm
        ty /= nrm
        nx, ny = -ty, tx

        dpos = ray_to_black(
            support_mask,
            black_mask,
            x,
            y,
            +nx,
            +ny,
            args.max_probe,
            args.probe_step,
            args.black_run,
            args.outside_run,
            args.outside_grace,
        )
        dneg = ray_to_black(
            support_mask,
            black_mask,
            x,
            y,
            -nx,
            -ny,
            args.max_probe,
            args.probe_step,
            args.black_run,
            args.outside_run,
            args.outside_grace,
        )
        valid = dpos is not None and dneg is not None
        if valid:
            w = float(dpos + dneg)
            widths.append(w)
        else:
            w = None

        samples.append(
            {
                "i": i,
                "x": float(x),
                "y": float(y),
                "valid": bool(valid),
                "d_pos": None if dpos is None else float(dpos),
                "d_neg": None if dneg is None else float(dneg),
                "width": w,
            }
        )

    # Optional second-pass rescue: if width is short (below median - 0.5 sigma),
    # test an extra sigma-length window and extend if support evidence is strong.
    init_vals = np.array([s["width"] for s in samples if s.get("valid") and s.get("width") is not None], dtype=np.float32)
    rescued_count = 0
    if init_vals.size > 5:
        med0 = float(np.median(init_vals))
        std0 = float(np.std(init_vals))
        low_thr = med0 - float(args.rescue_low_sigma) * std0
        extra = max(float(args.probe_step), float(args.rescue_extra_sigma) * std0)

        for i, s in enumerate(samples):
            if not s.get("valid") or s.get("width") is None:
                continue
            if float(s["width"]) >= low_thr:
                continue

            x = float(s["x"])
            y = float(s["y"])
            if i == 0:
                tx = samples[i + 1]["x"] - x
                ty = samples[i + 1]["y"] - y
            elif i == len(samples) - 1:
                tx = x - samples[i - 1]["x"]
                ty = y - samples[i - 1]["y"]
            else:
                tx = samples[i + 1]["x"] - samples[i - 1]["x"]
                ty = samples[i + 1]["y"] - samples[i - 1]["y"]

            nrm = math.hypot(tx, ty)
            if nrm < 1e-6:
                continue
            tx /= nrm
            ty /= nrm
            nx, ny = -ty, tx

            dpos = float(s["d_pos"])
            dneg = float(s["d_neg"])

            # Positive side rescue
            t0 = dpos + float(args.probe_step)
            t1 = min(float(args.max_probe), dpos + extra)
            if t1 > t0:
                rpos = support_ratio_in_window(support_mask, black_mask, x, y, +nx, +ny, t0, t1, float(args.probe_step))
                if rpos >= float(args.rescue_support_ratio):
                    bpos = first_boundary_in_window(
                        support_mask,
                        black_mask,
                        x,
                        y,
                        +nx,
                        +ny,
                        t0,
                        t1,
                        float(args.probe_step),
                        int(args.black_run),
                        int(args.outside_run),
                    )
                    if bpos is None:
                        dpos = t1
                    else:
                        dpos = max(dpos, float(bpos))

            # Negative side rescue
            t0 = dneg + float(args.probe_step)
            t1 = min(float(args.max_probe), dneg + extra)
            if t1 > t0:
                rneg = support_ratio_in_window(support_mask, black_mask, x, y, -nx, -ny, t0, t1, float(args.probe_step))
                if rneg >= float(args.rescue_support_ratio):
                    bneg = first_boundary_in_window(
                        support_mask,
                        black_mask,
                        x,
                        y,
                        -nx,
                        -ny,
                        t0,
                        t1,
                        float(args.probe_step),
                        int(args.black_run),
                        int(args.outside_run),
                    )
                    if bneg is None:
                        dneg = t1
                    else:
                        dneg = max(dneg, float(bneg))

            new_w = float(dpos + dneg)
            if new_w > float(s["width"]) + 1e-6:
                rescued_count += 1
                s["d_pos"] = float(dpos)
                s["d_neg"] = float(dneg)
                s["width"] = new_w
                s["rescued"] = True

    # Reject outliers caused by probes hitting text labels.
    # Any sample wider than median + 2*std is likely a text hit, not a real boundary.
    _raw_vals = np.array([s["width"] for s in samples if s.get("valid") and s.get("width") is not None], dtype=np.float32)
    if _raw_vals.size >= 4:
        _med = float(np.median(_raw_vals))
        _std = float(np.std(_raw_vals))
        _upper = _med + 2.0 * _std
        text_rejected = 0
        for s in samples:
            if s.get("valid") and s.get("width") is not None and float(s["width"]) > _upper:
                s["valid"] = False
                s["reason"] = "text_outlier"
                text_rejected += 1
        if text_rejected:
            print(f"Text-outlier rejection: {text_rejected} samples above {_upper:.1f}px (median={_med:.1f} + 2*std={2*_std:.1f})")

    # Integrate area from valid consecutive samples.
    area_px2 = 0.0
    length_px = 0.0
    used_pairs = 0
    for i in range(len(samples) - 1):
        s0 = samples[i]
        s1 = samples[i + 1]
        dx = s1["x"] - s0["x"]
        dy = s1["y"] - s0["y"]
        ds = math.hypot(dx, dy)
        length_px += ds
        if s0["valid"] and s1["valid"]:
            area_px2 += ds * 0.5 * (float(s0["width"]) + float(s1["width"]))
            used_pairs += 1

    mpx = meters_per_pixel(args)
    area_m2 = area_px2 * (mpx * mpx)
    length_m = length_px * mpx

    # Diagnostics from width distribution.
    vals = np.array([s["width"] for s in samples if s.get("valid") and s.get("width") is not None], dtype=np.float32)
    w_mean = float(np.mean(vals)) if vals.size else 0.0
    w_std  = float(np.std(vals))  if vals.size else 1.0

    # Option A: per-segment sigma-class envelope integration.
    # Each valid sample is classified by sigma distance; min/max widths perturbed accordingly.
    area_min_px2 = 0.0
    area_max_px2 = 0.0
    for i in range(len(samples) - 1):
        s0 = samples[i]
        s1 = samples[i + 1]
        if not (s0.get("valid") and s1.get("valid")):
            continue
        ds = math.hypot(s1["x"] - s0["x"], s1["y"] - s0["y"])
        def _bounds(w: float) -> tuple[float, float]:
            dist = abs(w - w_mean) / w_std if w_std > 1e-6 else 0.0
            if dist <= 1.0:
                delta = 0.0          # green: nominal, no perturbation
            elif dist <= 2.0:
                delta = w_std        # yellow: ±1σ envelope
            else:
                delta = 2.0 * w_std  # red: ±2σ envelope
            return max(0.0, w - delta), w + delta
        lo0, hi0 = _bounds(float(s0["width"]))
        lo1, hi1 = _bounds(float(s1["width"]))
        area_min_px2 += ds * 0.5 * (lo0 + lo1)
        area_max_px2 += ds * 0.5 * (hi0 + hi1)
    area_min_m2 = area_min_px2 * (mpx * mpx)
    area_max_m2 = area_max_px2 * (mpx * mpx)
    w_median = float(np.median(vals)) if vals.size else None
    # w_mean and w_std already computed above for envelope integration

    vis = viz_bgr.copy()
    ph, pw = vis.shape[:2]
    mh, mw = mask_bin.shape
    sx = pw / float(mw)
    sy = ph / float(mh)

    def normal_at_idx(idx: int) -> tuple[float, float]:
        """Unit normal (perpendicular to tangent) at samples[idx]."""
        if idx == 0:
            tx = samples[1]["x"] - samples[0]["x"]
            ty = samples[1]["y"] - samples[0]["y"]
        elif idx == len(samples) - 1:
            tx = samples[-1]["x"] - samples[-2]["x"]
            ty = samples[-1]["y"] - samples[-2]["y"]
        else:
            tx = samples[idx + 1]["x"] - samples[idx - 1]["x"]
            ty = samples[idx + 1]["y"] - samples[idx - 1]["y"]
        nrm = math.hypot(tx, ty)
        if nrm < 1e-6:
            return 0.0, 1.0
        return -ty / nrm, tx / nrm

    # Draw sampled centerline.
    for i in range(len(samples) - 1):
        p0 = (int(round(samples[i]["x"] * sx)), int(round(samples[i]["y"] * sy)))
        p1 = (int(round(samples[i + 1]["x"] * sx)), int(round(samples[i + 1]["y"] * sy)))
        cv2.line(vis, p0, p1, (60, 230, 80), 1)

    stride = max(1, len(samples) // 140)
    wiggle_n = 10
    # 10 offsets spread symmetrically across ±stride around each display tick.
    wiggle_offsets = np.round(np.linspace(-stride, stride, wiggle_n)).astype(int)

    # Pre-collect wiggle data for every display tick so we only iterate once.
    display_data: dict = {}
    for center_idx in range(0, len(samples), stride):
        seen_j: set = set()
        valid_data = []
        for off in wiggle_offsets:
            j = int(np.clip(center_idx + off, 0, len(samples) - 1))
            if j in seen_j:
                continue
            seen_j.add(j)
            sw = samples[j]
            if not sw.get("valid") or sw.get("d_pos") is None or sw.get("d_neg") is None:
                continue
            nx, ny = normal_at_idx(j)
            valid_data.append((j, sw, nx, ny))
        display_data[center_idx] = valid_data

    # Draw wiggle bars on a separate layer, then blend semi-transparently.
    wiggle_layer = vis.copy()
    for valid_data in display_data.values():
        for _, sw, nx, ny in valid_data:
            x, y = sw["x"], sw["y"]
            dp, dn = float(sw["d_pos"]), float(sw["d_neg"])
            q1 = (int(round((x + nx * dp) * sx)), int(round((y + ny * dp) * sy)))
            q2 = (int(round((x - nx * dn) * sx)), int(round((y - ny * dn) * sy)))
            cv2.line(wiggle_layer, q1, q2, (0, 200, 255), 1)
    cv2.addWeighted(wiggle_layer, 0.45, vis, 0.55, 0, vis)

    # Sigma-based colour for median bars: green=±1σ, yellow=±2σ, red=beyond.
    _w_mean = w_mean if w_mean is not None else float(np.mean(vals)) if vals.size else 0.0
    _w_std  = w_std  if w_std  is not None else 1.0

    def sigma_color(width_px: float) -> tuple[int, int, int]:
        if _w_std < 1e-6:
            return (0, 220, 0)
        dist = abs(width_px - _w_mean) / _w_std
        if dist <= 1.0:
            return (0, 200, 0)    # green  (BGR)
        if dist <= 2.0:
            return (0, 200, 255)  # yellow (BGR)
        return (0, 0, 220)        # red    (BGR)

    # Draw median bar at each display tick with full opacity on top.
    sigma_counts = {"green": 0, "yellow": 0, "red": 0}
    for center_idx, valid_data in display_data.items():
        if not valid_data:
            continue
        nx_c, ny_c = normal_at_idx(center_idx)
        sc = samples[center_idx]
        xc, yc = sc["x"], sc["y"]
        med_dpos = float(np.median([float(sw["d_pos"]) for (_, sw, _, _) in valid_data]))
        med_dneg = float(np.median([float(sw["d_neg"]) for (_, sw, _, _) in valid_data]))
        med_width = med_dpos + med_dneg
        color = sigma_color(med_width)
        if color == (0, 200, 0):
            sigma_counts["green"] += 1
        elif color == (0, 200, 255):
            sigma_counts["yellow"] += 1
        else:
            sigma_counts["red"] += 1
        m1 = (int(round((xc + nx_c * med_dpos) * sx)), int(round((yc + ny_c * med_dpos) * sy)))
        m2 = (int(round((xc - nx_c * med_dneg) * sx)), int(round((yc - ny_c * med_dneg) * sy)))
        cv2.line(vis, m1, m2, color, 3)
    total_bars = sum(sigma_counts.values())
    print(f"Width bars: green={sigma_counts['green']} yellow={sigma_counts['yellow']} red={sigma_counts['red']} total={total_bars} (mean={_w_mean:.1f}px ±1σ={_w_std:.1f}px)")

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    p_json = outdir / "seeds_08_first_hit_area.json"
    p_img = outdir / "seeds_08_first_hit_width_overlay.png"

    payload = {
        "probe_mask": args.probe_mask,
        "support_model": {
            "support_pixels": "raw_mask_white OR page_gray OR page_green",
            "boundary_pixels": "consecutive black OR consecutive outside-support",
            "black_run": int(args.black_run),
            "outside_run": int(args.outside_run),
        },
        "rescue_model": {
            "active": True,
            "criterion": "width < median - rescue_low_sigma * std",
            "rescue_low_sigma": float(args.rescue_low_sigma),
            "rescue_extra_sigma": float(args.rescue_extra_sigma),
            "rescue_support_ratio": float(args.rescue_support_ratio),
            "rescued_samples": int(rescued_count),
        },
        "samples_count": len(samples),
        "valid_width_samples": int(vals.size),
        "snapped_sample_centers": int(snapped),
        "length_px": float(length_px),
        "length_m": float(length_m),
        "area_px2": float(area_px2),
        "area_m2": float(area_m2),
        "area_envelope_m2": {
            "min": float(area_min_m2),
            "nominal": float(area_m2),
            "max": float(area_max_m2),
            "method": "per-segment sigma-class perturbation: green=0, yellow=±1σ, red=±2σ",
        },
        "width_px": {
            "mean": w_mean,
            "median": w_median,
            "std": w_std,
            "min": None if vals.size == 0 else float(np.min(vals)),
            "max": None if vals.size == 0 else float(np.max(vals)),
        },
        "integration": {
            "formula": "A = sum ds * (w_i + w_{i+1})/2 over valid neighboring sample pairs",
            "used_pairs": int(used_pairs),
            "total_pairs": int(max(0, len(samples) - 1)),
        },
    }

    p_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    cv2.imwrite(str(p_img), vis)

    print(f"Samples: {len(samples)} | valid widths: {vals.size} | snapped: {snapped}")
    print(f"Length: {length_px:.2f} px | {length_m:.2f} m")
    print(f"Area (first-hit-black): {area_px2:.2f} px2 | {area_m2:.2f} m2")
    print(f"Area envelope (sigma-class): min={area_min_m2:.2f} m2 | nom={area_m2:.2f} m2 | max={area_max_m2:.2f} m2")
    if vals.size:
        print(f"Width px mean={w_mean:.2f} median={w_median:.2f} std={w_std:.2f} min={float(np.min(vals)):.2f} max={float(np.max(vals)):.2f}")
    print(f"Wrote: {p_json}")
    print(f"Wrote: {p_img}")


if __name__ == "__main__":
    main()

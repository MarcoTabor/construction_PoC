#!/usr/bin/env python3
"""Extract inner and outer corridor lines from a mask and centerline.

Method:
1. Read centerline nodes.
2. For each node, compute tangent and normal.
3. Ray-march in both normal directions to hit mask boundary.
4. Classify which side is inner vs outer using circle-fit radius at bend.
5. Export full inner/outer polylines and QA overlay.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import cv2
import numpy as np


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Extract inner/outer lines from path mask")
    p.add_argument("--mask", required=True, help="Binary mask PNG")
    p.add_argument("--centerline-json", required=True, help="JSON containing path_nodes_yx and meters_per_pixel")
    p.add_argument("--boundary-fit-mode", choices=["raycast", "optimize"], default="raycast", help="Boundary fit method: direct normal ray-cast or offset optimization to boundary")
    p.add_argument("--ray-step-px", type=float, default=0.5)
    p.add_argument("--station-spacing-m", type=float, default=None, help="Spacing of centerline stations used to cast normals. Defaults to seed_spacing_m from centerline JSON.")
    p.add_argument("--tangent-span-stations", type=int, default=8, help="Half-window (in station count) used to estimate stable tangent directions")
    p.add_argument("--end-tangent-guard-stations", type=int, default=3, help="Force first/last casts to use nearby interior tangent direction")
    p.add_argument("--end-cast-neighbor-count", type=int, default=6, help="Neighbor casts used to align endpoint cast direction")
    p.add_argument("--opt-offset-max-px", type=float, default=140.0, help="Maximum normal offset searched by optimization mode")
    p.add_argument("--opt-offset-step-px", type=float, default=1.0, help="Offset increment for optimization mode")
    p.add_argument("--opt-smooth-lambda", type=float, default=0.6, help="Smoothness weight for optimization mode (higher = smoother offsets)")
    p.add_argument("--opt-near-bias", type=float, default=0.01, help="Small penalty for larger offsets to favor the nearest valid edge")
    p.add_argument("--bend-window-m", type=float, default=20.0)
    p.add_argument("--smooth-window", type=int, default=1)
    p.add_argument("--normal-viz-step", type=int, default=12, help="Draw every Nth normal in the normal-cast visualization")
    p.add_argument("--out-steps-dir", default="", help="Optional directory for step-by-step PNGs")
    p.add_argument("--out-json", default="outputs/joal502/joal_inner_outer_lines.json")
    p.add_argument("--out-overlay", default="outputs/joal502/visualizations/joal/joal_inner_outer_lines_overlay.png")
    return p.parse_args()


def cumulative_lengths(path_yx: np.ndarray) -> np.ndarray:
    out = np.zeros((len(path_yx),), dtype=np.float64)
    for i in range(1, len(path_yx)):
        dy = float(path_yx[i, 0] - path_yx[i - 1, 0])
        dx = float(path_yx[i, 1] - path_yx[i - 1, 1])
        out[i] = out[i - 1] + math.hypot(dx, dy)
    return out


def inside(mask: np.ndarray, y: float, x: float) -> bool:
    h, w = mask.shape
    yi = int(round(y))
    xi = int(round(x))
    if yi < 0 or yi >= h or xi < 0 or xi >= w:
        return False
    return bool(mask[yi, xi] > 0)


def march_to_boundary(mask: np.ndarray, y: float, x: float, dy: float, dx: float, step: float) -> tuple[float, float] | None:
    n = math.hypot(dx, dy)
    if n <= 1e-9:
        return None
    ux = dx / n
    uy = dy / n

    cy, cx = y, x
    last_in = None
    for _ in range(10000):
        cy += uy * step
        cx += ux * step
        if inside(mask, cy, cx):
            last_in = (cy, cx)
            continue
        break
    return last_in


def sample_centerline_stations(path_yx: np.ndarray, spacing_px: float) -> tuple[np.ndarray, np.ndarray]:
    """Sample centerline stations (including 0 and end) and return points plus tangents."""
    if len(path_yx) < 2:
        raise RuntimeError("Centerline must contain at least two points")

    cum = cumulative_lengths(path_yx)
    total = float(cum[-1])
    if total <= 1e-9:
        raise RuntimeError("Centerline has zero length")

    step = max(1e-6, float(spacing_px))
    stations = list(np.arange(0.0, total, step))
    if not stations or abs(stations[-1] - total) > 1e-6:
        stations.append(total)

    pts: list[np.ndarray] = []
    tans: list[np.ndarray] = []
    seg = 0
    for s in stations:
        while seg + 1 < len(cum) and cum[seg + 1] < s:
            seg += 1

        i0 = min(seg, len(path_yx) - 2)
        i1 = i0 + 1
        seg_len = max(1e-9, float(cum[i1] - cum[i0]))
        frac = float((s - cum[i0]) / seg_len)
        p = path_yx[i0] * (1.0 - frac) + path_yx[i1] * frac
        t = path_yx[i1] - path_yx[i0]

        pts.append(p)
        tans.append(t)

    return np.array(pts, dtype=np.float64), np.array(tans, dtype=np.float64)


def stable_station_tangents(stations_yx: np.ndarray, span: int) -> np.ndarray:
    """Estimate tangents from a neighborhood so normals stay aligned across nearby casts."""
    n = len(stations_yx)
    if n < 2:
        return np.zeros_like(stations_yx)

    k = max(1, int(span))
    tans = np.zeros_like(stations_yx)
    for i in range(n):
        i0 = max(0, i - k)
        i1 = min(n - 1, i + k)
        t = stations_yx[i1] - stations_yx[i0]
        tn = float(np.linalg.norm(t))
        if tn < 1e-9:
            if i > 0:
                t = stations_yx[i] - stations_yx[i - 1]
            else:
                t = stations_yx[min(1, n - 1)] - stations_yx[0]
            tn = float(np.linalg.norm(t))
            if tn < 1e-9:
                t = np.array([1.0, 0.0], dtype=np.float64)

        tans[i] = t

    return tans


def apply_endpoint_tangent_guard(tangents: np.ndarray, guard_stations: int) -> np.ndarray:
    """Align endpoint tangent directions with nearby interior casts to avoid end-angle outliers."""
    n = len(tangents)
    if n < 3:
        return tangents

    g = max(1, min(int(guard_stations), (n - 1) // 2))
    out = tangents.copy()

    left_ref = out[g].copy()
    right_ref = out[n - 1 - g].copy()

    for i in range(g):
        out[i] = left_ref
        out[n - 1 - i] = right_ref

    return out


def _mean_unit(vectors: np.ndarray) -> np.ndarray | None:
    if len(vectors) == 0:
        return None
    acc = np.array([0.0, 0.0], dtype=np.float64)
    for v in vectors:
        n = float(np.linalg.norm(v))
        if n < 1e-9:
            continue
        acc += v / n
    an = float(np.linalg.norm(acc))
    if an < 1e-9:
        return None
    return acc / an


def align_endpoint_cast_hits(
    mask: np.ndarray,
    stations_yx: np.ndarray,
    side_hits_yx: np.ndarray,
    neighbor_count: int,
    ray_step_px: float,
) -> np.ndarray:
    """Re-cast first and last side hit using consensus cast direction from nearby stations."""
    n = len(side_hits_yx)
    if n < 4:
        return side_hits_yx

    k = max(2, min(int(neighbor_count), n - 2))
    out = side_hits_yx.copy()

    side_vecs = out - stations_yx

    # Start endpoint.
    start_ref = _mean_unit(side_vecs[1 : 1 + k])
    if start_ref is not None:
        p = march_to_boundary(
            mask,
            float(stations_yx[0, 0]),
            float(stations_yx[0, 1]),
            float(start_ref[0]),
            float(start_ref[1]),
            step=float(ray_step_px),
        )
        if p is not None:
            out[0] = np.array([p[0], p[1]], dtype=np.float64)

    # End endpoint.
    end_ref = _mean_unit(side_vecs[n - 1 - k : n - 1])
    if end_ref is not None:
        p = march_to_boundary(
            mask,
            float(stations_yx[-1, 0]),
            float(stations_yx[-1, 1]),
            float(end_ref[0]),
            float(end_ref[1]),
            step=float(ray_step_px),
        )
        if p is not None:
            out[-1] = np.array([p[0], p[1]], dtype=np.float64)

    return out


def boundary_distance_map(mask: np.ndarray) -> np.ndarray:
    """Distance (px) from every pixel to the nearest mask boundary pixel."""
    ker = np.ones((3, 3), dtype=np.uint8)
    edge = cv2.morphologyEx((mask > 0).astype(np.uint8) * 255, cv2.MORPH_GRADIENT, ker)
    inv = 255 - edge
    return cv2.distanceTransform(inv, cv2.DIST_L2, 3)


def bilinear_sample(img: np.ndarray, y: float, x: float) -> float:
    h, w = img.shape
    if y < 0.0 or x < 0.0 or y > (h - 1) or x > (w - 1):
        return 1e6

    y0 = int(math.floor(y))
    x0 = int(math.floor(x))
    y1 = min(y0 + 1, h - 1)
    x1 = min(x0 + 1, w - 1)

    wy = y - y0
    wx = x - x0

    v00 = float(img[y0, x0])
    v01 = float(img[y0, x1])
    v10 = float(img[y1, x0])
    v11 = float(img[y1, x1])

    v0 = v00 * (1.0 - wx) + v01 * wx
    v1 = v10 * (1.0 - wx) + v11 * wx
    return v0 * (1.0 - wy) + v1 * wy


def optimize_side_hits(
    mask: np.ndarray,
    stations_yx: np.ndarray,
    station_tangents: np.ndarray,
    dist_to_edge: np.ndarray,
    side_sign: float,
    offset_max_px: float,
    offset_step_px: float,
    smooth_lambda: float,
    near_bias: float,
    ray_step_px: float,
) -> np.ndarray:
    """Fit one boundary side by searching normal offsets with smoothness regularization."""
    h, w = mask.shape
    n = len(stations_yx)
    out = np.zeros_like(stations_yx)

    ds = np.arange(0.0, max(1e-6, float(offset_max_px)) + 1e-9, max(1e-6, float(offset_step_px)), dtype=np.float64)

    prev_d = 0.0
    for i in range(n):
        t = station_tangents[i]
        tn = float(np.linalg.norm(t))
        if tn < 1e-9:
            out[i] = stations_yx[i]
            continue

        ny = -t[1] / tn
        nx = t[0] / tn
        ny *= float(side_sign)
        nx *= float(side_sign)

        py = float(stations_yx[i, 0])
        px = float(stations_yx[i, 1])

        # Constrain search to the first boundary hit along the selected side.
        first_hit = march_to_boundary(mask, py, px, ny, nx, step=float(ray_step_px))
        if first_hit is None:
            out[i] = stations_yx[i]
            continue
        d_cap = float(np.linalg.norm(np.array([first_hit[0] - py, first_hit[1] - px], dtype=np.float64)))
        if d_cap <= 1e-9:
            out[i] = np.array([first_hit[0], first_hit[1]], dtype=np.float64)
            prev_d = 0.0
            continue

        best_cost = 1e18
        best_p = np.array([py, px], dtype=np.float64)
        best_d = 0.0

        for d in ds:
            if float(d) > d_cap:
                break
            y = py + ny * float(d)
            x = px + nx * float(d)

            if y < 0.0 or y > (h - 1) or x < 0.0 or x > (w - 1):
                break

            edge_dist = bilinear_sample(dist_to_edge, y, x)
            smooth_cost = float(smooth_lambda) * abs(float(d) - prev_d)
            bias_cost = float(near_bias) * float(d)
            cost = edge_dist + smooth_cost + bias_cost

            if cost < best_cost:
                best_cost = cost
                best_p = np.array([y, x], dtype=np.float64)
                best_d = float(d)

        out[i] = best_p
        prev_d = best_d

    return out


def strongest_bend_index(path_yx: np.ndarray, k: int = 20) -> int:
    n = len(path_yx)
    if n < 2 * k + 1:
        return n // 2
    best_i = n // 2
    best_ang = -1.0
    for i in range(k, n - k):
        v1 = path_yx[i] - path_yx[i - k]
        v2 = path_yx[i + k] - path_yx[i]
        n1 = float(np.linalg.norm(v1))
        n2 = float(np.linalg.norm(v2))
        if n1 < 1e-6 or n2 < 1e-6:
            continue
        cross = float(v1[1] * v2[0] - v1[0] * v2[1])
        dot = float(v1[1] * v2[1] + v1[0] * v2[0])
        ang = abs(math.atan2(cross, dot))
        if ang > best_ang:
            best_ang = ang
            best_i = i
    return best_i


def fit_circle(points_xy: np.ndarray) -> tuple[float, float, float]:
    x = points_xy[:, 0]
    y = points_xy[:, 1]
    m = np.stack([x, y, np.ones_like(x)], axis=1)
    rhs = -(x * x + y * y)
    sol, *_ = np.linalg.lstsq(m, rhs, rcond=None)
    a, b, c = sol
    cx = -0.5 * a
    cy = -0.5 * b
    r2 = cx * cx + cy * cy - c
    r = math.sqrt(max(0.0, float(r2)))
    return cx, cy, r


def smooth_polyline_yx(path_yx: np.ndarray, window: int) -> np.ndarray:
    if len(path_yx) < 3:
        return path_yx
    k = max(1, int(window))
    if k % 2 == 0:
        k += 1
    if len(path_yx) < k:
        return path_yx

    pad = k // 2
    kernel = np.ones((k,), dtype=np.float64) / float(k)
    y_pad = np.pad(path_yx[:, 0], (pad, pad), mode="edge")
    x_pad = np.pad(path_yx[:, 1], (pad, pad), mode="edge")
    ys = np.convolve(y_pad, kernel, mode="valid")
    xs = np.convolve(x_pad, kernel, mode="valid")
    return np.stack([ys, xs], axis=1)


def enforce_forward_progress(line_yx: np.ndarray, center_yx: np.ndarray) -> np.ndarray:
    """Prevent local backtracking relative to centerline station direction."""
    if len(line_yx) < 2 or len(center_yx) != len(line_yx):
        return line_yx

    out = line_yx.copy()
    for i in range(1, len(out)):
        t = center_yx[i] - center_yx[i - 1]
        tn = float(np.linalg.norm(t))
        if tn < 1e-9:
            continue
        u = t / tn

        step = out[i] - out[i - 1]
        prog = float(np.dot(step, u))
        if prog < 0.0:
            # Shift point minimally along station direction to keep non-negative progress.
            out[i] = out[i] + (-prog + 1e-6) * u

    return out


def main() -> int:
    args = parse_args()

    mask = cv2.imread(str(Path(args.mask)), cv2.IMREAD_GRAYSCALE)
    if mask is None:
        raise RuntimeError(f"Failed to read mask: {args.mask}")

    data = json.loads(Path(args.centerline_json).read_text(encoding="utf-8"))
    path_nodes = data.get("path_nodes_yx") or []
    if not path_nodes:
        raise RuntimeError("Missing path_nodes_yx in centerline JSON")

    mpp = float((data.get("summary") or {}).get("meters_per_pixel"))
    path = np.array(path_nodes, dtype=np.float64)
    center_summary = data.get("summary") or {}
    spacing_m = args.station_spacing_m
    if spacing_m is None:
        spacing_m = float(center_summary.get("seed_spacing_m", 1.0))
    spacing_px = float(spacing_m) / max(1e-9, mpp)

    stations_yx, _ = sample_centerline_stations(path, spacing_px=spacing_px)
    station_tangents = stable_station_tangents(stations_yx, span=int(args.tangent_span_stations))
    station_tangents = apply_endpoint_tangent_guard(
        station_tangents,
        guard_stations=int(args.end_tangent_guard_stations),
    )

    left = np.zeros_like(stations_yx)
    right = np.zeros_like(stations_yx)

    if str(args.boundary_fit_mode) == "optimize":
        dist_to_edge = boundary_distance_map(mask)
        left = optimize_side_hits(
            mask,
            stations_yx,
            station_tangents,
            dist_to_edge,
            side_sign=1.0,
            offset_max_px=float(args.opt_offset_max_px),
            offset_step_px=float(args.opt_offset_step_px),
            smooth_lambda=float(args.opt_smooth_lambda),
            near_bias=float(args.opt_near_bias),
            ray_step_px=float(args.ray_step_px),
        )
        right = optimize_side_hits(
            mask,
            stations_yx,
            station_tangents,
            dist_to_edge,
            side_sign=-1.0,
            offset_max_px=float(args.opt_offset_max_px),
            offset_step_px=float(args.opt_offset_step_px),
            smooth_lambda=float(args.opt_smooth_lambda),
            near_bias=float(args.opt_near_bias),
            ray_step_px=float(args.ray_step_px),
        )
        left = align_endpoint_cast_hits(
            mask,
            stations_yx,
            left,
            neighbor_count=int(args.end_cast_neighbor_count),
            ray_step_px=float(args.ray_step_px),
        )
        right = align_endpoint_cast_hits(
            mask,
            stations_yx,
            right,
            neighbor_count=int(args.end_cast_neighbor_count),
            ray_step_px=float(args.ray_step_px),
        )
    else:
        for i in range(len(stations_yx)):
            t = station_tangents[i]
            tn = float(np.linalg.norm(t))
            if tn < 1e-9:
                left[i] = stations_yx[i]
                right[i] = stations_yx[i]
                continue

            ny = -t[1] / tn
            nx = t[0] / tn
            py = float(stations_yx[i, 0])
            px = float(stations_yx[i, 1])

            p_left = march_to_boundary(mask, py, px, ny, nx, step=float(args.ray_step_px))
            p_right = march_to_boundary(mask, py, px, -ny, -nx, step=float(args.ray_step_px))

            left[i] = np.array([p_left[0], p_left[1]], dtype=np.float64) if p_left else stations_yx[i]
            right[i] = np.array([p_right[0], p_right[1]], dtype=np.float64) if p_right else stations_yx[i]

        left = align_endpoint_cast_hits(
            mask,
            stations_yx,
            left,
            neighbor_count=int(args.end_cast_neighbor_count),
            ray_step_px=float(args.ray_step_px),
        )
        right = align_endpoint_cast_hits(
            mask,
            stations_yx,
            right,
            neighbor_count=int(args.end_cast_neighbor_count),
            ray_step_px=float(args.ray_step_px),
        )

    cum = cumulative_lengths(stations_yx)
    bend_idx = strongest_bend_index(stations_yx)
    bend_s = float(cum[bend_idx])
    window_px = float(args.bend_window_m) / max(1e-9, mpp)
    idxs = np.where(np.abs(cum - bend_s) <= window_px)[0]
    if len(idxs) < 20:
        idxs = np.arange(max(0, bend_idx - 40), min(len(stations_yx), bend_idx + 40))

    left_xy = np.stack([left[idxs, 1], left[idxs, 0]], axis=1)
    right_xy = np.stack([right[idxs, 1], right[idxs, 0]], axis=1)
    _, _, lr = fit_circle(left_xy)
    _, _, rr = fit_circle(right_xy)

    if lr <= rr:
        inner = left.copy()
        outer = right.copy()
        inner_name = "left"
    else:
        inner = right.copy()
        outer = left.copy()
        inner_name = "right"

    inner = smooth_polyline_yx(inner, window=int(args.smooth_window))
    outer = smooth_polyline_yx(outer, window=int(args.smooth_window))
    inner = enforce_forward_progress(inner, stations_yx)
    outer = enforce_forward_progress(outer, stations_yx)

    source_center_len_m = float(cumulative_lengths(path)[-1] * mpp)
    center_len_m = float(cumulative_lengths(stations_yx)[-1] * mpp)
    inner_len_m = float(cumulative_lengths(inner)[-1] * mpp)
    outer_len_m = float(cumulative_lengths(outer)[-1] * mpp)

    out = {
        "summary": {
            "mask": str(args.mask),
            "centerline_json": str(args.centerline_json),
            "meters_per_pixel": mpp,
            "boundary_fit_mode": str(args.boundary_fit_mode),
            "station_spacing_m": float(spacing_m),
            "centerline_length_m": source_center_len_m,
            "centerline_length_stations_m": center_len_m,
            "bend_index": int(bend_idx),
            "bend_window_m": float(args.bend_window_m),
            "inner_side_from_normals": inner_name,
            "inner_line_length_m": inner_len_m,
            "outer_line_length_m": outer_len_m,
            "sample_count": int(len(stations_yx)),
        },
        "centerline_yx": stations_yx.tolist(),
        "inner_line_yx": inner.tolist(),
        "outer_line_yx": outer.tolist(),
    }

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(out, indent=2), encoding="utf-8")

    def new_canvas() -> np.ndarray:
        v = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
        v[mask > 0] = (60, 235, 60)
        return v

    vis = new_canvas()

    def draw_line(arr: np.ndarray, color: tuple[int, int, int], thickness: int) -> None:
        for i in range(1, len(arr)):
            x0, y0 = int(round(arr[i - 1, 1])), int(round(arr[i - 1, 0]))
            x1, y1 = int(round(arr[i, 1])), int(round(arr[i, 0]))
            cv2.line(vis, (x0, y0), (x1, y1), color, thickness, lineType=cv2.LINE_AA)

    # Step 01: centerline stations (seeds).
    vis = new_canvas()
    draw_line(stations_yx, (0, 191, 255), 1)
    for i in range(len(stations_yx)):
        x = int(round(stations_yx[i, 1]))
        y = int(round(stations_yx[i, 0]))
        cv2.circle(vis, (x, y), 1, (0, 0, 255), -1)
    step_01 = vis.copy()

    # Step 02: normal casts from each station to left/right mask edges.
    vis = new_canvas()
    draw_line(stations_yx, (0, 191, 255), 1)
    normal_step = max(1, int(args.normal_viz_step))
    for i in range(0, len(stations_yx), normal_step):
        sx = int(round(stations_yx[i, 1]))
        sy = int(round(stations_yx[i, 0]))
        lx = int(round(left[i, 1]))
        ly = int(round(left[i, 0]))
        rx = int(round(right[i, 1]))
        ry = int(round(right[i, 0]))
        cv2.line(vis, (sx, sy), (lx, ly), (255, 120, 120), 1, lineType=cv2.LINE_AA)
        cv2.line(vis, (sx, sy), (rx, ry), (120, 255, 255), 1, lineType=cv2.LINE_AA)
    step_02 = vis.copy()

    # Step 03: classified inner/outer seeds (points only).
    vis = new_canvas()
    draw_line(stations_yx, (0, 191, 255), 1)
    for i in range(len(stations_yx)):
        ix = int(round(inner[i, 1]))
        iy = int(round(inner[i, 0]))
        ox = int(round(outer[i, 1]))
        oy = int(round(outer[i, 0]))
        cv2.circle(vis, (ix, iy), 1, (255, 0, 255), -1)
        cv2.circle(vis, (ox, oy), 1, (255, 255, 0), -1)
    step_03 = vis.copy()

    # Step 04: connected inner and outer lines.
    vis = new_canvas()
    draw_line(stations_yx, (0, 191, 255), 1)
    draw_line(inner, (255, 0, 255), 2)
    draw_line(outer, (255, 255, 0), 2)
    step_04 = vis.copy()

    out_overlay = Path(args.out_overlay)
    out_overlay.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_overlay), step_04)

    if str(args.out_steps_dir).strip():
        steps_dir = Path(args.out_steps_dir)
    else:
        steps_dir = out_overlay.parent / "inner_outer_steps"
    steps_dir.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(steps_dir / "step_01_centerline_seeds.png"), step_01)
    cv2.imwrite(str(steps_dir / "step_02_normal_casts.png"), step_02)
    cv2.imwrite(str(steps_dir / "step_03_inner_outer_seeds.png"), step_03)
    cv2.imwrite(str(steps_dir / "step_04_connected_lines.png"), step_04)

    print(f"Wrote lines JSON: {out_json}")
    print(f"Wrote overlay: {out_overlay}")
    print(f"Wrote step images dir: {steps_dir}")
    print(f"Centerline length: {source_center_len_m:.3f} m")
    print(f"Centerline length (stations): {center_len_m:.3f} m")
    print(f"Inner line length: {inner_len_m:.3f} m")
    print(f"Outer line length: {outer_len_m:.3f} m")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

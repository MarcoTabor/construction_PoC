#!/usr/bin/env python3
"""Derive inner/outer lines by splitting the shell contour into two arcs.

Workflow:
1) Load filled corridor shell mask (derived from vectors).
2) Extract largest closed contour of the shell.
3) Anchor near centerline start/end and split contour into two arcs.
4) Classify arc with smaller bend-center radius as inner; other as outer.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import cv2
import numpy as np


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Extract inner/outer lines from shell contour")
    p.add_argument("--mask", required=True, help="Filled shell mask PNG")
    p.add_argument("--centerline-json", required=True, help="Centerline JSON (path_nodes_yx + meters_per_pixel)")
    p.add_argument("--smooth-window", type=int, default=9, help="Moving-average window for contour smoothing")
    p.add_argument("--out-json", default="outputs/joal502/joal_inner_outer_from_shell.json")
    p.add_argument("--out-overlay", default="outputs/joal502/visualizations/joal/joal_inner_outer_from_shell_overlay.png")
    return p.parse_args()


def cumulative_lengths(path_yx: np.ndarray) -> np.ndarray:
    out = np.zeros((len(path_yx),), dtype=np.float64)
    for i in range(1, len(path_yx)):
        dy = float(path_yx[i, 0] - path_yx[i - 1, 0])
        dx = float(path_yx[i, 1] - path_yx[i - 1, 1])
        out[i] = out[i - 1] + math.hypot(dx, dy)
    return out


def smooth_polyline_yx(path_yx: np.ndarray, window: int) -> np.ndarray:
    if len(path_yx) < 3:
        return path_yx
    k = max(1, int(window))
    if k % 2 == 0:
        k += 1
    if len(path_yx) < k:
        return path_yx

    pad = k // 2
    ker = np.ones((k,), dtype=np.float64) / float(k)
    yp = np.pad(path_yx[:, 0], (pad, pad), mode="edge")
    xp = np.pad(path_yx[:, 1], (pad, pad), mode="edge")
    ys = np.convolve(yp, ker, mode="valid")
    xs = np.convolve(xp, ker, mode="valid")
    return np.stack([ys, xs], axis=1)


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


def estimate_bend_center(path_yx: np.ndarray) -> np.ndarray:
    bi = strongest_bend_index(path_yx)
    i0 = max(0, bi - 80)
    i1 = min(len(path_yx), bi + 81)
    pts = path_yx[i0:i1]
    if len(pts) < 10:
        return np.mean(path_yx, axis=0)
    xy = np.stack([pts[:, 1], pts[:, 0]], axis=1)
    cx, cy, r = fit_circle(xy)
    if not np.isfinite(cx) or not np.isfinite(cy) or r <= 1e-6:
        return np.mean(path_yx, axis=0)
    return np.array([cy, cx], dtype=np.float64)


def nearest_index(points_yx: np.ndarray, target_yx: np.ndarray) -> int:
    d2 = np.sum((points_yx - target_yx) ** 2, axis=1)
    return int(np.argmin(d2))


def split_closed_contour(loop_yx: np.ndarray, i0: int, i1: int) -> tuple[np.ndarray, np.ndarray]:
    if i0 <= i1:
        a = loop_yx[i0 : i1 + 1]
        b = np.vstack([loop_yx[i1:], loop_yx[: i0 + 1]])
    else:
        a = np.vstack([loop_yx[i0:], loop_yx[: i1 + 1]])
        b = loop_yx[i1 : i0 + 1]
    return a, b


def orient_start_to_end(curve_yx: np.ndarray, start_yx: np.ndarray, end_yx: np.ndarray) -> np.ndarray:
    forward = float(np.linalg.norm(curve_yx[0] - start_yx) + np.linalg.norm(curve_yx[-1] - end_yx))
    reverse = float(np.linalg.norm(curve_yx[-1] - start_yx) + np.linalg.norm(curve_yx[0] - end_yx))
    if reverse < forward:
        return curve_yx[::-1].copy()
    return curve_yx


def main() -> int:
    args = parse_args()

    mask = cv2.imread(str(Path(args.mask)), cv2.IMREAD_GRAYSCALE)
    if mask is None:
        raise RuntimeError(f"Failed to read mask: {args.mask}")

    data = json.loads(Path(args.centerline_json).read_text(encoding="utf-8"))
    path_nodes = data.get("path_nodes_yx") or []
    if not path_nodes:
        raise RuntimeError("Missing path_nodes_yx in centerline JSON")

    centerline = np.array(path_nodes, dtype=np.float64)
    mpp = float((data.get("summary") or {}).get("meters_per_pixel"))

    # Largest shell contour.
    bin_mask = (mask > 0).astype(np.uint8)
    contours, _ = cv2.findContours(bin_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        raise RuntimeError("No contour found in shell mask")
    contour = max(contours, key=cv2.contourArea)
    loop = contour[:, 0, :]  # Nx2 x,y
    loop_yx = np.stack([loop[:, 1], loop[:, 0]], axis=1).astype(np.float64)

    start = centerline[0]
    end = centerline[-1]
    i0 = nearest_index(loop_yx, start)
    i1 = nearest_index(loop_yx, end)

    arc_a, arc_b = split_closed_contour(loop_yx, i0, i1)
    bend_center = estimate_bend_center(centerline)

    ra = np.linalg.norm(arc_a - bend_center, axis=1)
    rb = np.linalg.norm(arc_b - bend_center, axis=1)

    if float(np.mean(ra)) <= float(np.mean(rb)):
        inner = arc_a.copy()
        outer = arc_b.copy()
    else:
        inner = arc_b.copy()
        outer = arc_a.copy()

    inner = orient_start_to_end(inner, start, end)
    outer = orient_start_to_end(outer, start, end)

    inner = smooth_polyline_yx(inner, window=int(args.smooth_window))
    outer = smooth_polyline_yx(outer, window=int(args.smooth_window))

    center_len_m = float(cumulative_lengths(centerline)[-1] * mpp)
    inner_len_m = float(cumulative_lengths(inner)[-1] * mpp)
    outer_len_m = float(cumulative_lengths(outer)[-1] * mpp)

    out = {
        "summary": {
            "mask": str(args.mask),
            "centerline_json": str(args.centerline_json),
            "meters_per_pixel": mpp,
            "method": "shell_contour_split",
            "smooth_window": int(args.smooth_window),
            "centerline_length_m": center_len_m,
            "inner_line_length_m": inner_len_m,
            "outer_line_length_m": outer_len_m,
            "contour_point_count": int(len(loop_yx)),
        },
        "centerline_yx": centerline.tolist(),
        "inner_line_yx": inner.tolist(),
        "outer_line_yx": outer.tolist(),
    }

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(out, indent=2), encoding="utf-8")

    vis = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
    vis[mask > 0] = (60, 235, 60)

    def draw(arr: np.ndarray, color: tuple[int, int, int]) -> None:
        for i in range(1, len(arr)):
            x0, y0 = int(round(arr[i - 1, 1])), int(round(arr[i - 1, 0]))
            x1, y1 = int(round(arr[i, 1])), int(round(arr[i, 0]))
            cv2.line(vis, (x0, y0), (x1, y1), color, 1, lineType=cv2.LINE_8)

    draw(inner, (255, 0, 255))
    draw(centerline, (0, 191, 255))
    draw(outer, (255, 255, 0))

    out_overlay = Path(args.out_overlay)
    out_overlay.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_overlay), vis)

    print(f"Wrote JSON: {out_json}")
    print(f"Wrote overlay: {out_overlay}")
    print(f"Centerline: {center_len_m:.3f} m")
    print(f"Inner: {inner_len_m:.3f} m")
    print(f"Outer: {outer_len_m:.3f} m")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

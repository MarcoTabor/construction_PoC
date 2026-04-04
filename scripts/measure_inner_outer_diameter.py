#!/usr/bin/env python3
"""Measure inner and outer diameters around the main bend of a corridor mask.

Method:
1. Load a binary mask and centerline path.
2. Detect the strongest bend on the centerline.
3. Sample boundary points on both sides of the centerline normal around that bend.
4. Fit circles to both boundary point sets.
5. Report inner/outer diameters (smaller/larger fitted circles).
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import cv2
import numpy as np


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Measure inner and outer bend diameters from mask + centerline")
    p.add_argument("--mask", required=True, help="Binary mask PNG")
    p.add_argument("--centerline-json", required=True, help="Centerline JSON with path_nodes_yx")
    p.add_argument("--window-m", type=float, default=20.0, help="Arc-length window around bend center")
    p.add_argument("--ray-step-px", type=float, default=0.5, help="Ray marching step")
    p.add_argument("--out-json", default="outputs/joal502/joal_inner_outer_diameter.json")
    p.add_argument("--out-overlay", default="outputs/joal502/visualizations/joal/joal_inner_outer_diameter_overlay.png")
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

    cy = y
    cx = x
    last_in = None
    for _ in range(8000):
        cy += uy * step
        cx += ux * step
        if inside(mask, cy, cx):
            last_in = (cy, cx)
            continue
        break
    return last_in


def fit_circle(points_xy: np.ndarray) -> tuple[float, float, float]:
    # x^2 + y^2 + A x + B y + C = 0
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


def main() -> int:
    args = parse_args()

    mask_path = Path(args.mask)
    centerline_path = Path(args.centerline_json)

    mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
    if mask is None:
        raise RuntimeError(f"Failed to read mask: {mask_path}")

    data = json.loads(centerline_path.read_text(encoding="utf-8"))
    path_nodes = data.get("path_nodes_yx") or []
    if not path_nodes:
        raise RuntimeError("Centerline JSON missing path_nodes_yx")

    mpp = float((data.get("summary") or {}).get("meters_per_pixel"))
    path = np.array(path_nodes, dtype=np.float64)

    cum = cumulative_lengths(path)
    bend_idx = strongest_bend_index(path)
    bend_s = float(cum[bend_idx])
    window_px = float(args.window_m) / max(1e-9, mpp)

    idxs = np.where(np.abs(cum - bend_s) <= window_px)[0]
    left_pts: list[tuple[float, float]] = []
    right_pts: list[tuple[float, float]] = []

    for i in idxs:
        i0 = max(0, int(i) - 1)
        i1 = min(len(path) - 1, int(i) + 1)
        t = path[i1] - path[i0]
        tn = float(np.linalg.norm(t))
        if tn < 1e-6:
            continue

        # yx tangent -> yx normal
        ny = -t[1] / tn
        nx = t[0] / tn

        py = float(path[i, 0])
        px = float(path[i, 1])

        p_left = march_to_boundary(mask, py, px, ny, nx, step=float(args.ray_step_px))
        p_right = march_to_boundary(mask, py, px, -ny, -nx, step=float(args.ray_step_px))

        if p_left is not None:
            left_pts.append((p_left[1], p_left[0]))
        if p_right is not None:
            right_pts.append((p_right[1], p_right[0]))

    if len(left_pts) < 20 or len(right_pts) < 20:
        raise RuntimeError("Insufficient boundary samples; try a larger --window-m")

    left_arr = np.array(left_pts, dtype=np.float64)
    right_arr = np.array(right_pts, dtype=np.float64)

    lcx, lcy, lr = fit_circle(left_arr)
    rcx, rcy, rr = fit_circle(right_arr)

    if lr <= rr:
        inner = {"cx_px": lcx, "cy_px": lcy, "r_px": lr}
        outer = {"cx_px": rcx, "cy_px": rcy, "r_px": rr}
    else:
        inner = {"cx_px": rcx, "cy_px": rcy, "r_px": rr}
        outer = {"cx_px": lcx, "cy_px": lcy, "r_px": lr}

    result = {
        "summary": {
            "mask": str(mask_path),
            "centerline_json": str(centerline_path),
            "meters_per_pixel": mpp,
            "bend_index": int(bend_idx),
            "window_m": float(args.window_m),
            "sample_count_left": int(len(left_pts)),
            "sample_count_right": int(len(right_pts)),
        },
        "inner": {
            **inner,
            "diameter_px": float(2.0 * inner["r_px"]),
            "diameter_m": float(2.0 * inner["r_px"] * mpp),
        },
        "outer": {
            **outer,
            "diameter_px": float(2.0 * outer["r_px"]),
            "diameter_m": float(2.0 * outer["r_px"] * mpp),
        },
    }

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(result, indent=2), encoding="utf-8")

    vis = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
    vis[mask > 0] = (60, 235, 60)

    for i in range(1, len(path)):
        x0, y0 = int(round(path[i - 1, 1])), int(round(path[i - 1, 0]))
        x1, y1 = int(round(path[i, 1])), int(round(path[i, 0]))
        cv2.line(vis, (x0, y0), (x1, y1), (0, 191, 255), 1, lineType=cv2.LINE_AA)

    for x, y in left_pts:
        cv2.circle(vis, (int(round(x)), int(round(y))), 1, (255, 0, 0), -1)
    for x, y in right_pts:
        cv2.circle(vis, (int(round(x)), int(round(y))), 1, (0, 0, 255), -1)

    cv2.circle(vis, (int(round(inner["cx_px"])), int(round(inner["cy_px"]))), int(round(inner["r_px"])), (255, 255, 0), 1)
    cv2.circle(vis, (int(round(outer["cx_px"])), int(round(outer["cy_px"]))), int(round(outer["r_px"])), (255, 0, 255), 1)

    out_overlay = Path(args.out_overlay)
    out_overlay.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_overlay), vis)

    print(f"Wrote diameters JSON: {out_json}")
    print(f"Wrote QA overlay: {out_overlay}")
    print(f"Inner diameter: {result['inner']['diameter_m']:.3f} m")
    print(f"Outer diameter: {result['outer']['diameter_m']:.3f} m")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

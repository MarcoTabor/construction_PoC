#!/usr/bin/env python3
"""Extract inner line using a distance-transform isoline and contour arc selection.

This is a no-seed, no-normal-cast pipeline:
1) Build inside distance map from mask.
2) Extract isoline contour(s) at a chosen inward offset (px).
3) Select arc between start/end anchors and keep the inner-side arc.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import cv2
import numpy as np
from skimage import measure


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Extract inner or outer line from mask using isoline contours")
    p.add_argument("--mask", required=True, help="Binary mask PNG")
    p.add_argument("--centerline-json", required=True, help="JSON with path_nodes_yx and meters_per_pixel")
    p.add_argument("--side", choices=["inner", "outer"], default="inner", help="Which side arc to extract")
    p.add_argument("--offset-px", type=float, default=2.0, help="Distance-transform isoline offset in pixels")
    p.add_argument("--endpoint-lookahead", type=int, default=24, help="Nodes used to estimate endpoint tangents")
    p.add_argument("--lock-endpoint-y", action=argparse.BooleanOptionalAction, default=True, help="Shift result so mean endpoint Y matches centerline endpoints")
    p.add_argument("--out-json", default="outputs/joal502/joal_inner_line_isoline.json")
    p.add_argument("--out-overlay", default="outputs/joal502/visualizations/joal/joal_inner_line_isoline_overlay.png")
    return p.parse_args()


def cumulative_lengths(path_yx: np.ndarray) -> np.ndarray:
    out = np.zeros((len(path_yx),), dtype=np.float64)
    for i in range(1, len(path_yx)):
        dy = float(path_yx[i, 0] - path_yx[i - 1, 0])
        dx = float(path_yx[i, 1] - path_yx[i - 1, 1])
        out[i] = out[i - 1] + math.hypot(dx, dy)
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


def estimate_bend_center(path_yx: np.ndarray) -> np.ndarray:
    bend_idx = strongest_bend_index(path_yx)
    i0 = max(0, bend_idx - 80)
    i1 = min(len(path_yx), bend_idx + 81)
    pts = path_yx[i0:i1]
    if len(pts) < 10:
        return np.mean(path_yx, axis=0)
    xy = np.stack([pts[:, 1], pts[:, 0]], axis=1)
    cx, cy, r = fit_circle(xy)
    if not np.isfinite(cx) or not np.isfinite(cy) or r <= 1e-6:
        return np.mean(path_yx, axis=0)
    return np.array([cy, cx], dtype=np.float64)


def unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    if n < 1e-9:
        return np.array([0.0, 0.0], dtype=np.float64)
    return v / n


def endpoint_side_target(path_yx: np.ndarray, bend_center_yx: np.ndarray, at_start: bool, lookahead: int, side: str) -> np.ndarray:
    n = len(path_yx)
    k = max(1, min(int(lookahead), n - 1))
    if at_start:
        p = path_yx[0]
        t = path_yx[k] - path_yx[0]
    else:
        p = path_yx[-1]
        t = path_yx[-1] - path_yx[-1 - k]

    n1 = np.array([-t[1], t[0]], dtype=np.float64)
    n2 = -n1
    to_center = bend_center_yx - p
    u1 = unit(n1)
    u2 = unit(n2)
    inner_u = u1 if float(np.dot(u1, to_center)) >= float(np.dot(u2, to_center)) else u2
    side_u = inner_u if str(side) == "inner" else -inner_u
    return p + side_u * 200.0


def nearest_index(points_yx: np.ndarray, target_yx: np.ndarray) -> int:
    d2 = np.sum((points_yx - target_yx) ** 2, axis=1)
    return int(np.argmin(d2))


def arc_between(loop_yx: np.ndarray, i0: int, i1: int) -> tuple[np.ndarray, np.ndarray]:
    if i0 <= i1:
        a = loop_yx[i0 : i1 + 1]
        b = np.vstack([loop_yx[i1:], loop_yx[: i0 + 1]])
    else:
        a = np.vstack([loop_yx[i0:], loop_yx[: i1 + 1]])
        b = loop_yx[i1 : i0 + 1]
    return a, b


def choose_side_arc(arc_a: np.ndarray, arc_b: np.ndarray, bend_center_yx: np.ndarray, side: str) -> np.ndarray:
    ra = np.linalg.norm(arc_a - bend_center_yx, axis=1)
    rb = np.linalg.norm(arc_b - bend_center_yx, axis=1)
    if str(side) == "inner":
        return arc_a if float(np.mean(ra)) <= float(np.mean(rb)) else arc_b
    return arc_a if float(np.mean(ra)) >= float(np.mean(rb)) else arc_b


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

    bin_mask = (mask > 0).astype(np.uint8)
    dist = cv2.distanceTransform(bin_mask * 255, cv2.DIST_L2, 5)

    contours = measure.find_contours(dist, level=float(args.offset_px))
    if not contours:
        raise RuntimeError("No isoline contour found at the requested offset")

    # Keep the longest contour loop as the primary offset ring.
    loop = max(contours, key=lambda c: len(c))
    loop_yx = np.array(loop, dtype=np.float64)

    bend_center = estimate_bend_center(centerline)
    start_tgt = endpoint_side_target(
        centerline,
        bend_center,
        at_start=True,
        lookahead=int(args.endpoint_lookahead),
        side=str(args.side),
    )
    end_tgt = endpoint_side_target(
        centerline,
        bend_center,
        at_start=False,
        lookahead=int(args.endpoint_lookahead),
        side=str(args.side),
    )

    i0 = nearest_index(loop_yx, start_tgt)
    i1 = nearest_index(loop_yx, end_tgt)

    arc_a, arc_b = arc_between(loop_yx, i0, i1)
    side_line = choose_side_arc(arc_a, arc_b, bend_center, side=str(args.side))

    # Ensure direction is start -> end relative to centerline endpoints.
    d_forward = float(np.linalg.norm(side_line[0] - centerline[0]) + np.linalg.norm(side_line[-1] - centerline[-1]))
    d_reverse = float(np.linalg.norm(side_line[-1] - centerline[0]) + np.linalg.norm(side_line[0] - centerline[-1]))
    if d_reverse < d_forward:
        side_line = side_line[::-1].copy()

    if bool(args.lock_endpoint_y) and len(side_line) >= 2:
        target_y = 0.5 * float(centerline[0, 0] + centerline[-1, 0])
        current_y = 0.5 * float(side_line[0, 0] + side_line[-1, 0])
        dy = target_y - current_y
        side_line[:, 0] = side_line[:, 0] + dy

    center_len_m = float(cumulative_lengths(centerline)[-1] * mpp)
    side_len_m = float(cumulative_lengths(side_line)[-1] * mpp)

    out = {
        "summary": {
            "mask": str(args.mask),
            "centerline_json": str(args.centerline_json),
            "meters_per_pixel": mpp,
            "method": "distance_isoline_arc_selection",
            "side": str(args.side),
            "offset_px": float(args.offset_px),
            "lock_endpoint_y": bool(args.lock_endpoint_y),
            "centerline_length_m": center_len_m,
            f"{args.side}_line_length_m": side_len_m,
            "contour_point_count": int(len(loop_yx)),
            f"{args.side}_point_count": int(len(side_line)),
        },
        "centerline_yx": centerline.tolist(),
        f"{args.side}_line_yx": side_line.tolist(),
        "line_yx": side_line.tolist(),
    }

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(out, indent=2), encoding="utf-8")

    vis = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
    vis[mask > 0] = (60, 235, 60)

    def draw_line(arr: np.ndarray, color: tuple[int, int, int], thickness: int) -> None:
        for i in range(1, len(arr)):
            x0, y0 = int(round(arr[i - 1, 1])), int(round(arr[i - 1, 0]))
            x1, y1 = int(round(arr[i, 1])), int(round(arr[i, 0]))
            cv2.line(vis, (x0, y0), (x1, y1), color, thickness, lineType=cv2.LINE_AA)

    draw_line(centerline, (0, 191, 255), 2)
    side_color = (255, 0, 255) if str(args.side) == "inner" else (255, 255, 0)
    draw_line(side_line, side_color, 2)

    cv2.circle(vis, (int(round(centerline[0, 1])), int(round(centerline[0, 0]))), 4, (0, 0, 255), -1)
    cv2.circle(vis, (int(round(centerline[-1, 1])), int(round(centerline[-1, 0]))), 4, (255, 255, 255), -1)

    out_overlay = Path(args.out_overlay)
    out_overlay.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_overlay), vis)

    print(f"Wrote {args.side} line JSON: {out_json}")
    print(f"Wrote overlay: {out_overlay}")
    print(f"Centerline length: {center_len_m:.3f} m")
    print(f"{args.side.capitalize()} line length: {side_len_m:.3f} m")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Optimize a single globally scaled centerline against a mask boundary.

This script treats the input centerline as one polyline object and applies a
single global scale factor around one origin. It does not create seeds or cast
normals. The objective is to find the scale factor that best overlays the mask
boundary while keeping the transformed line inside the mask.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import cv2
import numpy as np


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Optimize a globally scaled centerline against a mask boundary")
    parser.add_argument("--mask", required=True, help="Binary mask PNG")
    parser.add_argument("--centerline-json", required=True, help="JSON containing path_nodes_yx and meters_per_pixel")
    parser.add_argument("--origin-mode", choices=["bend-circle", "centroid"], default="bend-circle", help="Origin used for global scaling")
    parser.add_argument("--scale-min", type=float, default=0.85, help="Lower bound for global scale search")
    parser.add_argument("--scale-max", type=float, default=1.00, help="Upper bound for global scale search")
    parser.add_argument("--translate-y-min", type=float, default=-80.0, help="Lower bound for global Y translation search in pixels")
    parser.add_argument("--translate-y-max", type=float, default=80.0, help="Upper bound for global Y translation search in pixels")
    parser.add_argument("--translate-x-min", type=float, default=-80.0, help="Lower bound for global X translation search in pixels")
    parser.add_argument("--translate-x-max", type=float, default=80.0, help="Upper bound for global X translation search in pixels")
    parser.add_argument("--lock-endpoint-y", action=argparse.BooleanOptionalAction, default=True, help="Keep transformed start/end at the original endpoint Y level")
    parser.add_argument("--coarse-samples", type=int, default=61, help="Coarse grid samples for the first search pass")
    parser.add_argument("--translate-samples", type=int, default=17, help="Translation samples per axis for each search pass")
    parser.add_argument("--refine-rounds", type=int, default=4, help="Number of local refinement rounds")
    parser.add_argument("--refine-samples", type=int, default=31, help="Samples per local refinement round")
    parser.add_argument("--outside-penalty", type=float, default=1000.0, help="Penalty multiplier for transformed points that leave the mask")
    parser.add_argument("--edge-mean-weight", type=float, default=0.25, help="Weight of mean boundary distance in addition to median")
    parser.add_argument("--endpoint-y-penalty", type=float, default=500.0, help="Penalty multiplier for endpoint Y mismatch in pixels")
    parser.add_argument("--objective-sample-step", type=int, default=8, help="Use every Nth centerline point for objective evaluation")
    parser.add_argument("--out-json", default="outputs/joal502/joal_scaled_inner_line.json")
    parser.add_argument("--out-overlay", default="outputs/joal502/visualizations/joal/joal_scaled_inner_line_overlay.png")
    return parser.parse_args()


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


def estimate_origin(path_yx: np.ndarray, mode: str) -> np.ndarray:
    if mode == "centroid":
        return np.mean(path_yx, axis=0)

    bend_idx = strongest_bend_index(path_yx)
    i0 = max(0, bend_idx - 80)
    i1 = min(len(path_yx), bend_idx + 81)
    points = path_yx[i0:i1]
    if len(points) < 10:
        return np.mean(path_yx, axis=0)

    xy = np.stack([points[:, 1], points[:, 0]], axis=1)
    cx, cy, radius = fit_circle(xy)
    if not np.isfinite(cx) or not np.isfinite(cy) or radius <= 1e-6:
        return np.mean(path_yx, axis=0)
    return np.array([cy, cx], dtype=np.float64)


def boundary_distance_map(mask: np.ndarray) -> np.ndarray:
    kernel = np.ones((3, 3), dtype=np.uint8)
    edge = cv2.morphologyEx((mask > 0).astype(np.uint8) * 255, cv2.MORPH_GRADIENT, kernel)
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


def inside_mask(mask: np.ndarray, y: float, x: float) -> bool:
    h, w = mask.shape
    yi = int(round(y))
    xi = int(round(x))
    if yi < 0 or yi >= h or xi < 0 or xi >= w:
        return False
    return bool(mask[yi, xi] > 0)


def transform_polyline(
    path_yx: np.ndarray,
    origin_yx: np.ndarray,
    scale: float,
    translate_yx: np.ndarray,
) -> np.ndarray:
    return origin_yx + float(scale) * (path_yx - origin_yx) + translate_yx


def locked_translate_y_for_endpoints(path_yx: np.ndarray, origin_yx: np.ndarray, scale: float) -> float:
    """Choose the global Y translation that preserves the average endpoint Y exactly."""
    endpoints = path_yx[[0, -1]]
    transformed = transform_polyline(
        endpoints,
        origin_yx,
        scale,
        np.array([0.0, 0.0], dtype=np.float64),
    )
    target_y = 0.5 * float(endpoints[0, 0] + endpoints[1, 0])
    current_y = 0.5 * float(transformed[0, 0] + transformed[1, 0])
    return target_y - current_y


def sample_polyline_for_objective(path_yx: np.ndarray, sample_step: int) -> np.ndarray:
    step = max(1, int(sample_step))
    sampled = path_yx[::step]
    if len(sampled) == 0 or np.linalg.norm(sampled[-1] - path_yx[-1]) > 1e-9:
        sampled = np.vstack([sampled, path_yx[-1]]) if len(sampled) else path_yx[[-1]]
    return sampled


def evaluate_scale(
    path_yx: np.ndarray,
    objective_path_yx: np.ndarray,
    origin_yx: np.ndarray,
    scale: float,
    translate_yx: np.ndarray,
    mask: np.ndarray,
    dist_to_edge: np.ndarray,
    outside_penalty: float,
    edge_mean_weight: float,
    endpoint_y_penalty: float,
) -> tuple[float, dict[str, float], np.ndarray]:
    scaled = transform_polyline(path_yx, origin_yx, scale, translate_yx)
    scaled_objective = transform_polyline(objective_path_yx, origin_yx, scale, translate_yx)
    edge_dists = np.array([bilinear_sample(dist_to_edge, float(y), float(x)) for y, x in scaled_objective], dtype=np.float64)
    inside = np.array([inside_mask(mask, float(y), float(x)) for y, x in scaled_objective], dtype=bool)
    outside_count = int((~inside).sum())
    outside_ratio = float(outside_count) / float(max(1, len(scaled)))

    if inside.any():
        valid_edge = edge_dists[inside]
        edge_median = float(np.median(valid_edge))
        edge_mean = float(np.mean(valid_edge))
    else:
        edge_median = 1e6
        edge_mean = 1e6

    endpoint_y_error_start = abs(float(scaled[0, 0] - path_yx[0, 0]))
    endpoint_y_error_end = abs(float(scaled[-1, 0] - path_yx[-1, 0]))
    endpoint_y_error_max = max(endpoint_y_error_start, endpoint_y_error_end)

    objective = (
        edge_median
        + float(edge_mean_weight) * edge_mean
        + float(outside_penalty) * outside_ratio
        + float(endpoint_y_penalty) * endpoint_y_error_max
    )
    metrics = {
        "edge_median_px": edge_median,
        "edge_mean_px": edge_mean,
        "outside_ratio": outside_ratio,
        "outside_count": float(outside_count),
        "endpoint_y_error_start_px": float(endpoint_y_error_start),
        "endpoint_y_error_end_px": float(endpoint_y_error_end),
        "endpoint_y_error_max_px": float(endpoint_y_error_max),
        "objective": float(objective),
    }
    return float(objective), metrics, scaled


def search_best_scale(
    path_yx: np.ndarray,
    objective_path_yx: np.ndarray,
    origin_yx: np.ndarray,
    mask: np.ndarray,
    dist_to_edge: np.ndarray,
    scale_min: float,
    scale_max: float,
    translate_y_min: float,
    translate_y_max: float,
    translate_x_min: float,
    translate_x_max: float,
    coarse_samples: int,
    translate_samples: int,
    refine_rounds: int,
    refine_samples: int,
    outside_penalty: float,
    edge_mean_weight: float,
    endpoint_y_penalty: float,
    lock_endpoint_y: bool,
) -> tuple[float, np.ndarray, dict[str, float], np.ndarray]:
    lo = float(scale_min)
    hi = float(scale_max)
    ty_lo = float(translate_y_min)
    ty_hi = float(translate_y_max)
    tx_lo = float(translate_x_min)
    tx_hi = float(translate_x_max)
    best_scale = hi
    best_translate = np.array([0.0, 0.0], dtype=np.float64)
    best_metrics: dict[str, float] = {}
    best_path = path_yx.copy()

    for round_idx in range(max(1, int(refine_rounds) + 1)):
        scale_samples = max(3, int(coarse_samples) if round_idx == 0 else int(refine_samples))
        move_samples = max(3, int(translate_samples) if round_idx == 0 else int(refine_samples))
        scale_grid = np.linspace(lo, hi, scale_samples)
        ty_grid = np.linspace(ty_lo, ty_hi, move_samples)
        tx_grid = np.linspace(tx_lo, tx_hi, move_samples)
        round_best_obj = 1e18
        round_best_scale = scale_grid[0]
        round_best_translate = np.array([0.0, 0.0], dtype=np.float64)
        round_best_metrics: dict[str, float] = {}
        round_best_path = path_yx.copy()

        for scale in scale_grid:
            if bool(lock_endpoint_y):
                ty_values = [locked_translate_y_for_endpoints(path_yx, origin_yx, float(scale))]
            else:
                ty_values = [float(v) for v in ty_grid]

            for ty in ty_values:
                for tx in tx_grid:
                    translate = np.array([float(ty), float(tx)], dtype=np.float64)
                    obj, metrics, scaled = evaluate_scale(
                        path_yx,
                        objective_path_yx,
                        origin_yx,
                        float(scale),
                        translate,
                        mask,
                        dist_to_edge,
                        outside_penalty,
                        edge_mean_weight,
                        endpoint_y_penalty,
                    )
                    if obj < round_best_obj:
                        round_best_obj = obj
                        round_best_scale = float(scale)
                        round_best_translate = translate
                        round_best_metrics = metrics
                        round_best_path = scaled

        best_scale = round_best_scale
        best_translate = round_best_translate
        best_metrics = round_best_metrics
        best_path = round_best_path

        if round_idx >= int(refine_rounds):
            break

        scale_step = (hi - lo) / float(max(1, scale_samples - 1))
        ty_step = (ty_hi - ty_lo) / float(max(1, move_samples - 1))
        tx_step = (tx_hi - tx_lo) / float(max(1, move_samples - 1))
        lo = max(float(scale_min), best_scale - 2.0 * scale_step)
        hi = min(float(scale_max), best_scale + 2.0 * scale_step)
        if not bool(lock_endpoint_y):
            ty_lo = max(float(translate_y_min), float(best_translate[0]) - 2.0 * ty_step)
            ty_hi = min(float(translate_y_max), float(best_translate[0]) + 2.0 * ty_step)
        tx_lo = max(float(translate_x_min), float(best_translate[1]) - 2.0 * tx_step)
        tx_hi = min(float(translate_x_max), float(best_translate[1]) + 2.0 * tx_step)

    return best_scale, best_translate, best_metrics, best_path


def draw_polyline(vis: np.ndarray, path_yx: np.ndarray, color: tuple[int, int, int], thickness: int) -> None:
    for i in range(1, len(path_yx)):
        x0 = int(round(float(path_yx[i - 1, 1])))
        y0 = int(round(float(path_yx[i - 1, 0])))
        x1 = int(round(float(path_yx[i, 1])))
        y1 = int(round(float(path_yx[i, 0])))
        cv2.line(vis, (x0, y0), (x1, y1), color, thickness, lineType=cv2.LINE_AA)


def main() -> int:
    args = parse_args()

    mask = cv2.imread(str(Path(args.mask)), cv2.IMREAD_GRAYSCALE)
    if mask is None:
        raise RuntimeError(f"Failed to read mask: {args.mask}")

    data = json.loads(Path(args.centerline_json).read_text(encoding="utf-8"))
    path_nodes = data.get("path_nodes_yx") or []
    if not path_nodes:
        raise RuntimeError("Missing path_nodes_yx in centerline JSON")

    path_yx = np.array(path_nodes, dtype=np.float64)
    objective_path_yx = sample_polyline_for_objective(path_yx, sample_step=int(args.objective_sample_step))
    summary = data.get("summary") or {}
    mpp = float(summary.get("meters_per_pixel"))

    origin_yx = estimate_origin(path_yx, mode=str(args.origin_mode))
    dist_to_edge = boundary_distance_map(mask)
    best_scale, best_translate, best_metrics, best_path = search_best_scale(
        path_yx,
        objective_path_yx,
        origin_yx,
        mask,
        dist_to_edge,
        scale_min=float(args.scale_min),
        scale_max=float(args.scale_max),
        translate_y_min=float(args.translate_y_min),
        translate_y_max=float(args.translate_y_max),
        translate_x_min=float(args.translate_x_min),
        translate_x_max=float(args.translate_x_max),
        coarse_samples=int(args.coarse_samples),
        translate_samples=int(args.translate_samples),
        refine_rounds=int(args.refine_rounds),
        refine_samples=int(args.refine_samples),
        outside_penalty=float(args.outside_penalty),
        edge_mean_weight=float(args.edge_mean_weight),
        endpoint_y_penalty=float(args.endpoint_y_penalty),
        lock_endpoint_y=bool(args.lock_endpoint_y),
    )

    original_length_m = float(cumulative_lengths(path_yx)[-1] * mpp)
    scaled_length_m = float(cumulative_lengths(best_path)[-1] * mpp)

    out = {
        "summary": {
            "mask": str(args.mask),
            "centerline_json": str(args.centerline_json),
            "meters_per_pixel": mpp,
            "origin_mode": str(args.origin_mode),
            "origin_yx": [float(origin_yx[0]), float(origin_yx[1])],
            "best_scale": float(best_scale),
            "best_translate_yx": [float(best_translate[0]), float(best_translate[1])],
            "lock_endpoint_y": bool(args.lock_endpoint_y),
            "objective_sample_step": int(args.objective_sample_step),
            "objective_sample_count": int(len(objective_path_yx)),
            "original_centerline_length_m": original_length_m,
            "scaled_inner_line_length_m": scaled_length_m,
            **best_metrics,
        },
        "original_centerline_yx": path_yx.tolist(),
        "scaled_inner_line_yx": best_path.tolist(),
    }

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(out, indent=2), encoding="utf-8")

    vis = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
    vis[mask > 0] = (60, 235, 60)
    draw_polyline(vis, path_yx, (0, 191, 255), 2)
    draw_polyline(vis, best_path, (255, 0, 255), 2)
    cv2.circle(vis, (int(round(float(origin_yx[1]))), int(round(float(origin_yx[0])))), 4, (255, 255, 255), -1)

    out_overlay = Path(args.out_overlay)
    out_overlay.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_overlay), vis)

    print(f"Wrote scaled line JSON: {out_json}")
    print(f"Wrote overlay: {out_overlay}")
    print(f"Best scale: {best_scale:.6f}")
    print(f"Best translate yx: ({best_translate[0]:.3f}, {best_translate[1]:.3f})")
    print(f"Origin mode: {args.origin_mode}")
    print(f"Origin yx: ({origin_yx[0]:.3f}, {origin_yx[1]:.3f})")
    print(f"Original centerline length: {original_length_m:.3f} m")
    print(f"Scaled inner line length: {scaled_length_m:.3f} m")
    print(f"Edge median distance: {best_metrics['edge_median_px']:.3f} px")
    print(f"Outside ratio: {best_metrics['outside_ratio']:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
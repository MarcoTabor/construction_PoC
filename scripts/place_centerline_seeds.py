#!/usr/bin/env python3
"""Place equidistant seeds on the footpath centerline.

This script uses the binary footpath mask, extracts a skeleton, finds the
longest geodesic path (diameter-like centerline) across skeleton components,
and samples seeds at equal spacing along that centerline.
"""

from __future__ import annotations

import argparse
import heapq
import json
import math
from pathlib import Path

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Place equidistant centerline seeds from footpath mask")
    p.add_argument("--mask", default="outputs/joal502/visualizations/footpath_vector_mask.png", help="Binary footpath mask PNG")
    p.add_argument("--metrics-json", default="outputs/joal502/footpath_vector_metrics.json", help="Metrics JSON (for meters_per_pixel)")
    p.add_argument("--page-image", default="outputs/joal502/visualizations/footpath_cutout_white.png", help="Background image for visualization")
    p.add_argument("--out-json", default="outputs/joal502/centerline_seeds.json", help="Output seeds JSON")
    p.add_argument("--out-overlay", default="outputs/joal502/visualizations/centerline_seeds_overlay.png", help="Overlay PNG output")
    p.add_argument("--out-centerline", default="outputs/joal502/visualizations/centerline_path.png", help="Centerline-only PNG")
    p.add_argument("--seed-spacing-m", type=float, default=5.0, help="Seed spacing in meters")
    p.add_argument("--start-offset-m", type=float, default=0.0, help="Offset from path start before first seed")
    p.add_argument("--close-kernel", type=int, default=3, help="Morph close kernel size for mask cleanup")
    p.add_argument("--min-component-pixels", type=int, default=120, help="Ignore tiny skeleton components")
    return p.parse_args()


def ensure_cv2() -> None:
    if cv2 is None:
        raise RuntimeError("opencv-python-headless is required. Install from requirements.txt")


def skeletonize(binary_mask: np.ndarray) -> np.ndarray:
    img = binary_mask.copy()
    skel = np.zeros_like(img)
    element = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))

    while True:
        eroded = cv2.erode(img, element)
        temp = cv2.dilate(eroded, element)
        temp = cv2.subtract(img, temp)
        skel = cv2.bitwise_or(skel, temp)
        img = eroded
        if cv2.countNonZero(img) == 0:
            break

    return skel


def component_nodes(comp_mask: np.ndarray) -> tuple[np.ndarray, dict[tuple[int, int], int], list[list[tuple[int, float]]]]:
    coords = np.argwhere(comp_mask)
    pos_to_idx: dict[tuple[int, int], int] = {}
    for i, (y, x) in enumerate(coords):
        pos_to_idx[(int(y), int(x))] = i

    nbrs: list[list[tuple[int, float]]] = [[] for _ in range(len(coords))]
    for i, (y, x) in enumerate(coords):
        yi = int(y)
        xi = int(x)
        for dy, dx in [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]:
            j = pos_to_idx.get((yi + dy, xi + dx))
            if j is None:
                continue
            w = math.sqrt(2.0) if (dy != 0 and dx != 0) else 1.0
            nbrs[i].append((j, w))

    return coords, pos_to_idx, nbrs


def dijkstra(start: int, nbrs: list[list[tuple[int, float]]]) -> tuple[list[float], list[int]]:
    n = len(nbrs)
    dist = [math.inf] * n
    prev = [-1] * n
    dist[start] = 0.0
    heap: list[tuple[float, int]] = [(0.0, start)]

    while heap:
        cur_d, u = heapq.heappop(heap)
        if cur_d > dist[u]:
            continue
        for v, w in nbrs[u]:
            nd = cur_d + w
            if nd < dist[v]:
                dist[v] = nd
                prev[v] = u
                heapq.heappush(heap, (nd, v))

    return dist, prev


def best_component_path(skel: np.ndarray, min_component_pixels: int) -> tuple[np.ndarray, float]:
    n_labels, labels = cv2.connectedComponents((skel > 0).astype(np.uint8), connectivity=8)
    best_path = np.empty((0, 2), dtype=np.int32)
    best_length = 0.0

    for label in range(1, n_labels):
        comp = labels == label
        size = int(np.count_nonzero(comp))
        if size < min_component_pixels:
            continue

        coords, _, nbrs = component_nodes(comp)
        if len(coords) < 2:
            continue

        degrees = [len(n) for n in nbrs]
        endpoints = [i for i, d in enumerate(degrees) if d == 1]
        candidate_nodes = endpoints if len(endpoints) >= 2 else list(range(len(coords)))

        start = candidate_nodes[0]
        d1, _ = dijkstra(start, nbrs)
        a = max(candidate_nodes, key=lambda i: d1[i] if math.isfinite(d1[i]) else -1.0)

        d2, prev2 = dijkstra(a, nbrs)
        b = max(candidate_nodes, key=lambda i: d2[i] if math.isfinite(d2[i]) else -1.0)
        length = float(d2[b]) if math.isfinite(d2[b]) else 0.0

        if length <= best_length:
            continue

        # Reconstruct path from b to a.
        path_nodes: list[list[int]] = []
        cur = b
        while cur != -1:
            y, x = coords[cur]
            path_nodes.append([int(y), int(x)])
            if cur == a:
                break
            cur = prev2[cur]

        path_nodes.reverse()
        if len(path_nodes) >= 2:
            best_path = np.array(path_nodes, dtype=np.int32)
            best_length = length

    return best_path, best_length


def cumulative_lengths(path_yx: np.ndarray) -> np.ndarray:
    if len(path_yx) == 0:
        return np.zeros((0,), dtype=np.float64)
    out = np.zeros((len(path_yx),), dtype=np.float64)
    for i in range(1, len(path_yx)):
        dy = float(path_yx[i, 0] - path_yx[i - 1, 0])
        dx = float(path_yx[i, 1] - path_yx[i - 1, 1])
        out[i] = out[i - 1] + math.hypot(dx, dy)
    return out


def sample_seeds(path_yx: np.ndarray, spacing_px: float, start_offset_px: float) -> list[dict[str, float]]:
    if len(path_yx) < 2:
        return []

    cum = cumulative_lengths(path_yx)
    total = float(cum[-1])
    if total <= 0 or spacing_px <= 0:
        return []

    seeds: list[dict[str, float]] = []
    target = max(0.0, start_offset_px)

    while target <= total + 1e-6:
        idx = int(np.searchsorted(cum, target, side="left"))
        idx = min(max(1, idx), len(path_yx) - 1)

        d0 = float(cum[idx - 1])
        d1 = float(cum[idx])
        seg = max(1e-9, d1 - d0)
        t = (target - d0) / seg

        y = float(path_yx[idx - 1, 0] * (1.0 - t) + path_yx[idx, 0] * t)
        x = float(path_yx[idx - 1, 1] * (1.0 - t) + path_yx[idx, 1] * t)
        seeds.append({"x_px": x, "y_px": y, "distance_along_px": target})

        target += spacing_px

    return seeds


def main() -> int:
    ensure_cv2()
    args = parse_args()

    mask_path = Path(args.mask)
    metrics_path = Path(args.metrics_json)
    page_path = Path(args.page_image)
    out_json = Path(args.out_json)
    out_overlay = Path(args.out_overlay)
    out_centerline = Path(args.out_centerline)

    if not mask_path.exists():
        raise FileNotFoundError(f"Missing mask: {mask_path}")
    if not metrics_path.exists():
        raise FileNotFoundError(f"Missing metrics JSON: {metrics_path}")
    if not page_path.exists():
        raise FileNotFoundError(f"Missing page image: {page_path}")

    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    meters_per_pixel = float(metrics["summary"]["meters_per_pixel"])

    mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
    if mask is None:
        raise RuntimeError(f"Failed to read mask image: {mask_path}")

    _, mask = cv2.threshold(mask, 127, 255, cv2.THRESH_BINARY)

    k = max(1, int(args.close_kernel))
    if k % 2 == 0:
        k += 1
    if k > 1:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    skel = skeletonize(mask)
    centerline_yx, centerline_len_px = best_component_path(skel, min_component_pixels=int(args.min_component_pixels))
    if len(centerline_yx) < 2:
        raise RuntimeError("Could not extract a valid centerline path from skeleton")

    spacing_px = float(args.seed_spacing_m) / meters_per_pixel
    start_offset_px = float(args.start_offset_m) / meters_per_pixel
    seeds = sample_seeds(centerline_yx, spacing_px=spacing_px, start_offset_px=start_offset_px)

    for i, s in enumerate(seeds, start=1):
        s["seed_id"] = i
        s["distance_along_m"] = float(s["distance_along_px"] * meters_per_pixel)
        s["x_m"] = float(s["x_px"] * meters_per_pixel)
        s["y_m"] = float(s["y_px"] * meters_per_pixel)

    payload = {
        "summary": {
            "mask": str(mask_path),
            "metrics_json": str(metrics_path),
            "seed_spacing_m": float(args.seed_spacing_m),
            "start_offset_m": float(args.start_offset_m),
            "meters_per_pixel": meters_per_pixel,
            "centerline_length_px": float(centerline_len_px),
            "centerline_length_m": float(centerline_len_px * meters_per_pixel),
            "seed_count": len(seeds),
        },
        "seeds": seeds,
    }

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    bg = cv2.imread(str(page_path), cv2.IMREAD_COLOR)
    if bg is None:
        raise RuntimeError(f"Failed to read page image: {page_path}")

    centerline_vis = bg.copy()
    overlay_vis = bg.copy()

    for i in range(1, len(centerline_yx)):
        y0, x0 = int(centerline_yx[i - 1, 0]), int(centerline_yx[i - 1, 1])
        y1, x1 = int(centerline_yx[i, 0]), int(centerline_yx[i, 1])
        cv2.line(centerline_vis, (x0, y0), (x1, y1), (0, 180, 255), 2, lineType=cv2.LINE_AA)
        cv2.line(overlay_vis, (x0, y0), (x1, y1), (0, 180, 255), 2, lineType=cv2.LINE_AA)

    for s in seeds:
        x = int(round(float(s["x_px"])))
        y = int(round(float(s["y_px"])))
        cv2.circle(overlay_vis, (x, y), 5, (0, 0, 255), -1)
        cv2.circle(overlay_vis, (x, y), 6, (255, 255, 255), 1)

    out_overlay.parent.mkdir(parents=True, exist_ok=True)
    out_centerline.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_overlay), overlay_vis)
    cv2.imwrite(str(out_centerline), centerline_vis)

    print(f"Wrote seeds JSON: {out_json}")
    print(f"Wrote seed overlay: {out_overlay}")
    print(f"Wrote centerline image: {out_centerline}")
    print(f"Centerline length: {centerline_len_px * meters_per_pixel:.3f} m")
    print(f"Seed count: {len(seeds)} (spacing={args.seed_spacing_m} m)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

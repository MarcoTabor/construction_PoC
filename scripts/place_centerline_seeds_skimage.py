#!/usr/bin/env python3
"""Place equidistant seeds using a scikit-image medial axis centerline.

Pipeline:
1. Load binary footpath mask, preferably from transparent cutout alpha.
2. Clean the mask lightly.
3. Compute medial axis and distance map with scikit-image.
4. Build a graph on medial-axis pixels.
5. Pick the longest endpoint-to-endpoint route on the dominant component.
6. Sample equidistant seeds along that route.
"""

from __future__ import annotations

import argparse
import heapq
import json
import math
from pathlib import Path

import cv2
import numpy as np
from skimage.morphology import medial_axis, remove_small_holes, remove_small_objects


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Place equidistant seeds from a scikit-image medial axis")
    parser.add_argument("--mask", default="outputs/joal502/visualizations/footpath_cutout_alpha_mask.png")
    parser.add_argument("--metrics-json", default="outputs/joal502/footpath_vector_metrics.json")
    parser.add_argument("--page-image", default="outputs/joal502/visualizations/footpath_cutout_transparent.png")
    parser.add_argument("--out-json", default="outputs/joal502/centerline_seeds_skimage.json")
    parser.add_argument("--out-overlay", default="outputs/joal502/visualizations/centerline_seeds_skimage_overlay.png")
    parser.add_argument("--out-centerline", default="outputs/joal502/visualizations/centerline_skimage_path.png")
    parser.add_argument("--seed-spacing-m", type=float, default=5.0)
    parser.add_argument("--start-offset-m", type=float, default=0.0)
    parser.add_argument("--close-kernel", type=int, default=5)
    parser.add_argument("--min-object-pixels", type=int, default=200)
    parser.add_argument("--min-hole-pixels", type=int, default=200)
    parser.add_argument("--spur-threshold-px", type=float, default=40.0, help="Ignore skeleton components whose longest path is shorter than this")
    parser.add_argument(
        "--prune-branch-max-len-px",
        type=float,
        default=0.0,
        help="Prune skeleton side branches up to this length in pixels before path extraction (0 disables)",
    )
    parser.add_argument(
        "--endpoint-strategy",
        choices=["longest", "vertical"],
        default="longest",
        help="How to choose path endpoints from skeleton endpoints",
    )
    parser.add_argument(
        "--path-mode",
        choices=["skeleton", "row-center"],
        default="skeleton",
        help="Centerline extraction mode: skeleton graph or row-wise mask centerline",
    )
    parser.add_argument(
        "--trim-end-hook-px",
        type=float,
        default=28.0,
        help="Trim short endpoint hooks up to this arclength from each end (0 disables)",
    )
    parser.add_argument(
        "--trim-end-hook-angle-deg",
        type=float,
        default=55.0,
        help="Minimum local angle change to classify an endpoint hook",
    )
    parser.add_argument(
        "--trim-end-cap-factor",
        type=float,
        default=1.0,
        help="Trim each path end by factor * local endpoint half-width from distance map (0 disables)",
    )
    parser.add_argument(
        "--trim-start-cap-factor",
        type=float,
        default=-1.0,
        help="Trim start endpoint by factor * local half-width; negative uses trim-end-cap-factor",
    )
    parser.add_argument(
        "--trim-final-cap-factor",
        type=float,
        default=-1.0,
        help="Trim final endpoint by factor * local half-width; negative uses trim-end-cap-factor",
    )
    return parser.parse_args()


def dijkstra(start: int, neighbors: list[list[tuple[int, float]]]) -> tuple[list[float], list[int]]:
    n = len(neighbors)
    dist = [math.inf] * n
    prev = [-1] * n
    dist[start] = 0.0
    heap: list[tuple[float, int]] = [(0.0, start)]

    while heap:
        current_dist, node = heapq.heappop(heap)
        if current_dist > dist[node]:
            continue
        for nxt, weight in neighbors[node]:
            candidate = current_dist + weight
            if candidate < dist[nxt]:
                dist[nxt] = candidate
                prev[nxt] = node
                heapq.heappush(heap, (candidate, nxt))
    return dist, prev


def build_graph(component_mask: np.ndarray) -> tuple[np.ndarray, list[list[tuple[int, float]]]]:
    coords = np.argwhere(component_mask)
    pos_to_idx = {(int(y), int(x)): i for i, (y, x) in enumerate(coords)}
    neighbors: list[list[tuple[int, float]]] = [[] for _ in range(len(coords))]

    offsets = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
    for i, (y, x) in enumerate(coords):
        yi = int(y)
        xi = int(x)
        for dy, dx in offsets:
            j = pos_to_idx.get((yi + dy, xi + dx))
            if j is None:
                continue
            weight = math.sqrt(2.0) if dy != 0 and dx != 0 else 1.0
            neighbors[i].append((j, weight))
    return coords, neighbors


def pixel_neighbors(skel: np.ndarray, y: int, x: int) -> list[tuple[int, int]]:
    h, w = skel.shape
    out: list[tuple[int, int]] = []
    for dy, dx in [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]:
        yy = y + dy
        xx = x + dx
        if yy < 0 or yy >= h or xx < 0 or xx >= w:
            continue
        if skel[yy, xx]:
            out.append((yy, xx))
    return out


def prune_skeleton_branches(skel: np.ndarray, max_len_px: float) -> np.ndarray:
    if max_len_px <= 0:
        return skel.copy()

    work = skel.copy().astype(bool)

    while True:
        changed = False
        endpoints = np.argwhere(work)
        endpoint_list: list[tuple[int, int]] = []
        for y, x in endpoints:
            nbrs = pixel_neighbors(work, int(y), int(x))
            if len(nbrs) == 1:
                endpoint_list.append((int(y), int(x)))

        for start in endpoint_list:
            if not work[start[0], start[1]]:
                continue

            path: list[tuple[int, int]] = [start]
            prev: tuple[int, int] | None = None
            cur = start
            length = 0.0
            stop_degree = 0

            while True:
                nbrs = pixel_neighbors(work, cur[0], cur[1])
                stop_degree = len(nbrs)

                if prev is not None:
                    nbrs = [n for n in nbrs if n != prev]

                if not nbrs:
                    break
                if len(nbrs) > 1:
                    break

                nxt = nbrs[0]
                dy = abs(nxt[0] - cur[0])
                dx = abs(nxt[1] - cur[1])
                length += math.sqrt(2.0) if (dy == 1 and dx == 1) else 1.0

                prev = cur
                cur = nxt
                path.append(cur)

                if length > max_len_px:
                    break

                deg_cur = len(pixel_neighbors(work, cur[0], cur[1]))
                stop_degree = deg_cur
                if deg_cur != 2:
                    break

            if length <= max_len_px and stop_degree >= 3:
                for y, x in path[:-1]:
                    work[y, x] = False
                    changed = True

        if not changed:
            break

    return work


def extract_main_path(skel: np.ndarray, spur_threshold_px: float) -> tuple[np.ndarray, float, np.ndarray]:
    n_labels, labels = cv2.connectedComponents(skel.astype(np.uint8), connectivity=8)
    best_path = np.empty((0, 2), dtype=np.int32)
    best_length = 0.0
    best_component = np.zeros_like(skel, dtype=np.uint8)

    for label in range(1, n_labels):
        comp = labels == label
        if np.count_nonzero(comp) < 2:
            continue

        coords, neighbors = build_graph(comp)
        if len(coords) < 2:
            continue

        degrees = [len(nbrs) for nbrs in neighbors]
        endpoints = [i for i, degree in enumerate(degrees) if degree == 1]
        candidates = endpoints if len(endpoints) >= 2 else list(range(len(coords)))
        if not candidates:
            continue

        dist0, _ = dijkstra(candidates[0], neighbors)
        start = max(candidates, key=lambda idx: dist0[idx] if math.isfinite(dist0[idx]) else -1.0)
        dist1, prev1 = dijkstra(start, neighbors)
        end = max(candidates, key=lambda idx: dist1[idx] if math.isfinite(dist1[idx]) else -1.0)
        length = float(dist1[end]) if math.isfinite(dist1[end]) else 0.0

        if length < spur_threshold_px or length <= best_length:
            continue

        path_nodes: list[list[int]] = []
        cur = end
        while cur != -1:
            y, x = coords[cur]
            path_nodes.append([int(y), int(x)])
            if cur == start:
                break
            cur = prev1[cur]
        path_nodes.reverse()

        best_path = np.array(path_nodes, dtype=np.int32)
        best_length = length
        best_component = comp.astype(np.uint8) * 255

    return best_path, best_length, best_component


def pick_vertical_endpoint_pair(coords: np.ndarray, endpoints: list[int]) -> tuple[int, int] | None:
    if len(endpoints) < 2:
        return None

    best_pair: tuple[int, int] | None = None
    best_key: tuple[float, float, float] | None = None

    for i in range(len(endpoints)):
        for j in range(i + 1, len(endpoints)):
            a = endpoints[i]
            b = endpoints[j]
            y0, x0 = coords[a]
            y1, x1 = coords[b]

            vertical_span = abs(float(y1) - float(y0))
            horizontal_offset = abs(float(x1) - float(x0))
            # Favor mostly vertical trunks: large vertical span with minimal lateral drift.
            vertical_alignment = vertical_span / (1.0 + horizontal_offset)
            key = (vertical_alignment, vertical_span, -horizontal_offset)
            if best_key is None or key > best_key:
                best_key = key
                best_pair = (a, b)

    return best_pair


def extract_main_path_with_strategy(
    skel: np.ndarray,
    spur_threshold_px: float,
    endpoint_strategy: str,
) -> tuple[np.ndarray, float, np.ndarray]:
    if endpoint_strategy == "longest":
        return extract_main_path(skel, spur_threshold_px=spur_threshold_px)

    n_labels, labels = cv2.connectedComponents(skel.astype(np.uint8), connectivity=8)
    best_path = np.empty((0, 2), dtype=np.int32)
    best_length = 0.0
    best_component = np.zeros_like(skel, dtype=np.uint8)

    for label in range(1, n_labels):
        comp = labels == label
        if np.count_nonzero(comp) < 2:
            continue

        coords, neighbors = build_graph(comp)
        if len(coords) < 2:
            continue

        degrees = [len(nbrs) for nbrs in neighbors]
        endpoints = [i for i, degree in enumerate(degrees) if degree == 1]
        pair = pick_vertical_endpoint_pair(coords, endpoints)
        if pair is None:
            continue

        if len(endpoints) < 2:
            continue

        # Evaluate endpoint pairs: keep only long routes, then prefer vertical alignment.
        endpoint_dists: dict[int, tuple[list[float], list[int]]] = {}
        pair_rows: list[tuple[int, int, float, float, float]] = []
        max_pair_len = 0.0
        for a in endpoints:
            dist, prev = dijkstra(a, neighbors)
            endpoint_dists[a] = (dist, prev)
            for b in endpoints:
                if b <= a:
                    continue
                plen = float(dist[b]) if math.isfinite(dist[b]) else 0.0
                if plen <= 0.0:
                    continue
                y0, x0 = coords[a]
                y1, x1 = coords[b]
                dy = abs(float(y1) - float(y0))
                dx = abs(float(x1) - float(x0))
                pair_rows.append((a, b, plen, dy, dx))
                if plen > max_pair_len:
                    max_pair_len = plen

        if not pair_rows or max_pair_len <= 0.0:
            continue

        min_len = max(spur_threshold_px, 0.65 * max_pair_len)
        candidates = [row for row in pair_rows if row[2] >= min_len]
        if not candidates:
            continue

        def cand_key(row: tuple[int, int, float, float, float]) -> tuple[float, float, float]:
            _, _, plen, dy, dx = row
            vertical_alignment = dy / (1.0 + dx)
            return (vertical_alignment, plen, -dx)

        start, end, length, _, _ = max(candidates, key=cand_key)
        dist, prev = endpoint_dists[start]
        if not math.isfinite(dist[end]) or float(dist[end]) < spur_threshold_px:
            continue

        path_nodes: list[list[int]] = []
        cur = end
        while cur != -1:
            y, x = coords[cur]
            path_nodes.append([int(y), int(x)])
            if cur == start:
                break
            cur = prev[cur]
        path_nodes.reverse()

        length = float(dist[end])
        if length <= best_length:
            continue

        best_path = np.array(path_nodes, dtype=np.int32)
        best_length = length
        best_component = comp.astype(np.uint8) * 255

    return best_path, best_length, best_component


def cumulative_lengths(path_yx: np.ndarray) -> np.ndarray:
    out = np.zeros((len(path_yx),), dtype=np.float64)
    for i in range(1, len(path_yx)):
        dy = float(path_yx[i, 0] - path_yx[i - 1, 0])
        dx = float(path_yx[i, 1] - path_yx[i - 1, 1])
        out[i] = out[i - 1] + math.hypot(dx, dy)
    return out


def sample_seeds(path_yx: np.ndarray, spacing_px: float, start_offset_px: float) -> list[dict[str, float]]:
    if len(path_yx) < 2 or spacing_px <= 0:
        return []
    cumulative = cumulative_lengths(path_yx)
    total = float(cumulative[-1])
    seeds: list[dict[str, float]] = []
    target = max(0.0, start_offset_px)

    while target <= total + 1e-6:
        idx = int(np.searchsorted(cumulative, target, side="left"))
        idx = min(max(1, idx), len(path_yx) - 1)
        d0 = float(cumulative[idx - 1])
        d1 = float(cumulative[idx])
        span = max(1e-9, d1 - d0)
        t = (target - d0) / span
        y = float(path_yx[idx - 1, 0] * (1.0 - t) + path_yx[idx, 0] * t)
        x = float(path_yx[idx - 1, 1] * (1.0 - t) + path_yx[idx, 1] * t)
        seeds.append({"x_px": x, "y_px": y, "distance_along_px": target})
        target += spacing_px
    return seeds


def extract_row_center_path(binary_mask: np.ndarray) -> tuple[np.ndarray, float, np.ndarray]:
    n_labels, labels = cv2.connectedComponents(binary_mask.astype(np.uint8), connectivity=8)
    if n_labels <= 1:
        return np.empty((0, 2), dtype=np.float64), 0.0, np.zeros(binary_mask.shape, dtype=np.uint8)

    best_label = 1
    best_size = 0
    for label in range(1, n_labels):
        size = int(np.count_nonzero(labels == label))
        if size > best_size:
            best_size = size
            best_label = label

    comp = labels == best_label
    ys = np.where(np.any(comp, axis=1))[0]
    if len(ys) < 2:
        return np.empty((0, 2), dtype=np.float64), 0.0, (comp.astype(np.uint8) * 255)

    centers: list[tuple[float, float]] = []
    x_left: list[float] = []
    x_right: list[float] = []
    prev_x: float | None = None

    def row_runs(xs: np.ndarray) -> list[tuple[int, int]]:
        if xs.size == 0:
            return []
        runs: list[tuple[int, int]] = []
        s = int(xs[0])
        p = int(xs[0])
        for x in xs[1:]:
            xi = int(x)
            if xi == p + 1:
                p = xi
                continue
            runs.append((s, p))
            s = xi
            p = xi
        runs.append((s, p))
        return runs

    for y in ys:
        xs = np.where(comp[y])[0]
        if len(xs) == 0:
            continue
        runs = row_runs(xs)
        if not runs:
            continue

        if prev_x is None:
            # Start from the widest run at the top of the component.
            l0, r0 = max(runs, key=lambda lr: (lr[1] - lr[0] + 1))
        else:
            # Continue on the run whose center is nearest to previous row center.
            l0, r0 = min(
                runs,
                key=lambda lr: (abs(0.5 * (lr[0] + lr[1]) - prev_x), -(lr[1] - lr[0] + 1)),
            )

        x_center = 0.5 * float(l0 + r0)
        centers.append((float(y), x_center))
        x_left.append(float(l0))
        x_right.append(float(r0))
        prev_x = x_center

    if len(centers) < 2:
        return np.empty((0, 2), dtype=np.float64), 0.0, (comp.astype(np.uint8) * 255)

    path_yx = np.array(centers, dtype=np.float64)

    # Smooth x while preserving monotonic y, reduces stair/jitter from raster edges.
    window = 9
    if len(path_yx) >= window:
        kernel = np.ones((window,), dtype=np.float64) / float(window)
        pad = window // 2
        x_pad = np.pad(path_yx[:, 1], (pad, pad), mode="edge")
        x_smooth = np.convolve(x_pad, kernel, mode="valid")
        path_yx[:, 1] = x_smooth

    if len(path_yx) == len(x_left):
        left = np.array(x_left, dtype=np.float64)
        right = np.array(x_right, dtype=np.float64)
        path_yx[:, 1] = np.clip(path_yx[:, 1], left, right)

    path_len_px = float(cumulative_lengths(path_yx)[-1])
    return path_yx, path_len_px, (comp.astype(np.uint8) * 255)


def mean_half_width_on_path(distance: np.ndarray, path_yx: np.ndarray, meters_per_pixel: float) -> float:
    if len(path_yx) == 0:
        return 0.0
    h, w = distance.shape
    ys = np.clip(np.round(path_yx[:, 0]).astype(np.int32), 0, h - 1)
    xs = np.clip(np.round(path_yx[:, 1]).astype(np.int32), 0, w - 1)
    vals = distance[ys, xs]
    if vals.size == 0:
        return 0.0
    return float(np.mean(vals) * meters_per_pixel)


def _trim_one_end_hook(path_yx: np.ndarray, max_trim_px: float, angle_deg: float) -> np.ndarray:
    if max_trim_px <= 0 or len(path_yx) < 8:
        return path_yx

    c = cumulative_lengths(path_yx)
    total = float(c[-1])
    if total <= max_trim_px:
        return path_yx

    max_idx = int(np.searchsorted(c, max_trim_px, side="right"))
    max_idx = min(max_idx, len(path_yx) - 4)
    if max_idx < 3:
        return path_yx

    threshold = math.radians(float(angle_deg))
    trim_idx = 0

    for i in range(2, max_idx + 1):
        j = min(len(path_yx) - 1, i + 6)
        v1 = path_yx[i] - path_yx[0]
        v2 = path_yx[j] - path_yx[i]
        n1 = float(np.linalg.norm(v1))
        n2 = float(np.linalg.norm(v2))
        if n1 < 1e-6 or n2 < 1e-6:
            continue
        cosang = float(np.clip(np.dot(v1, v2) / (n1 * n2), -1.0, 1.0))
        ang = math.acos(cosang)
        if ang >= threshold:
            trim_idx = i

    if trim_idx <= 0:
        return path_yx

    return path_yx[trim_idx:]


def trim_endpoint_hooks(path_yx: np.ndarray, max_trim_px: float, angle_deg: float) -> np.ndarray:
    if len(path_yx) < 8 or max_trim_px <= 0:
        return path_yx

    out = _trim_one_end_hook(path_yx, max_trim_px=max_trim_px, angle_deg=angle_deg)
    out = _trim_one_end_hook(out[::-1], max_trim_px=max_trim_px, angle_deg=angle_deg)[::-1]
    return out


def trim_path_by_arclength(path_yx: np.ndarray, start_trim_px: float, end_trim_px: float) -> np.ndarray:
    if len(path_yx) < 2:
        return path_yx

    c = cumulative_lengths(path_yx)
    total = float(c[-1])
    if total <= 0:
        return path_yx

    s0 = max(0.0, float(start_trim_px))
    s1 = max(s0 + 1e-6, total - max(0.0, float(end_trim_px)))
    if s1 <= s0:
        return path_yx

    out_pts: list[np.ndarray] = []

    def interp_at(s: float) -> np.ndarray:
        idx = int(np.searchsorted(c, s, side="left"))
        idx = min(max(1, idx), len(path_yx) - 1)
        d0 = float(c[idx - 1])
        d1 = float(c[idx])
        t = 0.0 if d1 <= d0 else (s - d0) / (d1 - d0)
        return path_yx[idx - 1] * (1.0 - t) + path_yx[idx] * t

    out_pts.append(interp_at(s0))
    keep = (c > s0) & (c < s1)
    if np.any(keep):
        out_pts.extend(path_yx[keep])
    out_pts.append(interp_at(s1))

    out = np.array(out_pts, dtype=np.float64)
    if len(out) < 2:
        return path_yx
    return out


def main() -> int:
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
    binary = mask > 0

    k = max(1, int(args.close_kernel))
    if k % 2 == 0:
        k += 1
    if k > 1:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
        binary = cv2.morphologyEx(binary.astype(np.uint8) * 255, cv2.MORPH_CLOSE, kernel) > 0

    binary = remove_small_objects(binary, max_size=max(1, int(args.min_object_pixels) - 1))
    binary = remove_small_holes(binary, max_size=max(1, int(args.min_hole_pixels) - 1))

    skel, distance = medial_axis(binary, return_distance=True)

    if str(args.path_mode) == "row-center":
        path_yx, path_len_px, component_mask = extract_row_center_path(binary)
    else:
        skel = prune_skeleton_branches(skel, max_len_px=float(args.prune_branch_max_len_px))
        path_yx, path_len_px, component_mask = extract_main_path_with_strategy(
            skel,
            spur_threshold_px=float(args.spur_threshold_px),
            endpoint_strategy=str(args.endpoint_strategy),
        )

    if len(path_yx) < 2:
        raise RuntimeError("Could not extract a valid scikit-image centerline")

    path_yx = trim_endpoint_hooks(
        path_yx,
        max_trim_px=float(args.trim_end_hook_px),
        angle_deg=float(args.trim_end_hook_angle_deg),
    )

    base_cap_factor = max(0.0, float(args.trim_end_cap_factor))
    start_cap_factor = float(args.trim_start_cap_factor)
    final_cap_factor = float(args.trim_final_cap_factor)
    if start_cap_factor < 0:
        start_cap_factor = base_cap_factor
    if final_cap_factor < 0:
        final_cap_factor = base_cap_factor

    if (start_cap_factor > 0 or final_cap_factor > 0) and len(path_yx) >= 2:
        h, w = distance.shape
        y0 = int(np.clip(round(float(path_yx[0, 0])), 0, h - 1))
        x0 = int(np.clip(round(float(path_yx[0, 1])), 0, w - 1))
        y1 = int(np.clip(round(float(path_yx[-1, 0])), 0, h - 1))
        x1 = int(np.clip(round(float(path_yx[-1, 1])), 0, w - 1))
        start_trim_px = max(0.0, start_cap_factor) * float(distance[y0, x0])
        end_trim_px = max(0.0, final_cap_factor) * float(distance[y1, x1])
        path_yx = trim_path_by_arclength(path_yx, start_trim_px=start_trim_px, end_trim_px=end_trim_px)

    if len(path_yx) < 2:
        raise RuntimeError("Centerline collapsed after endpoint hook trimming")
    path_len_px = float(cumulative_lengths(path_yx)[-1])

    spacing_px = float(args.seed_spacing_m) / meters_per_pixel
    start_offset_px = float(args.start_offset_m) / meters_per_pixel
    seeds = sample_seeds(path_yx, spacing_px=spacing_px, start_offset_px=start_offset_px)

    for i, seed in enumerate(seeds, start=1):
        seed["seed_id"] = i
        seed["distance_along_m"] = float(seed["distance_along_px"] * meters_per_pixel)
        seed["x_m"] = float(seed["x_px"] * meters_per_pixel)
        seed["y_m"] = float(seed["y_px"] * meters_per_pixel)

    payload = {
        "summary": {
            "mask": str(mask_path),
            "metrics_json": str(metrics_path),
            "path_mode": str(args.path_mode),
            "endpoint_strategy": str(args.endpoint_strategy),
            "prune_branch_max_len_px": float(args.prune_branch_max_len_px),
            "trim_end_hook_px": float(args.trim_end_hook_px),
            "trim_end_hook_angle_deg": float(args.trim_end_hook_angle_deg),
            "trim_end_cap_factor": float(args.trim_end_cap_factor),
            "trim_start_cap_factor": float(start_cap_factor),
            "trim_final_cap_factor": float(final_cap_factor),
            "seed_spacing_m": float(args.seed_spacing_m),
            "start_offset_m": float(args.start_offset_m),
            "meters_per_pixel": meters_per_pixel,
            "centerline_length_px": float(path_len_px),
            "centerline_length_m": float(path_len_px * meters_per_pixel),
            "seed_count": len(seeds),
        },
        "seeds": seeds,
    }

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    page = cv2.imread(str(page_path), cv2.IMREAD_UNCHANGED)
    if page is None:
        raise RuntimeError(f"Failed to read page image: {page_path}")
    if page.ndim == 2:
        page = cv2.cvtColor(page, cv2.COLOR_GRAY2BGRA)
    elif page.shape[2] == 3:
        page = cv2.cvtColor(page, cv2.COLOR_BGR2BGRA)

    centerline_vis = page.copy()
    overlay_vis = page.copy()

    # dominant component in green, path in amber
    centerline_vis[component_mask > 0] = (60, 220, 60, 255)
    overlay_vis[component_mask > 0] = (60, 220, 60, 255)

    for i in range(1, len(path_yx)):
        y0, x0 = int(path_yx[i - 1, 0]), int(path_yx[i - 1, 1])
        y1, x1 = int(path_yx[i, 0]), int(path_yx[i, 1])
        cv2.line(centerline_vis, (x0, y0), (x1, y1), (0, 191, 255, 255), 2, lineType=cv2.LINE_AA)
        cv2.line(overlay_vis, (x0, y0), (x1, y1), (0, 191, 255, 255), 2, lineType=cv2.LINE_AA)

    radius_px = max(4, int(round(0.5 / max(meters_per_pixel, 1e-9))))
    for seed in seeds:
        x = int(round(float(seed["x_px"])))
        y = int(round(float(seed["y_px"])))
        cv2.circle(overlay_vis, (x, y), radius_px, (0, 0, 255, 255), -1)
        cv2.circle(overlay_vis, (x, y), radius_px + 1, (255, 255, 255, 255), 1)

    out_overlay.parent.mkdir(parents=True, exist_ok=True)
    out_centerline.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_overlay), overlay_vis)
    cv2.imwrite(str(out_centerline), centerline_vis)

    print(f"Wrote seeds JSON: {out_json}")
    print(f"Wrote seed overlay: {out_overlay}")
    print(f"Wrote centerline image: {out_centerline}")
    print(f"Centerline length: {path_len_px * meters_per_pixel:.3f} m")
    print(f"Seed count: {len(seeds)} (spacing={args.seed_spacing_m} m)")
    mean_half_width_m = mean_half_width_on_path(distance, path_yx, meters_per_pixel)
    print(f"Mean half-width on path: {mean_half_width_m:.3f} m")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

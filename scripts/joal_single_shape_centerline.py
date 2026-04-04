#!/usr/bin/env python3
"""Build a single-shape JOAL mask from vectors and extract a stable centerline + 1m seeds.

Pipeline:
1. Rasterize JOAL vectors to a binary mask.
2. Convert hatch-like geometry into one filled footprint (largest external contour after close).
3. Skeletonize with scikit-image medial axis.
4. Extract one trunk path between top and bottom anchor points on the skeleton.
5. Sample equidistant seeds along that trunk.
"""

from __future__ import annotations

import argparse
import heapq
import json
import math
from pathlib import Path
from typing import Any

import fitz
import cv2
import numpy as np
from skimage.morphology import medial_axis


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Single-shape JOAL centerline and seed extraction")
    p.add_argument("--pdf", default="examples/Joal 502.pdf")
    p.add_argument("--vectors-json", default="outputs/joal502/joal_vectors_relaxed.json")
    p.add_argument("--scale-json", default="outputs/scale_detection/scale_detection.json")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--pixels-per-point", type=float, default=3.0)
    p.add_argument("--curve-steps", type=int, default=16)
    p.add_argument("--close-kernel", type=int, default=41)
    p.add_argument("--seed-spacing-m", type=float, default=1.0)
    p.add_argument(
        "--clip-endcaps-px",
        type=float,
        default=0.0,
        help="Geodesic distance to clip from both skeleton ends before selecting final path",
    )
    p.add_argument(
        "--extend-ends-to-mask",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Extend both path ends as straight tangents until mask boundary",
    )
    p.add_argument(
        "--extend-lookahead-nodes",
        type=int,
        default=24,
        help="Number of path nodes used to estimate local tangent for end extension",
    )
    p.add_argument(
        "--trim-end-cap-factor",
        type=float,
        default=1.2,
        help="Trim each path end by factor * local endpoint half-width from distance map (0 disables)",
    )
    p.add_argument(
        "--trim-end-min-px",
        type=float,
        default=0.0,
        help="Absolute minimum trim length in pixels applied to each end",
    )
    p.add_argument(
        "--anchor-strategy",
        choices=["farthest-endpoints", "top-bottom"],
        default="farthest-endpoints",
        help="How to select skeleton path endpoints",
    )
    p.add_argument("--out-json", default="outputs/joal502/joal_single_shape_centerline_1m.json")
    p.add_argument("--out-mask", default="outputs/joal502/visualizations/joal/joal_single_shape_mask.png")
    p.add_argument("--out-overlay", default="outputs/joal502/visualizations/joal/joal_single_shape_centerline_1m_overlay.png")
    return p.parse_args()


def as_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def to_point(v: Any) -> np.ndarray | None:
    if not isinstance(v, (list, tuple)) or len(v) < 2:
        return None
    x = as_float(v[0])
    y = as_float(v[1])
    if x is None or y is None:
        return None
    return np.array([x, y], dtype=np.float64)


def bezier_points(p0: np.ndarray, c1: np.ndarray, c2: np.ndarray, p1: np.ndarray, steps: int) -> list[np.ndarray]:
    pts: list[np.ndarray] = []
    n = max(2, steps)
    for i in range(n + 1):
        t = i / n
        omt = 1.0 - t
        p = (omt ** 3) * p0 + 3.0 * (omt ** 2) * t * c1 + 3.0 * omt * (t ** 2) * c2 + (t ** 3) * p1
        pts.append(p)
    return pts


def pt_to_px(pt: np.ndarray, ppp: float) -> tuple[int, int]:
    return int(round(float(pt[0]) * ppp)), int(round(float(pt[1]) * ppp))


def build_polyline(path_items: list[dict[str, Any]], curve_steps: int) -> list[np.ndarray]:
    poly: list[np.ndarray] = []
    for item in path_items:
        op = item.get("op")
        if op == "line":
            p0 = to_point(item.get("p0"))
            p1 = to_point(item.get("p1"))
            if p0 is None or p1 is None:
                continue
            if not poly:
                poly.append(p0)
            elif np.linalg.norm(poly[-1] - p0) > 1e-6:
                poly.append(p0)
            poly.append(p1)
        elif op == "curve":
            p0 = to_point(item.get("p0"))
            c1 = to_point(item.get("c1"))
            c2 = to_point(item.get("c2"))
            p1 = to_point(item.get("p1"))
            if p0 is None or c1 is None or c2 is None or p1 is None:
                continue
            curve = bezier_points(p0, c1, c2, p1, curve_steps)
            if not poly:
                poly.extend(curve)
            else:
                if np.linalg.norm(poly[-1] - curve[0]) > 1e-6:
                    poly.append(curve[0])
                poly.extend(curve[1:])
    return poly


def draw_vector(mask: np.ndarray, vector: dict[str, Any], ppp: float, curve_steps: int) -> None:
    vtype = str(vector.get("type") or "")
    path_items = vector.get("path_items") or []

    for item in path_items:
        if item.get("op") != "rect":
            continue
        rect = item.get("rect")
        if not isinstance(rect, list) or len(rect) != 4:
            continue
        x0, y0 = int(round(float(rect[0]) * ppp)), int(round(float(rect[1]) * ppp))
        x1, y1 = int(round(float(rect[2]) * ppp)), int(round(float(rect[3]) * ppp))
        cv2.rectangle(mask, (min(x0, x1), min(y0, y1)), (max(x0, x1), max(y0, y1)), 255, thickness=-1)

    poly = build_polyline(path_items, curve_steps)
    if len(poly) < 2:
        return

    pts = np.array([pt_to_px(p, ppp) for p in poly], dtype=np.int32)

    if "f" in vtype and len(pts) >= 3:
        cv2.fillPoly(mask, [pts], 255)


def largest_external_filled(mask: np.ndarray, close_kernel: int) -> np.ndarray:
    k = max(1, int(close_kernel))
    if k % 2 == 0:
        k += 1
    if k > 1:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = np.zeros_like(mask)
    if not contours:
        return out
    c = max(contours, key=cv2.contourArea)
    cv2.drawContours(out, [c], -1, 255, thickness=-1)
    return out


def build_graph(skel: np.ndarray) -> tuple[np.ndarray, list[list[tuple[int, float]]], dict[tuple[int, int], int]]:
    coords = np.argwhere(skel)
    pos_to_idx = {(int(y), int(x)): i for i, (y, x) in enumerate(coords)}
    neighbors: list[list[tuple[int, float]]] = [[] for _ in range(len(coords))]
    offs = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
    for i, (y, x) in enumerate(coords):
        yi = int(y)
        xi = int(x)
        for dy, dx in offs:
            j = pos_to_idx.get((yi + dy, xi + dx))
            if j is None:
                continue
            w = math.sqrt(2.0) if (dy != 0 and dx != 0) else 1.0
            neighbors[i].append((j, w))
    return coords, neighbors, pos_to_idx


def dijkstra(start: int, neighbors: list[list[tuple[int, float]]]) -> tuple[list[float], list[int]]:
    n = len(neighbors)
    dist = [math.inf] * n
    prev = [-1] * n
    dist[start] = 0.0
    heap: list[tuple[float, int]] = [(0.0, start)]
    while heap:
        cd, u = heapq.heappop(heap)
        if cd > dist[u]:
            continue
        for v, w in neighbors[u]:
            nd = cd + w
            if nd < dist[v]:
                dist[v] = nd
                prev[v] = u
                heapq.heappush(heap, (nd, v))
    return dist, prev


def endpoint_indices(neighbors: list[list[tuple[int, float]]]) -> list[int]:
    return [i for i, nbrs in enumerate(neighbors) if len(nbrs) == 1]


def choose_farthest_endpoints(neighbors: list[list[tuple[int, float]]]) -> tuple[int, int] | None:
    ends = endpoint_indices(neighbors)
    if len(ends) < 2:
        return None

    start = ends[0]
    dist0, _ = dijkstra(start, neighbors)
    a = max(ends, key=lambda i: dist0[i] if math.isfinite(dist0[i]) else -1.0)
    dist1, _ = dijkstra(a, neighbors)
    b = max(ends, key=lambda i: dist1[i] if math.isfinite(dist1[i]) else -1.0)
    if not math.isfinite(dist1[b]):
        return None
    return (a, b)


def keep_largest_component(binary: np.ndarray) -> np.ndarray:
    n_labels, labels = cv2.connectedComponents(binary.astype(np.uint8), connectivity=8)
    if n_labels <= 1:
        return binary
    best_label = 1
    best_size = 0
    for label in range(1, n_labels):
        size = int(np.count_nonzero(labels == label))
        if size > best_size:
            best_size = size
            best_label = label
    out = np.zeros_like(binary, dtype=bool)
    out[labels == best_label] = True
    return out


def clip_skeleton_endcaps(skel: np.ndarray, clip_px: float) -> np.ndarray:
    if clip_px <= 0:
        return skel

    coords, neighbors, _ = build_graph(skel)
    if len(coords) < 3:
        return skel

    pair = choose_farthest_endpoints(neighbors)
    if pair is None:
        return skel

    a, b = pair
    dist_a, _ = dijkstra(a, neighbors)
    dist_b, _ = dijkstra(b, neighbors)

    keep = np.zeros((len(coords),), dtype=bool)
    for i in range(len(coords)):
        da = dist_a[i]
        db = dist_b[i]
        if not (math.isfinite(da) and math.isfinite(db)):
            continue
        if da >= clip_px and db >= clip_px:
            keep[i] = True

    if not np.any(keep):
        return skel

    out = np.zeros_like(skel, dtype=bool)
    kept_coords = coords[keep]
    out[kept_coords[:, 0], kept_coords[:, 1]] = True
    return keep_largest_component(out)


def nearest_node(coords: np.ndarray, target_yx: tuple[float, float]) -> int:
    ty, tx = target_yx
    d2 = (coords[:, 0] - ty) ** 2 + (coords[:, 1] - tx) ** 2
    return int(np.argmin(d2))


def cumulative_lengths(path_yx: np.ndarray) -> np.ndarray:
    out = np.zeros((len(path_yx),), dtype=np.float64)
    for i in range(1, len(path_yx)):
        dy = float(path_yx[i, 0] - path_yx[i - 1, 0])
        dx = float(path_yx[i, 1] - path_yx[i - 1, 1])
        out[i] = out[i - 1] + math.hypot(dx, dy)
    return out


def sample_seeds(path_yx: np.ndarray, spacing_px: float) -> list[dict[str, float]]:
    if len(path_yx) < 2 or spacing_px <= 0:
        return []
    cumulative = cumulative_lengths(path_yx)
    total = float(cumulative[-1])
    seeds: list[dict[str, float]] = []
    target = 0.0
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

    def interp_at(s: float) -> np.ndarray:
        idx = int(np.searchsorted(c, s, side="left"))
        idx = min(max(1, idx), len(path_yx) - 1)
        d0 = float(c[idx - 1])
        d1 = float(c[idx])
        t = 0.0 if d1 <= d0 else (s - d0) / (d1 - d0)
        return path_yx[idx - 1] * (1.0 - t) + path_yx[idx] * t

    out_pts: list[np.ndarray] = [interp_at(s0)]
    keep = (c > s0) & (c < s1)
    if np.any(keep):
        out_pts.extend(path_yx[keep])
    out_pts.append(interp_at(s1))
    out = np.array(out_pts, dtype=np.float64)
    return out if len(out) >= 2 else path_yx


def _inside_mask(binary: np.ndarray, y: float, x: float) -> bool:
    h, w = binary.shape
    yi = int(round(y))
    xi = int(round(x))
    if yi < 0 or yi >= h or xi < 0 or xi >= w:
        return False
    return bool(binary[yi, xi])


def _extend_point_to_mask_boundary(
    start_yx: np.ndarray,
    dir_yx: np.ndarray,
    binary: np.ndarray,
    step_px: float = 0.5,
    max_steps: int = 2000,
) -> np.ndarray:
    n = float(np.linalg.norm(dir_yx))
    if n <= 1e-9:
        return start_yx
    d = dir_yx / n

    y = float(start_yx[0])
    x = float(start_yx[1])
    last_inside = np.array([y, x], dtype=np.float64)

    for _ in range(max_steps):
        y += float(d[0]) * step_px
        x += float(d[1]) * step_px
        if not _inside_mask(binary, y, x):
            break
        last_inside = np.array([y, x], dtype=np.float64)

    return last_inside


def extend_path_ends_to_mask(path_yx: np.ndarray, binary: np.ndarray, lookahead_nodes: int) -> np.ndarray:
    if len(path_yx) < 3:
        return path_yx

    k = max(1, min(int(lookahead_nodes), len(path_yx) - 1))

    start = path_yx[0]
    start_ref = path_yx[k]
    start_dir = start - start_ref
    new_start = _extend_point_to_mask_boundary(start, start_dir, binary)

    end = path_yx[-1]
    end_ref = path_yx[-1 - k]
    end_dir = end - end_ref
    new_end = _extend_point_to_mask_boundary(end, end_dir, binary)

    def _interp_anchor_to_tip(anchor: np.ndarray, tip: np.ndarray) -> list[np.ndarray]:
        """Return densely-stepped points from anchor toward tip (anchor excluded)."""
        dist = float(np.linalg.norm(tip - anchor))
        if dist <= 1e-6:
            return []
        n = max(2, int(round(dist)))
        pts = []
        for j in range(1, n + 1):
            frac = float(j) / float(n)
            pts.append(anchor + (tip - anchor) * frac)
        return pts

    out_pts: list[np.ndarray] = []
    # Prepend start extension in forward path order: new_start -> ... -> near start.
    out_pts.extend(reversed(_interp_anchor_to_tip(start, new_start)))
    out_pts.extend(path_yx)
    # Append end extension in forward path order: near end -> ... -> new_end.
    out_pts.extend(_interp_anchor_to_tip(end, new_end))

    return np.array(out_pts, dtype=np.float64)


def meters_per_pixel(scale_json: Path, ppp: float) -> float:
    d = json.loads(scale_json.read_text(encoding="utf-8"))
    mpp = float((d.get("calibration") or {}).get("meters_per_point"))
    return mpp / ppp


def run(args: argparse.Namespace) -> int:
    pdf_path = Path(args.pdf)
    vectors_path = Path(args.vectors_json)
    scale_path = Path(args.scale_json)

    if not pdf_path.exists() or not vectors_path.exists() or not scale_path.exists():
        raise FileNotFoundError("Missing required input file")

    doc = fitz.open(pdf_path)
    page_index = max(0, int(args.page) - 1)
    page = doc[page_index]
    ppp = float(args.pixels_per_point)
    w = max(1, int(math.ceil(float(page.rect.width) * ppp)))
    h = max(1, int(math.ceil(float(page.rect.height) * ppp)))
    doc.close()

    payload = json.loads(vectors_path.read_text(encoding="utf-8"))
    vectors = [v for v in payload.get("vectors", []) if int(v.get("page", 0)) == int(args.page)]

    raw = np.zeros((h, w), dtype=np.uint8)
    for v in vectors:
        draw_vector(raw, v, ppp=ppp, curve_steps=int(args.curve_steps))

    shape = largest_external_filled(raw, close_kernel=int(args.close_kernel))
    binary = shape > 0

    skel, distance = medial_axis(binary, return_distance=True)
    skel = clip_skeleton_endcaps(skel, clip_px=float(args.clip_endcaps_px))
    if np.count_nonzero(skel) < 2:
        raise RuntimeError("Skeleton extraction failed on single-shape mask")

    coords, neighbors, _ = build_graph(skel)

    if str(args.anchor_strategy) == "farthest-endpoints":
        chosen = choose_farthest_endpoints(neighbors)
        if chosen is None:
            raise RuntimeError("Could not find skeleton endpoints for farthest-endpoints strategy")
        s, t = chosen
        top_anchor = (float(coords[s][0]), float(coords[s][1]))
        bot_anchor = (float(coords[t][0]), float(coords[t][1]))
    else:
        ys = np.where(np.any(binary, axis=1))[0]
        y_top = int(ys[0])
        y_bot = int(ys[-1])
        xs_top = np.where(binary[y_top])[0]
        xs_bot = np.where(binary[y_bot])[0]
        top_anchor = (float(y_top), 0.5 * float(xs_top[0] + xs_top[-1]))
        bot_anchor = (float(y_bot), 0.5 * float(xs_bot[0] + xs_bot[-1]))
        s = nearest_node(coords, top_anchor)
        t = nearest_node(coords, bot_anchor)

    dist, prev = dijkstra(s, neighbors)
    if not math.isfinite(dist[t]):
        raise RuntimeError("No path found between top and bottom anchors on skeleton")

    path_nodes: list[list[int]] = []
    cur = t
    while cur != -1:
        y, x = coords[cur]
        path_nodes.append([int(y), int(x)])
        if cur == s:
            break
        cur = prev[cur]
    path_nodes.reverse()
    path_yx = np.array(path_nodes, dtype=np.float64)

    cap_factor = max(0.0, float(args.trim_end_cap_factor))
    trim_min_px = max(0.0, float(args.trim_end_min_px))
    if cap_factor > 0 and len(path_yx) >= 2:
        hh, ww = distance.shape
        y0 = int(np.clip(round(float(path_yx[0, 0])), 0, hh - 1))
        x0 = int(np.clip(round(float(path_yx[0, 1])), 0, ww - 1))
        y1 = int(np.clip(round(float(path_yx[-1, 0])), 0, hh - 1))
        x1 = int(np.clip(round(float(path_yx[-1, 1])), 0, ww - 1))
        start_trim_px = max(trim_min_px, cap_factor * float(distance[y0, x0]))
        end_trim_px = max(trim_min_px, cap_factor * float(distance[y1, x1]))
        path_yx = trim_path_by_arclength(path_yx, start_trim_px=start_trim_px, end_trim_px=end_trim_px)
        if len(path_yx) < 2:
            raise RuntimeError("Path collapsed after endpoint cap trimming")

    if bool(args.extend_ends_to_mask):
        path_yx = extend_path_ends_to_mask(
            path_yx,
            binary=binary,
            lookahead_nodes=int(args.extend_lookahead_nodes),
        )
        if len(path_yx) < 2:
            raise RuntimeError("Path collapsed after endpoint extension")

    mpp = meters_per_pixel(scale_path, ppp=ppp)
    spacing_px = float(args.seed_spacing_m) / mpp
    seeds = sample_seeds(path_yx, spacing_px=spacing_px)
    for i, seed in enumerate(seeds, start=1):
        seed["seed_id"] = i
        seed["distance_along_m"] = float(seed["distance_along_px"] * mpp)
        seed["x_m"] = float(seed["x_px"] * mpp)
        seed["y_m"] = float(seed["y_px"] * mpp)

    overlay = np.zeros((h, w, 3), dtype=np.uint8)
    overlay[shape > 0] = (60, 235, 60)
    for i in range(1, len(path_yx)):
        y0, x0 = int(round(path_yx[i - 1, 0])), int(round(path_yx[i - 1, 1]))
        y1, x1 = int(round(path_yx[i, 0])), int(round(path_yx[i, 1]))
        cv2.line(overlay, (x0, y0), (x1, y1), (0, 191, 255), 2, lineType=cv2.LINE_AA)
    for s0 in seeds:
        x = int(round(float(s0["x_px"])))
        y = int(round(float(s0["y_px"])))
        cv2.circle(overlay, (x, y), 2, (0, 0, 255), -1)

    out_mask = Path(args.out_mask)
    out_overlay = Path(args.out_overlay)
    out_json = Path(args.out_json)
    out_mask.parent.mkdir(parents=True, exist_ok=True)
    out_overlay.parent.mkdir(parents=True, exist_ok=True)
    out_json.parent.mkdir(parents=True, exist_ok=True)

    cv2.imwrite(str(out_mask), shape)
    cv2.imwrite(str(out_overlay), overlay)

    result = {
        "summary": {
            "pdf": str(pdf_path),
            "vectors_json": str(vectors_path),
            "page": int(args.page),
            "vector_count_used": len(vectors),
            "pixels_per_point": ppp,
            "meters_per_pixel": mpp,
            "seed_spacing_m": float(args.seed_spacing_m),
            "close_kernel": int(args.close_kernel),
            "anchor_strategy": str(args.anchor_strategy),
            "clip_endcaps_px": float(args.clip_endcaps_px),
            "trim_end_cap_factor": float(args.trim_end_cap_factor),
            "trim_end_min_px": float(args.trim_end_min_px),
            "extend_ends_to_mask": bool(args.extend_ends_to_mask),
            "extend_lookahead_nodes": int(args.extend_lookahead_nodes),
            "centerline_length_m": float(cumulative_lengths(path_yx)[-1] * mpp),
            "seed_count": len(seeds),
        },
        "anchors": {
            "top_anchor_yx": [top_anchor[0], top_anchor[1]],
            "bottom_anchor_yx": [bot_anchor[0], bot_anchor[1]],
        },
        "path_nodes_yx": path_yx.tolist(),
        "seeds": seeds,
    }
    out_json.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print(f"Wrote single-shape mask: {out_mask}")
    print(f"Wrote overlay: {out_overlay}")
    print(f"Wrote seeds JSON: {out_json}")
    print(f"Centerline length: {result['summary']['centerline_length_m']:.3f} m")
    print(f"Seed count: {len(seeds)}")
    return 0


def main() -> int:
    args = parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())

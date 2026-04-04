#!/usr/bin/env python3
"""Measure footpath area and length from vector candidates.

This script rasterizes vector path items to a binary mask, then computes:
- Area from mask pixels
- Centerline length from skeleton graph length
- Width statistics from distance transform on skeleton

Units are reported in PDF points and meters using scale calibration.
"""

from __future__ import annotations

import argparse
import heapq
import json
import math
from pathlib import Path
from typing import Any

import fitz
import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Measure footpath length and area from vector set")
    parser.add_argument("--pdf", default="examples/Joal 502.pdf", help="Input PDF")
    parser.add_argument("--vectors-json", default="outputs/joal502/footpath_vectors_relaxed.json", help="Input vectors JSON")
    parser.add_argument("--scale-json", default="outputs/scale_detection/scale_detection.json", help="Scale calibration JSON")
    parser.add_argument("--out", default="outputs/joal502/footpath_vector_metrics.json", help="Output metrics JSON")
    parser.add_argument("--vis-dir", default="outputs/joal502/visualizations", help="Output visualization directory")
    parser.add_argument("--page", type=int, default=1, help="1-based page number")
    parser.add_argument("--pixels-per-point", type=float, default=3.0, help="Rasterization resolution in px per PDF point")
    parser.add_argument("--curve-steps", type=int, default=16, help="Segments for Bezier approximation")
    parser.add_argument("--close-kernel", type=int, default=3, help="Morph close kernel size (odd int)")
    parser.add_argument(
        "--keep-largest-component",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Keep only the largest connected mask component before skeletonization",
    )
    return parser.parse_args()


def ensure_cv2() -> None:
    if cv2 is None:
        raise RuntimeError("opencv-python-headless is required. Install from requirements.txt")


def to_point(value: Any) -> np.ndarray | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        return np.array([float(value[0]), float(value[1])], dtype=np.float64)
    except (TypeError, ValueError):
        return None


def bezier_points(p0: np.ndarray, c1: np.ndarray, c2: np.ndarray, p1: np.ndarray, steps: int) -> list[np.ndarray]:
    pts: list[np.ndarray] = []
    n = max(2, steps)
    for i in range(n + 1):
        t = i / n
        omt = 1.0 - t
        p = (omt**3) * p0 + 3.0 * (omt**2) * t * c1 + 3.0 * omt * (t**2) * c2 + (t**3) * p1
        pts.append(p)
    return pts


def pt_to_px(pt: np.ndarray, ppp: float) -> tuple[int, int]:
    return (int(round(float(pt[0]) * ppp)), int(round(float(pt[1]) * ppp)))


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

    # Handle rectangle commands directly for stability.
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

    if "s" in vtype:
        width_pt = vector.get("width")
        try:
            width_px = max(1, int(round(float(width_pt) * ppp)))
        except (TypeError, ValueError):
            width_px = 1
        cv2.polylines(mask, [pts], isClosed=False, color=255, thickness=width_px, lineType=cv2.LINE_AA)


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


def skeleton_length_pixels(skel: np.ndarray) -> float:
    b = skel > 0
    h, w = b.shape
    total = 0.0
    for y in range(h):
        row = b[y]
        for x in range(w):
            if not row[x]:
                continue
            if x + 1 < w and b[y, x + 1]:
                total += 1.0
            if y + 1 < h and b[y + 1, x]:
                total += 1.0
            if x + 1 < w and y + 1 < h and b[y + 1, x + 1]:
                total += math.sqrt(2.0)
            if x - 1 >= 0 and y + 1 < h and b[y + 1, x - 1]:
                total += math.sqrt(2.0)
    return total


def keep_largest_component(mask: np.ndarray) -> np.ndarray:
    n_labels, labels = cv2.connectedComponents((mask > 0).astype(np.uint8), connectivity=8)
    if n_labels <= 1:
        return mask

    best_label = 1
    best_size = 0
    for label in range(1, n_labels):
        size = int(np.count_nonzero(labels == label))
        if size > best_size:
            best_size = size
            best_label = label

    out = np.zeros_like(mask)
    out[labels == best_label] = 255
    return out


def main_path_length_pixels(skel: np.ndarray) -> tuple[float, np.ndarray, list[list[int]]]:
    n_labels, labels = cv2.connectedComponents((skel > 0).astype(np.uint8), connectivity=8)
    if n_labels <= 1:
        return (0.0, np.zeros_like(skel), [])

    # Focus on largest skeleton component to avoid disconnected artifacts.
    best_label = 1
    best_size = 0
    for label in range(1, n_labels):
        size = int(np.count_nonzero(labels == label))
        if size > best_size:
            best_size = size
            best_label = label

    comp = labels == best_label
    coords = np.argwhere(comp)
    if len(coords) == 0:
        return (0.0, np.zeros_like(skel), [])

    pos_to_idx: dict[tuple[int, int], int] = {}
    for i, (y, x) in enumerate(coords):
        pos_to_idx[(int(y), int(x))] = i

    neighbors: list[list[tuple[int, float]]] = [[] for _ in range(len(coords))]
    for i, (y, x) in enumerate(coords):
        for dy, dx in [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]:
            j = pos_to_idx.get((int(y + dy), int(x + dx)))
            if j is None:
                continue
            w = math.sqrt(2.0) if (dy != 0 and dx != 0) else 1.0
            neighbors[i].append((j, w))

    degrees = [len(nbrs) for nbrs in neighbors]
    endpoints = [i for i, d in enumerate(degrees) if d == 1]

    def dijkstra(start: int) -> tuple[list[float], list[int]]:
        dist = [math.inf] * len(coords)
        prev = [-1] * len(coords)
        dist[start] = 0.0
        heap: list[tuple[float, int]] = [(0.0, start)]
        while heap:
            cur_d, u = heapq.heappop(heap)
            if cur_d > dist[u]:
                continue
            for v, w in neighbors[u]:
                nd = cur_d + w
                if nd < dist[v]:
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(heap, (nd, v))
        return dist, prev

    # Two-sweep geodesic approximation on the dominant component.
    start = endpoints[0] if endpoints else 0
    dist1, _ = dijkstra(start)
    candidates = endpoints if len(endpoints) >= 2 else list(range(len(coords)))
    a = max(candidates, key=lambda i: dist1[i] if math.isfinite(dist1[i]) else -1.0)

    dist2, prev2 = dijkstra(a)
    b = max(candidates, key=lambda i: dist2[i] if math.isfinite(dist2[i]) else -1.0)
    length = float(dist2[b]) if math.isfinite(dist2[b]) else 0.0

    path_mask = np.zeros_like(skel)
    path_nodes: list[list[int]] = []
    cur = b
    while cur != -1:
        y, x = coords[cur]
        path_mask[int(y), int(x)] = 255
        path_nodes.append([int(y), int(x)])
        if cur == a:
            break
        cur = prev2[cur]

    return (length, path_mask, path_nodes[::-1])


def load_meters_per_point(scale_json_path: Path) -> float:
    data = json.loads(scale_json_path.read_text(encoding="utf-8"))
    calib = data.get("calibration") or {}
    mpp = calib.get("meters_per_point")
    if mpp is None:
        raise RuntimeError(f"meters_per_point missing in {scale_json_path}")
    return float(mpp)


def main() -> int:
    ensure_cv2()
    args = parse_args()

    pdf_path = Path(args.pdf)
    vectors_path = Path(args.vectors_json)
    scale_path = Path(args.scale_json)
    out_path = Path(args.out)
    vis_dir = Path(args.vis_dir)

    if not pdf_path.exists():
        raise FileNotFoundError(f"Missing PDF: {pdf_path}")
    if not vectors_path.exists():
        raise FileNotFoundError(f"Missing vectors JSON: {vectors_path}")
    if not scale_path.exists():
        raise FileNotFoundError(f"Missing scale JSON: {scale_path}")

    meters_per_point = load_meters_per_point(scale_path)
    ppp = float(args.pixels_per_point)
    meters_per_pixel = meters_per_point / ppp

    doc = fitz.open(pdf_path)
    page_index = max(0, args.page - 1)
    if page_index >= doc.page_count:
        raise ValueError(f"Page out of range: {args.page}")
    page = doc[page_index]
    width_px = max(1, int(math.ceil(float(page.rect.width) * ppp)))
    height_px = max(1, int(math.ceil(float(page.rect.height) * ppp)))
    doc.close()

    payload = json.loads(vectors_path.read_text(encoding="utf-8"))
    vectors = [v for v in payload.get("vectors", []) if int(v.get("page", 0)) == args.page]

    mask = np.zeros((height_px, width_px), dtype=np.uint8)
    for v in vectors:
        draw_vector(mask, v, ppp=ppp, curve_steps=int(args.curve_steps))

    k = max(1, int(args.close_kernel))
    if k % 2 == 0:
        k += 1
    if k > 1:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    if args.keep_largest_component:
        mask = keep_largest_component(mask)

    area_pixels = int(cv2.countNonZero(mask))
    area_points2 = area_pixels / (ppp * ppp)
    area_m2 = area_points2 * (meters_per_point * meters_per_point)

    skel = skeletonize(mask)
    length_pixels_total = skeleton_length_pixels(skel)
    length_points_total = length_pixels_total / ppp
    length_m_total = length_points_total * meters_per_point

    length_pixels_main, main_path_mask, main_path_nodes = main_path_length_pixels(skel)
    length_points_main = length_pixels_main / ppp
    length_m_main = length_points_main * meters_per_point

    dist = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
    skel_mask = main_path_mask > 0 if np.count_nonzero(main_path_mask) > 0 else (skel > 0)
    if np.any(skel_mask):
        widths_px = 2.0 * dist[skel_mask]
        width_mean_m = float(np.mean(widths_px) * meters_per_pixel)
        width_median_m = float(np.median(widths_px) * meters_per_pixel)
    else:
        width_mean_m = 0.0
        width_median_m = 0.0

    eps = 1e-9
    length_m_half_skeleton = 0.5 * length_m_total
    length_m_area_over_median_width = area_m2 / max(width_median_m, eps)
    length_m_area_over_mean_width = area_m2 / max(width_mean_m, eps)

    metrics = {
        "summary": {
            "pdf": str(pdf_path),
            "vectors_json": str(vectors_path),
            "scale_json": str(scale_path),
            "page": args.page,
            "vector_count_used": len(vectors),
            "pixels_per_point": ppp,
            "meters_per_point": meters_per_point,
            "meters_per_pixel": meters_per_pixel,
            "keep_largest_component": bool(args.keep_largest_component),
        },
        "results": {
            "area_pixels": area_pixels,
            "area_points2": area_points2,
            "area_m2": area_m2,
            "length_pixels_total_skeleton": length_pixels_total,
            "length_points_total_skeleton": length_points_total,
            "length_m_total_skeleton": length_m_total,
            "length_pixels_main_path": length_pixels_main,
            "length_points_main_path": length_points_main,
            "length_m_main_path": length_m_main,
            "length_m_half_skeleton": length_m_half_skeleton,
            "length_m_area_over_median_width": length_m_area_over_median_width,
            "length_m_area_over_mean_width": length_m_area_over_mean_width,
            "width_mean_m": width_mean_m,
            "width_median_m": width_median_m,
        },
        "main_path": {
            "node_count": len(main_path_nodes),
            "nodes_yx": main_path_nodes,
        },
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    vis_dir.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(vis_dir / "footpath_vector_mask.png"), mask)

    skel_bgr = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
    skel_bgr[skel > 0] = (0, 0, 255)
    skel_bgr[main_path_mask > 0] = (0, 255, 255)
    cv2.imwrite(str(vis_dir / "footpath_vector_skeleton.png"), skel_bgr)

    print(f"Wrote metrics to {out_path}")
    print(f"Area: {area_m2:.3f} m^2")
    print(f"Length (main path): {length_m_main:.3f} m")
    print(f"Length (all skeleton): {length_m_total:.3f} m")
    print(f"Median width: {width_median_m:.3f} m")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

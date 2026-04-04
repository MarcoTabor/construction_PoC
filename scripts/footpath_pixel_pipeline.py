#!/usr/bin/env python3
"""Footpath extraction pipeline starting at pixel level.

Stages:
1) Render page to image
2) Build raw color-distance mask from legend footpath color
3) Remove non-plan furniture (legend + title strip)
4) Find connected components
5) Score/select likely footpath components
6) Render baseline magenta overlay
7) Build non-surface overlap mask (text / utility lines)
8) Resolve overlaps + heal strip continuity
9) Polygon preview from resolved mask
"""

from __future__ import annotations

import argparse
import heapq
import json
import logging
import random
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

import fitz
import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


@dataclass
class ComponentStats:
    component_id: int
    area_px: int
    x: int
    y: int
    w: int
    h: int
    aspect_ratio: float
    fill_ratio: float
    keep: bool
    keep_reason: str


def setup_logger(log_path: Path, level: str) -> logging.Logger:
    logger = logging.getLogger("footpath_pixel_pipeline")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%Y-%m-%d %H:%M:%S")

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Footpath extraction from pixel level")
    parser.add_argument("--pdf", default="examples/Joal 502.pdf")
    parser.add_argument("--legend-json", default="outputs/legend_colors/legend_colors.json")
    parser.add_argument("--outdir", default="outputs/footpath_pixel_pipeline")
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--rgb-threshold", type=int, default=22, help="RGB distance threshold in 0..441")
    parser.add_argument("--seed-mask", default="raw", choices=["raw", "selected"], help="Mask used as starting point for overlap resolution")
    parser.add_argument("--min-area", type=int, default=120)
    parser.add_argument("--max-area-ratio", type=float, default=0.08)
    parser.add_argument("--max-fill-ratio", type=float, default=0.72)
    parser.add_argument("--max-thickness-ratio", type=float, default=0.40)
    parser.add_argument("--dark-threshold", type=int, default=75, help="Threshold for dark text/line mask")
    parser.add_argument("--green-threshold", type=int, default=150, help="Threshold for green utility line mask")
    parser.add_argument("--blue-threshold", type=int, default=150, help="Threshold for blue annotation mask")
    parser.add_argument("--bridge-kernel", type=int, default=7, help="Kernel size for reconnecting broken corridor segments")
    parser.add_argument("--min-corridor-span-ratio", type=float, default=0.14, help="Minimum component span ratio to keep as corridor")
    parser.add_argument("--width-percentile", type=float, default=70.0, help="Percentile for half-width estimation from support mask")
    parser.add_argument("--max-half-width", type=int, default=40, help="Upper cap for estimated half-width in pixels")
    parser.add_argument("--geodesic-pad", type=int, default=120, help="Padding around endpoint bbox for local geodesic search")
    parser.add_argument("--geo-cost-background", type=float, default=8.0, help="Base geodesic traversal cost in unknown regions")
    parser.add_argument("--geo-cost-support", type=float, default=1.2, help="Geodesic traversal cost in known footpath support regions")
    parser.add_argument("--geo-cost-overlay", type=float, default=0.35, help="Geodesic traversal cost in likely interruption regions (text/green overlays)")
    parser.add_argument("--log-level", default="DEBUG", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def read_footpath_color(legend_json_path: Path) -> tuple[int, int, int]:
    data = json.loads(legend_json_path.read_text(encoding="utf-8"))
    entries = data.get("legend_entries", [])
    for entry in entries:
        label = str(entry.get("label", "")).replace("\n", " ").strip().upper()
        if label == "PROPOSED FOOTPATH" and entry.get("status") == "ok":
            rgb = entry.get("swatch", {}).get("fill_rgb255")
            if isinstance(rgb, list) and len(rgb) == 3:
                return (int(rgb[0]), int(rgb[1]), int(rgb[2]))
    raise RuntimeError("Could not find PROPOSED FOOTPATH fill color in legend JSON")


def pdf_rect_to_px(rect: list[float], scale_x: float, scale_y: float) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = rect
    return (int(round(x0 * scale_x)), int(round(y0 * scale_y)), int(round(x1 * scale_x)), int(round(y1 * scale_y)))


def ensure_cv2() -> None:
    if cv2 is None:
        raise RuntimeError("opencv-python-headless is required. Install from requirements.txt")


def astar_path(cost: np.ndarray, start: tuple[int, int], goal: tuple[int, int]) -> list[tuple[int, int]]:
    """A* on a local cost grid. Coordinates are (y, x)."""
    h, w = cost.shape

    sy, sx = start
    gy, gx = goal
    if not (0 <= sy < h and 0 <= sx < w and 0 <= gy < h and 0 <= gx < w):
        return []

    def heuristic(y: int, x: int) -> float:
        return ((y - gy) ** 2 + (x - gx) ** 2) ** 0.5

    neighbors = [
        (-1, 0, 1.0),
        (1, 0, 1.0),
        (0, -1, 1.0),
        (0, 1, 1.0),
        (-1, -1, 1.4142),
        (-1, 1, 1.4142),
        (1, -1, 1.4142),
        (1, 1, 1.4142),
    ]

    gscore = np.full((h, w), np.inf, dtype=np.float32)
    gscore[sy, sx] = 0.0
    parent: dict[tuple[int, int], tuple[int, int]] = {}

    open_heap: list[tuple[float, int, int]] = [(heuristic(sy, sx), sy, sx)]
    visited = np.zeros((h, w), dtype=np.uint8)

    while open_heap:
        _, y, x = heapq.heappop(open_heap)
        if visited[y, x]:
            continue
        visited[y, x] = 1

        if (y, x) == (gy, gx):
            path: list[tuple[int, int]] = [(y, x)]
            while (y, x) in parent:
                y, x = parent[(y, x)]
                path.append((y, x))
            path.reverse()
            return path

        base_g = float(gscore[y, x])
        for dy, dx, step in neighbors:
            ny, nx = y + dy, x + dx
            if not (0 <= ny < h and 0 <= nx < w):
                continue
            if visited[ny, nx]:
                continue

            cand = base_g + float(cost[ny, nx]) * step
            if cand < float(gscore[ny, nx]):
                gscore[ny, nx] = cand
                parent[(ny, nx)] = (y, x)
                heapq.heappush(open_heap, (cand + heuristic(ny, nx), ny, nx))

    return []


def component_endpoints(bin_mask: np.ndarray) -> tuple[tuple[int, int], tuple[int, int]]:
    coords = np.argwhere(bin_mask > 0)
    if coords.shape[0] == 0:
        return (0, 0), (0, 0)
    if coords.shape[0] == 1:
        y, x = coords[0]
        return (int(y), int(x)), (int(y), int(x))

    pts = coords[:, ::-1].astype(np.float32)  # (x, y)
    mean = pts.mean(axis=0)
    centered = pts - mean
    cov = centered.T @ centered
    eigvals, eigvecs = np.linalg.eigh(cov)
    axis = eigvecs[:, int(np.argmax(eigvals))]
    proj = centered @ axis
    i0 = int(np.argmin(proj))
    i1 = int(np.argmax(proj))

    p0 = coords[i0]
    p1 = coords[i1]
    return (int(p0[0]), int(p0[1])), (int(p1[0]), int(p1[1]))


def run() -> int:
    args = parse_args()
    outdir = Path(args.outdir)
    vis_dir = outdir / "visualizations"
    outdir.mkdir(parents=True, exist_ok=True)
    vis_dir.mkdir(parents=True, exist_ok=True)

    logger = setup_logger(outdir / "run.log", args.log_level)
    ensure_cv2()

    pdf_path = Path(args.pdf)
    legend_path = Path(args.legend_json)
    if not pdf_path.exists():
        logger.error("Input PDF missing: %s", pdf_path)
        return 2
    if not legend_path.exists():
        logger.error("Legend JSON missing: %s", legend_path)
        return 2

    logger.info("STEP 01 | Loading footpath legend color")
    footpath_rgb = read_footpath_color(legend_path)
    logger.info("STEP 01 | Footpath RGB from legend: %s", footpath_rgb)

    logger.info("STEP 02 | Rendering PDF page at %d DPI", args.dpi)
    doc = fitz.open(pdf_path)
    page_index = max(0, args.page - 1)
    if page_index >= len(doc):
        logger.error("Page %d out of range. Total pages: %d", args.page, len(doc))
        return 2

    page = doc[page_index]
    pix = page.get_pixmap(dpi=args.dpi, alpha=False)
    rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3].copy()

    cv2.imwrite(str(vis_dir / "stage_01_page.png"), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))

    logger.info("STEP 03 | Building raw RGB-distance mask")
    target = np.array(footpath_rgb, dtype=np.float32)
    diff = rgb.astype(np.float32) - target[None, None, :]
    dist = np.sqrt((diff * diff).sum(axis=2))
    raw_mask = (dist <= float(args.rgb_threshold)).astype(np.uint8) * 255

    cv2.imwrite(str(vis_dir / "stage_02_raw_mask.png"), raw_mask)

    logger.info("STEP 04 | Removing page furniture regions")
    # Use legend bbox from legend JSON + bottom title strip heuristic.
    legend_data = json.loads(legend_path.read_text(encoding="utf-8"))
    legend_bbox_pdf = legend_data.get("legend_region_bbox")

    clean_mask = raw_mask.copy()
    scale_x = pix.width / page.rect.width
    scale_y = pix.height / page.rect.height

    if isinstance(legend_bbox_pdf, list) and len(legend_bbox_pdf) == 4:
        lx0, ly0, lx1, ly1 = pdf_rect_to_px(legend_bbox_pdf, scale_x, scale_y)
        pad = 10
        lx0 = max(0, lx0 - pad)
        ly0 = max(0, ly0 - pad)
        lx1 = min(pix.width, lx1 + pad)
        ly1 = min(pix.height, ly1 + pad)
        clean_mask[ly0:ly1, lx0:lx1] = 0
        logger.info("STEP 04 | Removed legend block px bbox=(%d,%d,%d,%d)", lx0, ly0, lx1, ly1)

    # Bottom title strip often contains many non-geometry matches.
    title_h = int(round(pix.height * 0.11))
    clean_mask[pix.height - title_h :, :] = 0
    logger.info("STEP 04 | Removed title strip height=%d px", title_h)

    # Light morphology to denoise tiny speckles.
    kernel = np.ones((2, 2), np.uint8)
    clean_mask = cv2.morphologyEx(clean_mask, cv2.MORPH_OPEN, kernel)
    cv2.imwrite(str(vis_dir / "stage_03_clean_mask.png"), clean_mask)

    logger.info("STEP 05 | Connected-components analysis")
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats((clean_mask > 0).astype(np.uint8), connectivity=8)
    logger.info("STEP 05 | Components found (excluding background): %d", max(0, num_labels - 1))

    img_area = float(pix.width * pix.height)
    max_area = float(args.max_area_ratio) * img_area

    comp_stats: list[ComponentStats] = []
    selected_ids: list[int] = []

    debug_img = cv2.cvtColor(rgb.copy(), cv2.COLOR_RGB2BGR)
    sel_mask = np.zeros_like(clean_mask)

    rng = random.Random(7)

    for comp_id in range(1, num_labels):
        x = int(stats[comp_id, cv2.CC_STAT_LEFT])
        y = int(stats[comp_id, cv2.CC_STAT_TOP])
        w = int(stats[comp_id, cv2.CC_STAT_WIDTH])
        h = int(stats[comp_id, cv2.CC_STAT_HEIGHT])
        area = int(stats[comp_id, cv2.CC_STAT_AREA])

        bbox_area = max(1, w * h)
        fill_ratio = float(area) / float(bbox_area)
        thickness_ratio = float(min(w, h)) / float(max(w, h)) if max(w, h) > 0 else 1.0

        keep = True
        reason = "ok"
        if area < int(args.min_area):
            keep = False
            reason = "too_small"
        elif float(area) > max_area:
            keep = False
            reason = "too_large"
        elif fill_ratio > float(args.max_fill_ratio) and thickness_ratio > float(args.max_thickness_ratio):
            keep = False
            reason = "blob_like"

        comp_stats.append(
            ComponentStats(
                component_id=comp_id,
                area_px=area,
                x=x,
                y=y,
                w=w,
                h=h,
                aspect_ratio=round((float(w) / float(max(1, h))), 4),
                fill_ratio=round(fill_ratio, 4),
                keep=keep,
                keep_reason=reason,
            )
        )

        if keep:
            selected_ids.append(comp_id)
            sel_mask[labels == comp_id] = 255
            color = (rng.randint(50, 255), rng.randint(50, 255), rng.randint(50, 255))
            cv2.rectangle(debug_img, (x, y), (x + w, y + h), color, 1)

    cv2.imwrite(str(vis_dir / "stage_04_components_bbox.png"), debug_img)
    cv2.imwrite(str(vis_dir / "stage_05_selected_mask.png"), sel_mask)

    # Choose starting mask for downstream processing.
    if args.seed_mask == "raw":
        seed_mask = clean_mask.copy()
        logger.info("STEP 05B | Seed mask mode=raw (from cleaned raw color mask)")
    else:
        seed_mask = sel_mask.copy()
        logger.info("STEP 05B | Seed mask mode=selected (from component-filtered mask)")

    cv2.imwrite(str(vis_dir / "stage_05b_seed_mask.png"), seed_mask)

    logger.info("STEP 06 | Rendering baseline magenta overlay")
    overlay = rgb.copy()
    # Magenta blend where selected.
    magenta = np.zeros_like(overlay)
    magenta[:, :, 0] = 255
    magenta[:, :, 2] = 255
    m = seed_mask > 0
    overlay[m] = (0.45 * overlay[m] + 0.55 * magenta[m]).astype(np.uint8)

    cv2.imwrite(str(vis_dir / "stage_06_overlay_footpath.png"), cv2.cvtColor(overlay, cv2.COLOR_RGB2BGR))

    logger.info("STEP 07 | Building non-surface overlap mask")
    # Dark text / contour annotations.
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    dark_mask = (gray <= int(args.dark_threshold)).astype(np.uint8) * 255

    # Green utility graphics.
    r = rgb[:, :, 0].astype(np.int16)
    g = rgb[:, :, 1].astype(np.int16)
    b = rgb[:, :, 2].astype(np.int16)
    green_mask = ((g >= int(args.green_threshold)) & (g - r >= 40) & (g - b >= 35)).astype(np.uint8) * 255

    # Blue annotations.
    blue_mask = ((b >= int(args.blue_threshold)) & (b - r >= 20) & (b - g >= 12)).astype(np.uint8) * 255

    non_surface_mask = cv2.bitwise_or(dark_mask, cv2.bitwise_or(green_mask, blue_mask))
    # Slight expansion so line overlaps are removed reliably.
    non_surface_mask = cv2.dilate(non_surface_mask, np.ones((2, 2), np.uint8), iterations=1)
    cv2.imwrite(str(vis_dir / "stage_07_non_surface_mask.png"), non_surface_mask)

    logger.info("STEP 08 | Resolving overlaps and healing strip continuity")
    resolved_mask = cv2.bitwise_and(seed_mask, cv2.bitwise_not(non_surface_mask))
    # Heal narrow cuts caused by line/text overlaps.
    resolved_mask = cv2.morphologyEx(resolved_mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    resolved_mask = cv2.morphologyEx(resolved_mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    cv2.imwrite(str(vis_dir / "stage_08_overlap_resolved_mask.png"), resolved_mask)

    logger.info("STEP 08C | Width-aware path reconstruction from resolved mask")
    # Build local support around resolved mask from the raw-cleaned color mask.
    guide = cv2.dilate(resolved_mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (19, 19)))
    support_mask = cv2.bitwise_and(clean_mask, guide)

    half_width_px = 6
    if np.any(support_mask > 0):
        dist_map = cv2.distanceTransform((support_mask > 0).astype(np.uint8), cv2.DIST_L2, 5)
        vals = dist_map[dist_map > 0]
        if vals.size > 0:
            pct = float(max(5.0, min(95.0, args.width_percentile)))
            half_width_px = int(round(float(np.percentile(vals, pct))))
            half_width_px = max(2, min(int(args.max_half_width), half_width_px))

    logger.info("STEP 08C | Estimated half-width=%d px", half_width_px)

    w_kernel = max(3, 2 * int(half_width_px) + 1)
    reconstruct_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (w_kernel, w_kernel))
    reconstructed_mask = cv2.dilate(resolved_mask, reconstruct_kernel)

    # Constrain reconstruction to local neighborhood of support to avoid flooding large regions.
    env_kernel = max(5, 2 * int(half_width_px * 3) + 1)
    support_env = cv2.dilate(support_mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (env_kernel, env_kernel)))
    reconstructed_mask = cv2.bitwise_and(reconstructed_mask, support_env)
    reconstructed_mask = cv2.morphologyEx(reconstructed_mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
    cv2.imwrite(str(vis_dir / "stage_08c_reconstructed_mask.png"), reconstructed_mask)

    logger.info("STEP 08D | Centerline best-fit reconstruction from raw support")
    # Build a support mask from raw-cleaned data with non-surface overlaps removed.
    support_line = cv2.bitwise_and(clean_mask, cv2.bitwise_not(non_surface_mask))
    support_line = cv2.morphologyEx(support_line, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))

    dist_support = cv2.distanceTransform((support_line > 0).astype(np.uint8), cv2.DIST_L2, 5)
    local_max = dist_support >= cv2.dilate(dist_support, np.ones((3, 3), np.uint8))
    center_seed = np.zeros_like(support_line)
    center_seed[(local_max) & (dist_support >= 1.5)] = 255

    # Connect fragmented center points into a continuous path candidate.
    centerline = cv2.morphologyEx(center_seed, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    centerline = cv2.morphologyEx(centerline, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))

    n3, labels3, stats3, _ = cv2.connectedComponentsWithStats((centerline > 0).astype(np.uint8), connectivity=8)
    centerline_filtered = np.zeros_like(centerline)
    center_components_kept = 0
    min_center_span = int(round(max(pix.width, pix.height) * 0.03))
    for cid in range(1, n3):
        w = int(stats3[cid, cv2.CC_STAT_WIDTH])
        h = int(stats3[cid, cv2.CC_STAT_HEIGHT])
        area = int(stats3[cid, cv2.CC_STAT_AREA])
        span = max(w, h)
        if area < max(40, int(args.min_area // 3)):
            continue
        if span < min_center_span:
            continue
        centerline_filtered[labels3 == cid] = 255
        center_components_kept += 1

    cv2.imwrite(str(vis_dir / "stage_08d_centerline_mask.png"), centerline_filtered)

    logger.info("STEP 08G | Geodesic linking of broken centerline components")
    geodesic_links = 0
    geodesic_mask = np.zeros_like(centerline_filtered)

    # Link from pre-merged center seeds (higher fragmentation -> better gap linking signal).
    n4, labels4, stats4, _ = cv2.connectedComponentsWithStats((center_seed > 0).astype(np.uint8), connectivity=8)
    comp_ids = []
    min_seed_span = int(round(max(pix.width, pix.height) * 0.008))
    for cid in range(1, n4):
        area = int(stats4[cid, cv2.CC_STAT_AREA])
        w = int(stats4[cid, cv2.CC_STAT_WIDTH])
        h = int(stats4[cid, cv2.CC_STAT_HEIGHT])
        span = max(w, h)
        if area < 8:
            continue
        if span < min_seed_span:
            continue
        comp_ids.append(cid)
    comp_ids = sorted(comp_ids, key=lambda c: int(stats4[c, cv2.CC_STAT_AREA]), reverse=True)

    if len(comp_ids) >= 2:
        # Link top components by nearest endpoint pair.
        main_ids = comp_ids[:8]
        endpoints: dict[int, tuple[tuple[int, int], tuple[int, int]]] = {}
        for cid in main_ids:
            endpoints[cid] = component_endpoints((labels4 == cid).astype(np.uint8))

        # Inverse bridging hypothesis:
        # interruption zones (text/green overlays) are likely where the path was cut,
        # so they get lower geodesic cost to encourage reconnecting across them.
        base_cost = np.full_like(centerline_filtered, float(args.geo_cost_background), dtype=np.float32)
        base_cost[support_line > 0] = float(args.geo_cost_support)
        base_cost[non_surface_mask > 0] = float(args.geo_cost_overlay)

        used_pairs: set[tuple[int, int]] = set()
        for i in range(len(main_ids) - 1):
            a = main_ids[i]
            # Connect each component to the closest remaining component.
            best = None
            for b in main_ids[i + 1 :]:
                key = tuple(sorted((a, b)))
                if key in used_pairs:
                    continue
                ea0, ea1 = endpoints[a]
                eb0, eb1 = endpoints[b]
                cand = [
                    (ea0, eb0),
                    (ea0, eb1),
                    (ea1, eb0),
                    (ea1, eb1),
                ]
                dmin = min(((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2) ** 0.5 for p, q in cand)
                if best is None or dmin < best[0]:
                    best = (dmin, b, cand)

            if best is None:
                continue

            _, b, cand_pairs = best
            used_pairs.add(tuple(sorted((a, b))))

            # Choose endpoint pair with minimal Euclidean distance.
            pair = min(cand_pairs, key=lambda pq: ((pq[0][0] - pq[1][0]) ** 2 + (pq[0][1] - pq[1][1]) ** 2) ** 0.5)
            p, q = pair

            pad = int(args.geodesic_pad)
            y0 = max(0, min(p[0], q[0]) - pad)
            y1 = min(pix.height, max(p[0], q[0]) + pad)
            x0 = max(0, min(p[1], q[1]) - pad)
            x1 = min(pix.width, max(p[1], q[1]) + pad)
            if y1 <= y0 or x1 <= x0:
                continue

            local_cost = base_cost[y0:y1, x0:x1]
            local_start = (p[0] - y0, p[1] - x0)
            local_goal = (q[0] - y0, q[1] - x0)

            path = astar_path(local_cost, local_start, local_goal)
            if not path:
                continue

            pts = np.array([(x + x0, y + y0) for (y, x) in path], dtype=np.int32)
            if pts.shape[0] >= 2:
                cv2.polylines(geodesic_mask, [pts.reshape(-1, 1, 2)], isClosed=False, color=255, thickness=2)
                geodesic_links += 1

    centerline_geodesic = cv2.bitwise_or(centerline_filtered, geodesic_mask)
    centerline_geodesic = cv2.morphologyEx(centerline_geodesic, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))

    cv2.imwrite(str(vis_dir / "stage_08g_centerline_geodesic_mask.png"), centerline_geodesic)

    center_half_width_px = half_width_px
    center_vals = dist_support[centerline_geodesic > 0]
    if center_vals.size > 0:
        pct = float(max(5.0, min(95.0, args.width_percentile)))
        center_half_width_px = int(round(float(np.percentile(center_vals, pct))))
        center_half_width_px = max(2, min(int(args.max_half_width), center_half_width_px))

    logger.info("STEP 08D | Centerline half-width=%d px kept_components=%d", center_half_width_px, center_components_kept)

    line_kernel = max(3, 2 * int(center_half_width_px) + 1)
    reconstructed_from_centerline = cv2.dilate(
        centerline_geodesic,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (line_kernel, line_kernel)),
    )
    line_env = cv2.dilate(
        support_line,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (max(7, line_kernel * 3), max(7, line_kernel * 3))),
    )
    reconstructed_from_centerline = cv2.bitwise_and(reconstructed_from_centerline, line_env)
    reconstructed_from_centerline = cv2.morphologyEx(
        reconstructed_from_centerline,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)),
    )
    cv2.imwrite(str(vis_dir / "stage_08e_centerline_reconstructed_mask.png"), reconstructed_from_centerline)

    logger.info("STEP 08B | Corridor continuity enforcement")
    k = max(3, int(args.bridge_kernel) | 1)
    bridge_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    combined_reconstruction = cv2.bitwise_or(reconstructed_mask, reconstructed_from_centerline)
    cv2.imwrite(str(vis_dir / "stage_08f_combined_reconstruction.png"), combined_reconstruction)

    bridged = cv2.morphologyEx(combined_reconstruction, cv2.MORPH_CLOSE, bridge_kernel)
    bridged = cv2.erode(bridged, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)), iterations=1)

    n2, labels2, stats2, _ = cv2.connectedComponentsWithStats((bridged > 0).astype(np.uint8), connectivity=8)
    corridor_mask = np.zeros_like(bridged)
    min_span = int(round(max(pix.width, pix.height) * float(args.min_corridor_span_ratio)))
    kept_corridors = 0
    for cid in range(1, n2):
        w = int(stats2[cid, cv2.CC_STAT_WIDTH])
        h = int(stats2[cid, cv2.CC_STAT_HEIGHT])
        area = int(stats2[cid, cv2.CC_STAT_AREA])
        span = max(w, h)

        if area < int(args.min_area):
            continue
        if span < min_span:
            continue

        corridor_mask[labels2 == cid] = 255
        kept_corridors += 1

    cv2.imwrite(str(vis_dir / "stage_08b_corridor_mask.png"), corridor_mask)

    logger.info("STEP 09 | Polygon preview from resolved mask")
    contours, _ = cv2.findContours((corridor_mask > 0).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    poly_overlay = rgb.copy()
    poly_fill = np.zeros_like(poly_overlay)
    kept_contours: list[np.ndarray] = []
    min_poly_area = max(120.0, float(args.min_area))
    for c in contours:
        a = float(cv2.contourArea(c))
        if a < min_poly_area:
            continue
        kept_contours.append(c)
        cv2.drawContours(poly_fill, [c], -1, (255, 0, 255), thickness=cv2.FILLED)

    mask_poly = np.any(poly_fill > 0, axis=2)
    poly_overlay[mask_poly] = (0.45 * poly_overlay[mask_poly] + 0.55 * poly_fill[mask_poly]).astype(np.uint8)
    for c in kept_contours:
        cv2.drawContours(poly_overlay, [c], -1, (255, 0, 255), thickness=2)

    cv2.imwrite(str(vis_dir / "stage_09_polygon_preview.png"), cv2.cvtColor(poly_overlay, cv2.COLOR_RGB2BGR))

    logger.info("STEP 10 | Writing diagnostics JSON")
    output = {
        "source_pdf": str(pdf_path),
        "page": args.page,
        "dpi": args.dpi,
        "footpath_rgb": footpath_rgb,
        "params": {
            "rgb_threshold": args.rgb_threshold,
            "seed_mask": args.seed_mask,
            "min_area": args.min_area,
            "max_area_ratio": args.max_area_ratio,
            "max_fill_ratio": args.max_fill_ratio,
            "max_thickness_ratio": args.max_thickness_ratio,
            "dark_threshold": args.dark_threshold,
            "green_threshold": args.green_threshold,
            "blue_threshold": args.blue_threshold,
            "bridge_kernel": args.bridge_kernel,
            "min_corridor_span_ratio": args.min_corridor_span_ratio,
            "width_percentile": args.width_percentile,
            "max_half_width": args.max_half_width,
            "geodesic_pad": args.geodesic_pad,
            "geo_cost_background": args.geo_cost_background,
            "geo_cost_support": args.geo_cost_support,
            "geo_cost_overlay": args.geo_cost_overlay,
            "estimated_half_width_px": half_width_px,
            "centerline_half_width_px": center_half_width_px,
        },
        "counts": {
            "components_total": max(0, num_labels - 1),
            "components_selected": len(selected_ids),
            "selected_pixel_count": int((sel_mask > 0).sum()),
            "seed_pixel_count": int((seed_mask > 0).sum()),
            "non_surface_pixel_count": int((non_surface_mask > 0).sum()),
            "resolved_pixel_count": int((resolved_mask > 0).sum()),
            "reconstructed_pixel_count": int((reconstructed_mask > 0).sum()),
            "centerline_pixel_count": int((centerline_filtered > 0).sum()),
            "geodesic_link_count": geodesic_links,
            "centerline_geodesic_pixel_count": int((centerline_geodesic > 0).sum()),
            "centerline_reconstructed_pixel_count": int((reconstructed_from_centerline > 0).sum()),
            "combined_reconstruction_pixel_count": int((combined_reconstruction > 0).sum()),
            "corridor_pixel_count": int((corridor_mask > 0).sum()),
            "corridor_components_kept": kept_corridors,
            "polygon_count": len(kept_contours),
        },
        "components": [asdict(c) for c in comp_stats],
        "selected_component_ids": selected_ids,
        "artifacts": {
            "stage_01_page": str(vis_dir / "stage_01_page.png"),
            "stage_02_raw_mask": str(vis_dir / "stage_02_raw_mask.png"),
            "stage_03_clean_mask": str(vis_dir / "stage_03_clean_mask.png"),
            "stage_04_components_bbox": str(vis_dir / "stage_04_components_bbox.png"),
            "stage_05_selected_mask": str(vis_dir / "stage_05_selected_mask.png"),
            "stage_05b_seed_mask": str(vis_dir / "stage_05b_seed_mask.png"),
            "stage_06_overlay_footpath": str(vis_dir / "stage_06_overlay_footpath.png"),
            "stage_07_non_surface_mask": str(vis_dir / "stage_07_non_surface_mask.png"),
            "stage_08_overlap_resolved_mask": str(vis_dir / "stage_08_overlap_resolved_mask.png"),
            "stage_08c_reconstructed_mask": str(vis_dir / "stage_08c_reconstructed_mask.png"),
            "stage_08d_centerline_mask": str(vis_dir / "stage_08d_centerline_mask.png"),
            "stage_08g_centerline_geodesic_mask": str(vis_dir / "stage_08g_centerline_geodesic_mask.png"),
            "stage_08e_centerline_reconstructed_mask": str(vis_dir / "stage_08e_centerline_reconstructed_mask.png"),
            "stage_08f_combined_reconstruction": str(vis_dir / "stage_08f_combined_reconstruction.png"),
            "stage_08b_corridor_mask": str(vis_dir / "stage_08b_corridor_mask.png"),
            "stage_09_polygon_preview": str(vis_dir / "stage_09_polygon_preview.png"),
        },
    }

    (outdir / "footpath_pixel_pipeline.json").write_text(json.dumps(output, indent=2), encoding="utf-8")

    logger.info("STEP 11 | Done. Selected components=%d polygons=%d", len(selected_ids), len(kept_contours))
    logger.info("STEP 11 | JSON: %s", outdir / "footpath_pixel_pipeline.json")

    return 0


if __name__ == "__main__":
    raise SystemExit(run())

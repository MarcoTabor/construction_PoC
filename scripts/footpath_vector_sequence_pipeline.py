#!/usr/bin/env python3
"""Footpath extraction: vector + draw-order continuity pipeline.

This stage sits after pixel-first exploration:
1) Use pixel result only for AOI proposal.
2) Build vector candidate mask from footpath-colored drawing objects.
3) Build overlay mask from later, non-surface objects (text/utility-like).
4) Resolve with sequence-aware subtraction.
5) Enforce continuity and keep dominant ribbon component.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from pathlib import Path
from typing import Any

import fitz
import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


def setup_logger(log_path: Path, level: str) -> logging.Logger:
    logger = logging.getLogger("footpath_vector_sequence_pipeline")
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
    parser = argparse.ArgumentParser(description="Vector sequence-aware footpath extraction")
    parser.add_argument("--pdf", default="examples/Joal 502.pdf")
    parser.add_argument("--legend-json", default="outputs/legend_colors/legend_colors.json")
    parser.add_argument("--pixel-json", default="outputs/footpath_pixel_pipeline/footpath_pixel_pipeline.json")
    parser.add_argument("--outdir", default="outputs/footpath_vector_sequence")
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--color-threshold", type=float, default=0.035, help="Color distance threshold in normalized RGB space")
    parser.add_argument("--max-candidate-thickness-pt", type=float, default=8.0, help="Max short-side thickness for candidate vector rectangles")
    parser.add_argument("--min-candidate-span-pt", type=float, default=5.0, help="Min long-side span for candidate vector rectangles")
    parser.add_argument("--bridge-kernel", type=int, default=11)
    parser.add_argument("--min-component-area", type=int, default=200)
    parser.add_argument("--log-level", default="DEBUG", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def ensure_cv2() -> None:
    if cv2 is None:
        raise RuntimeError("opencv-python-headless is required. Install from requirements.txt")


def nrm(c: Any) -> tuple[float, float, float] | None:
    if isinstance(c, (list, tuple)) and len(c) >= 3:
        return (float(c[0]), float(c[1]), float(c[2]))
    return None


def dist(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def read_footpath_color(legend_path: Path) -> tuple[float, float, float]:
    data = json.loads(legend_path.read_text(encoding="utf-8"))
    for entry in data.get("legend_entries", []):
        label = str(entry.get("label", "")).replace("\n", " ").strip().upper()
        if label == "PROPOSED FOOTPATH" and entry.get("status") == "ok":
            fill = entry.get("swatch", {}).get("fill")
            c = nrm(fill)
            if c:
                return c
    raise RuntimeError("PROPOSED FOOTPATH color not found in legend JSON")


def rect_to_px(rect: list[float], sx: float, sy: float, w: int, h: int) -> tuple[int, int, int, int]:
    x0 = max(0, min(w, int(round(rect[0] * sx))))
    y0 = max(0, min(h, int(round(rect[1] * sy))))
    x1 = max(0, min(w, int(round(rect[2] * sx))))
    y1 = max(0, min(h, int(round(rect[3] * sy))))
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    return x0, y0, x1, y1


def compute_aoi(pixel_json_path: Path, fallback_w: int, fallback_h: int) -> tuple[int, int, int, int]:
    if not pixel_json_path.exists():
        return (0, 0, fallback_w, fallback_h)

    data = json.loads(pixel_json_path.read_text(encoding="utf-8"))
    comps = [c for c in data.get("components", []) if c.get("keep")]
    if not comps:
        return (0, 0, fallback_w, fallback_h)

    x0 = min(int(c["x"]) for c in comps)
    y0 = min(int(c["y"]) for c in comps)
    x1 = max(int(c["x"]) + int(c["w"]) for c in comps)
    y1 = max(int(c["y"]) + int(c["h"]) for c in comps)

    pad_x = int(round(0.03 * fallback_w))
    pad_y = int(round(0.03 * fallback_h))

    return (
        max(0, x0 - pad_x),
        max(0, y0 - pad_y),
        min(fallback_w, x1 + pad_x),
        min(fallback_h, y1 + pad_y),
    )


def is_overlay_candidate(
    d: dict[str, Any],
    c_fill: tuple[float, float, float] | None,
    c_stroke: tuple[float, float, float] | None,
    rect_w: float,
    rect_h: float,
) -> bool:
    width = float(d.get("width") or 0.0)
    short_side = min(rect_w, rect_h)
    long_side = max(rect_w, rect_h)

    # Only treat thin objects as overlays; broad fills should not erase candidate surface.
    if short_side > 4.0 or long_side < 4.0:
        return False

    # Green utility overlays.
    for c in (c_fill, c_stroke):
        if c and c[1] > 0.58 and (c[1] - c[0]) > 0.18 and (c[1] - c[2]) > 0.15:
            return True

    # Blue annotation overlays.
    for c in (c_fill, c_stroke):
        if c and c[2] > 0.60 and (c[2] - c[0]) > 0.10 and (c[2] - c[1]) > 0.08:
            return True

    # Thin dark strokes likely text/leader/contour overlays.
    if c_stroke and max(c_stroke) < 0.30 and width <= 1.2 and c_fill is None:
        return True

    return False


def run() -> int:
    args = parse_args()
    outdir = Path(args.outdir)
    vis = outdir / "visualizations"
    outdir.mkdir(parents=True, exist_ok=True)
    vis.mkdir(parents=True, exist_ok=True)

    logger = setup_logger(outdir / "run.log", args.log_level)
    ensure_cv2()

    pdf_path = Path(args.pdf)
    legend_path = Path(args.legend_json)
    pixel_json_path = Path(args.pixel_json)

    if not pdf_path.exists():
        logger.error("Missing PDF: %s", pdf_path)
        return 2
    if not legend_path.exists():
        logger.error("Missing legend JSON: %s", legend_path)
        return 2

    logger.info("STEP 01 | Load footpath color and render page")
    footpath_color = read_footpath_color(legend_path)

    doc = fitz.open(pdf_path)
    page_index = max(0, args.page - 1)
    if page_index >= len(doc):
        logger.error("Page out of range: %d", args.page)
        return 2

    page = doc[page_index]
    pix = page.get_pixmap(dpi=args.dpi, alpha=False)
    rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3].copy()

    cv2.imwrite(str(vis / "stage_01_page.png"), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))

    sx = pix.width / page.rect.width
    sy = pix.height / page.rect.height

    logger.info("STEP 02 | Determine AOI from pixel pipeline output")
    aoi = compute_aoi(pixel_json_path, pix.width, pix.height)
    logger.info("STEP 02 | AOI px bbox=%s", aoi)

    aoi_mask = np.zeros((pix.height, pix.width), dtype=np.uint8)
    aoi_mask[aoi[1] : aoi[3], aoi[0] : aoi[2]] = 255
    cv2.imwrite(str(vis / "stage_02_aoi_mask.png"), aoi_mask)

    logger.info("STEP 03 | Build vector candidate mask from footpath-colored drawings")
    drawings = sorted(page.get_drawings(), key=lambda x: int(x.get("seqno", 0)))
    candidate_mask = np.zeros((pix.height, pix.width), dtype=np.uint8)
    candidate_seq = np.full((pix.height, pix.width), -1, dtype=np.int32)

    matches = 0
    earliest_seq = None

    for d in drawings:
        rect = d.get("rect")
        if rect is None:
            continue
        bbox = [float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)]
        rect_w_pt = abs(bbox[2] - bbox[0])
        rect_h_pt = abs(bbox[3] - bbox[1])
        short_side_pt = min(rect_w_pt, rect_h_pt)
        long_side_pt = max(rect_w_pt, rect_h_pt)

        if short_side_pt > float(args.max_candidate_thickness_pt):
            continue
        if long_side_pt < float(args.min_candidate_span_pt):
            continue

        x0, y0, x1, y1 = rect_to_px(bbox, sx, sy, pix.width, pix.height)
        if x1 <= x0 or y1 <= y0:
            continue

        # AOI gate.
        if x1 < aoi[0] or x0 > aoi[2] or y1 < aoi[1] or y0 > aoi[3]:
            continue

        c_fill = nrm(d.get("fill"))
        c_stroke = nrm(d.get("color"))

        is_match = False
        if c_fill and dist(c_fill, footpath_color) <= float(args.color_threshold):
            is_match = True
        if c_stroke and dist(c_stroke, footpath_color) <= float(args.color_threshold):
            is_match = True
        if not is_match:
            continue

        seq = int(d.get("seqno", 0))
        matches += 1
        if earliest_seq is None or seq < earliest_seq:
            earliest_seq = seq

        candidate_mask[y0:y1, x0:x1] = 255
        current = candidate_seq[y0:y1, x0:x1]
        update = (current < 0) | (seq < current)
        current[update] = seq
        candidate_seq[y0:y1, x0:x1] = current

    cv2.imwrite(str(vis / "stage_03_vector_candidate_mask.png"), candidate_mask)
    logger.info("STEP 03 | Candidate draw matches=%d earliest_seq=%s", matches, earliest_seq)

    logger.info("STEP 04 | Build sequence-aware overlay mask from later non-surface drawings")
    overlay_mask = np.zeros((pix.height, pix.width), dtype=np.uint8)
    overlay_hits = 0

    for d in drawings:
        seq = int(d.get("seqno", 0))
        if earliest_seq is not None and seq <= earliest_seq:
            continue

        rect = d.get("rect")
        if rect is None:
            continue
        bbox = [float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)]
        rect_w_pt = abs(bbox[2] - bbox[0])
        rect_h_pt = abs(bbox[3] - bbox[1])
        x0, y0, x1, y1 = rect_to_px(bbox, sx, sy, pix.width, pix.height)
        if x1 <= x0 or y1 <= y0:
            continue

        if x1 < aoi[0] or x0 > aoi[2] or y1 < aoi[1] or y0 > aoi[3]:
            continue

        c_fill = nrm(d.get("fill"))
        c_stroke = nrm(d.get("color"))
        if not is_overlay_candidate(d, c_fill, c_stroke, rect_w_pt, rect_h_pt):
            continue

        overlay_mask[y0:y1, x0:x1] = 255
        overlay_hits += 1

    overlay_mask = cv2.dilate(overlay_mask, np.ones((2, 2), np.uint8), iterations=1)
    cv2.imwrite(str(vis / "stage_04_overlay_mask.png"), overlay_mask)
    logger.info("STEP 04 | Overlay draw hits=%d", overlay_hits)

    logger.info("STEP 05 | Resolve overlaps and enforce continuity")
    resolved = cv2.bitwise_and(candidate_mask, cv2.bitwise_not(overlay_mask))
    k = max(3, int(args.bridge_kernel) | 1)
    resolved = cv2.morphologyEx(resolved, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    resolved = cv2.morphologyEx(resolved, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    resolved = cv2.bitwise_and(resolved, aoi_mask)
    cv2.imwrite(str(vis / "stage_05_resolved_mask.png"), resolved)

    logger.info("STEP 06 | Keep dominant ribbon-like connected component")
    n, labels, stats, _ = cv2.connectedComponentsWithStats((resolved > 0).astype(np.uint8), connectivity=8)
    final_mask = np.zeros_like(resolved)
    kept = []

    for cid in range(1, n):
        x = int(stats[cid, cv2.CC_STAT_LEFT])
        y = int(stats[cid, cv2.CC_STAT_TOP])
        w = int(stats[cid, cv2.CC_STAT_WIDTH])
        h = int(stats[cid, cv2.CC_STAT_HEIGHT])
        a = int(stats[cid, cv2.CC_STAT_AREA])
        if a < int(args.min_component_area):
            continue
        span = max(w, h)
        thickness = min(w, h)
        score = float(span) * (1.0 + 0.15 * math.log(max(a, 2))) - 0.4 * float(thickness)
        kept.append((score, cid, x, y, w, h, a))

    kept.sort(reverse=True)
    if kept:
        best_cid = kept[0][1]
        final_mask[labels == best_cid] = 255
        # Include near-touching fragments to preserve continuity.
        for _, cid, x, y, w, h, a in kept[1:]:
            comp = np.zeros_like(final_mask)
            comp[labels == cid] = 255
            dil = cv2.dilate(final_mask, np.ones((5, 5), np.uint8), iterations=1)
            if np.any((dil > 0) & (comp > 0)):
                final_mask[labels == cid] = 255

    cv2.imwrite(str(vis / "stage_06_final_mask.png"), final_mask)

    logger.info("STEP 07 | Polygon preview overlay")
    contours, _ = cv2.findContours((final_mask > 0).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    poly_overlay = rgb.copy()
    poly_fill = np.zeros_like(poly_overlay)
    poly_count = 0
    for c in contours:
        area = float(cv2.contourArea(c))
        if area < float(args.min_component_area):
            continue
        cv2.drawContours(poly_fill, [c], -1, (255, 0, 255), thickness=cv2.FILLED)
        cv2.drawContours(poly_overlay, [c], -1, (255, 0, 255), thickness=2)
        poly_count += 1

    mk = np.any(poly_fill > 0, axis=2)
    poly_overlay[mk] = (0.45 * poly_overlay[mk] + 0.55 * poly_fill[mk]).astype(np.uint8)
    cv2.imwrite(str(vis / "stage_07_polygon_preview.png"), cv2.cvtColor(poly_overlay, cv2.COLOR_RGB2BGR))

    logger.info("STEP 08 | Write diagnostics")
    out = {
        "source_pdf": str(pdf_path),
        "page": args.page,
        "dpi": args.dpi,
        "footpath_color_norm": footpath_color,
        "params": {
            "color_threshold": args.color_threshold,
            "max_candidate_thickness_pt": args.max_candidate_thickness_pt,
            "min_candidate_span_pt": args.min_candidate_span_pt,
            "bridge_kernel": args.bridge_kernel,
            "min_component_area": args.min_component_area,
        },
        "counts": {
            "drawings_total": len(drawings),
            "candidate_draw_matches": matches,
            "overlay_draw_matches": overlay_hits,
            "connected_components": max(0, n - 1),
            "polygons": poly_count,
            "final_pixel_count": int((final_mask > 0).sum()),
        },
        "aoi": {
            "x0": aoi[0],
            "y0": aoi[1],
            "x1": aoi[2],
            "y1": aoi[3],
        },
        "artifacts": {
            "stage_01_page": str(vis / "stage_01_page.png"),
            "stage_02_aoi_mask": str(vis / "stage_02_aoi_mask.png"),
            "stage_03_vector_candidate_mask": str(vis / "stage_03_vector_candidate_mask.png"),
            "stage_04_overlay_mask": str(vis / "stage_04_overlay_mask.png"),
            "stage_05_resolved_mask": str(vis / "stage_05_resolved_mask.png"),
            "stage_06_final_mask": str(vis / "stage_06_final_mask.png"),
            "stage_07_polygon_preview": str(vis / "stage_07_polygon_preview.png"),
        },
    }

    (outdir / "footpath_vector_sequence_pipeline.json").write_text(json.dumps(out, indent=2), encoding="utf-8")

    logger.info("STEP 09 | Done. polygons=%d final_pixels=%d", poly_count, int((final_mask > 0).sum()))
    logger.info("STEP 09 | JSON: %s", outdir / "footpath_vector_sequence_pipeline.json")

    return 0


if __name__ == "__main__":
    raise SystemExit(run())

#!/usr/bin/env python3
"""Focused detector for drawing scale label + scale bar with verbose logging.

This script intentionally does one thing only:
- Find the scale label block (e.g., A3 Scale 1: 500)
- Find nearby bar graphics and tick labels (e.g., 0, 10, 25)
- Emit JSON evidence and highlighted PNG outputs
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fitz

try:
    from PIL import Image, ImageDraw
except ImportError:  # pragma: no cover
    Image = None
    ImageDraw = None


@dataclass
class ScaleToken:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float


def setup_logger(log_path: Path, level: str) -> logging.Logger:
    logger = logging.getLogger("scale_detector")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(formatter)
    logger.addHandler(sh)

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    return logger


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Find and visualize drawing scale bar")
    parser.add_argument("--pdf", default="examples/Joal 502.pdf", help="Input PDF path")
    parser.add_argument("--outdir", default="outputs/scale_detection", help="Output directory")
    parser.add_argument("--log-level", default="DEBUG", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def to_rect(tokens: list[ScaleToken], pad: float = 0.0) -> list[float] | None:
    if not tokens:
        return None
    x0 = min(t.x0 for t in tokens) - pad
    y0 = min(t.y0 for t in tokens) - pad
    x1 = max(t.x1 for t in tokens) + pad
    y1 = max(t.y1 for t in tokens) + pad
    return [x0, y0, x1, y1]


def rect_union(a: list[float], b: list[float]) -> list[float]:
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]


def intersect(a: list[float], b: list[float]) -> bool:
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def detect_scale_block(words: list[tuple[Any, ...]], logger: logging.Logger) -> dict[str, Any]:
    logger.info("STEP 03 | Locating scale-related tokens")

    tokens = [
        ScaleToken(text=str(w[4]), x0=float(w[0]), y0=float(w[1]), x1=float(w[2]), y1=float(w[3]))
        for w in words
    ]

    primary = [t for t in tokens if t.text.lower() in {"a3", "scale", "1:"}]
    ratio_value = [t for t in tokens if re.fullmatch(r"\d{2,5}", t.text)]
    tick_values = [t for t in tokens if t.text in {"0", "5", "10", "20", "25", "50"}]

    logger.info("STEP 03 | Primary scale tokens=%d", len(primary))
    logger.info("STEP 03 | Candidate ratio values=%d", len(ratio_value))
    logger.info("STEP 03 | Candidate tick labels=%d", len(tick_values))

    if not primary:
        return {
            "status": "not_found",
            "reason": "missing_primary_scale_tokens",
            "label_tokens": [],
            "label_bbox": None,
            "tick_tokens": [],
            "tick_bbox": None,
            "search_bbox": None,
            "scale_text": None,
        }

    # Anchor explicitly on a local pair of A3 and Scale tokens.
    a3_tokens = [t for t in tokens if t.text.lower() == "a3"]
    scale_tokens = [t for t in tokens if t.text.lower() == "scale"]
    if not a3_tokens or not scale_tokens:
        return {
            "status": "not_found",
            "reason": "missing_a3_or_scale",
            "label_tokens": [],
            "label_bbox": None,
            "tick_tokens": [],
            "tick_bbox": None,
            "search_bbox": None,
            "scale_text": None,
        }

    best_pair: tuple[ScaleToken, ScaleToken] | None = None
    best_dist = float("inf")
    for a3 in a3_tokens:
        for scale in scale_tokens:
            if abs(a3.y0 - scale.y0) > 5.0:
                continue
            if scale.x0 < a3.x0:
                continue
            dist = scale.x0 - a3.x0
            if dist < best_dist:
                best_dist = dist
                best_pair = (a3, scale)

    if best_pair is None:
        return {
            "status": "not_found",
            "reason": "unable_to_pair_a3_and_scale",
            "label_tokens": [],
            "label_bbox": None,
            "tick_tokens": [],
            "tick_bbox": None,
            "search_bbox": None,
            "scale_text": None,
        }

    a3, scale = best_pair
    anchor_y = (a3.y0 + scale.y0) / 2.0
    label_tokens = [a3, scale]

    # Add ratio separator and value close to the A3/Scale anchor.
    for t in tokens:
        if t in label_tokens:
            continue
        if abs(t.y0 - anchor_y) > 6.0:
            continue
        if t.text == "1:" and scale.x0 <= t.x0 <= scale.x1 + 40:
            label_tokens.append(t)

    ratio_anchor = next((t for t in label_tokens if t.text == "1:"), None)
    if ratio_anchor is None:
        ratio_anchor = scale

    ratio_candidates = [
        num
        for num in ratio_value
        if abs(num.y0 - anchor_y) <= 8.0 and ratio_anchor.x1 <= num.x0 <= ratio_anchor.x1 + 40.0
    ]
    if ratio_candidates:
        nearest_ratio = min(ratio_candidates, key=lambda t: (t.x0 - ratio_anchor.x1))
        label_tokens.append(nearest_ratio)

    # De-duplicate in case an item matches multiple conditions.
    uniq = {(t.text, t.x0, t.y0, t.x1, t.y1): t for t in label_tokens}
    label_tokens = list(uniq.values())

    label_bbox = to_rect(label_tokens, pad=2.0)
    tick_tokens = [
        t
        for t in tick_values
        if label_bbox
        and t.x0 >= label_bbox[2] + 2.0
        and (label_bbox[1] - 12.0) <= t.y0 <= (label_bbox[3] + 4.0)
    ]
    tick_bbox = to_rect(tick_tokens, pad=2.0)

    search_bbox = None
    if label_bbox and tick_bbox:
        search_bbox = rect_union(label_bbox, tick_bbox)
        # Extend downwards to include the bar under tick labels.
        search_bbox[0] = max(0.0, search_bbox[0] - 2.0)
        search_bbox[1] = max(0.0, search_bbox[1] - 6.0)
        search_bbox[2] = search_bbox[2] + 2.0
        search_bbox[3] = search_bbox[3] + 14.0
    elif label_bbox:
        search_bbox = [label_bbox[2] + 4.0, label_bbox[1] - 6.0, label_bbox[2] + 220.0, label_bbox[3] + 14.0]

    label_text = " ".join(t.text for t in sorted(label_tokens, key=lambda t: t.x0)).strip() if label_tokens else None

    logger.debug("STEP 03 | Label text candidate: %s", label_text)
    logger.debug("STEP 03 | Label bbox: %s", label_bbox)
    logger.debug("STEP 03 | Tick bbox: %s", tick_bbox)
    logger.debug("STEP 03 | Search bbox: %s", search_bbox)

    return {
        "status": "found" if label_bbox else "partial",
        "reason": None,
        "label_tokens": [asdict(t) for t in sorted(label_tokens, key=lambda t: t.x0)],
        "label_bbox": label_bbox,
        "tick_tokens": [asdict(t) for t in sorted(tick_tokens, key=lambda t: t.x0)],
        "tick_bbox": tick_bbox,
        "search_bbox": search_bbox,
        "scale_text": label_text,
    }


def detect_bar_rectangles(drawings: list[dict[str, Any]], search_bbox: list[float] | None, logger: logging.Logger) -> list[dict[str, Any]]:
    logger.info("STEP 04 | Searching for scale-bar vector rectangles")

    if not search_bbox:
        logger.warning("STEP 04 | No search box available, cannot detect bar rectangles")
        return []

    candidates: list[dict[str, Any]] = []
    seen: set[tuple[float, float, float, float]] = set()
    for i, d in enumerate(drawings):
        rect = d.get("rect")
        if rect is None:
            continue
        bbox = [float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)]
        if not intersect(bbox, search_bbox):
            continue

        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        if width < 5.0 or height < 1.0:
            continue

        # Scale bars are usually thin horizontal rectangles.
        if width / max(height, 0.1) < 2.0:
            continue

        key = (round(bbox[0], 2), round(bbox[1], 2), round(bbox[2], 2), round(bbox[3], 2))
        if key in seen:
            continue
        seen.add(key)

        candidates.append(
            {
                "index": i,
                "bbox": bbox,
                "width": width,
                "height": height,
                "stroke_color": d.get("color"),
                "fill_color": d.get("fill"),
            }
        )

    logger.info("STEP 04 | Bar rectangle candidates=%d", len(candidates))
    return sorted(candidates, key=lambda c: (c["bbox"][1], c["bbox"][0]))


def parse_scale_ratio(scale_text: str | None) -> int | None:
    if not scale_text:
        return None
    # Supports patterns like "1: 500" or "1:500".
    match = re.search(r"\b1\s*:\s*(\d+)\b", scale_text)
    if not match:
        return None
    return int(match.group(1))


def compute_calibration(
    scale_result: dict[str, Any],
    render_dpi: int,
    logger: logging.Logger,
) -> dict[str, Any]:
    logger.info("STEP 04B | Computing unit calibration from detected scale ticks")

    tick_tokens = scale_result.get("tick_tokens", [])
    parsed_ticks: list[dict[str, Any]] = []
    for t in tick_tokens:
        text = str(t.get("text", "")).strip()
        if not re.fullmatch(r"\d+(?:\.\d+)?", text):
            continue
        value = float(text)
        x_center = (float(t["x0"]) + float(t["x1"])) / 2.0
        parsed_ticks.append({"value": value, "x_center": x_center, "token": t})

    ratio = parse_scale_ratio(scale_result.get("scale_text"))
    logger.info("STEP 04B | Parsed scale ratio denominator=%s", ratio)
    logger.info("STEP 04B | Parsed numeric tick count=%d", len(parsed_ticks))

    if len(parsed_ticks) < 2:
        return {
            "status": "not_enough_ticks",
            "reason": "need_at_least_two_numeric_ticks",
            "scale_ratio": ratio,
            "meters_per_point": None,
            "meters_per_pixel": None,
        }

    # Prefer 0 and 25 if present to align with the target scale bar range.
    start = next((x for x in parsed_ticks if abs(x["value"] - 0.0) < 1e-6), None)
    end = next((x for x in parsed_ticks if abs(x["value"] - 25.0) < 1e-6), None)

    if start is None or end is None:
        parsed_ticks_sorted = sorted(parsed_ticks, key=lambda x: x["value"])
        start = parsed_ticks_sorted[0]
        end = parsed_ticks_sorted[-1]

    delta_ticks_m = float(end["value"] - start["value"])
    delta_points = abs(float(end["x_center"] - start["x_center"]))

    logger.info(
        "STEP 04B | Tick pair selected: start=%.3f end=%.3f delta_m=%.3f delta_points=%.3f",
        start["value"],
        end["value"],
        delta_ticks_m,
        delta_points,
    )

    if delta_points <= 0.0 or delta_ticks_m <= 0.0:
        return {
            "status": "invalid_tick_spacing",
            "reason": "delta_ticks_or_delta_points_non_positive",
            "scale_ratio": ratio,
            "meters_per_point": None,
            "meters_per_pixel": None,
        }

    meters_per_point_from_bar = delta_ticks_m / delta_points
    points_to_pixels = render_dpi / 72.0
    meters_per_pixel_from_bar = meters_per_point_from_bar / points_to_pixels

    # Optional secondary check using ratio+render DPI formula.
    ratio_based_m_per_px = None
    if ratio is not None:
        ratio_based_m_per_px = (25.4 / float(render_dpi)) * (float(ratio) / 1000.0)

    calibration = {
        "status": "ok",
        "method": "graphic_scale_ticks",
        "scale_ratio": ratio,
        "selected_tick_start": start,
        "selected_tick_end": end,
        "delta_ticks_m": round(delta_ticks_m, 6),
        "delta_points": round(delta_points, 6),
        "meters_per_point": round(meters_per_point_from_bar, 9),
        "render_dpi": render_dpi,
        "pixels_per_point": round(points_to_pixels, 9),
        "meters_per_pixel": round(meters_per_pixel_from_bar, 9),
        "ratio_formula_m_per_pixel": None if ratio_based_m_per_px is None else round(ratio_based_m_per_px, 9),
        "notes": [
            "Use meters_per_pixel for raster measurements on this rendered page.",
            "Use meters_per_point for vector measurements in PDF point coordinates.",
        ],
    }

    logger.info(
        "STEP 04B | Calibration computed: m/pt=%.9f m/px=%.9f",
        calibration["meters_per_point"],
        calibration["meters_per_pixel"],
    )
    if calibration["ratio_formula_m_per_pixel"] is not None:
        logger.info(
            "STEP 04B | Ratio-formula cross-check m/px=%.9f",
            calibration["ratio_formula_m_per_pixel"],
        )

    return calibration


def save_visuals(
    page: Any,
    outdir: Path,
    scale_result: dict[str, Any],
    bar_candidates: list[dict[str, Any]],
    calibration: dict[str, Any],
    logger: logging.Logger,
) -> None:
    if Image is None or ImageDraw is None:
        logger.warning("STEP 06 | Pillow missing, skipping image generation")
        return

    logger.info("STEP 06 | Generating annotated images")
    vis_dir = outdir / "visualizations"
    vis_dir.mkdir(parents=True, exist_ok=True)

    render_dpi = int(calibration.get("render_dpi", 220))
    pix = page.get_pixmap(dpi=render_dpi, alpha=False)
    mode = "RGB" if pix.n < 4 else "RGBA"
    image = Image.frombytes(mode, [pix.width, pix.height], pix.samples)
    draw = ImageDraw.Draw(image)

    sx = pix.width / page.rect.width
    sy = pix.height / page.rect.height

    def draw_box(b: list[float], color: tuple[int, int, int], width: int) -> None:
        draw.rectangle([b[0] * sx, b[1] * sy, b[2] * sx, b[3] * sy], outline=color, width=width)

    if scale_result.get("label_bbox"):
        draw_box(scale_result["label_bbox"], (0, 255, 0), 3)
    if scale_result.get("tick_bbox"):
        draw_box(scale_result["tick_bbox"], (255, 165, 0), 2)
    if scale_result.get("search_bbox"):
        draw_box(scale_result["search_bbox"], (255, 255, 0), 2)

    for cand in bar_candidates:
        draw_box(cand["bbox"], (255, 0, 0), 2)

    # Highlight selected tick anchors used for calibration.
    start = calibration.get("selected_tick_start", {}).get("token") if calibration.get("status") == "ok" else None
    end = calibration.get("selected_tick_end", {}).get("token") if calibration.get("status") == "ok" else None
    if start:
        draw_box([start["x0"], start["y0"], start["x1"], start["y1"]], (0, 191, 255), 3)
    if end:
        draw_box([end["x0"], end["y0"], end["x1"], end["y1"]], (0, 191, 255), 3)

    annotated = vis_dir / "page_001_scale_annotated.png"
    image.save(annotated)

    if scale_result.get("search_bbox"):
        b = scale_result["search_bbox"]
        crop = page.get_pixmap(clip=fitz.Rect(b[0], b[1], b[2], b[3]), dpi=300, alpha=False)
        crop_img = Image.frombytes(mode, [crop.width, crop.height], crop.samples)
        crop_path = vis_dir / "page_001_scale_crop.png"
        crop_img.save(crop_path)

    logger.info("STEP 06 | Saved annotated image: %s", annotated)


def run() -> int:
    args = parse_args()
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    logger = setup_logger(outdir / "run.log", args.log_level)

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        logger.error("STEP 01 | PDF not found: %s", pdf_path)
        return 2

    logger.info("STEP 01 | Starting focused scale-bar detection")
    logger.info("STEP 01 | PDF: %s", pdf_path)
    logger.info("STEP 01 | Output dir: %s", outdir)

    started = datetime.now(timezone.utc).isoformat()
    doc = fitz.open(pdf_path)

    if len(doc) == 0:
        logger.error("STEP 02 | PDF has no pages")
        return 2

    page = doc[0]
    words = page.get_text("words")
    drawings = page.get_drawings()

    logger.info("STEP 02 | Page count: %d", len(doc))
    logger.info("STEP 02 | Page 1 words: %d", len(words))
    logger.info("STEP 02 | Page 1 drawings: %d", len(drawings))

    render_dpi = 220
    scale_result = detect_scale_block(words, logger)
    bar_candidates = detect_bar_rectangles(drawings, scale_result.get("search_bbox"), logger)
    calibration = compute_calibration(scale_result=scale_result, render_dpi=render_dpi, logger=logger)

    result = {
        "run_started_utc": started,
        "run_finished_utc": datetime.now(timezone.utc).isoformat(),
        "source_pdf": str(pdf_path),
        "page": 1,
        "scale_block": scale_result,
        "bar_candidates": bar_candidates,
        "calibration": calibration,
        "counts": {
            "words": len(words),
            "drawings": len(drawings),
            "bar_candidates": len(bar_candidates),
        },
    }

    logger.info("STEP 05 | Writing JSON outputs")
    with (outdir / "scale_detection.json").open("w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    save_visuals(
        page=page,
        outdir=outdir,
        scale_result=scale_result,
        bar_candidates=bar_candidates,
        calibration=calibration,
        logger=logger,
    )

    logger.info("STEP 07 | Done")
    logger.info("STEP 07 | Result file: %s", outdir / "scale_detection.json")
    logger.info("STEP 07 | Log file: %s", outdir / "run.log")

    return 0


if __name__ == "__main__":
    raise SystemExit(run())

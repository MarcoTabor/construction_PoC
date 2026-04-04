#!/usr/bin/env python3
"""Focused legend color extractor.

Goal:
- Find legend entries (e.g., PROPOSED SEAL / JOAL / FOOTPATH)
- Link each entry to its nearby swatch rectangle
- Extract swatch color and provide page-wide color hit counts
"""

from __future__ import annotations

import argparse
import json
import logging
import math
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
class LegendText:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float


def setup_logger(log_path: Path, level: str) -> logging.Logger:
    logger = logging.getLogger("legend_color_detector")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Find legend entries and their swatch colors")
    parser.add_argument("--pdf", default="examples/Joal 502.pdf", help="Input PDF")
    parser.add_argument("--outdir", default="outputs/legend_colors", help="Output folder")
    parser.add_argument("--page", type=int, default=1, help="1-based page index")
    parser.add_argument("--color-threshold", type=float, default=0.03, help="RGB distance threshold in normalized 0..1 space")
    parser.add_argument(
        "--max-highlight-area-ratio",
        type=float,
        default=0.02,
        help="Skip highlighting boxes larger than this fraction of page area",
    )
    parser.add_argument(
        "--region-gap",
        type=float,
        default=1.5,
        help="Gap tolerance in PDF points when merging matched boxes into regions",
    )
    parser.add_argument(
        "--min-region-boxes",
        type=int,
        default=6,
        help="Minimum number of matched boxes required to keep a merged region",
    )
    parser.add_argument("--log-level", default="DEBUG", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def extract_text_lines(page: Any) -> list[LegendText]:
    text_dict = page.get_text("dict")
    lines: list[LegendText] = []
    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            if not spans:
                continue
            text = " ".join((s.get("text") or "").strip() for s in spans).strip()
            if not text:
                continue
            x0, y0, x1, y1 = line.get("bbox", (0.0, 0.0, 0.0, 0.0))
            lines.append(LegendText(text=text, x0=float(x0), y0=float(y0), x1=float(x1), y1=float(y1)))
    return lines


def to_rgb255(color: Any) -> tuple[int, int, int] | None:
    if color is None:
        return None
    if isinstance(color, (list, tuple)) and len(color) >= 3:
        vals = [max(0.0, min(1.0, float(c))) for c in color[:3]]
        return (int(round(vals[0] * 255)), int(round(vals[1] * 255)), int(round(vals[2] * 255)))
    return None


def rgb_hex(rgb: tuple[int, int, int] | None) -> str | None:
    if rgb is None:
        return None
    return f"#{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}"


def rgb_distance(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def normalized_rgb(c: Any) -> tuple[float, float, float] | None:
    if c is None:
        return None
    if isinstance(c, (list, tuple)) and len(c) >= 3:
        return (float(c[0]), float(c[1]), float(c[2]))
    return None


def y_overlap_ratio(a: list[float], b: list[float]) -> float:
    top = max(a[1], b[1])
    bottom = min(a[3], b[3])
    if bottom <= top:
        return 0.0
    overlap = bottom - top
    h = min(a[3] - a[1], b[3] - b[1])
    if h <= 0:
        return 0.0
    return overlap / h


def dedupe_rects(rects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    out: list[dict[str, Any]] = []
    for r in rects:
        b = r["bbox"]
        key = (
            round(b[0], 2),
            round(b[1], 2),
            round(b[2], 2),
            round(b[3], 2),
            tuple(round(float(x), 4) for x in (r.get("fill") or ())),
            tuple(round(float(x), 4) for x in (r.get("stroke") or ())),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def find_legend_region(lines: list[LegendText], page_width: float, page_height: float) -> list[float]:
    for line in lines:
        if re.search(r"\blegend\b", line.text, flags=re.IGNORECASE):
            return [max(0.0, line.x0 - 20.0), max(0.0, line.y0 - 20.0), min(page_width, line.x1 + 260.0), min(page_height, line.y1 + 260.0)]
    return [page_width * 0.05, page_height * 0.05, page_width * 0.45, page_height * 0.55]


def normalize_label(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().upper()


def boxes_connected(a: list[float], b: list[float], gap: float) -> bool:
    return not (a[2] + gap < b[0] or b[2] + gap < a[0] or a[3] + gap < b[1] or b[3] + gap < a[1])


def cluster_boxes(boxes: list[list[float]], gap: float) -> list[list[list[float]]]:
    clusters: list[list[list[float]]] = []
    for box in boxes:
        attached: list[int] = []
        for i, cluster in enumerate(clusters):
            if any(boxes_connected(box, other, gap) for other in cluster):
                attached.append(i)

        if not attached:
            clusters.append([box])
            continue

        merged = [box]
        for idx in reversed(attached):
            merged.extend(clusters.pop(idx))
        clusters.append(merged)

    return clusters


def cluster_envelope(cluster: list[list[float]]) -> list[float]:
    return [
        min(b[0] for b in cluster),
        min(b[1] for b in cluster),
        max(b[2] for b in cluster),
        max(b[3] for b in cluster),
    ]


def run() -> int:
    args = parse_args()
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    logger = setup_logger(outdir / "run.log", args.log_level)

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        logger.error("STEP 01 | PDF not found: %s", pdf_path)
        return 2

    logger.info("STEP 01 | Start legend color extraction")
    logger.info("STEP 01 | PDF: %s", pdf_path)
    logger.info("STEP 01 | Outdir: %s", outdir)

    started = datetime.now(timezone.utc).isoformat()
    doc = fitz.open(pdf_path)

    page_index = max(0, args.page - 1)
    if page_index >= len(doc):
        logger.error("STEP 02 | Page %d does not exist, total pages=%d", args.page, len(doc))
        return 2

    page = doc[page_index]
    lines = extract_text_lines(page)
    drawings = page.get_drawings()

    logger.info("STEP 02 | Page=%d lines=%d drawings=%d", args.page, len(lines), len(drawings))

    legend_region = find_legend_region(lines, page.rect.width, page.rect.height)
    logger.info("STEP 03 | Legend region bbox=%s", legend_region)

    legend_texts = [
        l
        for l in lines
        if l.x0 >= legend_region[0]
        and l.y0 >= legend_region[1]
        and l.x1 <= legend_region[2]
        and l.y1 <= legend_region[3]
        and re.search(r"\bproposed\b", l.text, flags=re.IGNORECASE)
    ]
    logger.info("STEP 03 | Proposed legend entries=%d", len(legend_texts))

    drawing_rects: list[dict[str, Any]] = []
    for i, d in enumerate(drawings):
        r = d.get("rect")
        if r is None:
            continue
        bbox = [float(r.x0), float(r.y0), float(r.x1), float(r.y1)]
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        if w < 8.0 or h < 2.0:
            continue
        fill = d.get("fill")
        stroke = d.get("color")
        if fill is None and stroke is None:
            continue
        drawing_rects.append(
            {
                "index": i,
                "bbox": bbox,
                "width": w,
                "height": h,
                "fill": fill,
                "stroke": stroke,
            }
        )

    drawing_rects = dedupe_rects(drawing_rects)
    logger.info("STEP 04 | Candidate swatch rectangles after dedupe=%d", len(drawing_rects))

    entries: list[dict[str, Any]] = []

    for text in sorted(legend_texts, key=lambda t: (t.y0, t.x0)):
        t_bbox = [text.x0, text.y0, text.x1, text.y1]
        matches: list[dict[str, Any]] = []
        for rect in drawing_rects:
            b = rect["bbox"]
            if b[2] > text.x0 + 2.0:
                continue
            if text.x0 - b[2] > 140.0:
                continue
            if y_overlap_ratio(t_bbox, b) < 0.4:
                continue
            dist = text.x0 - b[2]
            y_mid_delta = abs(((b[1] + b[3]) / 2.0) - ((text.y0 + text.y1) / 2.0))
            score = dist + 0.5 * y_mid_delta
            candidate = dict(rect)
            candidate["score"] = round(score, 4)
            matches.append(candidate)

        if not matches:
            entries.append(
                {
                    "label": text.text,
                    "label_bbox": t_bbox,
                    "status": "no_swatch_found",
                }
            )
            logger.warning("STEP 05 | No swatch matched for legend text: %s", text.text)
            continue

        best = sorted(matches, key=lambda x: x["score"])[0]
        fill_rgb255 = to_rgb255(best.get("fill"))
        stroke_rgb255 = to_rgb255(best.get("stroke"))

        entry = {
            "label": text.text,
            "label_bbox": t_bbox,
            "status": "ok",
            "swatch": {
                "drawing_index": best["index"],
                "bbox": best["bbox"],
                "fill": best.get("fill"),
                "stroke": best.get("stroke"),
                "fill_rgb255": fill_rgb255,
                "stroke_rgb255": stroke_rgb255,
                "fill_hex": rgb_hex(fill_rgb255),
                "stroke_hex": rgb_hex(stroke_rgb255),
                "match_score": best["score"],
            },
        }
        entries.append(entry)
        logger.info(
            "STEP 05 | Matched '%s' -> fill=%s stroke=%s bbox=%s",
            text.text,
            entry["swatch"]["fill_hex"],
            entry["swatch"]["stroke_hex"],
            entry["swatch"]["bbox"],
        )

    # Optional page-wide color search counts.
    logger.info("STEP 06 | Counting page-wide drawing objects by extracted legend colors")
    color_search: list[dict[str, Any]] = []
    matched_bboxes_by_label: dict[str, list[list[float]]] = {}
    for entry in entries:
        if entry.get("status") != "ok":
            continue
        swatch_fill = normalized_rgb(entry["swatch"].get("fill"))
        swatch_stroke = normalized_rgb(entry["swatch"].get("stroke"))
        if swatch_fill is None and swatch_stroke is None:
            continue

        fill_hits = 0
        stroke_hits = 0
        matched_bboxes: list[list[float]] = []
        for d in drawings:
            f = normalized_rgb(d.get("fill"))
            s = normalized_rgb(d.get("color"))
            matched = False
            if swatch_fill is not None and f is not None and rgb_distance(f, swatch_fill) <= args.color_threshold:
                fill_hits += 1
                matched = True
            if swatch_stroke is not None and s is not None and rgb_distance(s, swatch_stroke) <= args.color_threshold:
                stroke_hits += 1
                matched = True
            if matched:
                r = d.get("rect")
                if r is not None:
                    matched_bboxes.append([float(r.x0), float(r.y0), float(r.x1), float(r.y1)])

        matched_bboxes_by_label[normalize_label(entry["label"])] = matched_bboxes

        color_search.append(
            {
                "label": entry["label"],
                "fill_hex": entry["swatch"].get("fill_hex"),
                "stroke_hex": entry["swatch"].get("stroke_hex"),
                "threshold": args.color_threshold,
                "fill_hits": fill_hits,
                "stroke_hits": stroke_hits,
                "matched_object_count": len(matched_bboxes),
            }
        )

    result = {
        "run_started_utc": started,
        "run_finished_utc": datetime.now(timezone.utc).isoformat(),
        "source_pdf": str(pdf_path),
        "page": args.page,
        "legend_region_bbox": legend_region,
        "legend_entries": entries,
        "color_search_counts": color_search,
        "region_params": {
            "region_gap": args.region_gap,
            "min_region_boxes": args.min_region_boxes,
            "max_highlight_area_ratio": args.max_highlight_area_ratio,
        },
    }

    with (outdir / "legend_colors.json").open("w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    if Image is not None and ImageDraw is not None:
        logger.info("STEP 07 | Rendering legend annotation image")
        vis = outdir / "visualizations"
        vis.mkdir(parents=True, exist_ok=True)

        pix = page.get_pixmap(dpi=220, alpha=False)
        mode = "RGB" if pix.n < 4 else "RGBA"
        image = Image.frombytes(mode, [pix.width, pix.height], pix.samples).convert("RGBA")
        draw = ImageDraw.Draw(image)

        sx = pix.width / page.rect.width
        sy = pix.height / page.rect.height

        # Legend region
        lr = legend_region
        draw.rectangle([lr[0] * sx, lr[1] * sy, lr[2] * sx, lr[3] * sy], outline=(255, 255, 0), width=3)

        for entry in entries:
            lb = entry["label_bbox"]
            draw.rectangle([lb[0] * sx, lb[1] * sy, lb[2] * sx, lb[3] * sy], outline=(0, 255, 0), width=2)
            if entry.get("status") == "ok":
                sb = entry["swatch"]["bbox"]
                draw.rectangle([sb[0] * sx, sb[1] * sy, sb[2] * sx, sb[3] * sy], outline=(255, 0, 0), width=2)

        image.save(vis / "page_legend_colors.png")

        logger.info("STEP 07A | Rendering target magenta highlight images")
        target_labels = ["PROPOSED FOOTPATH", "PROPOSED JOAL", "PROPOSED SEAL"]
        base_rgba = Image.frombytes(mode, [pix.width, pix.height], pix.samples).convert("RGBA")
        region_summary: dict[str, Any] = {}

        page_area = float(page.rect.width * page.rect.height)
        max_box_area = page_area * float(args.max_highlight_area_ratio)

        for target in target_labels:
            target_key = normalize_label(target)
            matched_boxes = matched_bboxes_by_label.get(target_key, [])

            per_target = base_rgba.copy()
            overlay = Image.new("RGBA", per_target.size, (0, 0, 0, 0))
            od = ImageDraw.Draw(overlay)

            kept = 0
            skipped_large = 0
            kept_boxes: list[list[float]] = []
            for b in matched_boxes:
                area = max(0.0, (b[2] - b[0]) * (b[3] - b[1]))
                if area > max_box_area:
                    skipped_large += 1
                    continue
                kept_boxes.append(b)
                od.rectangle(
                    [b[0] * sx, b[1] * sy, b[2] * sx, b[3] * sy],
                    outline=(255, 0, 255, 255),
                    width=2,
                )
                kept += 1

            per_target = Image.alpha_composite(per_target, overlay)
            safe_name = target.lower().replace(" ", "_")
            out_path = vis / f"page_legend_highlight_{safe_name}.png"
            per_target.save(out_path)

            # Region-level validation: merge connected micro-boxes into coherent regions.
            clusters = cluster_boxes(kept_boxes, gap=float(args.region_gap))
            filtered_clusters = [c for c in clusters if len(c) >= int(args.min_region_boxes)]
            envelopes = [cluster_envelope(c) for c in filtered_clusters]

            region_img = base_rgba.copy()
            region_overlay = Image.new("RGBA", region_img.size, (0, 0, 0, 0))
            rd = ImageDraw.Draw(region_overlay)
            for env in envelopes:
                rd.rectangle(
                    [env[0] * sx, env[1] * sy, env[2] * sx, env[3] * sy],
                    fill=(255, 0, 255, 48),
                    outline=(255, 0, 255, 255),
                    width=3,
                )
            region_img = Image.alpha_composite(region_img, region_overlay)
            region_out_path = vis / f"page_legend_regions_{safe_name}.png"
            region_img.save(region_out_path)

            region_summary[target_key] = {
                "matched_boxes": len(matched_boxes),
                "kept_boxes": len(kept_boxes),
                "skipped_large": skipped_large,
                "clusters_raw": len(clusters),
                "clusters_kept": len(filtered_clusters),
                "cluster_envelopes": envelopes,
                "micro_highlight_image": str(out_path),
                "region_highlight_image": str(region_out_path),
            }
            logger.info(
                "STEP 07A | Saved %s and %s (matched=%d kept=%d skipped_large=%d clusters=%d kept_clusters=%d)",
                out_path.name,
                region_out_path.name,
                len(matched_boxes),
                kept,
                skipped_large,
                len(clusters),
                len(filtered_clusters),
            )

        result["region_summary"] = region_summary
        with (outdir / "legend_colors.json").open("w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)

    logger.info("STEP 08 | Done")
    logger.info("STEP 08 | JSON: %s", outdir / "legend_colors.json")
    logger.info("STEP 08 | LOG: %s", outdir / "run.log")

    return 0


if __name__ == "__main__":
    raise SystemExit(run())

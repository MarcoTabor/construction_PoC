#!/usr/bin/env python3
"""Pilot extractor for Joal 502 drawing PDFs with extensive stage-by-stage logging.

This script focuses on extraction observability first:
- Vector primitives and text extraction per page
- Keyword detection for target features
- Spatial linking of label hits to nearest drawing primitives
- Audit artifacts written as JSON for each stage

Usage:
    python scripts/extract_joal502.py
    python scripts/extract_joal502.py --pdf "examples/Joal 502.pdf" --outdir outputs/joal502
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import fitz  # PyMuPDF
except ImportError:  # pragma: no cover
    fitz = None

try:
    from PIL import Image, ImageDraw
except ImportError:  # pragma: no cover
    Image = None
    ImageDraw = None


@dataclass
class TextHit:
    page: int
    keyword: str
    feature_type: str
    text: str
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class LegendEntry:
    page: int
    feature_type: str
    text: str
    x0: float
    y0: float
    x1: float
    y1: float


FEATURE_COLORS = {
    "concrete": (255, 99, 71),
    "footpath": (46, 139, 87),
    "subsoil_drain": (30, 144, 255),
    "flush_nib": (255, 140, 0),
    "gap65": (138, 43, 226),
    "unknown": (220, 220, 220),
}


def setup_logger(log_path: Path, level: str) -> logging.Logger:
    logger = logging.getLogger("joal502_extractor")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(fmt)
    logger.addHandler(stream_handler)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    return logger


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract Joal 502 drawing entities with detailed logs.")
    parser.add_argument("--pdf", default="examples/Joal 502.pdf", help="Path to source PDF")
    parser.add_argument("--outdir", default="outputs/joal502", help="Output directory for JSON and logs")
    parser.add_argument("--log-level", default="DEBUG", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--max-pages", type=int, default=0, help="Limit processed pages (0 = all pages)")
    return parser.parse_args()


def midpoint(x0: float, y0: float, x1: float, y1: float) -> tuple[float, float]:
    return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)


def euclidean(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def detect_feature_type(text: str) -> str | None:
    normalized = text.lower()
    if "subsoil drain" in normalized:
        return "subsoil_drain"
    if "flush nib" in normalized:
        return "flush_nib"
    if "gap65" in normalized:
        return "gap65"
    if "footpath" in normalized:
        return "footpath"
    if "conc" in normalized or "concrete" in normalized:
        return "concrete"
    return None


def keyword_patterns() -> list[re.Pattern[str]]:
    raw = [
        r"\b150\s*mm\b",
        r"\bconc\b",
        r"\bconcrete\b",
        r"\bgap\s*65\b",
        r"\bsubsoil\s+drain\b",
        r"\bflush\s+nib\b",
        r"\bfootpath\b",
    ]
    return [re.compile(p, flags=re.IGNORECASE) for p in raw]


def extract_draw_segments(items: list[Any]) -> float:
    """Approximate drawable line length from drawing commands.

    PyMuPDF drawing items are command tuples. The first item is the opcode.
    We use line-like opcodes and rectangle dimensions for a stable length proxy.
    """
    total = 0.0
    for item in items:
        if not item:
            continue
        op = item[0]
        if op == "l" and len(item) >= 3:
            p0, p1 = item[1], item[2]
            total += math.hypot(p1.x - p0.x, p1.y - p0.y)
        elif op == "re" and len(item) >= 2:
            rect = item[1]
            total += 2.0 * (abs(rect.width) + abs(rect.height))
    return total


def extract_text_lines(page: Any) -> list[dict[str, Any]]:
    """Return text lines with bounding boxes and concatenated text."""
    text_dict = page.get_text("dict")
    lines: list[dict[str, Any]] = []
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
            lines.append({"text": text, "bbox": [x0, y0, x1, y1]})
    return lines


def detect_scale_candidates(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    patterns = [
        re.compile(r"\bscale\b", re.IGNORECASE),
        re.compile(r"\b\d+\s*:\s*\d+\b", re.IGNORECASE),
        re.compile(r"\bnts\b", re.IGNORECASE),
    ]
    hits: list[dict[str, Any]] = []
    for line in lines:
        text = line["text"]
        if any(p.search(text) for p in patterns):
            hits.append(line)
    return hits


def detect_legend_region(lines: list[dict[str, Any]], page_width: float, page_height: float) -> dict[str, Any] | None:
    legend_heading = None
    for line in lines:
        if re.search(r"\blegend\b", line["text"], flags=re.IGNORECASE):
            legend_heading = line
            break

    if legend_heading:
        x0, y0, x1, y1 = legend_heading["bbox"]
        return {
            "source": "legend_heading",
            "bbox": [max(0.0, x0 - 20.0), max(0.0, y0 - 20.0), min(page_width, x1 + 260.0), min(page_height, y1 + 220.0)],
        }

    # Fallback: common legend placement in lower-right quadrant.
    return {
        "source": "heuristic_lower_right",
        "bbox": [page_width * 0.6, page_height * 0.55, page_width * 0.98, page_height * 0.98],
    }


def detect_legend_entries(lines: list[dict[str, Any]], page_no: int, legend_region: dict[str, Any] | None) -> list[LegendEntry]:
    entries: list[LegendEntry] = []
    if not legend_region:
        return entries

    rx0, ry0, rx1, ry1 = legend_region["bbox"]
    for line in lines:
        x0, y0, x1, y1 = line["bbox"]
        if x1 < rx0 or x0 > rx1 or y1 < ry0 or y0 > ry1:
            continue
        feature_type = detect_feature_type(line["text"])
        if feature_type is None:
            continue
        entries.append(
            LegendEntry(
                page=page_no,
                feature_type=feature_type,
                text=line["text"],
                x0=x0,
                y0=y0,
                x1=x1,
                y1=y1,
            )
        )
    return entries


def link_legend_symbols(entries: list[LegendEntry], drawings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    drawings_by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for d in drawings:
        drawings_by_page[int(d["page"])].append(d)

    for entry in entries:
        center = midpoint(entry.x0, entry.y0, entry.x1, entry.y1)
        nearest = None
        nearest_dist = float("inf")
        for d in drawings_by_page.get(entry.page, []):
            bx0, by0, bx1, by1 = d["bbox"]
            d_center = midpoint(bx0, by0, bx1, by1)
            dist = euclidean(center, d_center)
            if dist < nearest_dist:
                nearest_dist = dist
                nearest = d
        links.append(
            {
                "legend_entry": asdict(entry),
                "linked_drawing_id": nearest["drawing_id"] if nearest else None,
                "distance": None if nearest is None else round(nearest_dist, 3),
                "linked_drawing_bbox": None if nearest is None else nearest["bbox"],
            }
        )
    return links


def render_overlays(
    doc: Any,
    outdir: Path,
    pages_to_process: int,
    linked_records: list[dict[str, Any]],
    legend_regions: dict[int, dict[str, Any]],
    legend_entries: list[LegendEntry],
    legend_symbol_links: list[dict[str, Any]],
    logger: logging.Logger,
) -> None:
    if Image is None or ImageDraw is None:
        logger.warning("STEP 06A | Pillow not installed, skipping PNG overlay generation")
        return

    out_vis = outdir / "visualizations"
    out_vis.mkdir(parents=True, exist_ok=True)

    by_page_links: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for rec in linked_records:
        by_page_links[int(rec["hit"]["page"])].append(rec)

    entries_by_page: dict[int, list[LegendEntry]] = defaultdict(list)
    for entry in legend_entries:
        entries_by_page[entry.page].append(entry)

    symbols_by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for link in legend_symbol_links:
        page = int(link["legend_entry"]["page"])
        symbols_by_page[page].append(link)

    for idx in range(pages_to_process):
        page_no = idx + 1
        page = doc[idx]
        pix = page.get_pixmap(dpi=150, alpha=False)
        mode = "RGB" if pix.n < 4 else "RGBA"
        image = Image.frombytes(mode, [pix.width, pix.height], pix.samples)
        draw = ImageDraw.Draw(image)

        scale_x = pix.width / page.rect.width
        scale_y = pix.height / page.rect.height

        # Highlight linked feature geometry.
        for rec in by_page_links.get(page_no, []):
            if rec.get("link_status") != "linked" or "drawing" not in rec:
                continue
            feature = rec["hit"].get("feature_type", "unknown")
            color = FEATURE_COLORS.get(feature, FEATURE_COLORS["unknown"])
            bx0, by0, bx1, by1 = rec["drawing"]["bbox"]
            draw.rectangle(
                [bx0 * scale_x, by0 * scale_y, bx1 * scale_x, by1 * scale_y],
                outline=color,
                width=2,
            )

        image.save(out_vis / f"page_{page_no:03d}_features.png")

        # Dedicated legend visualization.
        legend_image = image.copy()
        legend_draw = ImageDraw.Draw(legend_image)

        region = legend_regions.get(page_no)
        if region:
            rx0, ry0, rx1, ry1 = region["bbox"]
            legend_draw.rectangle(
                [rx0 * scale_x, ry0 * scale_y, rx1 * scale_x, ry1 * scale_y],
                outline=(255, 255, 0),
                width=3,
            )

        for entry in entries_by_page.get(page_no, []):
            color = FEATURE_COLORS.get(entry.feature_type, FEATURE_COLORS["unknown"])
            legend_draw.rectangle(
                [entry.x0 * scale_x, entry.y0 * scale_y, entry.x1 * scale_x, entry.y1 * scale_y],
                outline=color,
                width=2,
            )

        for link in symbols_by_page.get(page_no, []):
            bbox = link.get("linked_drawing_bbox")
            feature = link["legend_entry"].get("feature_type", "unknown")
            color = FEATURE_COLORS.get(feature, FEATURE_COLORS["unknown"])
            if not bbox:
                continue
            bx0, by0, bx1, by1 = bbox
            legend_draw.rectangle(
                [bx0 * scale_x, by0 * scale_y, bx1 * scale_x, by1 * scale_y],
                outline=color,
                width=3,
            )

        legend_image.save(out_vis / f"page_{page_no:03d}_legend.png")


def run() -> int:
    args = parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    log_path = outdir / "run.log"
    logger = setup_logger(log_path=log_path, level=args.log_level)

    if fitz is None:
        logger.error("PyMuPDF is not installed. Install dependencies with: pip install -r requirements.txt")
        return 2

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        logger.error("PDF not found: %s", pdf_path)
        return 2

    logger.info("STEP 01 | Starting extraction run")
    logger.info("STEP 01 | Input PDF: %s", pdf_path)
    logger.info("STEP 01 | Output dir: %s", outdir)

    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    pages_to_process = total_pages if args.max_pages <= 0 else min(total_pages, args.max_pages)

    logger.info("STEP 02 | Opened PDF successfully")
    logger.info("STEP 02 | Pages in document: %d", total_pages)
    logger.info("STEP 02 | Pages to process: %d", pages_to_process)
    logger.debug("STEP 02 | Metadata: %s", doc.metadata)

    patterns = keyword_patterns()
    extraction_started = datetime.now(timezone.utc).isoformat()

    all_pages_payload: list[dict[str, Any]] = []
    all_hits: list[TextHit] = []
    all_drawings: list[dict[str, Any]] = []
    all_legend_entries: list[LegendEntry] = []
    all_scale_candidates: list[dict[str, Any]] = []
    legend_regions: dict[int, dict[str, Any]] = {}

    logger.info("STEP 03 | Beginning per-page extraction loop")

    for page_index in range(pages_to_process):
        page_no = page_index + 1
        page = doc[page_index]
        page_rect = page.rect

        logger.info("STEP 03.%02d | Page %d extraction start", page_no, page_no)
        logger.debug(
            "STEP 03.%02d | Page dimensions (pt): width=%.2f height=%.2f",
            page_no,
            page_rect.width,
            page_rect.height,
        )

        # Text extraction
        words = page.get_text("words")
        text_lines = page.get_text("text")
        line_records = extract_text_lines(page)

        logger.info("STEP 03.%02d | Extracted words=%d", page_no, len(words))
        logger.info("STEP 03.%02d | Extracted text lines=%d", page_no, len(line_records))

        # Scale and legend discovery to move from extraction to interpretable structure.
        scale_candidates = detect_scale_candidates(line_records)
        all_scale_candidates.extend({"page": page_no, **item} for item in scale_candidates)
        logger.info("STEP 03.%02d | Scale candidates=%d", page_no, len(scale_candidates))

        legend_region = detect_legend_region(line_records, page_rect.width, page_rect.height)
        legend_regions[page_no] = legend_region or {}
        legend_entries = detect_legend_entries(line_records, page_no, legend_region)
        all_legend_entries.extend(legend_entries)
        logger.info(
            "STEP 03.%02d | Legend region source=%s entries=%d",
            page_no,
            (legend_region or {}).get("source", "none"),
            len(legend_entries),
        )

        # Drawing extraction
        drawings = page.get_drawings()
        logger.info("STEP 03.%02d | Extracted drawing objects=%d", page_no, len(drawings))

        drawing_payloads: list[dict[str, Any]] = []
        for d_idx, drawing in enumerate(drawings):
            rect = drawing.get("rect")
            if rect is None:
                continue
            seg_length = extract_draw_segments(drawing.get("items", []))
            record = {
                "drawing_id": f"p{page_no}_d{d_idx}",
                "page": page_no,
                "bbox": [rect.x0, rect.y0, rect.x1, rect.y1],
                "stroke_color": drawing.get("color"),
                "fill_color": drawing.get("fill"),
                "width": drawing.get("width"),
                "line_cap": drawing.get("lineCap"),
                "line_join": drawing.get("lineJoin"),
                "path_length_proxy": seg_length,
            }
            drawing_payloads.append(record)
            all_drawings.append(record)

        # Keyword matching by word token first, then full text fallback.
        page_hits: list[TextHit] = []
        for w in words:
            x0, y0, x1, y1, token = w[0], w[1], w[2], w[3], str(w[4])
            matched = False
            for pat in patterns:
                if pat.search(token):
                    feature_type = detect_feature_type(token)
                    page_hits.append(
                        TextHit(
                            page=page_no,
                            keyword=pat.pattern,
                            feature_type=feature_type or "unknown",
                            text=token,
                            x0=x0,
                            y0=y0,
                            x1=x1,
                            y1=y1,
                        )
                    )
                    matched = True
            if matched:
                logger.debug(
                    "STEP 03.%02d | Token keyword hit token='%s' bbox=[%.2f, %.2f, %.2f, %.2f]",
                    page_no,
                    token,
                    x0,
                    y0,
                    x1,
                    y1,
                )

        # Add line-based fallback detection for phrase patterns.
        for line in text_lines.splitlines():
            lowered = line.lower()
            if any(p.search(lowered) for p in patterns):
                feature_type = detect_feature_type(line)
                if feature_type is None:
                    feature_type = "unknown"
                # Coordinate-less fallback hit to preserve evidence chain.
                page_hits.append(
                    TextHit(
                        page=page_no,
                        keyword="line_fallback",
                        feature_type=feature_type,
                        text=line.strip(),
                        x0=-1.0,
                        y0=-1.0,
                        x1=-1.0,
                        y1=-1.0,
                    )
                )

        logger.info("STEP 03.%02d | Matched keyword hits=%d", page_no, len(page_hits))
        all_hits.extend(page_hits)

        all_pages_payload.append(
            {
                "page": page_no,
                "width": page_rect.width,
                "height": page_rect.height,
                "word_count": len(words),
                "text_line_count": len(line_records),
                "drawing_count": len(drawing_payloads),
                "keyword_hit_count": len(page_hits),
                "scale_candidate_count": len(scale_candidates),
                "legend_entry_count": len(legend_entries),
            }
        )

        dump_json(outdir / "pages" / f"page_{page_no:03d}_drawings.json", drawing_payloads)
        dump_json(outdir / "pages" / f"page_{page_no:03d}_hits.json", [asdict(h) for h in page_hits])
        dump_json(outdir / "pages" / f"page_{page_no:03d}_line_records.json", line_records)
        dump_json(outdir / "pages" / f"page_{page_no:03d}_scale_candidates.json", scale_candidates)
        dump_json(outdir / "pages" / f"page_{page_no:03d}_legend_entries.json", [asdict(x) for x in legend_entries])

    logger.info("STEP 03B | Linking legend entries to nearby symbol geometry")
    legend_symbol_links = link_legend_symbols(all_legend_entries, all_drawings)
    logger.info("STEP 03B | Legend symbol links=%d", len(legend_symbol_links))

    logger.info("STEP 04 | Building spatial links between text hits and drawing candidates")

    by_page_drawings: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for d in all_drawings:
        by_page_drawings[int(d["page"])].append(d)

    linked_records: list[dict[str, Any]] = []
    unlinked_count = 0

    for hit in all_hits:
        page_drawings = by_page_drawings.get(hit.page, [])
        if hit.x0 < 0 or hit.y0 < 0 or not page_drawings:
            unlinked_count += 1
            linked_records.append(
                {
                    "hit": asdict(hit),
                    "linked_drawing_id": None,
                    "distance": None,
                    "score": 0.0,
                    "link_status": "unlinked",
                    "reason": "no_coordinates_or_no_drawings",
                }
            )
            continue

        hit_center = midpoint(hit.x0, hit.y0, hit.x1, hit.y1)

        nearest = None
        nearest_dist = float("inf")
        for d in page_drawings:
            bx0, by0, bx1, by1 = d["bbox"]
            d_center = midpoint(bx0, by0, bx1, by1)
            dist = euclidean(hit_center, d_center)
            if dist < nearest_dist:
                nearest_dist = dist
                nearest = d

        if nearest is None:
            unlinked_count += 1
            linked_records.append(
                {
                    "hit": asdict(hit),
                    "linked_drawing_id": None,
                    "distance": None,
                    "score": 0.0,
                    "link_status": "unlinked",
                    "reason": "no_nearest_candidate",
                }
            )
            continue

        # Distance-based score using a smooth decay curve.
        score = 1.0 / (1.0 + nearest_dist / 100.0)
        linked_records.append(
            {
                "hit": asdict(hit),
                "linked_drawing_id": nearest["drawing_id"],
                "distance": nearest_dist,
                "score": round(score, 4),
                "link_status": "linked",
                "reason": "nearest_bbox_center",
                "drawing": nearest,
            }
        )

    logger.info("STEP 04 | Spatial linking complete: total_hits=%d linked=%d unlinked=%d", len(all_hits), len(all_hits) - unlinked_count, unlinked_count)

    logger.info("STEP 05 | Aggregating feature-level summary metrics")
    feature_summary: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "hit_count": 0,
        "linked_count": 0,
        "avg_link_score": 0.0,
        "length_proxy_sum": 0.0,
    })

    for rec in linked_records:
        feature = rec["hit"].get("feature_type", "unknown")
        item = feature_summary[feature]
        item["hit_count"] += 1
        if rec["link_status"] == "linked":
            item["linked_count"] += 1
            item["avg_link_score"] += float(rec["score"])
            drawing = rec.get("drawing")
            if drawing:
                item["length_proxy_sum"] += float(drawing.get("path_length_proxy", 0.0))

    for f, item in feature_summary.items():
        if item["linked_count"] > 0:
            item["avg_link_score"] = round(item["avg_link_score"] / item["linked_count"], 4)
        else:
            item["avg_link_score"] = 0.0
        item["length_proxy_sum"] = round(item["length_proxy_sum"], 3)

    summary = {
        "run_started_utc": extraction_started,
        "run_finished_utc": datetime.now(timezone.utc).isoformat(),
        "source_pdf": str(pdf_path),
        "total_pages": total_pages,
        "processed_pages": pages_to_process,
        "page_stats": all_pages_payload,
        "scale_candidates": all_scale_candidates,
        "legend_regions": legend_regions,
        "legend_entries": [asdict(x) for x in all_legend_entries],
        "feature_summary": feature_summary,
        "counts": {
            "text_hits": len(all_hits),
            "drawing_candidates": len(all_drawings),
            "linked_hits": len([r for r in linked_records if r["link_status"] == "linked"]),
            "unlinked_hits": len([r for r in linked_records if r["link_status"] == "unlinked"]),
            "legend_symbol_links": len(legend_symbol_links),
        },
        "notes": [
            "length_proxy_sum is a pilot metric based on linked drawing path segments, not final engineering quantity.",
            "Area/volume calculations are intentionally deferred until polygon closure and unit calibration are added.",
        ],
    }

    logger.info("STEP 06 | Writing output artifacts")
    dump_json(outdir / "pages_index.json", all_pages_payload)
    dump_json(outdir / "drawings_all.json", all_drawings)
    dump_json(outdir / "text_hits_all.json", [asdict(h) for h in all_hits])
    dump_json(outdir / "links.json", linked_records)
    dump_json(outdir / "scale_candidates.json", all_scale_candidates)
    dump_json(outdir / "legend_entries_all.json", [asdict(x) for x in all_legend_entries])
    dump_json(outdir / "legend_symbol_links.json", legend_symbol_links)
    dump_json(outdir / "summary.json", summary)

    review_queue = [
        r
        for r in linked_records
        if r["link_status"] == "unlinked" or float(r.get("score", 0.0)) < 0.45
    ]
    dump_json(outdir / "review_queue.json", review_queue)

    logger.info("STEP 06A | Rendering visual overlays")
    render_overlays(
        doc=doc,
        outdir=outdir,
        pages_to_process=pages_to_process,
        linked_records=linked_records,
        legend_regions=legend_regions,
        legend_entries=all_legend_entries,
        legend_symbol_links=legend_symbol_links,
        logger=logger,
    )

    logger.info("STEP 07 | Review queue size=%d", len(review_queue))
    logger.info("STEP 08 | Run complete")
    logger.info("STEP 08 | Summary file: %s", outdir / "summary.json")
    logger.info("STEP 08 | Log file: %s", log_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(run())

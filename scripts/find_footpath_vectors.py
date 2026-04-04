#!/usr/bin/env python3
"""Find likely footpath vectors in a PDF using legend color matching.

This script reads the "PROPOSED FOOTPATH" swatch color from legend_colors.json,
then scans PDF drawing objects and keeps vectors whose fill/stroke color matches
that swatch within a configurable threshold and basic geometry constraints.

Usage:
    python scripts/find_footpath_vectors.py
    python scripts/find_footpath_vectors.py --pdf "examples/Joal 502.pdf" --page 1
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import fitz


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Find likely footpath vectors from a PDF")
    parser.add_argument("--pdf", default="examples/Joal 502.pdf", help="Input PDF path")
    parser.add_argument("--legend-json", default="outputs/legend_colors/legend_colors.json", help="Legend color JSON path")
    parser.add_argument("--label", default="PROPOSED FOOTPATH", help="Legend label to target")
    parser.add_argument("--out", default="outputs/joal502/footpath_vectors.json", help="Output JSON path")
    parser.add_argument("--page", type=int, default=0, help="1-based page number (0 = all pages)")
    parser.add_argument("--color-threshold", type=float, default=0.035, help="Max RGB distance in normalized [0,1] color space")
    parser.add_argument("--min-bbox-area", type=float, default=40.0, help="Minimum drawing bbox area in PDF points^2")
    parser.add_argument("--min-short-side", type=float, default=1.2, help="Minimum short side of drawing bbox in points")
    parser.add_argument("--min-long-side", type=float, default=6.0, help="Minimum long side of drawing bbox in points")
    parser.add_argument(
        "--require-fill",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Require fill color match (default: on)",
    )
    parser.add_argument("--allow-stroke-match", action="store_true", help="Allow stroke-only matches in addition to fill")
    return parser.parse_args()


def as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def norm_color(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return None
    rgb = [as_float(value[0]), as_float(value[1]), as_float(value[2])]
    if any(v is None for v in rgb):
        return None
    return [float(rgb[0]), float(rgb[1]), float(rgb[2])]


def rect_to_list(rect: Any) -> list[float] | None:
    if rect is None:
        return None
    values = [as_float(getattr(rect, name, None)) for name in ("x0", "y0", "x1", "y1")]
    if any(v is None for v in values):
        return None
    return [float(v) for v in values]


def point_to_list(point: Any) -> list[float] | None:
    x = as_float(getattr(point, "x", None))
    y = as_float(getattr(point, "y", None))
    if x is None or y is None:
        return None
    return [x, y]


def convert_path_item(item: Any) -> dict[str, Any]:
    if not isinstance(item, (list, tuple)) or not item:
        return {"op": "unknown", "raw": repr(item)}

    op = item[0]
    if op == "l" and len(item) >= 3:
        return {"op": "line", "p0": point_to_list(item[1]), "p1": point_to_list(item[2])}
    if op == "c" and len(item) >= 5:
        return {
            "op": "curve",
            "p0": point_to_list(item[1]),
            "c1": point_to_list(item[2]),
            "c2": point_to_list(item[3]),
            "p1": point_to_list(item[4]),
        }
    if op == "re" and len(item) >= 2:
        return {
            "op": "rect",
            "rect": rect_to_list(item[1]),
            "orientation": as_float(item[2]) if len(item) > 2 else None,
        }

    return {"op": str(op), "raw": repr(item[1:])}


def color_distance(a: list[float], b: list[float]) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def read_target_color(legend_json_path: Path, label: str) -> list[float]:
    data = json.loads(legend_json_path.read_text(encoding="utf-8"))
    for entry in data.get("legend_entries", []):
        if str(entry.get("label", "")).replace("\n", " ").strip().upper() != label.upper():
            continue
        swatch = entry.get("swatch") or {}
        fill = norm_color(swatch.get("fill"))
        if fill is not None:
            return fill
    raise RuntimeError(f"Label '{label}' with usable fill color not found in {legend_json_path}")


def passes_geometry(rect: list[float] | None, min_bbox_area: float, min_short_side: float, min_long_side: float) -> tuple[bool, float, float, float]:
    if rect is None:
        return (False, 0.0, 0.0, 0.0)
    w = abs(rect[2] - rect[0])
    h = abs(rect[3] - rect[1])
    area = w * h
    short_side = min(w, h)
    long_side = max(w, h)
    ok = area >= min_bbox_area and short_side >= min_short_side and long_side >= min_long_side
    return (ok, area, short_side, long_side)


def find_footpath_vectors(args: argparse.Namespace) -> dict[str, Any]:
    pdf_path = Path(args.pdf)
    legend_json_path = Path(args.legend_json)

    if not pdf_path.exists():
        raise FileNotFoundError(f"Missing PDF: {pdf_path}")
    if not legend_json_path.exists():
        raise FileNotFoundError(f"Missing legend JSON: {legend_json_path}")

    target = read_target_color(legend_json_path, args.label)

    doc = fitz.open(pdf_path)
    page_indices = [args.page - 1] if args.page > 0 else list(range(doc.page_count))

    candidates: list[dict[str, Any]] = []

    for page_index in page_indices:
        if page_index < 0 or page_index >= doc.page_count:
            raise ValueError(f"Page out of range: {args.page}")

        page = doc[page_index]
        for idx, drawing in enumerate(page.get_drawings()):
            rect = rect_to_list(drawing.get("rect"))
            ok_geom, area, short_side, long_side = passes_geometry(
                rect,
                args.min_bbox_area,
                args.min_short_side,
                args.min_long_side,
            )
            if not ok_geom:
                continue

            fill = norm_color(drawing.get("fill"))
            stroke = norm_color(drawing.get("color"))

            fill_dist = color_distance(fill, target) if fill is not None else None
            stroke_dist = color_distance(stroke, target) if stroke is not None else None

            channel: str | None = None
            match_distance: float | None = None

            if args.require_fill and fill_dist is not None and fill_dist <= args.color_threshold:
                channel = "fill"
                match_distance = fill_dist
            elif args.allow_stroke_match and stroke_dist is not None and stroke_dist <= args.color_threshold:
                channel = "stroke"
                match_distance = stroke_dist

            if channel is None or match_distance is None:
                continue

            candidates.append(
                {
                    "page": page_index + 1,
                    "index_on_page": idx,
                    "seqno": int(drawing.get("seqno", -1)),
                    "type": drawing.get("type"),
                    "rect": rect,
                    "bbox_area": area,
                    "bbox_short_side": short_side,
                    "bbox_long_side": long_side,
                    "width": as_float(drawing.get("width")),
                    "lineCap": drawing.get("lineCap"),
                    "lineJoin": drawing.get("lineJoin"),
                    "dashes": drawing.get("dashes"),
                    "fill": fill,
                    "stroke": stroke,
                    "match_channel": channel,
                    "match_distance": match_distance,
                    "path_items": [convert_path_item(it) for it in (drawing.get("items") or [])],
                }
            )

    doc.close()

    candidates.sort(key=lambda x: (x["match_distance"], -x["bbox_area"]))

    summary = {
        "pdf": str(pdf_path),
        "pages": [int(i + 1) for i in page_indices],
        "target_label": args.label,
        "target_color": target,
        "threshold": args.color_threshold,
        "min_bbox_area": args.min_bbox_area,
        "min_short_side": args.min_short_side,
        "min_long_side": args.min_long_side,
        "require_fill": bool(args.require_fill),
        "allow_stroke_match": bool(args.allow_stroke_match),
        "footpath_vector_count": len(candidates),
    }

    return {"summary": summary, "vectors": candidates}


def main() -> int:
    args = parse_args()
    payload = find_footpath_vectors(args)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"Wrote {payload['summary']['footpath_vector_count']} footpath candidates to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Extract vector drawing objects from a PDF into JSON.

Default target is examples/Joal 502.pdf.
The output contains one entry per drawing object reported by PyMuPDF
(page.get_drawings), including style metadata and path commands.

Usage:
    python scripts/find_pdf_vectors.py
    python scripts/find_pdf_vectors.py --pdf "examples/Joal 502.pdf" --out outputs/joal502/vectors_all.json
    python scripts/find_pdf_vectors.py --page 1
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import fitz


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract all vector drawing objects from a PDF")
    parser.add_argument("--pdf", default="examples/Joal 502.pdf", help="Input PDF path")
    parser.add_argument("--out", default="outputs/joal502/vectors_all.json", help="Output JSON path")
    parser.add_argument("--page", type=int, default=0, help="1-based page number (0 = all pages)")
    return parser.parse_args()


def as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def point_to_list(point: Any) -> list[float] | None:
    if point is None:
        return None
    x = as_float(getattr(point, "x", None))
    y = as_float(getattr(point, "y", None))
    if x is None or y is None:
        return None
    return [x, y]


def rect_to_list(rect: Any) -> list[float] | None:
    if rect is None:
        return None
    values = [as_float(getattr(rect, key, None)) for key in ("x0", "y0", "x1", "y1")]
    if any(v is None for v in values):
        return None
    return [float(v) for v in values]


def norm_color(c: Any) -> list[float] | None:
    if not isinstance(c, (list, tuple)) or len(c) < 3:
        return None
    out: list[float] = []
    for v in c[:3]:
        f = as_float(v)
        if f is None:
            return None
        out.append(f)
    return out


def convert_path_item(item: Any) -> dict[str, Any]:
    if not isinstance(item, (list, tuple)) or not item:
        return {"op": "unknown", "raw": repr(item)}

    op = item[0]
    if op == "l" and len(item) >= 3:
        return {
            "op": "line",
            "p0": point_to_list(item[1]),
            "p1": point_to_list(item[2]),
        }
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
    if op == "qu" and len(item) >= 2:
        return {
            "op": "quad",
            "quad": repr(item[1]),
        }

    return {
        "op": str(op),
        "raw": repr(item[1:]),
    }


def drawing_to_json(page_number: int, index_on_page: int, drawing: dict[str, Any]) -> dict[str, Any]:
    rect = drawing.get("rect")
    items = drawing.get("items") or []

    return {
        "page": page_number,
        "index_on_page": index_on_page,
        "seqno": int(drawing.get("seqno", -1)),
        "type": drawing.get("type"),
        "rect": rect_to_list(rect),
        "width": as_float(drawing.get("width")),
        "lineCap": drawing.get("lineCap"),
        "lineJoin": drawing.get("lineJoin"),
        "dashes": drawing.get("dashes"),
        "stroke": norm_color(drawing.get("color")),
        "fill": norm_color(drawing.get("fill")),
        "even_odd": bool(drawing.get("even_odd", False)),
        "closePath": bool(drawing.get("closePath", False)),
        "fill_opacity": as_float(drawing.get("fill_opacity")),
        "stroke_opacity": as_float(drawing.get("stroke_opacity")),
        "path_items": [convert_path_item(it) for it in items],
    }


def process(pdf_path: Path, page_number: int) -> dict[str, Any]:
    doc = fitz.open(pdf_path)
    results: list[dict[str, Any]] = []

    if page_number > 0:
        page_indices = [page_number - 1]
    else:
        page_indices = list(range(doc.page_count))

    for i in page_indices:
        if i < 0 or i >= doc.page_count:
            raise ValueError(f"Page out of range: {page_number}")

        page = doc[i]
        drawings = page.get_drawings()
        for idx, drawing in enumerate(drawings):
            results.append(drawing_to_json(i + 1, idx, drawing))

    doc.close()

    summary = {
        "pdf": str(pdf_path),
        "page_count": int(len(page_indices)),
        "vector_count": int(len(results)),
        "pages": [int(i + 1) for i in page_indices],
    }

    return {
        "summary": summary,
        "vectors": results,
    }


def main() -> int:
    args = parse_args()
    pdf_path = Path(args.pdf)
    out_path = Path(args.out)

    if not pdf_path.exists():
        raise FileNotFoundError(f"Missing PDF: {pdf_path}")

    payload = process(pdf_path, args.page)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"Wrote {payload['summary']['vector_count']} vectors to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

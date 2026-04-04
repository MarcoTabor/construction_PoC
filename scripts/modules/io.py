from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import cv2
import fitz
import numpy as np


def read_json(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8"))


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_mask(path: str | Path) -> np.ndarray:
    img = cv2.imread(str(Path(path)), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise RuntimeError(f"Failed to read mask: {path}")
    return img


def save_image(path: str | Path, image: np.ndarray) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(p), image)


def load_curve_from_json(path: str | Path, key: str) -> np.ndarray:
    data = read_json(path)
    pts = data.get(key) or []
    if not pts:
        raise RuntimeError(f"Missing key {key} in {path}")
    return np.array(pts, dtype=np.float64)


def render_pdf_page_bgr(pdf_path: str | Path, page: int, pixels_per_point: float) -> np.ndarray:
    doc = fitz.open(str(pdf_path))
    page_obj = doc[max(0, int(page) - 1)]
    mat = fitz.Matrix(float(pixels_per_point), float(pixels_per_point))
    pix = page_obj.get_pixmap(matrix=mat, alpha=False)
    doc.close()

    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        return cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)
    return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)

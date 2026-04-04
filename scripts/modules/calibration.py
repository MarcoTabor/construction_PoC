from __future__ import annotations

from pathlib import Path

from .contracts import Calibration
from .io import read_json


def load_calibration(scale_json_path: str | Path) -> Calibration:
    payload = read_json(scale_json_path)
    cal = payload.get("calibration") or {}
    if not cal:
        raise RuntimeError("Missing calibration block")

    return Calibration(
        meters_per_point=float(cal.get("meters_per_point")),
        meters_per_pixel=float(cal.get("meters_per_pixel")),
        pixels_per_point=float(cal.get("pixels_per_point")),
        source_file=str(scale_json_path),
        method=str(cal.get("method", "unknown")),
    )

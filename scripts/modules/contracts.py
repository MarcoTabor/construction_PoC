from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class Calibration:
    meters_per_point: float
    meters_per_pixel: float
    pixels_per_point: float
    source_file: str
    method: str


@dataclass
class Curves:
    centerline_yx: np.ndarray
    inner_line_yx: np.ndarray
    outer_line_yx: np.ndarray
    stage: str
    filters_applied: list[str] = field(default_factory=list)
    smoothing_window: int | None = None


@dataclass
class Metrics:
    lengths_px: dict[str, float]
    lengths_m: dict[str, float]
    area_px2: dict[str, float]
    area_m2: dict[str, float]
    calibration_ref: str
    measurement_policy: dict[str, Any]


@dataclass
class VisualManifest:
    overlay_mask: str
    overlay_plan: str
    transparent_layer: str
    line_styles: dict[str, Any]
    notes: list[str] = field(default_factory=list)

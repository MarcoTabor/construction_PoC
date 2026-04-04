from __future__ import annotations

import numpy as np

from .contracts import Calibration, Curves, Metrics


def _length_px(curve_yx: np.ndarray) -> float:
    if len(curve_yx) < 2:
        return 0.0
    return float(np.linalg.norm(np.diff(curve_yx, axis=0), axis=1).sum())


def _polygon_area_px2(poly_yx: np.ndarray) -> float:
    x = poly_yx[:, 1]
    y = poly_yx[:, 0]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def corridor_area_between_inner_outer_px2(inner_yx: np.ndarray, outer_yx: np.ndarray) -> float:
    poly = np.vstack([outer_yx, inner_yx[::-1]])
    return _polygon_area_px2(poly)


def compute_metrics(curves: Curves, calibration: Calibration, policy: dict[str, object]) -> Metrics:
    center_px = _length_px(curves.centerline_yx)
    inner_px = _length_px(curves.inner_line_yx)
    outer_px = _length_px(curves.outer_line_yx)
    area_px2 = corridor_area_between_inner_outer_px2(curves.inner_line_yx, curves.outer_line_yx)

    mpp = float(calibration.meters_per_pixel)
    return Metrics(
        lengths_px={
            "centerline_px": center_px,
            "inner_px": inner_px,
            "outer_px": outer_px,
        },
        lengths_m={
            "centerline_m": center_px * mpp,
            "inner_m": inner_px * mpp,
            "outer_m": outer_px * mpp,
        },
        area_px2={"corridor_px2": area_px2},
        area_m2={"corridor_m2": area_px2 * (mpp ** 2)},
        calibration_ref=str(calibration.source_file),
        measurement_policy=policy,
    )

from __future__ import annotations

import numpy as np

from .contracts import Curves


def exclude_rows(curve_yx: np.ndarray, rows: set[int]) -> np.ndarray:
    keep = np.array([int(round(y)) not in rows for y in curve_yx[:, 0]], dtype=bool)
    return curve_yx[keep]


def smooth_curve(curve_yx: np.ndarray, window: int) -> np.ndarray:
    if len(curve_yx) < 3:
        return curve_yx.copy()

    k = max(1, int(window))
    if k % 2 == 0:
        k += 1
    if len(curve_yx) < k:
        return curve_yx.copy()

    pad = k // 2
    kernel = np.ones((k,), dtype=np.float64) / float(k)
    y_pad = np.pad(curve_yx[:, 0], (pad, pad), mode="edge")
    x_pad = np.pad(curve_yx[:, 1], (pad, pad), mode="edge")
    ys = np.convolve(y_pad, kernel, mode="valid")
    xs = np.convolve(x_pad, kernel, mode="valid")
    return np.stack([ys, xs], axis=1)


def apply_endpoint_row_exclusion(curves: Curves) -> Curves:
    rows = {
        int(round(curves.centerline_yx[0, 0])),
        int(round(curves.centerline_yx[-1, 0])),
    }
    return Curves(
        centerline_yx=exclude_rows(curves.centerline_yx, rows),
        inner_line_yx=exclude_rows(curves.inner_line_yx, rows),
        outer_line_yx=exclude_rows(curves.outer_line_yx, rows),
        stage="row_excluded",
        filters_applied=curves.filters_applied + [f"exclude_rows={sorted(rows)}"],
        smoothing_window=curves.smoothing_window,
    )


def apply_equal_smoothing(curves: Curves, window: int) -> Curves:
    return Curves(
        centerline_yx=smooth_curve(curves.centerline_yx, window),
        inner_line_yx=smooth_curve(curves.inner_line_yx, window),
        outer_line_yx=smooth_curve(curves.outer_line_yx, window),
        stage="smoothed",
        filters_applied=curves.filters_applied + [f"moving_average_k={int(window)}"],
        smoothing_window=int(window),
    )

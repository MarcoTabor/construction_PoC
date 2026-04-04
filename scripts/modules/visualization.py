from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .contracts import Curves, VisualManifest
from .io import save_image


COLORS = {
    "inner": (255, 0, 255),
    "center": (0, 191, 255),
    "outer": (255, 255, 0),
}


def draw_polyline(image: np.ndarray, curve_yx: np.ndarray, color: tuple[int, int, int], thickness: int = 1) -> None:
    for i in range(1, len(curve_yx)):
        x0, y0 = int(round(curve_yx[i - 1, 1])), int(round(curve_yx[i - 1, 0]))
        x1, y1 = int(round(curve_yx[i, 1])), int(round(curve_yx[i, 0]))
        cv2.line(image, (x0, y0), (x1, y1), color, thickness, lineType=cv2.LINE_8)


def make_mask_overlay(mask: np.ndarray, curves: Curves, out_path: str | Path) -> None:
    vis = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
    vis[mask > 0] = (60, 235, 60)
    draw_polyline(vis, curves.inner_line_yx, COLORS["inner"], 1)
    draw_polyline(vis, curves.centerline_yx, COLORS["center"], 1)
    draw_polyline(vis, curves.outer_line_yx, COLORS["outer"], 1)
    save_image(out_path, vis)


def make_transparent_line_layer(shape_hw: tuple[int, int], curves: Curves, out_path: str | Path) -> None:
    h, w = shape_hw
    rgba = np.zeros((h, w, 4), dtype=np.uint8)

    for curve, color in [
        (curves.inner_line_yx, COLORS["inner"]),
        (curves.centerline_yx, COLORS["center"]),
        (curves.outer_line_yx, COLORS["outer"]),
    ]:
        for i in range(1, len(curve)):
            x0, y0 = int(round(curve[i - 1, 1])), int(round(curve[i - 1, 0]))
            x1, y1 = int(round(curve[i, 1])), int(round(curve[i, 0]))
            cv2.line(rgba, (x0, y0), (x1, y1), (color[0], color[1], color[2], 255), 1, lineType=cv2.LINE_8)

    save_image(out_path, rgba)


def make_plan_composite(
    plan_bgr: np.ndarray,
    curves: Curves,
    out_path: str | Path,
    plan_alpha: int = 95,
    line_thickness: int = 1,
) -> None:
    h, w = plan_bgr.shape[:2]
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[:, :, :3] = plan_bgr
    rgba[:, :, 3] = np.uint8(max(0, min(255, int(plan_alpha))))

    for curve, color in [
        (curves.inner_line_yx, COLORS["inner"]),
        (curves.centerline_yx, COLORS["center"]),
        (curves.outer_line_yx, COLORS["outer"]),
    ]:
        for i in range(1, len(curve)):
            x0, y0 = int(round(curve[i - 1, 1])), int(round(curve[i - 1, 0]))
            x1, y1 = int(round(curve[i, 1])), int(round(curve[i, 0]))
            cv2.line(
                rgba,
                (x0, y0),
                (x1, y1),
                (color[0], color[1], color[2], 255),
                max(1, int(line_thickness)),
                lineType=cv2.LINE_8,
            )

    save_image(out_path, rgba)


def build_visual_manifest(mask_overlay: str, plan_overlay: str, transparent_layer: str) -> VisualManifest:
    return VisualManifest(
        overlay_mask=str(mask_overlay),
        overlay_plan=str(plan_overlay),
        transparent_layer=str(transparent_layer),
        line_styles={
            "inner": {"color_bgr": COLORS["inner"], "thickness": 1},
            "center": {"color_bgr": COLORS["center"], "thickness": 1},
            "outer": {"color_bgr": COLORS["outer"], "thickness": 1},
        },
        notes=["Mask overlay uses green fill for shell visibility."],
    )

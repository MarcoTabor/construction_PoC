#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict
from pathlib import Path

from modules.boundaries import extract_inner_outer_from_shell
from modules.calibration import load_calibration
from modules.centerline import extract_centerline
from modules.contracts import Curves
from modules.filters import apply_endpoint_row_exclusion, apply_equal_smoothing
from modules.io import load_curve_from_json, load_mask, render_pdf_page_bgr, write_json
from modules.metrics import compute_metrics
from modules.visualization import (
    build_visual_manifest,
    make_mask_overlay,
    make_plan_composite,
    make_transparent_line_layer,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run JOAL modular geometry pipeline")
    p.add_argument("--pdf", default="examples/Joal 502.pdf")
    p.add_argument("--vectors-json", default="outputs/joal502/joal_vectors_relaxed.json")
    p.add_argument("--scale-json", default="outputs/scale_detection/scale_detection.json")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--pixels-per-point", type=float, default=3.0)
    p.add_argument("--seed-spacing-m", type=float, default=1.0)
    p.add_argument("--clip-endcaps-px", type=float, default=55.0)
    p.add_argument("--extend-lookahead-nodes", type=int, default=24)
    p.add_argument("--shell-smooth-window", type=int, default=9)
    p.add_argument("--smooth-window", type=int, default=9)
    p.add_argument("--plan-alpha", type=int, default=170)
    p.add_argument("--plan-line-thickness", type=int, default=2)
    p.add_argument("--outdir", default="outputs/joal502/modular")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    outdir = Path(args.outdir)
    vis_dir = outdir / "visualizations"
    vis_dir.mkdir(parents=True, exist_ok=True)

    center_json = outdir / "centerline.json"
    shell_mask = outdir / "shell_mask.png"
    center_overlay = vis_dir / "centerline_overlay.png"

    extract_centerline(
        pdf=args.pdf,
        vectors_json=args.vectors_json,
        scale_json=args.scale_json,
        page=args.page,
        seed_spacing_m=args.seed_spacing_m,
        pixels_per_point=args.pixels_per_point,
        clip_endcaps_px=args.clip_endcaps_px,
        extend_lookahead_nodes=args.extend_lookahead_nodes,
        out_json=center_json,
        out_mask=shell_mask,
        out_overlay=center_overlay,
    )

    shell_json = outdir / "inner_outer_shell.json"
    shell_overlay = vis_dir / "inner_outer_shell_overlay.png"
    extract_inner_outer_from_shell(
        mask=shell_mask,
        centerline_json=center_json,
        smooth_window=args.shell_smooth_window,
        out_json=shell_json,
        out_overlay=shell_overlay,
    )

    curves = Curves(
        centerline_yx=load_curve_from_json(shell_json, "centerline_yx"),
        inner_line_yx=load_curve_from_json(shell_json, "inner_line_yx"),
        outer_line_yx=load_curve_from_json(shell_json, "outer_line_yx"),
        stage="raw_shell",
    )

    curves = apply_endpoint_row_exclusion(curves)
    curves = apply_equal_smoothing(curves, window=args.smooth_window)

    calib = load_calibration(args.scale_json)
    metrics = compute_metrics(
        curves,
        calibration=calib,
        policy={
            "boundary_method": "shell_contour_split",
            "filters": curves.filters_applied,
            "smooth_window": curves.smoothing_window,
        },
    )

    mask = load_mask(shell_mask)
    plan = render_pdf_page_bgr(args.pdf, page=args.page, pixels_per_point=args.pixels_per_point)

    mask_overlay = vis_dir / "final_mask_overlay.png"
    transparent_layer = vis_dir / "final_lines_transparent.png"
    plan_overlay = vis_dir / "final_lines_on_plan_transparent.png"

    make_mask_overlay(mask, curves, mask_overlay)
    make_transparent_line_layer(mask.shape, curves, transparent_layer)
    make_plan_composite(
        plan,
        curves,
        plan_overlay,
        plan_alpha=int(args.plan_alpha),
        line_thickness=int(args.plan_line_thickness),
    )

    manifest = build_visual_manifest(mask_overlay, plan_overlay, transparent_layer)

    curves_json = outdir / "curves_smoothed.json"
    write_json(
        curves_json,
        {
            "summary": {
                "stage": curves.stage,
                "filters_applied": curves.filters_applied,
                "smoothing_window": curves.smoothing_window,
            },
            "centerline_yx": curves.centerline_yx.tolist(),
            "inner_line_yx": curves.inner_line_yx.tolist(),
            "outer_line_yx": curves.outer_line_yx.tolist(),
        },
    )

    metrics_json = outdir / "metrics.json"
    write_json(metrics_json, asdict(metrics))

    run_summary = outdir / "run_summary.json"
    write_json(
        run_summary,
        {
            "inputs": {
                "pdf": args.pdf,
                "vectors_json": args.vectors_json,
                "scale_json": args.scale_json,
                "page": args.page,
                "pixels_per_point": args.pixels_per_point,
            },
            "outputs": {
                "centerline_json": str(center_json),
                "shell_mask": str(shell_mask),
                "shell_inner_outer_json": str(shell_json),
                "curves_smoothed_json": str(curves_json),
                "metrics_json": str(metrics_json),
                "visual_manifest": asdict(manifest),
            },
        },
    )

    print(f"Wrote run summary: {run_summary}")
    print(f"Centerline length m: {metrics.lengths_m['centerline_m']:.3f}")
    print(f"Inner length m: {metrics.lengths_m['inner_m']:.3f}")
    print(f"Outer length m: {metrics.lengths_m['outer_m']:.3f}")
    print(f"Corridor area m2: {metrics.area_m2['corridor_m2']:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

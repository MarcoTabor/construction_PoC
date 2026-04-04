from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def extract_centerline(
    pdf: str | Path,
    vectors_json: str | Path,
    scale_json: str | Path,
    page: int,
    seed_spacing_m: float,
    pixels_per_point: float,
    clip_endcaps_px: float,
    extend_lookahead_nodes: int,
    out_json: str | Path,
    out_mask: str | Path,
    out_overlay: str | Path,
) -> None:
    cmd = [
        sys.executable,
        "scripts/joal_single_shape_centerline.py",
        "--pdf",
        str(pdf),
        "--vectors-json",
        str(vectors_json),
        "--scale-json",
        str(scale_json),
        "--page",
        str(int(page)),
        "--seed-spacing-m",
        str(float(seed_spacing_m)),
        "--pixels-per-point",
        str(float(pixels_per_point)),
        "--anchor-strategy",
        "farthest-endpoints",
        "--clip-endcaps-px",
        str(float(clip_endcaps_px)),
        "--trim-end-cap-factor",
        "0",
        "--trim-end-min-px",
        "0",
        "--extend-ends-to-mask",
        "--extend-lookahead-nodes",
        str(int(extend_lookahead_nodes)),
        "--out-json",
        str(out_json),
        "--out-mask",
        str(out_mask),
        "--out-overlay",
        str(out_overlay),
    ]
    subprocess.run(cmd, check=True)

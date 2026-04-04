from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def ensure_shell_mask(
    pdf: str | Path,
    vectors_json: str | Path,
    scale_json: str | Path,
    page: int,
    pixels_per_point: float,
    close_kernel: int,
    out_mask: str | Path,
) -> None:
    """Generate/refresh shell mask by running centerline script in mask-only mode.

    We reuse joal_single_shape_centerline.py because it already contains the
    vector->filled-shell construction used in the accepted workflow.
    """
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
        "--pixels-per-point",
        str(float(pixels_per_point)),
        "--close-kernel",
        str(int(close_kernel)),
        "--out-mask",
        str(out_mask),
        "--out-overlay",
        str(Path(out_mask).with_name("_tmp_shell_overlay.png")),
        "--out-json",
        str(Path(out_mask).with_name("_tmp_shell_centerline_unused.json")),
    ]
    subprocess.run(cmd, check=True)

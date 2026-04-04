from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def extract_inner_outer_from_shell(
    mask: str | Path,
    centerline_json: str | Path,
    smooth_window: int,
    out_json: str | Path,
    out_overlay: str | Path,
) -> None:
    cmd = [
        sys.executable,
        "scripts/extract_inner_outer_from_shell_contour.py",
        "--mask",
        str(mask),
        "--centerline-json",
        str(centerline_json),
        "--smooth-window",
        str(int(smooth_window)),
        "--out-json",
        str(out_json),
        "--out-overlay",
        str(out_overlay),
    ]
    subprocess.run(cmd, check=True)

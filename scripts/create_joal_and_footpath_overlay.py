#!/usr/bin/env python3
"""Run to overlay both Joal and Footpath masks onto a single image."""
import json
from pathlib import Path

import fitz
import numpy as np
import cv2

def main():
    pdf_path = Path("examples/Joal 502.pdf")
    joal_mask_path = Path("outputs/joal502/modular/shell_mask.png")
    footpath_mask_path = Path("outputs/joal502/visualizations/footpath_cutout_alpha_mask.png")
    out_path = Path("outputs/joal502/visualizations/joal_and_footpath_overlay.png")

    doc = fitz.open(pdf_path)
    page = doc[0]
    
    # Read masks FIRST to determine exact PDF render scale
    joal_mask = cv2.imread(str(joal_mask_path), cv2.IMREAD_GRAYSCALE)
    if joal_mask is None:
        print(f"Could not read {joal_mask_path}")
        return
        
    h, w = joal_mask.shape
    
    # Calculate exactly what DPI produces width `w`
    # Points to inch is 72. (w / rect.width) gives multiplier.
    scale = w / page.rect.width
    dpi = int(scale * 72)

    # Render PDF matching that mask width exactly.
    # Force white background since transparency messes up blend
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=True)
    
    # Composite onto white background
    img_array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        # Create solid white background
        white_bg = np.full((pix.height, pix.width, 3), 255, dtype=np.uint8)
        alpha_channel = img_array[:, :, 3] / 255.0
        for c in range(3):
            white_bg[:, :, c] = (alpha_channel * img_array[:, :, c] + (1 - alpha_channel) * white_bg[:, :, c]).astype(np.uint8)
        rgb = white_bg
    else:
        rgb = img_array[:, :, :3].copy()

    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

    # Resize PDF render exactly to match mask if it's off by exactly 1 pixel because of float rounding
    if bgr.shape[:2] != (h, w):
        bgr = cv2.resize(bgr, (w, h), interpolation=cv2.INTER_LANCZOS4)

    alpha = 0.45
    faded = cv2.addWeighted(bgr, alpha, np.full_like(bgr, 255), 1.0 - alpha, 0.0)

    # Overlay Joal Mask (Blue/Orangeish)
    # Using light blue fill: (255, 191, 0) deep sky blue in BGR -> (235, 206, 135) light blue
    joal_color = np.array([255, 144, 30], dtype=np.uint8) # BGR: deep sky blue tint
    joal_indices = joal_mask > 80
    faded[joal_indices] = (faded[joal_indices] * 0.4 + joal_color * 0.6).astype(np.uint8)

    # Footpath mask
    if footpath_mask_path.exists():
        footpath_mask = cv2.imread(str(footpath_mask_path), cv2.IMREAD_GRAYSCALE)
        if footpath_mask is not None:
             if footpath_mask.shape != joal_mask.shape:
                 footpath_mask = cv2.resize(footpath_mask, (w, h), interpolation=cv2.INTER_NEAREST)
             
             # Yellowish orange for Footpath
             footpath_color = np.array([0, 165, 255], dtype=np.uint8) # BGR: orange
             footpath_indices = footpath_mask > 80
             faded[footpath_indices] = (faded[footpath_indices] * 0.4 + footpath_color * 0.6).astype(np.uint8)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), faded)
    print(f"Created overlay at {out_path}")

if __name__ == "__main__":
    main()
#!/usr/bin/env python3
"""Step 4 - Refine seed positions toward path center while keeping connectivity fixed.

Input graph edges remain unchanged; only active seed coordinates are optimized on
clean mask using a distance-transform objective plus continuity regularization
and curvature penalty for smooth paths.

Outputs:
- outputs/footpath_pixel_pipeline/visualizations/seeds_04_refined_graph.json
- outputs/footpath_pixel_pipeline/visualizations/seeds_04_refined_on_clean_mask.png
- outputs/footpath_pixel_pipeline/visualizations/seeds_04_refined_on_page.png
- outputs/footpath_pixel_pipeline/visualizations/seeds_04_refine_stats.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Refine connected seeds toward clean-mask centerline")
    p.add_argument("--graph-json", default="outputs/footpath_pixel_pipeline/visualizations/seeds_02_graph.json")
    p.add_argument("--clean-mask", default="outputs/footpath_pixel_pipeline/visualizations/stage_03_clean_mask.png")
    p.add_argument("--page", default="outputs/footpath_pixel_pipeline/visualizations/stage_01_page.png")
    p.add_argument("--outdir", default="outputs/footpath_pixel_pipeline/visualizations")
    p.add_argument("--iters", type=int, default=8, help="Maximum optimization iterations")
    p.add_argument("--search-radius", type=int, default=12, help="Candidate search radius (px)")
    p.add_argument("--endpoint-radius", type=int, default=4, help="Smaller search radius for endpoints")
    p.add_argument("--snap-radius", type=int, default=8, help="Initial snap radius to nearest clean white pixel")
    p.add_argument("--w-dist", type=float, default=1.0, help="Weight for distance-transform center attraction")
    p.add_argument("--w-mid", type=float, default=0.03, help="Weight for midpoint regularization")
    p.add_argument("--w-move", type=float, default=0.02, help="Weight for staying near previous iteration")
    p.add_argument("--w-curv", type=float, default=0.015, help="Weight for curvature penalty (penalize sharp turns)")
    return p.parse_args()


def load_graph(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_adj(edges: list[dict], active_ids: set[int]) -> dict[int, list[int]]:
    adj: dict[int, list[int]] = {i: [] for i in active_ids}
    for e in edges:
        a = int(e["a"])
        b = int(e["b"])
        if a in active_ids and b in active_ids:
            adj[a].append(b)
            adj[b].append(a)
    return adj


def ordered_path(adj: dict[int, list[int]], active_ids: set[int], start_end: list[int]) -> list[int]:
    endpoints = [i for i in active_ids if len(adj[i]) == 1]
    if start_end and int(start_end[0]) in active_ids:
        start = int(start_end[0])
    elif endpoints:
        start = endpoints[0]
    else:
        start = min(active_ids)

    out = [start]
    prev = -1
    cur = start
    seen = {start}
    while True:
        nxts = [n for n in adj[cur] if n != prev]
        if not nxts:
            break
        nxt = nxts[0]
        if nxt in seen:
            break
        out.append(nxt)
        seen.add(nxt)
        prev, cur = cur, nxt
    return out


def snap_to_white(mask_bin: np.ndarray, x: float, y: float, radius: int) -> tuple[float, float, bool]:
    px = int(round(x))
    py = int(round(y))
    h, w = mask_bin.shape
    if 0 <= px < w and 0 <= py < h and mask_bin[py, px] > 0:
        return float(px), float(py), False

    r = int(radius)
    x0, x1 = max(0, px - r), min(w, px + r + 1)
    y0, y1 = max(0, py - r), min(h, py + r + 1)
    if x1 <= x0 or y1 <= y0:
        return float(px), float(py), False

    roi = mask_bin[y0:y1, x0:x1]
    ys, xs = np.where(roi > 0)
    if len(xs) == 0:
        return float(px), float(py), False

    gx = xs + x0
    gy = ys + y0
    d2 = (gx - px) ** 2 + (gy - py) ** 2
    k = int(np.argmin(d2))
    return float(gx[k]), float(gy[k]), True


def candidate_points(mask_bin: np.ndarray, cx: float, cy: float, radius: int) -> list[tuple[int, int]]:
    px = int(round(cx))
    py = int(round(cy))
    h, w = mask_bin.shape
    r = int(radius)
    x0, x1 = max(0, px - r), min(w, px + r + 1)
    y0, y1 = max(0, py - r), min(h, py + r + 1)
    if x1 <= x0 or y1 <= y0:
        return []

    roi = mask_bin[y0:y1, x0:x1]
    ys, xs = np.where(roi > 0)
    if len(xs) == 0:
        return []

    out: list[tuple[int, int]] = []
    rr2 = r * r
    for xx, yy in zip(xs.tolist(), ys.tolist()):
        gx = xx + x0
        gy = yy + y0
        if (gx - px) * (gx - px) + (gy - py) * (gy - py) <= rr2:
            out.append((gx, gy))
    return out


def perpendicular_distance(p0: tuple[float, float], p1: tuple[float, float], p2: tuple[float, float]) -> float:
    """Distance from p1 to the line segment p0-p2."""
    x0, y0 = p0
    x1, y1 = p1
    x2, y2 = p2
    
    dx = x2 - x0
    dy = y2 - y0
    norm2 = dx * dx + dy * dy
    if norm2 < 1e-6:
        return ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
    
    t_proj = ((x1 - x0) * dx + (y1 - y0) * dy) / norm2
    t_proj = max(0.0, min(1.0, t_proj))
    
    closest_x = x0 + t_proj * dx
    closest_y = y0 + t_proj * dy
    
    return ((x1 - closest_x) ** 2 + (y1 - closest_y) ** 2) ** 0.5


def main() -> None:
    args = parse_args()

    graph = load_graph(Path(args.graph_json))
    seeds = graph["seeds"]
    edges = graph["edges"]
    start_end = graph.get("start_end") or graph.get("endpoint_ids", [])

    active_ids = {int(s["id"]) for s in seeds if s.get("active", True)}
    adj = build_adj(edges, active_ids)
    order = ordered_path(adj, active_ids, start_end)
    if len(order) < 3:
        raise RuntimeError("Path too short to refine")

    mask_bgr = cv2.imread(str(args.clean_mask))
    page_bgr = cv2.imread(str(args.page))
    if mask_bgr is None:
        raise FileNotFoundError(f"Clean mask not found: {args.clean_mask}")
    if page_bgr is None:
        raise FileNotFoundError(f"Page not found: {args.page}")

    gray = cv2.cvtColor(mask_bgr, cv2.COLOR_BGR2GRAY)
    _, mask_bin = cv2.threshold(gray, 127, 255, cv2.THRESH_BINARY)
    mask_bin = cv2.morphologyEx(mask_bin, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))

    dist = cv2.distanceTransform(mask_bin, cv2.DIST_L2, 5)

    id_to_xy0: dict[int, tuple[float, float]] = {int(s["id"]): (float(s["x"]), float(s["y"])) for s in seeds}
    id_to_xy: dict[int, tuple[float, float]] = dict(id_to_xy0)

    snapped = 0
    for sid in active_ids:
        x, y = id_to_xy[sid]
        sx, sy, moved = snap_to_white(mask_bin, x, y, args.snap_radius)
        id_to_xy[sid] = (sx, sy)
        if moved:
            snapped += 1

    endpoint_set = set(int(v) for v in start_end[:2])
    if len(endpoint_set) < 2:
        endpoint_set = {i for i in active_ids if len(adj[i]) == 1}

    move_history: list[float] = []
    for _ in range(int(args.iters)):
        updates: dict[int, tuple[float, float]] = {}
        total_move = 0.0

        for idx, sid in enumerate(order):
            x, y = id_to_xy[sid]

            if sid in endpoint_set:
                radius = int(args.endpoint_radius)
            else:
                radius = int(args.search_radius)

            cands = candidate_points(mask_bin, x, y, radius)
            if not cands:
                updates[sid] = (x, y)
                continue

            if idx == 0:
                x_n, y_n = id_to_xy[order[idx + 1]]
                mx, my = x_n, y_n
            elif idx == len(order) - 1:
                x_p, y_p = id_to_xy[order[idx - 1]]
                mx, my = x_p, y_p
            else:
                x_p, y_p = id_to_xy[order[idx - 1]]
                x_n, y_n = id_to_xy[order[idx + 1]]
                mx, my = 0.5 * (x_p + x_n), 0.5 * (y_p + y_n)

            best = (x, y)
            best_score = -1e18
            for cx, cy in cands:
                d_center = float(dist[cy, cx])
                d_mid2 = (cx - mx) * (cx - mx) + (cy - my) * (cy - my)
                d_move2 = (cx - x) * (cx - x) + (cy - y) * (cy - y)
                
                d_curv = 0.0
                if idx > 0 and idx < len(order) - 1:
                    x_p, y_p = id_to_xy[order[idx - 1]]
                    x_n, y_n = id_to_xy[order[idx + 1]]
                    d_curv = perpendicular_distance((x_p, y_p), (cx, cy), (x_n, y_n))

                score = (float(args.w_dist) * d_center 
                         - float(args.w_mid) * d_mid2 
                         - float(args.w_move) * d_move2 
                         - float(args.w_curv) * d_curv)
                if score > best_score:
                    best_score = score
                    best = (float(cx), float(cy))

            updates[sid] = best
            dx = best[0] - x
            dy = best[1] - y
            total_move += (dx * dx + dy * dy) ** 0.5

        for sid, xy in updates.items():
            id_to_xy[sid] = xy

        mean_move = total_move / float(len(order))
        move_history.append(mean_move)
        if mean_move < 0.15:
            break

    disps = []
    for sid in order:
        x0, y0 = id_to_xy0[sid]
        x1, y1 = id_to_xy[sid]
        disps.append(((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5)

    disp_arr = np.array(disps, dtype=np.float32)

    refined = json.loads(json.dumps(graph))
    for s in refined["seeds"]:
        sid = int(s["id"])
        if sid in id_to_xy and sid in active_ids:
            rx, ry = id_to_xy[sid]
            s["x"] = int(round(rx))
            s["y"] = int(round(ry))

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    vis_mask = cv2.cvtColor(mask_bin, cv2.COLOR_GRAY2BGR)
    for e in edges:
        a = int(e["a"])
        b = int(e["b"])
        if a in active_ids and b in active_ids and a in id_to_xy0 and b in id_to_xy0:
            p0 = (int(round(id_to_xy0[a][0])), int(round(id_to_xy0[a][1])))
            p1 = (int(round(id_to_xy0[b][0])), int(round(id_to_xy0[b][1])))
            cv2.line(vis_mask, p0, p1, (0, 140, 255), 1)
    for e in edges:
        a = int(e["a"])
        b = int(e["b"])
        if a in active_ids and b in active_ids and a in id_to_xy and b in id_to_xy:
            p0 = (int(round(id_to_xy[a][0])), int(round(id_to_xy[a][1])))
            p1 = (int(round(id_to_xy[b][0])), int(round(id_to_xy[b][1])))
            cv2.line(vis_mask, p0, p1, (60, 220, 80), 2)

    for sid in order:
        x, y = id_to_xy[sid]
        cv2.circle(vis_mask, (int(round(x)), int(round(y))), 3, (0, 0, 255), -1)

    vis_page = page_bgr.copy()
    ph, pw = vis_page.shape[:2]
    mh, mw = mask_bin.shape
    sx = pw / float(mw)
    sy = ph / float(mh)

    for e in edges:
        a = int(e["a"])
        b = int(e["b"])
        if a in active_ids and b in active_ids and a in id_to_xy and b in id_to_xy:
            p0 = (int(round(id_to_xy[a][0] * sx)), int(round(id_to_xy[a][1] * sy)))
            p1 = (int(round(id_to_xy[b][0] * sx)), int(round(id_to_xy[b][1] * sy)))
            cv2.line(vis_page, p0, p1, (60, 220, 80), 2)

    if start_end and len(start_end) >= 2:
        s_id = int(start_end[0])
        e_id = int(start_end[1])
        if s_id in id_to_xy:
            sp = (int(round(id_to_xy[s_id][0] * sx)), int(round(id_to_xy[s_id][1] * sy)))
            cv2.circle(vis_page, sp, 9, (255, 0, 255), 2)
            cv2.putText(vis_page, "S", (sp[0] + 7, sp[1] - 7), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 0, 255), 2, cv2.LINE_AA)
        if e_id in id_to_xy:
            ep = (int(round(id_to_xy[e_id][0] * sx)), int(round(id_to_xy[e_id][1] * sy)))
            cv2.circle(vis_page, ep, 9, (0, 255, 255), 2)
            cv2.putText(vis_page, "E", (ep[0] + 7, ep[1] - 7), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2, cv2.LINE_AA)

    p_graph = outdir / "seeds_04_refined_graph.json"
    p_v1 = outdir / "seeds_04_refined_on_clean_mask.png"
    p_v2 = outdir / "seeds_04_refined_on_page.png"
    p_stats = outdir / "seeds_04_refine_stats.json"

    p_graph.write_text(json.dumps(refined, indent=2), encoding="utf-8")
    cv2.imwrite(str(p_v1), vis_mask)
    cv2.imwrite(str(p_v2), vis_page)

    stats = {
        "input_graph": str(args.graph_json),
        "w_dist": float(args.w_dist),
        "w_mid": float(args.w_mid),
        "w_move": float(args.w_move),
        "w_curv": float(args.w_curv),
        "iters_requested": int(args.iters),
        "iters_run": len(move_history),
        "mean_move_per_iter": move_history,
        "snapped_seed_count": int(snapped),
        "path_len": len(order),
        "disp_px": {
            "median": float(np.median(disp_arr)),
            "mean": float(np.mean(disp_arr)),
            "p90": float(np.percentile(disp_arr, 90)),
            "max": float(np.max(disp_arr)),
        },
    }
    p_stats.write_text(json.dumps(stats, indent=2), encoding="utf-8")

    print(f"Refined active path nodes: {len(order)}")
    print(f"Iterations run: {len(move_history)}")
    print(f"Weights: w_dist={args.w_dist}, w_mid={args.w_mid}, w_move={args.w_move}, w_curv={args.w_curv}")
    print(f"Median displacement: {stats['disp_px']['median']:.2f} px")
    print(f"Wrote: {p_graph}")
    print(f"Wrote: {p_v1}")
    print(f"Wrote: {p_v2}")
    print(f"Wrote: {p_stats}")


if __name__ == "__main__":
    main()

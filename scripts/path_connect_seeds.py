#!/usr/bin/env python3
"""Step 2 – Connect seeds with a degree-constrained optimized graph.

Heuristic:
1) Generate seeds from raw mask (with legend exclusion).
2) Create candidate edges among k-nearest seeds.
3) Edge cost = distance + outside_penalty (if segment leaves white mask).
4) Build graph greedily with constraints:
   - each seed degree <= 2
   - no cycles (until we connect all components)

Outputs:
- outputs/footpath_pixel_pipeline/visualizations/seeds_02_connected_on_mask.png
- outputs/footpath_pixel_pipeline/visualizations/seeds_02_connected_on_page.png
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Connect footpath seeds with degree constraints")
    p.add_argument("--mask", default="outputs/footpath_pixel_pipeline/visualizations/stage_02_raw_mask.png")
    p.add_argument("--page", default="outputs/footpath_pixel_pipeline/visualizations/stage_01_page.png")
    p.add_argument("--outdir", default="outputs/footpath_pixel_pipeline/visualizations")
    p.add_argument("--legend-json", default="outputs/legend_colors/legend_colors.json")
    p.add_argument("--pdf", default="examples/Joal 502.pdf")
    p.add_argument("--pdf-page", type=int, default=1)
    p.add_argument("--legend-pad", type=int, default=12)
    p.add_argument("--min-dist", type=int, default=40)
    p.add_argument("--erode-px", type=int, default=1)
    p.add_argument("--k-neighbors", type=int, default=8,
                   help="Nearest neighbors considered per seed")
    p.add_argument("--max-edge-dist", type=float, default=95.0,
                   help="Hard cap for allowed distance (px) between connected seeds")
    p.add_argument("--component-mode", choices=["largest", "all"], default="largest",
                   help="Use only the largest connected seed component, or keep all components")
    p.add_argument("--outside-weight", type=float, default=5.0,
                   help="Penalty multiplier when candidate edge leaves white mask")
    p.add_argument("--samples", type=int, default=40,
                   help="Segment sampling points for outside-mask ratio")
    return p.parse_args()


def greedy_seeds(mask: np.ndarray, min_dist: int) -> list[tuple[int, int]]:
    ys, xs = np.where(mask > 0)
    if len(xs) == 0:
        return []

    order = np.lexsort((xs, ys))
    pts = list(zip(xs[order].tolist(), ys[order].tolist()))

    taken = np.zeros_like(mask, dtype=bool)
    seeds: list[tuple[int, int]] = []
    min_d2 = min_dist * min_dist

    for x, y in pts:
        if taken[y, x]:
            continue
        seeds.append((x, y))
        r = min_dist
        y0, y1 = max(0, y - r), min(mask.shape[0], y + r + 1)
        x0, x1 = max(0, x - r), min(mask.shape[1], x + r + 1)
        gy, gx = np.ogrid[y0:y1, x0:x1]
        circle = (gy - y) ** 2 + (gx - x) ** 2 <= min_d2
        taken[y0:y1, x0:x1][circle] = True

    return seeds


def legend_exclusion_rect(args: argparse.Namespace, mask_shape: tuple[int, int]) -> tuple[int, int, int, int] | None:
    if fitz is None:
        return None

    legend_json_path = Path(args.legend_json)
    if not legend_json_path.exists():
        return None

    legend_data = json.loads(legend_json_path.read_text(encoding="utf-8"))

    doc = fitz.open(str(args.pdf))
    page = doc.load_page(args.pdf_page - 1)
    h, w = mask_shape
    sx = w / page.rect.width
    sy = h / page.rect.height
    doc.close()

    x0s: list[float] = []
    y0s: list[float] = []
    x1s: list[float] = []
    y1s: list[float] = []
    for entry in legend_data.get("legend_entries", []):
        lb = entry.get("label_bbox")
        if isinstance(lb, list) and len(lb) == 4:
            x0s.append(float(lb[0]))
            y0s.append(float(lb[1]))
            x1s.append(float(lb[2]))
            y1s.append(float(lb[3]))

        sw = entry.get("swatch") or {}
        sb = sw.get("bbox") if isinstance(sw, dict) else None
        if isinstance(sb, list) and len(sb) == 4:
            x0s.append(float(sb[0]))
            y0s.append(float(sb[1]))
            x1s.append(float(sb[2]))
            y1s.append(float(sb[3]))

    bbox_pdf = None
    if x0s:
        bbox_pdf = [min(x0s), min(y0s), max(x1s), max(y1s)]
    else:
        maybe = legend_data.get("legend_region_bbox")
        if isinstance(maybe, list) and len(maybe) == 4:
            bbox_pdf = maybe

    if bbox_pdf is None:
        return None

    pad = int(args.legend_pad)
    lx0 = max(0, int(round(float(bbox_pdf[0]) * sx)) - pad)
    ly0 = max(0, int(round(float(bbox_pdf[1]) * sy)) - pad)
    lx1 = min(w, int(round(float(bbox_pdf[2]) * sx)) + pad)
    ly1 = min(h, int(round(float(bbox_pdf[3]) * sy)) + pad)
    return (lx0, ly0, lx1, ly1)


def sample_outside_ratio(mask_bin: np.ndarray, p0: tuple[int, int], p1: tuple[int, int], samples: int) -> float:
    x0, y0 = p0
    x1, y1 = p1

    ts = np.linspace(0.0, 1.0, max(2, samples))
    xs = np.clip(np.round(x0 + (x1 - x0) * ts).astype(np.int32), 0, mask_bin.shape[1] - 1)
    ys = np.clip(np.round(y0 + (y1 - y0) * ts).astype(np.int32), 0, mask_bin.shape[0] - 1)

    vals = mask_bin[ys, xs] > 0
    inside = np.count_nonzero(vals)
    return 1.0 - (inside / float(len(vals)))


class DSU:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))
        self.rank = [0] * n

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1
        return True


def pick_start_end(endpoints: list[int], seeds: list[tuple[int, int]]) -> tuple[int, int] | None:
    """Deterministically pick start/end among endpoint nodes."""
    if len(endpoints) != 2:
        return None
    i0, i1 = endpoints
    x0, y0 = seeds[i0]
    x1, y1 = seeds[i1]
    # Prefer top-most as start; tie-breaker left-most.
    if (y0, x0) <= (y1, x1):
        return i0, i1
    return i1, i0


def main() -> None:
    args = parse_args()

    mask_bgr = cv2.imread(str(args.mask))
    page_bgr = cv2.imread(str(args.page))
    if mask_bgr is None:
        raise FileNotFoundError(f"Mask not found: {args.mask}")
    if page_bgr is None:
        raise FileNotFoundError(f"Page not found: {args.page}")

    gray = cv2.cvtColor(mask_bgr, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 127, 255, cv2.THRESH_BINARY)

    if args.erode_px > 0:
        k = 2 * int(args.erode_px) + 1
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
        binary = cv2.erode(binary, kernel)

    seeds = greedy_seeds(binary, int(args.min_dist))

    legend_rect = legend_exclusion_rect(args, binary.shape)
    if legend_rect is not None:
        lx0, ly0, lx1, ly1 = legend_rect
        seeds = [(x, y) for (x, y) in seeds if not (lx0 <= x <= lx1 and ly0 <= y <= ly1)]

    n = len(seeds)
    print(f"Seeds after legend filter: {n}")
    if n < 2:
        raise RuntimeError("Not enough seeds to connect")

    pts = np.array(seeds, dtype=np.float32)  # (n,2) as (x,y)
    dmat = np.sqrt(((pts[:, None, :] - pts[None, :, :]) ** 2).sum(axis=2))

    k = max(2, int(args.k_neighbors))
    candidate_set: set[tuple[int, int]] = set()
    for i in range(n):
        order = np.argsort(dmat[i])
        neigh = [j for j in order if j != i][:k]
        for j in neigh:
            a, b = (i, j) if i < j else (j, i)
            candidate_set.add((a, b))

    edges: list[tuple[float, int, int, float, float]] = []
    # (cost, i, j, dist, outside_ratio)
    skipped_long = 0
    for i, j in candidate_set:
        pi = (int(seeds[i][0]), int(seeds[i][1]))
        pj = (int(seeds[j][0]), int(seeds[j][1]))
        dist = float(dmat[i, j])
        if dist > float(args.max_edge_dist):
            skipped_long += 1
            continue
        outside_ratio = sample_outside_ratio(binary, pi, pj, int(args.samples))
        cost = dist * (1.0 + float(args.outside_weight) * outside_ratio)
        edges.append((cost, i, j, dist, outside_ratio))

    edges.sort(key=lambda e: e[0])

    dsu = DSU(n)
    deg = np.zeros(n, dtype=np.int32)
    chosen: list[tuple[int, int, float, float]] = []

    # Phase 1: Kruskal-like forest with degree <= 2.
    for _, i, j, dist, outside_ratio in edges:
        if deg[i] >= 2 or deg[j] >= 2:
            continue
        if dsu.find(i) == dsu.find(j):
            continue
        dsu.union(i, j)
        deg[i] += 1
        deg[j] += 1
        chosen.append((i, j, dist, outside_ratio))

    # Phase 2: if still disconnected, bridge components with endpoint-only links.
    def comp_count() -> int:
        roots = {dsu.find(i) for i in range(n)}
        return len(roots)

    if comp_count() > 1:
        for _, i, j, dist, outside_ratio in edges:
            if deg[i] >= 2 or deg[j] >= 2:
                continue
            if dsu.find(i) == dsu.find(j):
                continue
            dsu.union(i, j)
            deg[i] += 1
            deg[j] += 1
            chosen.append((i, j, dist, outside_ratio))
            if comp_count() == 1:
                break

    # Optionally keep only the largest connected component.
    active_nodes: set[int] = set(range(n))
    if args.component_mode == "largest":
        root_to_nodes: dict[int, list[int]] = {}
        for i in range(n):
            r = dsu.find(i)
            root_to_nodes.setdefault(r, []).append(i)
        if root_to_nodes:
            keep_nodes = max(root_to_nodes.values(), key=len)
            active_nodes = set(keep_nodes)
            chosen = [(i, j, dist, outside) for (i, j, dist, outside) in chosen if i in active_nodes and j in active_nodes]
            # Rebuild degree and DSU over active subgraph for consistent stats.
            dsu = DSU(n)
            deg = np.zeros(n, dtype=np.int32)
            for i, j, _, _ in chosen:
                dsu.union(i, j)
                deg[i] += 1
                deg[j] += 1

    endpoint_ids = [int(i) for i in active_nodes if deg[i] == 1]
    endpoints = len(endpoint_ids)
    isolated = int(sum(1 for i in active_nodes if deg[i] == 0))
    components = len({dsu.find(i) for i in active_nodes}) if active_nodes else 0
    start_end = pick_start_end(endpoint_ids, seeds)

    longest = max((d for _, _, d, _ in chosen), default=0.0)
    print(f"Candidate edges skipped by max-edge-dist: {skipped_long}")
    print(f"Chosen edges: {len(chosen)}")
    print(f"Components: {components}, endpoints(deg=1): {endpoints}, isolated(deg=0): {isolated}")
    print(f"Longest chosen edge: {longest:.2f} px (limit={float(args.max_edge_dist):.2f})")
    if start_end is not None:
        s, e = start_end
        print(f"Start seed id={s} xy={seeds[s]}")
        print(f"End   seed id={e} xy={seeds[e]}")
    else:
        print("Warning: expected exactly 2 endpoints for a single open path")

    vis_mask = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    if legend_rect is not None:
        lx0, ly0, lx1, ly1 = legend_rect
        cv2.rectangle(vis_mask, (lx0, ly0), (lx1, ly1), (255, 100, 0), 2)

    for i, j, _, outside_ratio in chosen:
        p0 = seeds[i]
        p1 = seeds[j]
        # greener edge means mostly inside mask; orange/red means it exits mask.
        if outside_ratio < 0.10:
            color = (60, 220, 80)
        elif outside_ratio < 0.25:
            color = (0, 210, 255)
        else:
            color = (0, 120, 255)
        cv2.line(vis_mask, p0, p1, color, 2)

    for idx, (x, y) in enumerate(seeds):
        if idx not in active_nodes:
            c = (90, 90, 90)
        elif deg[idx] == 0:
            c = (0, 0, 255)
        elif deg[idx] == 1:
            c = (255, 255, 0)
        else:
            c = (0, 0, 255)
        cv2.circle(vis_mask, (x, y), 4, c, -1)

    if start_end is not None:
        s, e = start_end
        sx0, sy0 = seeds[s]
        ex0, ey0 = seeds[e]
        cv2.circle(vis_mask, (sx0, sy0), 8, (255, 0, 255), 2)
        cv2.circle(vis_mask, (ex0, ey0), 8, (0, 255, 255), 2)
        cv2.putText(vis_mask, "S", (sx0 + 6, sy0 - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 255), 2, cv2.LINE_AA)
        cv2.putText(vis_mask, "E", (ex0 + 6, ey0 - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2, cv2.LINE_AA)

    vis_page = page_bgr.copy()
    sh, sw = page_bgr.shape[:2]
    mh, mw = gray.shape[:2]
    sx = sw / float(mw)
    sy = sh / float(mh)

    for i, j, _, _ in chosen:
        x0, y0 = seeds[i]
        x1, y1 = seeds[j]
        p0 = (int(round(x0 * sx)), int(round(y0 * sy)))
        p1 = (int(round(x1 * sx)), int(round(y1 * sy)))
        cv2.line(vis_page, p0, p1, (80, 230, 80), 2)

    for idx, (x, y) in enumerate(seeds):
        px, py = int(round(x * sx)), int(round(y * sy))
        c = (90, 90, 90) if idx not in active_nodes else (0, 0, 255)
        cv2.circle(vis_page, (px, py), 4, c, -1)

    if start_end is not None:
        s, e = start_end
        sx0, sy0 = seeds[s]
        ex0, ey0 = seeds[e]
        sp = (int(round(sx0 * sx)), int(round(sy0 * sy)))
        ep = (int(round(ex0 * sx)), int(round(ey0 * sy)))
        cv2.circle(vis_page, sp, 9, (255, 0, 255), 2)
        cv2.circle(vis_page, ep, 9, (0, 255, 255), 2)
        cv2.putText(vis_page, "S", (sp[0] + 7, sp[1] - 7), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 0, 255), 2, cv2.LINE_AA)
        cv2.putText(vis_page, "E", (ep[0] + 7, ep[1] - 7), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2, cv2.LINE_AA)

    out = Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)
    p1 = out / "seeds_02_connected_on_mask.png"
    p2 = out / "seeds_02_connected_on_page.png"
    p3 = out / "seeds_02_graph.json"
    cv2.imwrite(str(p1), vis_mask)
    cv2.imwrite(str(p2), vis_page)

    graph_payload = {
        "seed_count": n,
        "active_seed_count": len(active_nodes),
        "edge_count": len(chosen),
        "components": components,
        "component_mode": args.component_mode,
        "endpoint_ids": endpoint_ids,
        "start_end": list(start_end) if start_end is not None else None,
        "seeds": [{"id": i, "x": int(x), "y": int(y), "degree": int(deg[i]), "active": bool(i in active_nodes)} for i, (x, y) in enumerate(seeds)],
        "edges": [{"a": int(i), "b": int(j), "dist": float(dist), "outside_ratio": float(outside)} for i, j, dist, outside in chosen],
    }
    p3.write_text(json.dumps(graph_payload, indent=2), encoding="utf-8")

    print(f"Wrote: {p1}")
    print(f"Wrote: {p2}")
    print(f"Wrote: {p3}")


if __name__ == "__main__":
    main()

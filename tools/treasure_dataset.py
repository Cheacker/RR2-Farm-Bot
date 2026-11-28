"""
Build a crop dataset from the treasure labels (tools/treasure_label.py).

For every reviewed frame:
  - each mark -> a crop in dataset/treasure/<class>/ (plus small shifted copies)
  - hard negatives: beam suggestions the labeler dismissed, and the brightest glow spots of the
    world area that are not near a mark
  - random negatives from the world area, away from every mark
dataset/treasure/index.csv lists every crop (file, class, match, frame, x, y).
The folder is rebuilt from scratch on every run.

Usage:
  uv run python tools/treasure_dataset.py
  uv run python tools/treasure_dataset.py --size 96 --neg 4 --hard 3
"""
import argparse
import csv
import glob
import json
import os
import shutil
import sys

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from treasure_vision import crop, glow, world_mask  # noqa: E402

FRAMES_DIR = os.path.join(ROOT, "logs", "treasure_frames")
OUT_DIR = os.path.join(ROOT, "dataset", "treasure")
SHIFTS = [(0, 0), (-8, 0), (8, 0), (0, -8), (0, 8)]


def far_from(x, y, marks, dist):
    return all((m["x"] - x) ** 2 + (m["y"] - y) ** 2 > dist ** 2 for m in marks)


def hard_points(frame, marks, n, dist):
    """Brightest glow peaks in the world area, at least `dist` from marks and each other."""
    g = cv2.GaussianBlur(glow(frame), (0, 0), 6) * world_mask(*frame.shape[:2])
    pts = []
    for _ in range(n * 4):
        _, v, _, (x, y) = cv2.minMaxLoc(g)
        if v <= 5:
            break
        cv2.circle(g, (x, y), dist, 0, -1)
        if far_from(x, y, marks, dist):
            pts.append((x, y))
            if len(pts) == n:
                break
    return pts


def random_points(frame, marks, n, dist, rng):
    ys, xs = np.nonzero(world_mask(*frame.shape[:2]))
    pts = []
    for _ in range(n * 20):
        k = rng.integers(len(xs))
        x, y = int(xs[k]), int(ys[k])
        if far_from(x, y, marks, dist):
            pts.append((x, y))
            if len(pts) == n:
                break
    return pts


def main():
    ap = argparse.ArgumentParser(description="Crop dataset from treasure labels.")
    ap.add_argument("--size", type=int, default=96, help="crop size in px (default 96)")
    ap.add_argument("--neg", type=int, default=4, help="random negatives per frame")
    ap.add_argument("--hard", type=int, default=3, help="glow-peak negatives per frame")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)

    if os.path.isdir(OUT_DIR):
        shutil.rmtree(OUT_DIR)
    os.makedirs(OUT_DIR)
    rows = []
    counts = {}
    n_frames = 0

    def put(img, cls, match, fname, x, y, tag):
        d = os.path.join(OUT_DIR, cls)
        os.makedirs(d, exist_ok=True)
        name = f"{match}_{os.path.splitext(fname)[0]}_{tag}.png"
        cv2.imwrite(os.path.join(d, name), img)
        rows.append([f"{cls}/{name}", cls, match, fname, x, y])
        counts[cls] = counts.get(cls, 0) + 1

    for lp in sorted(glob.glob(os.path.join(FRAMES_DIR, "*", "labels.json"))):
        match_dir = os.path.dirname(lp)
        match = os.path.basename(match_dir)
        with open(lp, encoding="utf-8") as f:
            labels = json.load(f)
        frames = labels.get("frames", {})
        rejected = labels.get("rejected", {})
        skipped = set(labels.get("skipped", []))          # treasure hidden: neither positive nor negative
        for fname, marks in sorted(frames.items()):
            if fname in skipped:
                continue
            frame = cv2.imread(os.path.join(match_dir, fname))
            if frame is None:
                continue
            n_frames += 1
            for j, m in enumerate(marks):
                for s, (dx, dy) in enumerate(SHIFTS):
                    put(crop(frame, m["x"] + dx, m["y"] + dy, args.size), m["cls"], match, fname,
                        m["x"] + dx, m["y"] + dy, f"m{j}s{s}")
            for j, r in enumerate(rejected.get(fname, [])):    # suggestions the labeler dismissed
                put(crop(frame, r["x"], r["y"], args.size), "neg_hard", match, fname, r["x"], r["y"], f"x{j}")
            for j, (x, y) in enumerate(hard_points(frame, marks, args.hard, args.size)):
                put(crop(frame, x, y, args.size), "neg_hard", match, fname, x, y, f"h{j}")
            for j, (x, y) in enumerate(random_points(frame, marks, args.neg, args.size, rng)):
                put(crop(frame, x, y, args.size), "neg", match, fname, x, y, f"r{j}")

    with open(os.path.join(OUT_DIR, "index.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["file", "class", "match", "frame", "x", "y"])
        w.writerows(rows)
    print(f"{n_frames} reviewed frames -> {len(rows)} crops in {os.path.relpath(OUT_DIR, ROOT)}")
    for cls, n in sorted(counts.items()):
        print(f"  {cls:10s} {n}")
    if n_frames == 0:
        print("No labels yet: run tools/treasure_label.py first.")


if __name__ == "__main__":
    main()

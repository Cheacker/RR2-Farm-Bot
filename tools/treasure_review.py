"""
Contact sheet of every treasure/beam mark, to catch wrong ones.

Writes dataset/review_marks.jpg: one numbered tile per mark (crop around it, the mark drawn in),
with the match, frame and class under it. The same list is printed with its numbers.
Fix a wrong mark with:  uv run python tools/treasure_label.py --match <match> --frame <frame>
(right click on the mark removes it).

Usage:
  uv run python tools/treasure_review.py
  uv run python tools/treasure_review.py --cols 8 --tile 200
"""
import argparse
import glob
import json
import os
import sys

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from treasure_vision import crop  # noqa: E402

FRAMES_DIR = os.path.join(ROOT, "logs", "treasure_frames")
OUT = os.path.join(ROOT, "dataset", "review_marks.jpg")
COLORS = {"treasure": (0, 255, 0), "beam": (0, 200, 255)}


def all_marks():
    for lp in sorted(glob.glob(os.path.join(FRAMES_DIR, "*", "labels.json"))):
        match_dir = os.path.dirname(lp)
        with open(lp, encoding="utf-8") as f:
            frames = json.load(f).get("frames", {})
        for fname, marks in sorted(frames.items()):
            for m in marks:
                yield match_dir, fname, m


def tile(frame, m, size):
    side = max(int(max(m.get("w", 0), m.get("h", 0)) * 2.2), 200)
    img = crop(frame, m["x"], m["y"], side)
    c = COLORS.get(m["cls"], (255, 255, 255))
    h = side // 2
    if "w" in m:
        cv2.rectangle(img, (h - m["w"] // 2, h - m["h"] // 2), (h + m["w"] // 2, h + m["h"] // 2), c, 2)
    else:
        cv2.circle(img, (h, h), 26, c, 2)
    return cv2.resize(img, (size, size), interpolation=cv2.INTER_AREA)


def main():
    ap = argparse.ArgumentParser(description="Contact sheet of all treasure marks.")
    ap.add_argument("--cols", type=int, default=6)
    ap.add_argument("--tile", type=int, default=220)
    args = ap.parse_args()
    tiles = []
    cache = {}
    for n, (match_dir, fname, m) in enumerate(all_marks(), 1):
        path = os.path.join(match_dir, fname)
        if path not in cache:
            cache = {path: cv2.imread(path)}
        frame = cache[path]
        if frame is None:
            continue
        match = os.path.basename(match_dir)
        print(f"#{n:3d}  {m['cls']:8s}  --match {match} --frame {fname}")
        t = tile(frame, m, args.tile)
        bar = np.zeros((44, args.tile, 3), np.uint8)
        cv2.putText(bar, f"#{n} {m['cls']}", (4, 18), 0, 0.55, COLORS.get(m["cls"], (255, 255, 255)), 2)
        cv2.putText(bar, f"{match[-4:]} {fname[:6]}", (4, 38), 0, 0.5, (255, 255, 255), 1)
        tiles.append(np.vstack([t, bar]))
    if not tiles:
        print("No marks yet.")
        return
    blank = np.zeros_like(tiles[0])
    rows = [np.hstack(tiles[i:i + args.cols] + [blank] * (args.cols - len(tiles[i:i + args.cols])))
            for i in range(0, len(tiles), args.cols)]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    cv2.imwrite(OUT, np.vstack(rows))
    print(f"{len(tiles)} marks -> {os.path.relpath(OUT, ROOT)}")


if __name__ == "__main__":
    main()

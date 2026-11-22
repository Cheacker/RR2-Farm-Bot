"""
Offline check of template search regions -- no emulator needed.

For every saved 1600x900 screenshot in the given folders and every template:
  * whole-screen best score/position vs. default-region best score/position.
A template that matches well on the whole screen (>= --min) but is missed or
found elsewhere inside its default region means the region is wrong (e.g. the
button moved after a game update).

Also prints, per template, its best score on every screenshot so false positives
(an unrelated screen scoring high) are visible.

Usage:
  uv run python tools/check_regions.py fail_debug dev_tools/atlas_2026-11-21
  uv run python tools/check_regions.py --min 0.85 --top 3 <dirs...>
"""
import argparse
import glob
import os
import sys

import cv2

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from vision import DEFAULT_REGIONS, VisionInterpreter  # noqa: E402


def best(area, template):
    res = cv2.matchTemplate(area, template, cv2.TM_CCOEFF_NORMED)
    _, mx, _, loc = cv2.minMaxLoc(res)
    h, w = template.shape[:2]
    return mx, (loc[0] + w // 2, loc[1] + h // 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="+")
    ap.add_argument("--min", type=float, default=0.80, help="score that counts as a real match")
    ap.add_argument("--top", type=int, default=3, help="best N screenshots to list per template")
    args = ap.parse_args()

    shots = {}
    for d in args.dirs:
        for p in sorted(glob.glob(os.path.join(d, "*.png"))):
            im = cv2.imread(p)
            if im is not None and im.shape[:2] == (900, 1600):
                shots[os.path.relpath(p)] = im
    print(f"{len(shots)} screenshots")

    vision = VisionInterpreter()
    problems = 0
    for name, tpl in sorted(vision.templates.items()):
        if tpl is None:
            continue
        scores = []
        for path, im in shots.items():
            full_score, full_pos = best(im, tpl)
            scores.append((full_score, path, full_pos))
            if name in DEFAULT_REGIONS and full_score >= args.min:
                area, ox, oy = vision._search_area(im, tpl, name, None)
                if area is None:
                    print(f"  !! {name}: default region smaller than template")
                    problems += 1
                    break
                reg_score, reg_pos = best(area, tpl)
                reg_pos = (reg_pos[0] + ox, reg_pos[1] + oy)
                if reg_score < full_score - 0.01 or abs(reg_pos[0] - full_pos[0]) > 3 or abs(reg_pos[1] - full_pos[1]) > 3:
                    print(f"  !! {name} on {path}: full {full_score:.2f}@{full_pos} vs region {reg_score:.2f}@{reg_pos}")
                    problems += 1
        scores.sort(reverse=True)
        top = ", ".join(f"{s:.2f} {os.path.basename(p)}@{pos}" for s, p, pos in scores[:args.top])
        tag = "region" if name in DEFAULT_REGIONS else "full  "
        print(f"[{tag}] {name:24s} {top}")
    print(f"\n{problems} region problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

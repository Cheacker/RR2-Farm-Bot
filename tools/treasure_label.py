"""
Mark treasures on the in-match frames that treasure mode saves (logs/treasure_frames/).

Each match folder gets a labels.json. Every frame you step past is saved as reviewed,
even with no marks: an empty frame is a confirmed "no treasure here" example.

Usage:
  uv run python tools/treasure_label.py                 # matches not finished yet
  uv run python tools/treasure_label.py --all           # every match
  uv run python tools/treasure_label.py --match 20261126_010553_b004

Mouse:  left drag = box around the treasure (preferred: gives its size)   left click = point mark
        right click = remove the nearest mark, or dismiss the nearest suggestion (saved as a "not a treasure" example)
Keys:   d / space = next frame    a = previous frame    n = next match
        1 = class "treasure" (the item on the ground)   2 = class "beam" (foot of a light beam)
        h = glow heat map on/off   c = copy the previous frame's marks   q / Esc = save and quit
        s = beam suggestions on/off (yellow dashed circles)   g = take every suggestion as a "beam" mark
        x = skip this frame on/off (treasure hidden, e.g. behind a tower: the frame stays out of the dataset)
Keys work with Caps Lock on too.
The title bar shows what the result screen said the match gave (result.json).
A beam stays until its treasure is picked up, so the same treasure shows up in many frames: mark it in each.
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
from treasure_vision import beam_candidates, glow_overlay  # noqa: E402

FRAMES_DIR = os.path.join(ROOT, "logs", "treasure_frames")
CLASSES = {ord("1"): "treasure", ord("2"): "beam"}
COLORS = {"treasure": (0, 255, 0), "beam": (0, 200, 255)}
REMOVE_RADIUS = 40
HINT_RADIUS = 50
MIN_BOX = 12              # a drag shorter than this is a click


def frame_files(match_dir):
    return sorted(os.path.basename(p) for p in glob.glob(os.path.join(match_dir, "*.jpg")))


def load_labels(match_dir):
    path = os.path.join(match_dir, "labels.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {"frames": {}, "done": False}


def save_labels(match_dir, labels):
    path = os.path.join(match_dir, "labels.json")
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(labels, f, indent=1)
    os.replace(path + ".tmp", path)


def result_text(match_dir):
    path = os.path.join(match_dir, "result.json")
    if not os.path.exists(path):
        return "no result.json"
    with open(path, encoding="utf-8") as f:
        r = json.load(f)
    return f"{r.get('outcome')}: " + (" | ".join(r.get("hidden_treasures") or []) or "-")


def make_mark(x0, y0, x1, y1, cls):
    """A click gives a point mark; a drag of at least MIN_BOX px gives a box (centre + size)."""
    if abs(x1 - x0) < MIN_BOX and abs(y1 - y0) < MIN_BOX:
        return {"x": x0, "y": y0, "cls": cls}
    w, h = max(abs(x1 - x0), MIN_BOX), max(abs(y1 - y0), MIN_BOX)
    return {"x": (x0 + x1) // 2, "y": (y0 + y1) // 2, "w": w, "h": h, "cls": cls}


def remove_nearest(marks, x, y, radius=REMOVE_RADIUS):
    """Removes and returns the mark under (x, y) (a box containing it, else the nearest
    within `radius`), else None."""
    if not marks:
        return None
    inside = [k for k, m in enumerate(marks)
              if "w" in m and abs(m["x"] - x) <= m["w"] / 2 and abs(m["y"] - y) <= m["h"] / 2]
    if inside:
        return marks.pop(inside[-1])
    d = [(m["x"] - x) ** 2 + (m["y"] - y) ** 2 for m in marks]
    i = int(np.argmin(d))
    if d[i] <= radius ** 2:
        return marks.pop(i)
    return None


def hint_points(frame, rejected):
    """Beam suggestions for a frame, minus those already dismissed (right click)."""
    pts = [{"x": h["foot"][0], "y": h["foot"][1]} for h in beam_candidates(frame)]
    return [p for p in pts
            if all((p["x"] - r["x"]) ** 2 + (p["y"] - r["y"]) ** 2 > HINT_RADIUS ** 2 for r in rejected)]


def dashed_circle(img, center, r, color):
    for a in range(0, 360, 30):
        cv2.ellipse(img, center, (r, r), 0, a, a + 15, color, 2)


def render(frame, marks, cls, heat, header, scale, hints=(), heat_img=None, skipped=False, drag=None):
    if heat:
        img = (heat_img if heat_img is not None else glow_overlay(frame)).copy()
    else:
        img = frame.copy()
    if skipped:
        cv2.rectangle(img, (0, 0), (img.shape[1] - 1, img.shape[0] - 1), (0, 0, 255), 12)
        cv2.putText(img, "SKIPPED (x)", (img.shape[1] // 2 - 150, 90), 0, 1.5, (0, 0, 255), 4)
    for h in hints:
        dashed_circle(img, (h["x"], h["y"]), 34, (0, 255, 255))
    for m in marks:
        c = COLORS.get(m["cls"], (255, 255, 255))
        if "w" in m:
            x0, y0 = m["x"] - m["w"] // 2, m["y"] - m["h"] // 2
            cv2.rectangle(img, (x0, y0), (x0 + m["w"], y0 + m["h"]), c, 3)
            cv2.putText(img, m["cls"][0].upper(), (x0 + m["w"] + 4, y0 + 22), 0, 0.9, c, 2)
        else:
            cv2.circle(img, (m["x"], m["y"]), 26, c, 3)
            cv2.putText(img, m["cls"][0].upper(), (m["x"] + 28, m["y"] + 8), 0, 0.9, c, 2)
    if drag is not None:
        cv2.rectangle(img, drag[:2], drag[2:], COLORS.get(cls, (255, 255, 255)), 2)
    cv2.rectangle(img, (0, 0), (img.shape[1], 34), (0, 0, 0), -1)
    cv2.putText(img, f"{header}   [mark: {cls}]{'  [heat]' if heat else ''}", (8, 24), 0, 0.7,
                (255, 255, 255), 2)
    if scale != 1.0:
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return img


def label_match(match_dir, scale, state):
    """Returns 'quit' or 'next'."""
    files = frame_files(match_dir)
    if not files:
        return "next"
    labels = load_labels(match_dir)
    frames = labels["frames"]
    rejected = labels.setdefault("rejected", {})
    skipped = set(labels.get("skipped", []))
    name = os.path.basename(match_dir)
    res = result_text(match_dir)
    # start at the first frame not reviewed yet
    i = next((k for k, f in enumerate(files) if f not in frames), 0)
    win = "treasure labels"
    cv2.namedWindow(win, cv2.WINDOW_AUTOSIZE)
    cur = {"marks": None, "hints": None, "rejected": None, "down": None, "drag": None}

    def on_mouse(event, x, y, flags, _):
        fx, fy = int(x / scale), int(y / scale)
        if event == cv2.EVENT_LBUTTONDOWN:
            cur["down"] = (fx, fy)
        elif event == cv2.EVENT_MOUSEMOVE and cur["down"] is not None:
            cur["drag"] = cur["down"] + (fx, fy)
        elif event == cv2.EVENT_LBUTTONUP and cur["down"] is not None:
            x0, y0 = cur["down"]
            cur["down"] = cur["drag"] = None
            mark = make_mark(x0, y0, fx, fy, state["cls"])
            cur["marks"].append(mark)
        elif event == cv2.EVENT_RBUTTONDOWN:
            # a mark first; otherwise dismiss a suggestion (kept as a hard negative)
            if remove_nearest(cur["marks"], fx, fy) is None and state["hints"]:
                h = remove_nearest(cur["hints"], fx, fy, HINT_RADIUS)
                if h is not None:
                    cur["rejected"].append(h)

    cv2.setMouseCallback(win, on_mouse)
    while True:
        fname = files[i]
        frame = cv2.imread(os.path.join(match_dir, fname))
        cur["marks"] = [dict(m) for m in frames.get(fname, [])]
        cur["rejected"] = [dict(r) for r in rejected.get(fname, [])]
        cur["hints"] = hint_points(frame, cur["rejected"] + cur["marks"]) if frame is not None else []
        heat_img = None
        while True:
            reviewed = sum(1 for f in files if f in frames)
            header = f"{name}  {i + 1}/{len(files)}  {fname}  reviewed {reviewed}  |  {res}"
            shown = cur["hints"] if state["hints"] else ()
            if state["heat"] and heat_img is None:
                heat_img = glow_overlay(frame)          # once per frame, not every redraw
            cv2.imshow(win, render(frame, cur["marks"], state["cls"], state["heat"], header, scale, shown,
                                   heat_img, fname in skipped, cur["drag"]))
            k = cv2.waitKey(30)
            if k == -1:
                if cv2.getWindowProperty(win, cv2.WND_PROP_VISIBLE) < 1:
                    k = ord("q")
                else:
                    continue
            k &= 0xFF
            if ord("A") <= k <= ord("Z"):                # Caps Lock / Shift
                k += 32
            if k == ord("x"):
                skipped.symmetric_difference_update({fname})
                labels["skipped"] = sorted(skipped)
            elif k in CLASSES:
                state["cls"] = CLASSES[k]
            elif k == ord("h"):
                state["heat"] = not state["heat"]
            elif k == ord("s"):
                state["hints"] = not state["hints"]
            elif k == ord("g") and state["hints"]:
                cur["marks"] += [{"x": h["x"], "y": h["y"], "cls": "beam"} for h in cur["hints"]]
                cur["hints"] = []
            elif k == ord("c") and i > 0:
                cur["marks"] += [dict(m) for m in frames.get(files[i - 1], [])]
            elif k in (ord("d"), ord(" "), ord("a"), ord("n"), ord("q"), 27):
                frames[fname] = cur["marks"]
                if cur["rejected"]:
                    rejected[fname] = cur["rejected"]
                else:
                    rejected.pop(fname, None)
                labels["done"] = all(f in frames for f in files)
                save_labels(match_dir, labels)
                if k in (ord("q"), 27):
                    return "quit"
                if k == ord("n"):
                    return "next"
                if k == ord("a"):
                    i = max(i - 1, 0)
                elif i == len(files) - 1:
                    return "next"
                else:
                    i += 1
                break


def main():
    ap = argparse.ArgumentParser(description="Mark treasures on saved treasure-mode frames.")
    ap.add_argument("--match", help="one match folder name (or path)")
    ap.add_argument("--all", action="store_true", help="include matches already finished")
    ap.add_argument("--scale", type=float, default=0.8, help="window scale (default 0.8)")
    args = ap.parse_args()

    if args.match:
        d = args.match if os.path.isdir(args.match) else os.path.join(FRAMES_DIR, args.match)
        matches = [d]
    else:
        matches = sorted(d for d in glob.glob(os.path.join(FRAMES_DIR, "*")) if os.path.isdir(d))
        if not args.all:
            matches = [d for d in matches if not load_labels(d).get("done")]
    if not matches:
        print("Nothing to label (use --all to revisit finished matches).")
        return
    print(f"{len(matches)} match(es) to label")
    state = {"cls": "treasure", "heat": False, "hints": True}
    for d in matches:
        if label_match(d, args.scale, state) == "quit":
            break
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()

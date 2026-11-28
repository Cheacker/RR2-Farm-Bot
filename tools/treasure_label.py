"""
Mark treasures on the in-match frames that treasure mode saves (logs/treasure_frames/).

Each match folder gets a labels.json. Every frame you step past is saved as reviewed,
even with no marks: an empty frame is a confirmed "no treasure here" example.

Usage:
  uv run python tools/treasure_label.py                 # matches not finished yet
  uv run python tools/treasure_label.py --all           # every match
  uv run python tools/treasure_label.py --match 20261126_010553_b004

Mouse:  left click = mark (current class)   right click = remove the nearest mark
Keys:   d / space = next frame    a = previous frame    n = next match
        1 = class "treasure" (the item on the ground)   2 = class "beam" (foot of a light beam)
        h = glow heat map on/off   c = copy the previous frame's marks   q / Esc = save and quit
        s = beam suggestions on/off (yellow dashed circles)   g = take every suggestion as a "beam" mark
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


def remove_nearest(marks, x, y):
    if not marks:
        return
    d = [(m["x"] - x) ** 2 + (m["y"] - y) ** 2 for m in marks]
    i = int(np.argmin(d))
    if d[i] <= REMOVE_RADIUS ** 2:
        marks.pop(i)


def dashed_circle(img, center, r, color):
    for a in range(0, 360, 30):
        cv2.ellipse(img, center, (r, r), 0, a, a + 15, color, 2)


def render(frame, marks, cls, heat, header, scale, hints=()):
    img = glow_overlay(frame) if heat else frame.copy()
    for h in hints:
        dashed_circle(img, h["foot"], 34, (0, 255, 255))
    for m in marks:
        c = COLORS.get(m["cls"], (255, 255, 255))
        cv2.circle(img, (m["x"], m["y"]), 26, c, 3)
        cv2.putText(img, m["cls"][0].upper(), (m["x"] + 28, m["y"] + 8), 0, 0.9, c, 2)
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
    name = os.path.basename(match_dir)
    res = result_text(match_dir)
    # start at the first frame not reviewed yet
    i = next((k for k, f in enumerate(files) if f not in frames), 0)
    win = "treasure labels"
    cv2.namedWindow(win, cv2.WINDOW_AUTOSIZE)
    cur = {"marks": None}

    def on_mouse(event, x, y, flags, _):
        fx, fy = int(x / scale), int(y / scale)
        if event == cv2.EVENT_LBUTTONDOWN:
            cur["marks"].append({"x": fx, "y": fy, "cls": state["cls"]})
        elif event == cv2.EVENT_RBUTTONDOWN:
            remove_nearest(cur["marks"], fx, fy)

    cv2.setMouseCallback(win, on_mouse)
    while True:
        fname = files[i]
        frame = cv2.imread(os.path.join(match_dir, fname))
        cur["marks"] = [dict(m) for m in frames.get(fname, [])]
        hints = beam_candidates(frame) if frame is not None else []
        while True:
            reviewed = sum(1 for f in files if f in frames)
            header = f"{name}  {i + 1}/{len(files)}  {fname}  reviewed {reviewed}  |  {res}"
            shown = hints if state["hints"] else ()
            cv2.imshow(win, render(frame, cur["marks"], state["cls"], state["heat"], header, scale, shown))
            k = cv2.waitKey(30) & 0xFF
            if k == 255:
                if cv2.getWindowProperty(win, cv2.WND_PROP_VISIBLE) < 1:
                    k = ord("q")
                else:
                    continue
            if k in CLASSES:
                state["cls"] = CLASSES[k]
            elif k == ord("h"):
                state["heat"] = not state["heat"]
            elif k == ord("s"):
                state["hints"] = not state["hints"]
            elif k == ord("g"):
                cur["marks"] += [{"x": h["foot"][0], "y": h["foot"][1], "cls": "beam"} for h in hints]
            elif k == ord("c") and i > 0:
                cur["marks"] += [dict(m) for m in frames.get(files[i - 1], [])]
            elif k in (ord("d"), ord(" "), ord("a"), ord("n"), ord("q"), 27):
                frames[fname] = cur["marks"]
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

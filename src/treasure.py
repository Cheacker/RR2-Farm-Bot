"""Treasure mode: favorite bases, the king's path follower, and the Hidden Treasures
read-out. Used by RR2Bot when started with --mode treasure.

Goal (user, 2026-11-26): destroy as many towers as possible; reaching the castle
counts as success, dying at the castle is normal (the treasure set has no resistance).
"""
import csv
import json
import math
import os
import time

import cv2
import numpy as np

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASES_DIR = os.path.join(ROOT_DIR, "treasure_bases")   # local, gitignored (name crops)
BASES_JSON = os.path.join(BASES_DIR, "bases.json")

# Favorite Players list rows (1600x900). The row is found by its yellow swords button.
FAV_ROW_TROPHIES = (1245, -25, 1350, 25)    # x1, dy1, x2, dy2 around the swords' centre y
FAV_ROW_NAME = (630, -38, 1100, 8)
NAME_MATCH = 0.85                           # name-crop similarity to call two rows the same base


def _gray(img):
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


class TreasureBases:
    """Favorite bases recognised by an image of their name (OCR can't read many of the
    names, e.g. Vietnamese or the italic list font). Persists attack history and the
    bases where the king died on the path (those are never attacked again)."""

    def __init__(self):
        os.makedirs(BASES_DIR, exist_ok=True)
        self.meta = {}
        if os.path.exists(BASES_JSON):
            try:
                with open(BASES_JSON, encoding="utf-8") as f:
                    self.meta = json.load(f)
            except (OSError, ValueError) as e:
                print(f"[TREASURE] bases.json unreadable, starting fresh: {e}")
        self.crops = {}
        for bid in self.meta:
            img = cv2.imread(os.path.join(BASES_DIR, f"{bid}.png"), cv2.IMREAD_GRAYSCALE)
            if img is not None:
                self.crops[bid] = img

    def _save(self):
        tmp = BASES_JSON + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.meta, f, indent=1)
        os.replace(tmp, BASES_JSON)

    def identify(self, name_crop_bgr):
        """Base id for this name crop, registering a new base if unknown."""
        g = _gray(name_crop_bgr)
        best, best_id = 0.0, None
        for bid, ref in self.crops.items():
            # the stored crop is matched inside the new one shrunk by a few px, so a
            # row that sits a pixel or two off still matches
            inner = ref[4:-4, 6:-6]
            if inner.shape[0] > g.shape[0] or inner.shape[1] > g.shape[1]:
                continue
            v = cv2.minMaxLoc(cv2.matchTemplate(g, inner, cv2.TM_CCOEFF_NORMED))[1]
            if v > best:
                best, best_id = v, bid
        if best_id is not None and best >= NAME_MATCH:
            return best_id
        bid = f"b{len(self.meta) + 1:03d}"
        while bid in self.meta:
            bid += "x"
        cv2.imwrite(os.path.join(BASES_DIR, f"{bid}.png"), g)
        self.crops[bid] = g
        self.meta[bid] = {"first_seen": time.strftime("%Y-%m-%d %H:%M:%S"), "attacks": 0,
                          "last_attack": 0, "banned": False, "trophies": None}
        self._save()
        return bid

    def note_trophies(self, bid, trophies):
        if trophies and self.meta[bid].get("trophies") != trophies:
            self.meta[bid]["trophies"] = trophies
            self._save()

    def attacked(self, bid):
        m = self.meta[bid]
        m["attacks"] = m.get("attacks", 0) + 1
        m["last_attack"] = time.time()
        self._save()

    def ban(self, bid, reason):
        self.meta[bid]["banned"] = True
        self.meta[bid]["ban_reason"] = reason
        self._save()

    def seen_active(self, bid):
        self.meta[bid]["seen_active"] = time.time()   # in memory; saved with the next write

    def recently_active(self, bid, window=30 * 60):
        return time.time() - self.meta.get(bid, {}).get("seen_active", 0) < window

    def mark_busy(self, bid, seconds=15 * 60):
        """Owner is online ('Take me back' while loading): skip the base for a while."""
        self.meta[bid]["busy_until"] = time.time() + seconds
        self._save()

    def available(self, bid):
        m = self.meta.get(bid, {})
        return not m.get("banned") and m.get("busy_until", 0) < time.time()

    def banned(self, bid):
        return self.meta.get(bid, {}).get("banned", False)

    def last_attack(self, bid):
        return self.meta.get(bid, {}).get("last_attack", 0)


class PathFollower:
    """Walks the king along the path with one finger held down.

    Feedback: the king's icon on the top progress bar moves right as he advances.
    Keep the heading while it advances, otherwise try the nearest untried heading
    (±45°, ±90°, ..., reversing last). At the castle the finger is lifted and only
    spells are cast. Troops/spells/archers are spammed by key all match."""

    CENTER, RADIUS = (800, 380), 230      # the king is roughly at screen centre
    SAFE = (550, 150, 1050, 600)          # finger targets: away from every button
    ANGLES = list(range(0, 360, 45))
    END_X = 1120                          # icon x at the castle

    def __init__(self, touch, vision):
        self.t, self.v = touch, vision
        self.heading, self.tried, self.last_x, self.n = 90, [], None, 0
        self.max_x = None
        self.last_seen_x = None
        self.at_castle = False

    def _target(self, a):
        r = math.radians(a)
        x = self.CENTER[0] + self.RADIUS * math.cos(r)
        y = self.CENTER[1] - self.RADIUS * math.sin(r)
        x1, y1, x2, y2 = self.SAFE
        return int(min(x2, max(x1, x))), int(min(y2, max(y1, y)))

    @staticmethod
    def _adiff(a, b):
        d = abs(a - b) % 360
        return min(d, 360 - d)

    def king_x(self, screen):
        pos = self.v.find_template(screen, "icon_king_progress", threshold=0.65)
        return pos[0] if pos else None

    def start(self):
        self.t.down(*self._target(self.heading))

    def stop(self):
        if self.t.is_down:
            self.t.up()

    def step(self, screen):
        """One control step per captured frame (~1.2-1.5 s apart)."""
        self.n += 1
        self.t.keys("1")
        if self.n % 2 == 0:
            self.t.keys("1235q")
        if self.n % 3 == 0:
            self.t.keys("67w")
        x = self.king_x(screen)
        self.last_seen_x = x
        if x is not None:
            self.max_x = x if self.max_x is None else max(self.max_x, x)
        if x is not None and x >= self.END_X:
            if not self.at_castle:
                print(f"[TREASURE] king at the castle (icon x={x}) -> finger up, spells only")
            self.at_castle = True
            self.stop()
            self.t.keys("567")
            self.last_x = x
            return
        if x is None or self.last_x is None:
            self.last_x = x if x is not None else self.last_x
            self.t.move(*self._target(self.heading))
            return
        dx = x - self.last_x
        if dx >= 3:
            self.tried = []
        else:
            self.tried.append(self.heading)
            cand = [a for a in sorted(self.ANGLES, key=lambda a: self._adiff(a, self.heading))
                    if a not in self.tried]
            old = self.heading
            self.heading = cand[0] if cand else 90
            if not cand:
                self.tried = []
            print(f"[TREASURE] king stalled at x={x} ({dx:+d}), heading {old} -> {self.heading}")
        self.last_x = x
        self.t.move(*self._target(self.heading))


def read_hidden_treasures(vision, screen):
    """OCR the 'Hidden Treasures' rows of the result screen. The panel scrolls itself
    to the bottom, so rows are read from the last one (just above CONTINUE, y≈631,
    92 px apart) upwards, until the section title or the panel's top clip (y≈385).
    Returns row strings, e.g. ["gargoyle: carnage (level 1) +1", "+320"]; [] if the
    match dropped nothing."""
    title = vision.find_template(screen, "text_hidden_treasures", threshold=0.80)
    if not title and vision.find_template(screen, "icon_result_gold", threshold=0.80):
        return []        # gold row sits where treasure rows would be: nothing dropped
    top = title[1] + 40 if title else 400   # 3+ rows push the title out of view
    rows = []
    y = 631
    while y > top:
        text = vision.read_region_text(screen, 920, y - 30, 1290, y + 30).strip()
        if text:
            rows.append(text)
        y -= 92
    return rows[::-1]


def write_treasure_row(path, row):
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time", "base", "trophies", "outcome", "king_x_max", "match_s", "treasures"])
        w.writerow(row)


class FrameRecorder:
    """Saves every in-match frame of treasure mode as JPEG (training data for the
    treasure detector): logs/treasure_frames/<match>/<seconds>_<kingx>.jpg.
    Stops saving when the folder passes MAX_BYTES so a long run can't fill the disk."""

    MAX_BYTES = 8 * 1024 ** 3

    def __init__(self, root):
        self.root = root
        self.dir = None
        self.t0 = 0
        self.full = False
        self._size = None

    def _used(self):
        if self._size is None:
            self._size = 0
            for d, _, files in os.walk(self.root):
                self._size += sum(os.path.getsize(os.path.join(d, f)) for f in files)
        return self._size

    def start(self, tag):
        self.t0 = time.time()
        self.dir = os.path.join(self.root, f"{time.strftime('%Y%m%d_%H%M%S')}_{tag}")
        if self._used() >= self.MAX_BYTES:
            if not self.full:
                print(f"[TREASURE] frame folder over {self.MAX_BYTES // 1024 ** 3} GB - not saving frames")
            self.full = True
            self.dir = None
            return
        os.makedirs(self.dir, exist_ok=True)

    def save(self, frame, king_x=None):
        if self.dir is None or frame is None:
            return
        name = f"{time.time() - self.t0:06.1f}_{king_x if king_x is not None else 'na'}.jpg"
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if ok:
            with open(os.path.join(self.dir, name), "wb") as f:
                f.write(buf.tobytes())
            self._size += len(buf)

    def finish(self, treasures, outcome):
        """Ground truth for the match: what the result screen said was collected."""
        if self.dir is None:
            return
        with open(os.path.join(self.dir, "result.json"), "w", encoding="utf-8") as f:
            json.dump({"outcome": outcome, "hidden_treasures": treasures}, f, indent=1)
        self.dir = None

import cv2
import numpy as np
import os
import pytesseract

# Default Tesseract path on Windows — change if installed elsewhere
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# Name region relative to sword icon center:
# sword center ~(1350,340) → name top-left offset (-705, -20), size 525x40
NAME_REGION = (-705, -20, 525, 40)

# Templates in this set will NOT print "not found" messages (polled every loop)
SILENT_ON_MISS = {
    "btn_continue", "btn_melt", "btn_retreat",
    "popup_break_time", "popup_update", "title_shop", "text_downloading",
}

# Pass region=FULL to force a whole-screen search (e.g. the _find_state fallback).
FULL = "full"

# Default search region per template, (x1, y1, x2, y2) on the 1600x900 screen.
# A whole-screen match costs ~330 ms; a region of a few hundred pixels ~5-10 ms.
# Each box is the button's observed position (game 12.9.0, LDPlayer, 2026-11-21)
# padded generously (~60 px beyond the template on every side), so small layout
# shifts still match. Templates not listed here are searched on the whole screen.
# Verify after a game update with: uv run python tools/check_regions.py <screenshot dirs>
DEFAULT_REGIONS = {
    "icon_forge":            (75, 512, 275, 710),
    "icon_trophy":           (1452, 40, 1600, 224),
    "btn_close":             (1400, 0, 1600, 200),
    "btn_green_back":        (1300, 0, 1510, 200),
    "btn_collect_league":    (700, 640, 1400, 900),
    "btn_start_search":      (500, 680, 1120, 900),
    "area_top_opponent":     (1330, 200, 1510, 900),
    "btn_attack_start":      (1000, 650, 1480, 860),
    "btn_attack_start_gray": (1000, 650, 1480, 900),
    "btn_archer":            (0, 580, 300, 850),
    "btn_continue":          (900, 680, 1340, 890),
    # COF "Better luck next time" dialog: GIVE UP seen at (426, 491) live on 2026-11-22.
    # Wide row box on purpose — the dialog's buttons may sit elsewhere in other variants.
    # (The first guess here, copied from old WIP coords, was CONTINUE's spot and made
    # the COF loop time out.)
    "btn_give_up":           (150, 380, 1450, 620),
    # btn_sell: position never observed live yet → no default region (whole screen).
    "btn_melt":              (370, 230, 680, 540),
    "btn_take_me_back":      (540, 380, 1090, 620),
    "chest_1":               (190, 170, 540, 490),
    "chest_2":               (610, 170, 980, 490),
    "chest_3":               (1020, 170, 1410, 490),
    "chest_4":               (190, 540, 540, 900),
    "chest_5":               (610, 540, 980, 900),
    "chest_6":               (1020, 540, 1410, 900),
    # Phase 0 additions (cropped from saved screenshots)
    "popup_break_time":      (580, 290, 1110, 490),
    "popup_update":          (580, 270, 1110, 510),
    "btn_retreat":           (920, 640, 1340, 860),
    "title_shop":            (0, 0, 260, 190),
    "text_downloading":      (480, 520, 900, 700),
    # btn_play_update lives in the Play Store, whose layout we don't control: full screen.
}


class VisionInterpreter:
    def __init__(self, template_dir=None):
        if template_dir is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            self.template_dir = os.path.join(base_dir, "En_Templates")
        else:
            self.template_dir = template_dir
        self.templates = {}
        self.load_templates()

    def load_templates(self):
        if not os.path.exists(self.template_dir):
            print(f"[VISION] Template directory not found: {self.template_dir}")
            return
        for file in os.listdir(self.template_dir):
            if file.endswith(".png"):
                name = file[:-4]
                path = os.path.join(self.template_dir, file)
                self.templates[name] = cv2.imread(path)
        print(f"[VISION] {len(self.templates)} templates loaded: {self.template_dir}")

    @staticmethod
    def _search_area(screen, template, template_name, region):
        """Resolve the region to search: explicit box, FULL, or the template's default.
        Returns (area, ox, oy) or (None, 0, 0) if the box can't hold the template."""
        if region is None:
            region = DEFAULT_REGIONS.get(template_name)
        if region is None or region == FULL:
            return screen, 0, 0
        h, w = screen.shape[:2]
        x1, y1, x2, y2 = region
        x1, y1, x2, y2 = max(0, x1), max(0, y1), min(w, x2), min(h, y2)
        th, tw = template.shape[:2]
        if x2 - x1 < tw or y2 - y1 < th:
            return None, 0, 0
        return screen[y1:y2, x1:x2], x1, y1

    def find_template(self, screen, template_name, threshold=0.98, region=None):
        """Return center (x, y) of the best match, or None if below threshold.
        region: None = template's default region (whole screen if it has none),
        FULL = whole screen, or an explicit (x1, y1, x2, y2) box."""
        if template_name not in self.templates or screen is None:
            return None
        template = self.templates[template_name]
        if template is None:
            return None
        area, ox, oy = self._search_area(screen, template, template_name, region)
        if area is None:
            return None

        res = cv2.matchTemplate(area, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)

        if max_val >= threshold:
            h, w = template.shape[:2]
            center_x = ox + max_loc[0] + w // 2
            center_y = oy + max_loc[1] + h // 2
            print(f"[VISION] {template_name} found (max={max_val:.3f}, threshold={threshold})")
            return (center_x, center_y)
        if template_name not in SILENT_ON_MISS:
            print(f"[VISION] {template_name} not found (max={max_val:.3f}, threshold={threshold})")
        return None

    def find_multiple_templates(self, screen, template_name, threshold=0.99, region=None):
        """Return list of (x, y) for all non-overlapping matches above threshold.
        region: same meaning as in find_template."""
        if template_name not in self.templates or screen is None:
            return []
        template = self.templates[template_name]
        area, ox, oy = self._search_area(screen, template, template_name, region)
        if area is None:
            return []

        res = cv2.matchTemplate(area, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(res)
        locs = np.where(res >= threshold)

        h, w = template.shape[:2]
        centers = []
        for pt in zip(*locs[::-1]):
            val = float(res[pt[1], pt[0]])
            centers.append((ox + pt[0] + w // 2, oy + pt[1] + h // 2, val))

        if not centers:
            print(f"[VISION] {template_name} (multi) not found (max={max_val:.3f}, threshold={threshold})")
            return []

        # Simple non-max suppression: merge points closer than 20px
        grouped = []
        for c in centers:
            if not any(np.sqrt((c[0]-g[0])**2 + (c[1]-g[1])**2) < 20 for g in grouped):
                grouped.append(c)

        min_match = min(c[2] for c in grouped)
        print(f"[VISION] {template_name} (multi) found (min_max={min_match:.3f}, threshold={threshold}, count={len(grouped)})")
        return [(c[0], c[1]) for c in grouped]

    def read_region_number(self, screen, x1: int, y1: int, x2: int, y2: int) -> int | None:
        """OCR a rectangular region and return the integer found, or None."""
        region = screen[y1:y2, x1:x2]
        if region.size == 0:
            return None
        gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
        upscaled = cv2.resize(gray, (0, 0), fx=2, fy=2, interpolation=cv2.INTER_LINEAR)
        _, thresh = cv2.threshold(upscaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        raw = pytesseract.image_to_string(
            thresh, config="--psm 7 --oem 3 -c tessedit_char_whitelist=0123456789"
        ).strip()
        digits = ''.join(filter(str.isdigit, raw))
        return int(digits) if digits else None

    def read_player_name(self, screen, sword_x, sword_y):
        """Read player name via OCR from the region left of the sword icon."""
        h, w = screen.shape[:2]
        dx, dy, rw, rh = NAME_REGION
        x1 = max(0, sword_x + dx)
        y1 = max(0, sword_y + dy)
        x2 = min(w, x1 + rw)
        y2 = min(h, y1 + rh)
        region = screen[y1:y2, x1:x2]
        if region.size == 0:
            return ""
        gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
        upscaled = cv2.resize(gray, (0, 0), fx=2, fy=2, interpolation=cv2.INTER_LINEAR)
        _, thresh = cv2.threshold(upscaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        name = pytesseract.image_to_string(thresh, config="--psm 7 --oem 3").strip()
        return name

    def read_region_text(self, screen, x1: int, y1: int, x2: int, y2: int) -> str:
        """OCR a rectangular region and return the detected text (lowercase), or empty string."""
        if screen is None:
            return ""
        region = screen[y1:y2, x1:x2]
        if region.size == 0:
            return ""
        gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
        upscaled = cv2.resize(gray, (0, 0), fx=2, fy=2, interpolation=cv2.INTER_LINEAR)
        _, thresh = cv2.threshold(upscaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        raw = pytesseract.image_to_string(thresh, config="--psm 7 --oem 3").strip()
        return raw.lower()


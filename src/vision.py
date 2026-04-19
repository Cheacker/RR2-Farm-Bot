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
SILENT_ON_MISS = {"btn_continue"}


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

    def find_template(self, screen, template_name, threshold=0.98):
        """Return center (x, y) of the best match, or None if below threshold."""
        if template_name not in self.templates or screen is None:
            return None
        template = self.templates[template_name]
        if template is None:
            return None

        res = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)

        if max_val >= threshold:
            h, w = template.shape[:2]
            center_x = max_loc[0] + w // 2
            center_y = max_loc[1] + h // 2
            print(f"[VISION] {template_name} found (max={max_val:.3f}, threshold={threshold})")
            return (center_x, center_y)
        if template_name not in SILENT_ON_MISS:
            print(f"[VISION] {template_name} not found (max={max_val:.3f}, threshold={threshold})")
        return None

    def find_multiple_templates(self, screen, template_name, threshold=0.99):
        """Return list of (x, y) for all non-overlapping matches above threshold."""
        if template_name not in self.templates or screen is None:
            return []
        template = self.templates[template_name]

        res = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(res)
        locs = np.where(res >= threshold)

        h, w = template.shape[:2]
        centers = []
        for pt in zip(*locs[::-1]):
            val = float(res[pt[1], pt[0]])
            centers.append((pt[0] + w // 2, pt[1] + h // 2, val))

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

"""
Live screenshot tool — crops and saves templates from the emulator.

Usage:
  python recrop.py
"""
import cv2
import numpy as np
import adbutils
import os
import time

PORT = 21503  # Change to match your emulator ADB port

SESSIONS = {
    "1":  ("Trophy icon (home screen)",               ["icon_trophy"]),
    "2":  ("Yellow search button*",                    ["btn_start_search"]),
    "3":  ("Sword / attack icon (ranked list)",       ["area_top_opponent"]),
    "4":  ("Attack start button (pre-match screen)",  ["btn_attack_start"]),
    "5":  ("Continue button*",                         ["btn_continue"]),
    "6":  ("Sell button*",                             ["btn_sell"]),
    "7":  ("Give up button*",                          ["btn_give_up"]),
    "8-13": ("Chests (6 separate crops)",               ["chest_1","chest_2","chest_3","chest_4","chest_5","chest_6"]),
    "14": ("Archer button (appears when match starts)",["btn_archer"]),
    "15": ("Bring me back button (active player warning)*", ["btn_bring_me_back"]),
    "16": ("Green back button",                       ["btn_green_back"]),
    "17": ("Collect button (appears on home screen for vouchers)*", ["btn_collect"]),
    "18": ("Big collect button (appears after attack prep for breads)*", ["big_collect"]),
    "19": ("Close / X button*",                        ["btn_close"]),
}

SAVE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "En_Templates")
os.makedirs(SAVE_DIR, exist_ok=True)
print(f"Saving templates to: {SAVE_DIR}")

# ── ADB connection ─────────────────────────────────────────────────────────────
adb = adbutils.AdbClient(host="127.0.0.1", port=5037)
adb.connect(f"127.0.0.1:{PORT}")
time.sleep(0.5)
device = adb.device_list()[0]


def crop_and_save(img, template_name):
    h, w = img.shape[:2]
    scale = min(1.0, 1000 / max(h, w))
    disp  = cv2.resize(img, (0, 0), fx=scale, fy=scale)
    print(f"  Select: {template_name}  |  SPACE/ENTER to confirm, C to cancel")
    roi = cv2.selectROI(f"Select: {template_name}", disp,
                        showCrosshair=True, fromCenter=False)
    cv2.destroyAllWindows()
    x, y, rw, rh = roi
    if rw == 0 or rh == 0:
        print("  Skipped.")
        return
    x, y, rw, rh = int(x/scale), int(y/scale), int(rw/scale), int(rh/scale)
    cropped = img[y:y+rh, x:x+rw]
    path = os.path.join(SAVE_DIR, f"{template_name}.png")
    cv2.imwrite(path, cropped)
    print(f"  Saved: {path}")


while True:
    print("\nWhich template do you want to capture?")
    for k, (name, _) in SESSIONS.items():
        print(f"  {k}) {name}")
    print("  q) Quit")
    choice = input("> ").strip()

    if choice == "q":
        break
    if choice not in SESSIONS:
        print("Invalid choice.")
        continue

    name, templates = SESSIONS[choice]
    print(f"\nNavigate to '{name}' screen in the emulator, then press ENTER...")
    input()
    time.sleep(1.5)

    screen = None
    for attempt in range(8):
        img_bytes = device.shell("screencap -p", encoding=None)
        nparr = np.frombuffer(img_bytes, np.uint8)
        img   = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is not None and img.mean() < 250:
            screen = img
            break
        print(f"  Blank/white frame ({attempt+1}/8), retrying...")
        time.sleep(0.2)

    if screen is None:
        print("Screenshot failed (8 attempts, all white). Try again.")
        continue

    print(f"Screenshot captured: {screen.shape[1]}x{screen.shape[0]}")
    for template_name in templates:
        crop_and_save(screen, template_name)

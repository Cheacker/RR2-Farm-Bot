"""
Offline template cropper — crops from a saved screenshot file instead of a
live device capture. recrop.py always grabs a fresh screenshot from the
emulator, so it can't be used to crop something you already captured earlier
(e.g. captured_inactives/*.png) without also touching a running bot/emulator.
This tool never talks to ADB at all, so it's safe to use any time, even
while the bot is running.

Usage:
  python crop_from_file.py <screenshot_path> <template_name>

Example:
  python crop_from_file.py captured_inactives/20260816_120501_unrecognized.png btn_attack_start_gray_2
"""
import cv2
import os
import sys

SAVE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "En_Templates")
os.makedirs(SAVE_DIR, exist_ok=True)


def crop_and_save(img, template_name):
    h, w = img.shape[:2]
    scale = min(1.0, 1000 / max(h, w))
    disp  = cv2.resize(img, (0, 0), fx=scale, fy=scale)
    print(f"Select: {template_name}  |  SPACE/ENTER to confirm, C to cancel")
    roi = cv2.selectROI(f"Select: {template_name}", disp,
                        showCrosshair=True, fromCenter=False)
    cv2.destroyAllWindows()
    x, y, rw, rh = roi
    if rw == 0 or rh == 0:
        print("Skipped.")
        return
    x, y, rw, rh = int(x/scale), int(y/scale), int(rw/scale), int(rh/scale)
    cropped = img[y:y+rh, x:x+rw]
    path = os.path.join(SAVE_DIR, f"{template_name}.png")
    cv2.imwrite(path, cropped)
    print(f"Saved: {path}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python crop_from_file.py <screenshot_path> <template_name>")
        sys.exit(1)
    img_path, template_name = sys.argv[1], sys.argv[2]
    img = cv2.imread(img_path)
    if img is None:
        print(f"Could not read image: {img_path}")
        sys.exit(1)
    print(f"Loaded: {img_path} ({img.shape[1]}x{img.shape[0]})")
    crop_and_save(img, template_name)

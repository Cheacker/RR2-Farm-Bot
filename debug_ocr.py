"""
OCR bölge seçici ve test aracı.

Kullanım:
  python debug_ocr.py

1. Emülatörde trophy menüsüne git (filtre ekranı açık olsun).
2. ENTER'a bas — ekran görüntüsü alınır.
3. Açılan pencerede fare ile bir bölge seç (ROI).
4. SPACE veya ENTER ile onayla → koordinatlar + OCR sonucu yazdırılır.
5. Tekrar seçmek için 'r', çıkmak için 'q'.
"""
import sys
import cv2
import numpy as np
import pytesseract
import adbutils
import time

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
PORT = 21503  # emülatör ADB portu

# ── ADB bağlantı ──────────────────────────────────────────────────────────────
adb = adbutils.AdbClient(host="127.0.0.1", port=5037)
adb.connect(f"127.0.0.1:{PORT}")
time.sleep(0.5)
devices = adb.device_list()
if not devices:
    print("ADB cihazı bulunamadı.")
    sys.exit(1)
device = devices[0]
print(f"Bağlandı: {device.serial}")


def capture():
    for _ in range(8):
        raw = device.shell("screencap -p", encoding=None)
        arr = np.frombuffer(raw, np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is not None and img.mean() < 250:
            return img
        time.sleep(0.2)
    return None


def ocr_region(img, x1, y1, x2, y2):
    region = img[y1:y2, x1:x2]
    if region.size == 0:
        return None, None
    gray     = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    up       = cv2.resize(gray, (0, 0), fx=2, fy=2, interpolation=cv2.INTER_LINEAR)
    _, thresh = cv2.threshold(up, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    raw = pytesseract.image_to_string(
        thresh, config="--psm 7 --oem 3 -c tessedit_char_whitelist=0123456789"
    ).strip()
    digits = ''.join(filter(str.isdigit, raw))
    return (int(digits) if digits else None), thresh


# ── Koordinat gösterimi için mouse callback ────────────────────────────────────
_mouse_pos = [0, 0]
def _on_mouse(event, x, y, flags, param):
    _mouse_pos[0], _mouse_pos[1] = x, y


screen = None

while True:
    print("\nEmülatörde trophy menüsüne git, filtre ekranı açık olsun.")
    print("Hazır olunca ENTER'a bas (ekran görüntüsü alınır)...")
    input()

    screen = capture()
    if screen is None:
        print("Ekran görüntüsü alınamadı, tekrar dene.")
        continue

    h, w = screen.shape[:2]
    scale = min(1.0, 1200 / max(h, w))
    disp  = cv2.resize(screen, (0, 0), fx=scale, fy=scale)

    print(f"Ekran: {w}x{h}  |  Görüntü ölçeği: {scale:.2f}")
    print("Bölge seç → SPACE/ENTER onayla, C iptal et")

    while True:
        roi = cv2.selectROI("OCR Bolge Sec", disp, showCrosshair=True, fromCenter=False)
        cv2.destroyAllWindows()
        rx, ry, rw, rh = roi
        if rw == 0 or rh == 0:
            print("Seçim iptal edildi.")
            break

        # Ölçeği geri al → gerçek koordinatlar
        x1 = int(rx / scale)
        y1 = int(ry / scale)
        x2 = int((rx + rw) / scale)
        y2 = int((ry + rh) / scale)

        print(f"\n--- Seçilen bölge ---")
        print(f"  bot.py koordinatları : ({x1}, {y1}, {x2}, {y2})")
        print(f"  Boyut                : {x2-x1} x {y2-y1} px")

        value, thresh_img = ocr_region(screen, x1, y1, x2, y2)
        print(f"  OCR sonucu (rakam)   : {value}")

        # Threshold görüntüyü göster (OCR'ın ne gördüğü)
        if thresh_img is not None:
            t_disp = cv2.resize(thresh_img, (0, 0), fx=2, fy=2,
                                interpolation=cv2.INTER_NEAREST)
            cv2.imshow("OCR threshold (ESC ile kapat)", t_disp)
            cv2.waitKey(0)
            cv2.destroyAllWindows()

        # Seçilen bölgeyi orijinal görüntüde çerçevele
        marked = disp.copy()
        cv2.rectangle(marked,
                      (int(x1*scale), int(y1*scale)),
                      (int(x2*scale), int(y2*scale)),
                      (0, 255, 0), 2)
        cv2.imshow("Secilen bolge", marked)
        cv2.waitKey(1500)
        cv2.destroyAllWindows()

        print("\nBaşka bölge seçmek için ENTER, yeni ekran görüntüsü için 'r', çıkmak için 'q':")
        cmd = input("> ").strip().lower()
        if cmd == 'q':
            sys.exit(0)
        elif cmd == 'r':
            break  # dış döngüye dön, yeni screenshot al
        # else: aynı screenshot ile tekrar seç

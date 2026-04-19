"""
Koordinat belirleyici — emülatör ekranında tıkladığın noktanın koordinatını verir.

Kullanım:
  python get_coords.py

1. Emülatörde hedef ekrana git (örn. trophy menüsü).
2. ENTER'a bas → ekran görüntüsü alınır.
3. Açılan pencerede bir noktaya tıkla → koordinat yazdırılır.
4. İstediğin kadar tıkla, 'q' tuşuyla kapat.
"""
import sys
import cv2
import numpy as np
import adbutils
import time

PORT = 21503

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
    for attempt in range(8):
        try:
            raw = device.shell("screencap -p", encoding=None)
            if not raw or len(raw) < 1000:
                print(f"  [{attempt+1}/8] Boş response ({len(raw) if raw else 0} bytes)")
                time.sleep(0.3)
                continue
            arr = np.frombuffer(raw, np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            mean = img.mean() if img is not None else -1
            print(f"  [{attempt+1}/8] mean={mean:.1f}")
            if img is not None and mean < 250:
                return img
        except Exception as e:
            print(f"  [{attempt+1}/8] Hata: {e}")
        time.sleep(0.3)
    return None


scale = 1.0
img_orig = None


def on_click(event, x, y, flags, param):
    if event == cv2.EVENT_LBUTTONDOWN:
        rx = int(x / scale)
        ry = int(y / scale)
        print(f"  Koordinat: ({rx}, {ry})")
        # Tıklanan noktayı göster
        marked = param.copy()
        cv2.drawMarker(marked, (x, y), (0, 255, 0), cv2.MARKER_CROSS, 20, 2)
        cv2.putText(marked, f"({rx},{ry})", (x + 8, y - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        cv2.imshow("Koordinat Sec (q=kapat, r=yeni ekran)", marked)


while True:
    print("\nEmülatörü hedef ekrana getir, sonra ENTER'a bas...")
    cmd = input("> ").strip().lower()
    if cmd == 'q':
        break

    img_orig = capture()
    if img_orig is None:
        print("Ekran görüntüsü alınamadı.")
        continue

    h, w = img_orig.shape[:2]
    scale = min(1.0, 1200 / max(h, w))
    disp  = cv2.resize(img_orig, (0, 0), fx=scale, fy=scale)

    print(f"Ekran: {w}x{h} | Tıkla → koordinat, q = kapat, r = yeni ekran")
    cv2.namedWindow("Koordinat Sec (q=kapat, r=yeni ekran)")
    cv2.setMouseCallback("Koordinat Sec (q=kapat, r=yeni ekran)", on_click, disp.copy())
    cv2.imshow("Koordinat Sec (q=kapat, r=yeni ekran)", disp)

    while True:
        key = cv2.waitKey(50) & 0xFF
        if key == ord('q'):
            cv2.destroyAllWindows()
            sys.exit(0)
        elif key == ord('r'):
            cv2.destroyAllWindows()
            break  # yeni ekran al

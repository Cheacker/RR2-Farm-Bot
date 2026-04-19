import adbutils
import cv2
import numpy as np
import time
import subprocess


class ADBController:
    def __init__(self, serial=None, port=21503):
        self._port   = port
        self._serial = serial
        self.adb     = adbutils.AdbClient(host="127.0.0.1", port=5037)
        self.device  = None
        self._connect()

    def _connect(self):
        print(f"Connecting to emulator on port {self._port}...")
        try:
            self.adb.connect(f"127.0.0.1:{self._port}")
            time.sleep(0.5)
            if any(str(self._port) in d.serial for d in self.adb.device_list()):
                print(f"Connected to emulator on port {self._port}!")
        except Exception as e:
            print(f"Connection failed: {e}")

        if self._serial:
            self.device = self.adb.device(self._serial)
        else:
            devices = self.adb.device_list()
            if not devices:
                print("No ADB devices found. Make sure the emulator is running.")
                self.device = None
            else:
                self.device = devices[0]
                print(f"Connected to device: {self.device.serial}")

    def _reconnect(self):
        print("[ADB] Connection lost — kill-server/start-server...")
        try:
            subprocess.run(["adb", "kill-server"], timeout=5, capture_output=True)
            time.sleep(1)
            subprocess.run(["adb", "start-server"], timeout=10, capture_output=True)
            time.sleep(1)
        except Exception as e:
            print(f"[ADB] Server restart failed: {e}")
        try:
            self.adb = adbutils.AdbClient(host="127.0.0.1", port=5037)
            self._connect()
        except Exception as e:
            print(f"[ADB] Reconnect failed: {e}")

    def current_screen(self, retries=4):
        if not self.device:
            self._reconnect()
            return None
        for attempt in range(retries):
            try:
                img_bytes = self.device.shell("screencap -p", encoding=None)
                if not img_bytes:
                    continue
                nparr = np.frombuffer(img_bytes, np.uint8)
                img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                if img is not None and 5 < img.mean() < 250:
                    return img
                if attempt < retries - 1:
                    time.sleep(0.15)
            except Exception as e:
                print(f"Failed to capture screen: {e}")
                if attempt == retries - 1:
                    self._reconnect()
        return None

    def tap(self, x, y):
        if not self.device:
            return
        self.device.shell(f"input tap {int(x)} {int(y)}")

    def swipe(self, x1, y1, x2, y2, duration=300):
        if not self.device:
            return
        self.device.shell(f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(duration)}")

    def hold(self, x, y, duration_ms=4000):
        if not self.device:
            return
        self.device.shell(f"input swipe {int(x)} {int(y)} {int(x)} {int(y)} {int(duration_ms)}")

    def keyevent(self, key):
        if not self.device:
            return
        self.device.shell(f"input keyevent {key}")

    def restart_game(self, package: str):
        if not self.device:
            return
        print(f"[ADB] Stopping game: {package}")
        self.device.shell(f"am force-stop {package}")
        time.sleep(2)
        print(f"[ADB] Launching game: {package}")
        self.device.shell(
            f"monkey -p {package} -c android.intent.category.LAUNCHER 1"
        )
        time.sleep(5)


if __name__ == "__main__":
    ctrl = ADBController()
    if ctrl.device:
        img = ctrl.current_screen()
        if img is not None:
            print(f"Screen captured: {img.shape}")
        else:
            print("Failed to capture screen.")

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
        self.last_capture_error = None
        self._connect()

    def _candidate_ports(self):
        """LDPlayer doesn't always bind adb on the expected port — it can end up one
        off (e.g. 5555 configured, instance actually comes up on 5554) depending on
        what else grabbed ports first. Scan the configured port and its neighbor
        instead of assuming the configured one is always right."""
        port = self._port
        candidates = [port]
        for alt in (port - 1, port + 1):
            if alt not in candidates and alt > 0:
                candidates.append(alt)
        return candidates

    def _adb_server_reset(self):
        print("[ADB] Resetting adb server (kill-server/start-server)...")
        try:
            subprocess.run(["adb", "kill-server"], timeout=5, capture_output=True)
            time.sleep(1)
            subprocess.run(["adb", "start-server"], timeout=10, capture_output=True)
            time.sleep(1)
        except Exception as e:
            print(f"[ADB] Server reset failed: {e}")

    def _connect(self):
        # No automatic server reset in here: this gets polled every few seconds
        # while an emulator is booting (__init__, _restart_emulator), and a
        # kill-server/start-server mid-boot can itself prevent LDPlayer from ever
        # attaching to adb. Resetting the server is a deliberate action a caller
        # takes once via _reconnect(), never something _connect() decides on its own.
        candidates = self._candidate_ports()
        for candidate in candidates:
            print(f"Connecting to emulator on port {candidate}...")
            try:
                subprocess.run(
                    ["adb", "connect", f"127.0.0.1:{candidate}"],
                    capture_output=True, timeout=10
                )
            except Exception as e:
                print(f"[ADB] adb connect failed: {e}")
        time.sleep(1)

        try:
            self.adb = adbutils.AdbClient(host="127.0.0.1", port=5037)
            if self._serial:
                self.device = self.adb.device(self._serial)
                return
            devices = self.adb.device_list()
            if not devices:
                print("No ADB devices found. Make sure the emulator is running.")
                self.device = None
                return

            # Multiple emulators can be registered with adb at once (e.g. another
            # emulator left running alongside LDPlayer) — devices[0] would silently
            # pick whichever adb happens to list first, not the one we asked to
            # connect to. Match one of the candidate ports' serials explicitly.
            candidate_serials = [f"127.0.0.1:{p}" for p in candidates]
            match = next((d for d in devices if d.serial in candidate_serials), None)
            if match:
                if match.serial != f"127.0.0.1:{self._port}":
                    matched_port = int(match.serial.rsplit(":", 1)[1])
                    print(f"[ADB] Instance is on port {matched_port}, not the configured "
                          f"{self._port} — using {matched_port} going forward.")
                    self._port = matched_port
                self.device = match
            else:
                self.device = devices[0]
                if len(devices) > 1:
                    print(f"[ADB] WARNING: {len(devices)} devices registered, none match "
                          f"{candidate_serials} — falling back to {self.device.serial}. "
                          f"Close unused emulators to avoid controlling the wrong one.")
            print(f"Connected to device: {self.device.serial}")
        except Exception as e:
            print(f"Connection failed: {e}")
            self.device = None

    def ensure_resolution(self, width=1600, height=900):
        """Force the emulator's rendered resolution — templates/coords are all captured at 1600x900."""
        if not self.device:
            return
        target = f"{width}x{height}"
        try:
            out = self.device.shell("wm size").strip()
            if target in out:
                print(f"[ADB] Resolution OK ({out})")
                return
            print(f"[ADB] Resolution mismatch ({out}) — forcing {target}...")
            self.device.shell(f"wm size {target}")
            time.sleep(1)
            out2 = self.device.shell("wm size").strip()
            print(f"[ADB] Resolution now: {out2}")
        except Exception as e:
            print(f"[ADB] Failed to set resolution: {e}")

    def _reconnect(self):
        print("[ADB] Connection lost — kill-server/start-server...")
        self._adb_server_reset()
        try:
            self.adb = adbutils.AdbClient(host="127.0.0.1", port=5037)
            self._connect()
        except Exception as e:
            print(f"[ADB] Reconnect failed: {e}")

    def current_screen(self, retries=4):
        if not self.device:
            return None
        self.last_capture_error = None
        for attempt in range(retries):
            try:
                img_bytes = self.device.shell("screencap -p", encoding=None, timeout=15)
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
                self.last_capture_error = str(e)
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

    def quick_screen_check(self):
        """Single screencap — no reconnect side-effects. Returns True if screen is readable."""
        if not self.device:
            return False
        try:
            img_bytes = self.device.shell("screencap -p", encoding=None, timeout=15)
            if not img_bytes:
                return False
            nparr = np.frombuffer(img_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            return img is not None and 5 < img.mean() < 250
        except Exception:
            return False

    def keyevent(self, key):
        if not self.device:
            return
        self.device.shell(f"input keyevent {key}")

    def _resolve_launch_component(self, package: str):
        """Ask the device for '<package>/<launcher-activity>'. Not all emulator
        Android images ship 'monkey' (LDPlayer's doesn't), so we can't rely on
        it to launch by package name alone — resolve the real component instead."""
        try:
            out = self.device.shell(f"cmd package resolve-activity --brief {package}")
            for line in out.strip().splitlines():
                line = line.strip()
                if line.startswith(f"{package}/"):
                    return line
        except Exception as e:
            print(f"[ADB] resolve-activity failed: {e}")
        return None

    def restart_game(self, package: str, wait: int = 13):
        if not self.device:
            return
        print(f"[ADB] Stopping game: {package}")
        self.device.shell(f"am force-stop {package}")
        time.sleep(2)
        component = self._resolve_launch_component(package)
        if component:
            print(f"[ADB] Launching game: {component}")
            self.device.shell(f"am start -n {component}")
        else:
            print(f"[ADB] Could not resolve launcher activity, falling back to monkey: {package}")
            self.device.shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
        time.sleep(wait)


if __name__ == "__main__":
    ctrl = ADBController()
    if ctrl.device:
        img = ctrl.current_screen()
        if img is not None:
            print(f"Screen captured: {img.shape}")
        else:
            print("Failed to capture screen.")

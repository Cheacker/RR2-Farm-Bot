import adbutils
import cv2
import numpy as np
import time
import subprocess

SHELL_TIMEOUT = 10  # seconds; any single ADB shell command taking longer is treated as a wedged socket
CAPTURE_TIMEOUT = 8  # seconds per screencap attempt; a healthy one takes ~1.2 s


class ADBController:
    def __init__(self, serial=None, port=21503, connect_on_init=True):
        self._port   = port
        self._serial = serial
        self.adb     = adbutils.AdbClient(host="127.0.0.1", port=5037)
        self.device  = None
        self.last_capture_error = None
        self.capture_count = 0      # screencaps taken, for the hourly timing summary
        self.capture_seconds = 0.0  # total time spent in them
        if connect_on_init:
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

    def _connect(self):
        # Deliberately no adb kill-server/start-server anywhere in this class. This
        # gets polled every few seconds while an emulator is booting, and resetting
        # the adb server mid-boot was actively breaking connections that would have
        # come up fine on their own -- plain 'adb connect' polling is enough.
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
            # Right after an LDPlayer (re)start only its transient 'emulator-5554' entry may be
            # listed; it vanishes a little later and left the bot blind for 5 min (2026-11-26).
            # Give 'adb connect' up to ~20 s to register the TCP serial before falling back.
            candidate_serials = [f"127.0.0.1:{p}" for p in candidates]
            for _ in range(10):
                if any(d.serial in candidate_serials for d in devices):
                    break
                time.sleep(2)
                for candidate in candidates:
                    subprocess.run(["adb", "connect", f"127.0.0.1:{candidate}"], capture_output=True, timeout=10)
                devices = self.adb.device_list()
            if not devices:
                print("No ADB devices found. Make sure the emulator is running.")
                self.device = None
                return

            # Multiple emulators can be registered with adb at once (e.g. another
            # emulator left running alongside LDPlayer) — devices[0] would silently
            # pick whichever adb happens to list first, not the one we asked to
            # connect to. Match one of the candidate ports' serials explicitly.
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
            out = self._sh("wm size").strip()
            if target in out:
                print(f"[ADB] Resolution OK ({out})")
                return
            print(f"[ADB] Resolution mismatch ({out}) — forcing {target}...")
            self._sh(f"wm size {target}")
            time.sleep(1)
            out2 = self._sh("wm size").strip()
            print(f"[ADB] Resolution now: {out2}")
        except Exception as e:
            print(f"[ADB] Failed to set resolution: {e}")

    def current_screen(self, retries=4):
        if not self.device:
            return None
        self.last_capture_error = None
        for attempt in range(retries):
            try:
                t0 = time.perf_counter()
                img_bytes = self.device.shell("screencap -p", encoding=None, timeout=CAPTURE_TIMEOUT)
                if not img_bytes:
                    continue
                nparr = np.frombuffer(img_bytes, np.uint8)
                img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                self.capture_count += 1
                self.capture_seconds += time.perf_counter() - t0
                if img is not None and 5 < img.mean() < 250:
                    return img
                if attempt < retries - 1:
                    time.sleep(0.15)
            except Exception as e:
                print(f"Failed to capture screen: {e}")
                self.last_capture_error = str(e)
                if "not found" in str(e):
                    # the serial itself is gone (e.g. the transient emulator-5554 entry):
                    # retrying it is pointless, reconnect right away
                    self._connect()
                    if not self.device:
                        return None
        return None

    def _sh(self, cmd, timeout=SHELL_TIMEOUT):
        """Every ADB shell call goes through a timeout: a wedged ADB socket used to
        block a plain shell() forever, freezing the whole bot with no error to catch."""
        return self.device.shell(cmd, timeout=timeout)

    def tap(self, x, y):
        if not self.device:
            return
        self._sh(f"input tap {int(x)} {int(y)}")

    def swipe(self, x1, y1, x2, y2, duration=300):
        if not self.device:
            return
        self._sh(f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(duration)}")

    def hold(self, x, y, duration_ms=4000):
        if not self.device:
            return
        self._sh(f"input swipe {int(x)} {int(y)} {int(x)} {int(y)} {int(duration_ms)}",
                 timeout=SHELL_TIMEOUT + duration_ms / 1000)

    def focused_package(self):
        """Package of the window that has focus (e.g. com.android.vending when the
        Play Store is in front), or None."""
        if not self.device:
            return None
        try:
            out = self._sh("dumpsys window windows | grep -E 'mCurrentFocus'")
            for token in out.replace("}", " ").split():
                if "/" in token and "." in token:
                    return token.split("/")[0]
        except Exception as e:
            print(f"[ADB] focused_package failed: {e}")
        return None

    def package_version(self, package):
        if not self.device:
            return None
        try:
            out = self._sh(f"dumpsys package {package} | grep versionName")
            for line in out.splitlines():
                if "versionName=" in line:
                    return line.split("versionName=", 1)[1].strip()
        except Exception as e:
            print(f"[ADB] package_version failed: {e}")
        return None

    def force_stop(self, package):
        if self.device:
            self._sh(f"am force-stop {package}")

    def wait_boot_completed(self, timeout=120):
        """Poll sys.boot_completed after an emulator (re)start. ADB answers well before
        Android has finished booting; screencaps taken in that window were seen to
        hang (2026-11-22). Returns True once booted."""
        deadline = time.time() + timeout
        while self.device and time.time() < deadline:
            try:
                if self._sh("getprop sys.boot_completed", timeout=5).strip() == "1":
                    return True
            except Exception:
                pass
            time.sleep(3)
        return False

    def quick_screen_check(self):
        """Single screencap — no reconnect side-effects. Returns True if screen is readable."""
        if not self.device:
            return False
        try:
            img_bytes = self.device.shell("screencap -p", encoding=None, timeout=CAPTURE_TIMEOUT)
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
        self._sh(f"input keyevent {key}")

    def _resolve_launch_component(self, package: str):
        """Ask the device for '<package>/<launcher-activity>'. Not all emulator
        Android images ship 'monkey' (LDPlayer's doesn't), so we can't rely on
        it to launch by package name alone — resolve the real component instead."""
        try:
            out = self._sh(f"cmd package resolve-activity --brief {package}")
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
        self._sh(f"am force-stop {package}")
        time.sleep(2)
        component = self._resolve_launch_component(package)
        if component:
            print(f"[ADB] Launching game: {component}")
            self._sh(f"am start -n {component}")
        else:
            print(f"[ADB] Could not resolve launcher activity, falling back to monkey: {package}")
            self._sh(f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
        time.sleep(wait)

    # ── Real finger input (treasure mode) ─────────────────────────────────────
    def touch(self):
        """A TouchInput bound to the current device (recreated after a reconnect)."""
        if not self.device:
            return None
        t = getattr(self, "_touch", None)
        if t is None or t.serial != self.device.serial or not t.alive():
            if t is not None:
                t.close()
            self._touch = TouchInput(self.device.serial, self._find_touch_device())
        return self._touch

    def _find_touch_device(self):
        """The /dev/input node that reports multitouch positions (LDPlayer: event2)."""
        try:
            current = None
            for line in self._sh("getevent -pl").splitlines():
                if line.startswith("add device"):
                    current = line.split(":", 1)[1].strip()
                elif "ABS_MT_POSITION_X" in line and current:
                    return current
        except Exception as e:
            print(f"[ADB] touch device lookup failed: {e}")
        return "/dev/input/event2"


class TouchInput:
    """A finger that can stay down and move, via sendevent on the emulator's touch device.

    `input tap/swipe` produce very short (or motionless) touches; in a match the king
    walks toward where a finger *is* and keeps updating the target while it is held,
    which those commands can't express. One persistent `adb shell` takes the event
    lines, so a move costs no process spawn. Keys go through the same device, using
    Linux key codes; 9 and 0 are deliberately absent (15-gem boosts in a match)."""

    EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
    ABS_MT_SLOT, ABS_MT_X, ABS_MT_Y, ABS_MT_TRACKING_ID, ABS_MT_PRESSURE = 47, 53, 54, 57, 58
    BTN_TOUCH, BTN_TOOL_FINGER = 330, 325
    KEYS = {"1": 2, "2": 3, "3": 4, "5": 6, "6": 7, "7": 8, "q": 16, "w": 17, "e": 18}

    def __init__(self, serial, dev):
        self.serial, self.dev = serial, dev
        self.is_down = False
        self._tid = 100
        self._p = subprocess.Popen(["adb", "-s", serial, "shell"], stdin=subprocess.PIPE,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)

    def alive(self):
        return self._p.poll() is None

    def _send(self, events):
        try:
            self._p.stdin.write("".join(f"sendevent {self.dev} {t} {c} {v}\n" for t, c, v in events))
            self._p.stdin.flush()
        except (OSError, ValueError) as e:
            print(f"[TOUCH] send failed: {e}")

    def down(self, x, y):
        self._tid = self._tid % 60000 + 1
        self._send([(self.EV_ABS, self.ABS_MT_SLOT, 0), (self.EV_ABS, self.ABS_MT_TRACKING_ID, self._tid),
                    (self.EV_ABS, self.ABS_MT_X, int(x)), (self.EV_ABS, self.ABS_MT_Y, int(y)),
                    (self.EV_ABS, self.ABS_MT_PRESSURE, 1), (self.EV_KEY, self.BTN_TOUCH, 1),
                    (self.EV_KEY, self.BTN_TOOL_FINGER, 1), (self.EV_SYN, 0, 0)])
        self.is_down = True

    def move(self, x, y):
        if not self.is_down:
            return self.down(x, y)
        self._send([(self.EV_ABS, self.ABS_MT_SLOT, 0), (self.EV_ABS, self.ABS_MT_X, int(x)),
                    (self.EV_ABS, self.ABS_MT_Y, int(y)), (self.EV_SYN, 0, 0)])

    def up(self):
        self._send([(self.EV_ABS, self.ABS_MT_SLOT, 0), (self.EV_ABS, self.ABS_MT_TRACKING_ID, 4294967295),
                    (self.EV_KEY, self.BTN_TOUCH, 0), (self.EV_KEY, self.BTN_TOOL_FINGER, 0),
                    (self.EV_SYN, 0, 0)])
        self.is_down = False

    def keys(self, names):
        ev = []
        for n in names:
            k = self.KEYS[n]   # KeyError for anything not whitelisted (e.g. the gem keys 9/0)
            ev += [(self.EV_KEY, k, 1), (self.EV_SYN, 0, 0), (self.EV_KEY, k, 0), (self.EV_SYN, 0, 0)]
        self._send(ev)

    def close(self):
        if self.is_down:
            self.up()
        try:
            self._p.stdin.write("exit\n"); self._p.stdin.flush()
            self._p.wait(timeout=5)
        except Exception:
            self._p.kill()


if __name__ == "__main__":
    ctrl = ADBController()
    if ctrl.device:
        img = ctrl.current_screen()
        if img is not None:
            print(f"Screen captured: {img.shape}")
        else:
            print("Failed to capture screen.")

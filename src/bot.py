import time
import os
import glob
import argparse
import subprocess
import cv2
from controller import ADBController
from vision import VisionInterpreter
from player_db import PlayerDB

FAIL_DEBUG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fail_debug")
os.makedirs(FAIL_DEBUG_DIR, exist_ok=True)

RR2_PACKAGE            = "com.flaregames.rrtournament"
EMULATOR_RESTART_INTERVAL = 3 * 3600  # restart the emulator every 3 hours

# ldconsole.exe lives inside the versioned LDPlayer install folder (LDPlayer9,
# LDPlayer14, ...) which differs per machine/drive — glob instead of hardcoding.
_LDCONSOLE_GLOBS = [
    r"C:\LDPlayer\LDPlayer*\ldconsole.exe",
    r"D:\LDPlayer\LDPlayer*\ldconsole.exe",
    r"C:\Program Files\LDPlayer\LDPlayer*\ldconsole.exe",
    r"D:\Program Files\LDPlayer\LDPlayer*\ldconsole.exe",
    r"C:\Program Files (x86)\LDPlayer\LDPlayer*\ldconsole.exe",
]


def find_ldconsole():
    """Best-effort auto-detect of ldconsole.exe. Returns None if not found —
    callers must fall back to requiring --ldconsole explicitly."""
    for pattern in _LDCONSOLE_GLOBS:
        matches = glob.glob(pattern)
        if matches:
            return matches[0]
    return None

# ── Coordinates ──────────────────────────────────────────────────────────────
TROPHY_COORDS       = (1546, 114)     # set with get_coords.py
COLLECT_ALL_RESOURCES  = (60, 506)     # set with get_coords.py
BLUE_SEARCH_COORDS  = (1436, 213)
ARCHER_COORDS      = (200, 800)
CANNON_COORDS      = (240, 800)
MINUS_LEFT_COORDS  = (810, 226)
MINUS_RIGHT_COORDS = (1084, 226)
PLUS_LEFT_COORDS   = (985, 229)
PLUS_RIGHT_COORDS  = (1258, 226)
IN_GAME_TAP_COORDS = (10, 10) # (1130, 274) for real tap on map
GEAR_SET_1_COORDS  = (226, 818)
GEAR_SET_2_COORDS  = (298, 820)
GEAR_SET_3_COORDS  = (366, 818)
VIDEO_CLOSE_COORDS = (1522, 94)
SHOP_COORDS        = (1540, 508)
GREEN_BACK_COORDS  = (1430, 85)    # back out of attack prep / loading screen
SCROLL_CONFIRM_COORDS = (812, 832) # ranked-list confirm after scroll — on the
                                   # attack-prep screen this is "New Opponent",
                                   # so never tap it without confirming the screen

# ── Drop-trophies mode (only used when --drop-trophies drop_yes) ─────────────
# All four are unset until captured — get_coords.py for the two tap coordinates
# and the home-return coordinate, and get_coords.py again (or just eyeball the
# region) for the OCR box around your own trophy count. Until all four are set,
# drop mode falls back to the old behavior: a plain timeout + full game restart.
OWN_TROPHY_REGION    = None  # (x1, y1, x2, y2) OCR region showing your own trophy count
DROP_BUTTON_1_COORDS = None  # tapped first, 2s after the match starts
DROP_BUTTON_2_COORDS = None  # tapped right after button 1
DROP_HOME_COORDS     = None  # final tap that returns to HOME after dropping
DROP_TROPHY_MARGIN   = 400   # dynamic filter target = own trophies - this


class State:
    HOME               = "HOME"
    TROPHY_MENU        = "TROPHY_MENU"
    FILTERED_RANKS     = "FILTERED_RANKS"
    ATTACK_PREP        = "ATTACK_PREP"
    GAME_LOAD          = "GAME_LOAD"
    GOING_BACK         = "GOING_BACK"
    IN_GAME            = "IN_GAME"
    CHAMBER_OF_FORTUNE = "CHAMBER_OF_FORTUNE"


class RR2Bot:
    def __init__(self, port=21503, template_dir=None, trophy_filter=600, melt_threshold=1_000_000,
                 drop_trophies=False, ld_index=0, ldconsole=None):
        self.adb = ADBController(port=port)
        self._ld_index = ld_index
        self._ldconsole = ldconsole or find_ldconsole()
        self._emulator_just_launched = False
        if not self.adb.device:
            if not self._ldconsole:
                print("[EMULATOR] No ADB device, and ldconsole.exe could not be auto-detected — "
                      "start the LDPlayer instance manually, or pass its path with --ldconsole.")
                exit(1)
            print("[EMULATOR] No ADB device — closing any existing instance and launching fresh...")
            subprocess.run([self._ldconsole, "quit", "--index", str(self._ld_index)], capture_output=True)
            time.sleep(3)
            subprocess.Popen([self._ldconsole, "launch", "--index", str(self._ld_index)])
            self._emulator_just_launched = True
            print("[EMULATOR] Waiting for the instance to boot...")
            time.sleep(15)
            self.adb._reconnect()
            deadline = time.time() + 90
            while time.time() < deadline:
                if self.adb.device:
                    break
                self.adb._connect()
                time.sleep(5)
        if not self.adb.device:
            print("ADB connection failed.")
            exit(1)
        self.adb.ensure_resolution(1600, 900)
        self.vision = VisionInterpreter(template_dir=template_dir)
        if "btn_attack_start_gray" not in self.vision.templates:
            print("[WARN] btn_attack_start_gray.png missing — unattackable opponents "
                  "will only be caught by the 12s timeout. Capture it with: "
                  "python recrop.py → option 25")
        if drop_trophies and not (OWN_TROPHY_REGION and DROP_BUTTON_1_COORDS
                                   and DROP_BUTTON_2_COORDS and DROP_HOME_COORDS):
            print("[WARN] --drop-trophies is on but the drop-mode coordinates in bot.py "
                  "aren't set — falling back to the old timeout+restart behavior. Fill in, "
                  "at the top of bot.py: OWN_TROPHY_REGION (OCR box around your own trophy "
                  "count, x1,y1,x2,y2 — use get_coords.py to find the corners), "
                  "DROP_BUTTON_1_COORDS and DROP_BUTTON_2_COORDS (the two buttons tapped in "
                  "order 2s after a match starts to end it), and DROP_HOME_COORDS (final tap "
                  "back to HOME) — all via get_coords.py.")
        self.state  = State.HOME
        self.running = True

        self._last_end_check    = 0
        self._last_tap          = 0
        self._skip_top          = 0
        self._scroll_count      = 0
        self._chest_taps        = 0
        self._match_count       = 0
        self._start_time        = time.time()
        self._loop_start        = time.time()
        self._no_opponent_count = 0
        self._current_target    = None
        self._attack_prep_start = 0
        self._trophy_miss_count = 0
        self._trophy_menu_miss  = 0
        self._in_game_start     = 0
        self._trophy_filter     = trophy_filter
        self._melt_threshold    = melt_threshold
        self._drop_trophies     = drop_trophies
        self._in_game_timeout   = 3 if drop_trophies else 180
        self._gold_start        = None
        self._pearl_start       = None
        self._gold_last         = None
        self._pearl_last        = None
        self._game_load_miss    = 0
        self._screen_none_count = 0
        self._anchor_miss_streak = 0
        self.db = PlayerDB()


    # ── Emulator restart ──────────────────────────────────────────────────────
    def _restart_emulator(self):
        if not self._ldconsole:
            print("[EMULATOR] ldconsole.exe unknown — cannot restart the instance, "
                  "skipping (pass --ldconsole to enable this). Restarting the game only.")
            self.adb.restart_game(RR2_PACKAGE)
            self.state = State.HOME
            return
        print(f"[EMULATOR] Closing instance --index {self._ld_index}...")
        subprocess.run([self._ldconsole, "quit", "--index", str(self._ld_index)], capture_output=True)
        time.sleep(5)
        print(f"[EMULATOR] Launching instance --index {self._ld_index}...")
        subprocess.Popen([self._ldconsole, "launch", "--index", str(self._ld_index)])
        self.db.set_last_emulator_restart()
        print("[EMULATOR] Waiting for ADB to be ready...")
        time.sleep(20)
        deadline = time.time() + 75
        while time.time() < deadline:
            self.adb._connect()
            if self.adb.device:
                print("[EMULATOR] Instance is ready.")
                self.adb.ensure_resolution(1600, 900)
                break
            time.sleep(5)
        else:
            print("[EMULATOR] Timeout — instance did not start within 90s.")
        self.db.set_last_emulator_restart()
        self.adb.restart_game(RR2_PACKAGE)
        self.state = State.HOME
        print("[EMULATOR] Restart complete.")

    # ── Shutdown helper ───────────────────────────────────────────────────────
    def _shutdown(self, reason: str):
        print(f"[SHUTDOWN] Reason: {reason}")
        fresh = self.adb.current_screen()
        if fresh is not None:
            ts   = time.strftime('%Y%m%d_%H%M%S')
            path = os.path.join(FAIL_DEBUG_DIR, f'{ts}_{reason}.png')
            cv2.imwrite(path, fresh)
            print(f"[SHUTDOWN] Screenshot saved: {path}")
        self.running = False

    # ── Main loop ─────────────────────────────────────────────────────────────
    def loop(self):
        print("Bot started! Press Ctrl+C to stop.")
        last_restart = self.db.get_last_emulator_restart()
        if self._emulator_just_launched:
            # Instance was just launched in __init__ — treat as fresh restart, don't close again
            self.db.set_last_emulator_restart()
            self.adb.restart_game(RR2_PACKAGE)
        elif last_restart is None:
            self.db.set_last_emulator_restart()
            self.adb.restart_game(RR2_PACKAGE)
        elif time.time() - last_restart >= EMULATOR_RESTART_INTERVAL:
            self._restart_emulator()
        else:
            self.adb.restart_game(RR2_PACKAGE)
        while self.running:
            try:
                # Runs every iteration regardless of state — previously gated behind
                # State.HOME, so a session stuck anywhere else (e.g. spinning in
                # TROPHY_MENU) never reached this check and the safety-net restart
                # that's supposed to catch a broken/hung emulator never fired.
                last_restart = self.db.get_last_emulator_restart() or self._start_time
                if time.time() - last_restart >= EMULATOR_RESTART_INTERVAL:
                    self._restart_emulator()
                    continue

                screen = self.adb.current_screen()
                if screen is None:
                    self._screen_none_count += 1
                    if self._screen_none_count >= 100:
                        print(f"[LOOP] No screen for {self._screen_none_count} attempts — "
                              f"emulator likely hung or disconnected, restarting it...")
                        self._screen_none_count = 0
                        self._restart_emulator()
                        continue
                    time.sleep(0.1)
                    continue
                self._screen_none_count = 0

                if   self.state == State.HOME:               self.handle_home(screen)
                elif self.state == State.TROPHY_MENU:        self.handle_trophy_menu(screen)
                elif self.state == State.FILTERED_RANKS:     self.handle_filtered_ranks(screen)
                elif self.state == State.ATTACK_PREP:        self.handle_attack_prep(screen)
                elif self.state == State.GAME_LOAD:          self.handle_game_load(screen)
                elif self.state == State.GOING_BACK:         self.handle_going_back(screen)
                elif self.state == State.IN_GAME:            self.handle_in_game(screen)
                elif self.state == State.CHAMBER_OF_FORTUNE: self.handle_chamber_of_fortune(screen)

                # A handler's primary anchor template missing 10 times in a row usually
                # means we're not actually on the screen self.state assumes (stuck popup,
                # a screen the templates weren't built for, etc). Rather than keep tapping
                # blind coordinates for a screen that isn't there, scan every state's
                # anchor to find out what's really on screen and resync to it.
                if self._anchor_miss_streak >= 10:
                    print(f"[RESYNC] {self._anchor_miss_streak} consecutive misses in "
                          f"{self.state} — scanning all anchors for the real screen...")
                    detected = self._find_state(screen)
                    if detected and detected != self.state:
                        print(f"[RESYNC] Screen actually shows {detected}, not {self.state} — resyncing.")
                        self.state = detected
                    elif detected == self.state:
                        print(f"[RESYNC] Screen still matches {self.state} — false alarm, continuing.")
                    else:
                        print("[RESYNC] Screen doesn't match any known anchor.")
                    self._anchor_miss_streak = 0

                time.sleep(0.1)

            except KeyboardInterrupt:
                print("Bot stopped.")
                self.running = False
            except Exception as e:
                print(f"Error: {e}")
                time.sleep(2)

    # ── HOME ──────────────────────────────────────────────────────────────────
    def handle_home(self, screen):
        print("[HOME] Searching for forge icon...")
        pos = self.vision.find_template(screen, "icon_forge", 0.90)
        if pos:
            self._trophy_miss_count = 0
            self._anchor_miss_streak = 0
            gold  = self.vision.read_region_number(screen, 102, 29, 253, 72)
            pearl = self.vision.read_region_number(screen, 88, 194, 213, 228)
            if gold is not None and pearl is not None:
                if self._gold_start is None:
                    self._gold_start  = gold
                    self._pearl_start = pearl
                self._gold_last  = gold
                self._pearl_last = pearl
            if self._match_count % 3 == 0:
                print(f"[HOME] Match #{self._match_count} — collecting all resources...")
                self.adb.tap(*COLLECT_ALL_RESOURCES)
                time.sleep(0.5)
            print("[HOME] Forge icon found, tapping trophy...")
            self.adb.tap(*TROPHY_COORDS)
            self.state = State.TROPHY_MENU
            time.sleep(0.5)
        else:
            self._trophy_miss_count += 1
            self._anchor_miss_streak += 1
            if self._trophy_miss_count > 21:
                print(f"[HOME] Forge not found after {self._trophy_miss_count} attempts — restarting game...")
                self._trophy_miss_count = 0
                self.adb.restart_game(RR2_PACKAGE)
                self.state = State.HOME
                return

            # Search for btn_close on every miss, not just every 6th — if the forge
            # icon is missing because a popup/shop leftover is covering the screen
            # (e.g. after the GAME_LOAD food-purchase flow didn't fully close out),
            # waiting up to 6 misses to even try closing it just prolongs being stuck.
            close = self.vision.find_template(screen, "btn_close", threshold=0.57)
            if close:
                print("[HOME] Pressing btn_close...")
                self.adb.tap(close[0], close[1])
                time.sleep(0.5)

            if self._trophy_miss_count % 2 == 0:
                self.adb.tap(10, 10)
                time.sleep(0.1)

            if self._trophy_miss_count % 6 == 0:
                btn_big_collect = self.vision.find_template(screen, "btn_big_collect", threshold=0.80)
                if btn_big_collect:
                    print("[HOME] btn_big_collect found, tapping...")
                    self.adb.tap(btn_big_collect[0], btn_big_collect[1])
                    time.sleep(0.5)

            if self._trophy_miss_count % 10 == 0:
                league = self.vision.find_template(screen, "btn_collect_league", threshold=0.80)
                if league:
                    print(f"[HOME] {self._trophy_miss_count}th miss → pressing btn_collect_league...")
                    self.adb.tap(league[0], league[1])
                    time.sleep(1.0)

            if self._trophy_miss_count % 5 == 0:
                collect = self.vision.find_template(screen, "btn_collect", threshold=0.80)
                if collect:
                    print(f"[HOME] {self._trophy_miss_count}th miss → pressing btn_collect...")
                    self.adb.tap(collect[0], collect[1])
                    time.sleep(3.5)
                    self.adb.tap(1524, 86)
                else:
                    close = self.vision.find_template(screen, "btn_close", threshold=0.80)
                    if close:
                        print("[HOME] No collect found, trying btn_close...")
                        self.adb.tap(close[0], close[1])

    # ── Helper: identify the actual screen from its anchor template(s) ───────
    def _find_state(self, screen):
        """Probe every state's anchor template(s), independent of self.state. Used
        only as a resync fallback after a handler's expected anchor keeps missing —
        cheap enough to run occasionally, too much vision work to run every loop."""
        if screen is None:
            return None
        if self.vision.find_template(screen, "icon_forge", threshold=0.90):
            return State.HOME
        if self.vision.find_template(screen, "btn_start_search", threshold=0.80):
            return State.TROPHY_MENU
        if self.vision.find_multiple_templates(screen, "area_top_opponent", threshold=0.92):
            return State.FILTERED_RANKS
        if self._on_attack_prep_screen(screen):
            return State.ATTACK_PREP
        if (self.vision.find_template(screen, "btn_archer", threshold=0.85)
                or self.vision.find_template(screen, "btn_bring_me_back", threshold=0.85)):
            return State.GAME_LOAD
        if (self.vision.find_template(screen, "btn_give_up", threshold=0.70)
                or self.vision.find_template(screen, "btn_sell", threshold=0.70)
                or self._find_chests(screen)):
            return State.CHAMBER_OF_FORTUNE
        return None

    # ── Helper: is the attack-prep screen showing? ────────────────────────────
    def _on_attack_prep_screen(self, screen):
        """True if either the yellow or gray attack button is visible."""
        if screen is None:
            return False
        return bool(
            self.vision.find_template(screen, "btn_attack_start", threshold=0.85)
            or self.vision.find_template(screen, "btn_attack_start_gray", threshold=0.85)
        )

    # ── Helper: leave attack prep ─────────────────────────────────────────────
    def _leave_attack_prep(self, reason, permanent=False):
        """Navigate back to the ranked list. Never just flip state — if we stay on
        the attack-prep screen, FILTERED_RANKS blind-taps 'New Opponent'.

        permanent=True is for a confirmed-gray attack button: the trophy gap makes this
        opponent unattackable regardless of when we last saw them, so it's recorded
        permanently instead of the 15-minute active-player mark (which is only a "they
        might be online, try again later" guess, not "this matchup can never work")."""
        if self._current_target:
            if permanent:
                self.db.mark_unattackable(self._current_target)
                print(f"[ATTACK_PREP] {reason} — '{self._current_target}' marked permanently unattackable")
            else:
                self.db.mark_active(self._current_target)
                print(f"[ATTACK_PREP] {reason} — '{self._current_target}' marked active")
        else:
            print(f"[ATTACK_PREP] {reason} — backing out")
        self.adb.tap(*GREEN_BACK_COORDS)
        self._current_target = None
        self._skip_top += 1
        self.state = State.FILTERED_RANKS
        time.sleep(0.6)

    # ── Helper: scroll ranked list ────────────────────────────────────────────
    def _scroll_list(self, times=1):
        for _ in range(times):
            self.adb.swipe(650, 600, 650, 300, 300)
            time.sleep(0.4)
        # Guard the blind confirm tap — if we are actually on the attack-prep
        # screen this coordinate is "New Opponent" (unfiltered reroll, costs trophies).
        f = self.adb.current_screen()
        if self._on_attack_prep_screen(f):
            print("[SCROLL] Attack-prep screen detected — skipping confirm tap, backing out")
            self.adb.tap(*GREEN_BACK_COORDS)
            time.sleep(0.6)
            self.state = State.FILTERED_RANKS
            self._skip_top = 0
            return
        self.adb.tap(*SCROLL_CONFIRM_COORDS)
        # Poll instead of a fixed sleep — the list reload after the confirm tap
        # doesn't take a constant amount of time (varies by machine/emulator), and a
        # fixed sleep that's too short hands handle_filtered_ranks a still-loading
        # screen, which reads as "no swords found" and looks like vision breaking.
        deadline = time.time() + 5
        while time.time() < deadline:
            time.sleep(0.3)
            f = self.adb.current_screen()
            if f is not None and self.vision.find_multiple_templates(f, "area_top_opponent", threshold=0.92):
                break
        print(f"List scrolled {times} time(s).")
        self._skip_top = 0

    # ── TROPHY_MENU ───────────────────────────────────────────────────────────
    def handle_trophy_menu(self, screen):
        # 0.80, not the original 0.95 — on LDPlayer this template consistently scores
        # ~0.84 (stable across frames, so it's a genuine match, not noise), likely due
        # to rendering differences from the MEmu-captured source image. Ideally
        # recapture btn_start_search via recrop.py (option 2) on LDPlayer directly.
        yellow = self.vision.find_template(screen, "btn_start_search", threshold=0.80)
        if yellow:
            self._trophy_menu_miss = 0
            self._anchor_miss_streak = 0
        
            if self._drop_trophies:
                if OWN_TROPHY_REGION:
                    own_trophies = self.vision.read_region_number(screen, *OWN_TROPHY_REGION)
                    if own_trophies is not None:
                        self._trophy_filter = max(100, own_trophies - DROP_TROPHY_MARGIN)
                        print(f"[TROPHY_MENU] Drop mode: own trophies={own_trophies} → "
                              f"filter set to {self._trophy_filter}")
                    else:
                        print("[TROPHY_MENU] Drop mode: OCR of own trophy count failed, "
                              f"keeping last filter ({self._trophy_filter})")
                else:
                    print("[TROPHY_MENU] Drop mode is on but OWN_TROPHY_REGION isn't set — "
                          f"using the static --trophy-filter value instead ({self._trophy_filter})")

            val_left  = self.vision.read_region_number(screen, 850, 212, 948, 250)
            val_right = self.vision.read_region_number(screen, 1122, 209, 1218, 252)
            print(f"[TROPHY_MENU] OCR → left={val_left}, right={val_right}")

            if val_left is None or val_right is None:
                print(f"[TROPHY_MENU] OCR failed (left={val_left}, right={val_right}), skipping adjustment")
            else:
                left_presses  = max(0, (val_left  - (300)) // 100)
                right_delta   = (val_right - self._trophy_filter) // 100
                if left_presses or right_delta != 0:
                    print(f"[TROPHY_MENU] Adjusting: left -{left_presses}x, right -{right_delta}x")
                for _ in range(left_presses):
                    self.adb.tap(*MINUS_LEFT_COORDS)
                    time.sleep(0.15)
                if right_delta > 0:
                    for _ in range(right_delta):
                        self.adb.tap(*MINUS_RIGHT_COORDS)
                        time.sleep(0.15)
                elif right_delta < 0:
                    for _ in range(-right_delta):
                        self.adb.tap(*PLUS_RIGHT_COORDS)
                        time.sleep(0.15)

            print("[TROPHY_MENU] Search button found, adjusting filters then tapping...")
            self.adb.tap(yellow[0], yellow[1])
            time.sleep(1)
            self.state = State.FILTERED_RANKS
            return
        print(f"[TROPHY_MENU] Search button not found, tapping blue search coordinates: {BLUE_SEARCH_COORDS}")
        self.adb.tap(*BLUE_SEARCH_COORDS)
        self._trophy_menu_miss += 1
        self._anchor_miss_streak += 1
        # This used to spin forever with no recovery and no diagnostic trail if the
        # search button never appeared — save one screenshot for later inspection, then
        # eventually restart the game rather than getting stuck for the whole session.
        if self._trophy_menu_miss == 50:
            fresh = self.adb.current_screen()
            if fresh is not None:
                ts   = time.strftime('%Y%m%d_%H%M%S')
                path = os.path.join(FAIL_DEBUG_DIR, f'{ts}_trophy_menu_stuck.png')
                cv2.imwrite(path, fresh)
                print(f"[TROPHY_MENU] Still stuck after {self._trophy_menu_miss} misses — saved {path}")
        if self._trophy_menu_miss >= 300:
            print(f"[TROPHY_MENU] Search button not found after {self._trophy_menu_miss} attempts — restarting game...")
            self._trophy_menu_miss = 0
            self.adb.restart_game(RR2_PACKAGE)
            self.state = State.HOME
            return
        time.sleep(0.15)

    # ── FILTERED_RANKS ────────────────────────────────────────────────────────
    def handle_filtered_ranks(self, screen):
        opponents = self.vision.find_multiple_templates(screen, "area_top_opponent", threshold=0.92)
        if not opponents:
            self._no_opponent_count += 1
            self._anchor_miss_streak += 1
            if self._no_opponent_count >= 27:
                print(f"[FILTERED_RANKS] No sword found after {self._no_opponent_count} attempts — restarting game...")
                self._no_opponent_count = 0
                self.adb.restart_game(RR2_PACKAGE)
                self.state = State.HOME
                return
            if self._no_opponent_count % 9 == 0:
                # 0.85, not 0.5 — a 0.5 threshold matches almost anything and
                # used to drag the bot into ATTACK_PREP on unrelated screens.
                if self._on_attack_prep_screen(screen):
                    print("[FILTERED_RANKS] Attack-prep screen detected → ATTACK_PREP")
                    self.state = State.ATTACK_PREP
                    self._attack_prep_start = time.time()
                    return
            if self._no_opponent_count % 3 == 0:
                print(f"[FILTERED_RANKS] No sword found after {self._no_opponent_count} attempts, scrolling...")
                self._scroll_list()
            return

        self._no_opponent_count = 0
        self._anchor_miss_streak = 0
        opponents.sort(key=lambda pos: pos[1])
        for i, sword_pos in enumerate(opponents):
            is_last = (i == len(opponents) - 1)
            name = self.vision.read_player_name(screen, sword_pos[0], sword_pos[1])
            if not name:
                if is_last:
                    print("[FILTERED_RANKS] Last opponent name unreadable (partially visible) → scrolling")
                    self._scroll_list()
                    return
                print("[FILTERED_RANKS] Could not read player name → skipping")
                continue
            active       = self.db.is_active(name)
            unattackable = self.db.is_unattackable(name)
            info   = self.db.info_str(name)
            print(f"Player detected: [{name}], {info}")
            if active or unattackable:
                self._skip_top += 1
                reason = "Unattackable" if unattackable else "Active"
                print(f"  → {reason}, skipping. skip={self._skip_top}")
                if self._skip_top >= 4:
                    self._scroll_count += 1
                    print(f"[FILTERED_RANKS] skip={self._skip_top} >= 4, scrolling x{self._scroll_count}...")
                    self._scroll_list(self._scroll_count)
                continue
            print(f"[FILTERED_RANKS] Tapping: {sword_pos}")
            self._current_target = name
            self.adb.tap(sword_pos[0], sword_pos[1])
            time.sleep(0.1)
            self.state = State.ATTACK_PREP
            self._attack_prep_start = time.time()
            return
        print(f"[FILTERED_RANKS] All swords active/skipped, skip={self._skip_top}")

    # ── ATTACK_PREP ───────────────────────────────────────────────────────────
    def handle_attack_prep(self, screen):
        # Unattackable opponent: the attack button renders gray instead of yellow.
        # Check this FIRST so we back out in one loop instead of waiting out 12s.
        if self.vision.find_template(screen, "btn_attack_start_gray", threshold=0.90):
            self._anchor_miss_streak = 0
            self._leave_attack_prep("Attack button is gray (unattackable)", permanent=True)
            return

        pos = self.vision.find_template(screen, "btn_attack_start", threshold=0.9)
        if pos:
            self._anchor_miss_streak = 0
            print("[ATTACK_PREP] Attack button found, pressing → GAME_LOAD...")
            self.adb.tap(*GEAR_SET_3_COORDS)
            time.sleep(0.1)
            self.adb.tap(pos[0], pos[1])
            self.state = State.GAME_LOAD
            time.sleep(0.2)
        elif time.time() - self._attack_prep_start > 12:
            self._leave_attack_prep("Button not found within 12s")
        else:
            self._anchor_miss_streak += 1
            print("[ATTACK_PREP] Waiting for attack button...")

    # ── GAME_LOAD ─────────────────────────────────────────────────────────────
    def handle_game_load(self, screen):
        time.sleep(0.1)
        self.adb.tap(*CANNON_COORDS)
        video_btn = self.vision.find_template(screen, "btn_video", threshold=0.90)
        if video_btn:
            print("[GAME_LOAD] Video/food offer detected → buying food...")
            self.adb.tap(*VIDEO_CLOSE_COORDS)
            time.sleep(1)
            self.adb.tap(*SHOP_COORDS)
            time.sleep(7)
            f = self.adb.current_screen()
            if f is not None:
                food_btn = self.vision.find_template(f, "btn_food", threshold=0.90)
                if food_btn:
                    print("[GAME_LOAD] Food found, buying...")
                    self.adb.tap(food_btn[0], food_btn[1])
                    time.sleep(1)
                else:
                    print("[GAME_LOAD] Food button not found in shop.")
            # Prefer a template-matched close over the fixed coordinate — VIDEO_CLOSE_COORDS
            # is reused here on the assumption the shop's X sits in the same spot as the
            # video popup's, which isn't guaranteed on every emulator/resolution.
            f = self.adb.current_screen()
            close_btn = self.vision.find_template(f, "btn_close", threshold=0.70) if f is not None else None
            self.adb.tap(*close_btn) if close_btn else self.adb.tap(*VIDEO_CLOSE_COORDS)
            time.sleep(1)
            # Verify we actually left the shop/popup before declaring HOME — forcing the
            # state blindly here is what left the bot stuck showing the shop on screen
            # while HOME kept looking for icon_forge and never finding it.
            f = self.adb.current_screen()
            still_stuck = f is not None and (
                self.vision.find_template(f, "btn_video", threshold=0.90)
                or self.vision.find_template(f, "btn_food", threshold=0.90)
            )
            if not still_stuck:
                self.state = State.HOME
                return
            print("[GAME_LOAD] Shop/video popup still visible after close attempt — retrying via miss counter.")
            # Fall through to the miss counter below instead of returning — the existing
            # 15-miss restart-game safety net will recover if this keeps failing.
        go_back = self.vision.find_template(screen, "btn_bring_me_back", threshold=0.9)
        if go_back:
            self._game_load_miss = 0
            self._anchor_miss_streak = 0
            self._skip_top += 1
            if self._current_target:
                self.db.mark_active(self._current_target)
                print(f"[GAME_LOAD] Active player! '{self._current_target}' marked active, skip={self._skip_top}...")
            else:
                print(f"[GAME_LOAD] Active player! skip={self._skip_top}...")
            self.adb.tap(go_back[0], go_back[1])
            self.state = State.GOING_BACK
            time.sleep(0.5)
            return
        time.sleep(0.1)
        archer = self.vision.find_template(screen, "btn_archer", threshold=0.9)
        if archer:
            self._game_load_miss = 0
            self._anchor_miss_streak = 0
            print("[GAME_LOAD] Archer button visible, match started!")
            self._skip_top = 0
            self.adb.tap(*ARCHER_COORDS)
            time.sleep(0.1)
            self.adb.tap(*CANNON_COORDS)
            time.sleep(0.1)
            self.adb.tap(*CANNON_COORDS)
            self._in_game_start = time.time()
            self.state = State.IN_GAME
            return 
        self._game_load_miss += 1
        self._anchor_miss_streak += 1
        if self._game_load_miss >= 15:
            print(f"[GAME_LOAD] Nothing found for {self._game_load_miss} attempts — restarting game...")
            self._game_load_miss = 0
            self.adb.restart_game(RR2_PACKAGE)
            self.state = State.HOME
            return
        print(f"[GAME_LOAD] Waiting... ({self._game_load_miss}/15)")

    # ── GOING_BACK ────────────────────────────────────────────────────────────
    def handle_going_back(self, screen):
        print("[GOING_BACK] Tapping green back button → FILTERED_RANKS...")
        self.adb.tap(*GREEN_BACK_COORDS)
        self._skip_top = 0
        self.state = State.FILTERED_RANKS
        time.sleep(0.5)

    # ── IN_GAME ───────────────────────────────────────────────────────────────
    def handle_in_game(self, screen):
        now = time.time()

        drop_coords_ready = DROP_BUTTON_1_COORDS and DROP_BUTTON_2_COORDS and DROP_HOME_COORDS
        if (self._drop_trophies and drop_coords_ready
                and self._in_game_start > 0 and now - self._in_game_start >= 2):
            print("[IN_GAME] Drop-trophies: 2s elapsed, tapping drop sequence...")
            self.adb.tap(*DROP_BUTTON_1_COORDS)
            time.sleep(0.3)
            self.adb.tap(*DROP_BUTTON_2_COORDS)
            time.sleep(1.5)
            self.adb.tap(*DROP_HOME_COORDS)
            self._in_game_start = 0
            self.state = State.HOME
            time.sleep(0.5)
            return

        if self._in_game_start > 0 and now - self._in_game_start > self._in_game_timeout:
            print(f"[IN_GAME] {self._in_game_timeout}s timeout — restarting game...")
            self._in_game_start = 0
            self.adb.restart_game(RR2_PACKAGE)
            self.state = State.HOME
            return
        if now - self._last_tap >= 0.65:
            self._last_tap = now
            self.adb.tap(*IN_GAME_TAP_COORDS)
            print(f"Tapped: {IN_GAME_TAP_COORDS}")

        if screen is not None and now - self._last_end_check >= 4:
            self._last_end_check = now
            continue_pos = self.vision.find_template(screen, "btn_continue", threshold=0.95)
            if continue_pos:
                print("[IN_GAME] Match ended, going to result screen...")
                self.adb.tap(continue_pos[0], continue_pos[1])
                self.state = State.CHAMBER_OF_FORTUNE
                time.sleep(1.25)

    # ── COF helpers ───────────────────────────────────────────────────────────
    def _cof_tap_home(self):
        now = time.time()
        self._match_count += 1
        loop_dur  = now - self._loop_start
        total_secs = int(now - self._start_time)
        self._loop_start = now
        h = total_secs // 3600
        m = (total_secs % 3600) // 60
        s = total_secs % 60
        total_str = f"{h:02d}:{m:02d}:{s:02d}"
        if self._gold_last is not None and self._gold_start is not None:
            gold_gain  = self._gold_last  - self._gold_start
            pearl_gain = self._pearl_last - self._pearl_start
            resources  = f" | Gold: ~+{gold_gain:,} | Pearls: ~+{pearl_gain}"
        else:
            resources = ""
        print("--------------------------------------------------------------------")
        print(f"[COF] Match #{self._match_count} | Loop: {loop_dur:.0f}s | Total: {total_str} | Avg: {total_secs/self._match_count:.0f}s{resources}")
        print("--------------------------------------------------------------------")
        self.adb.tap(500, 500)
        self._chest_taps    = 0
        self._current_target = None
        self._scroll_count  = 0
        self.state = State.HOME
        time.sleep(1)

    def _find_chests(self, f) -> list:
        positions = []
        for i in range(1, 7):
            pos = self.vision.find_template(f, f"chest_{i}", threshold=0.7)
            if pos:
                positions.append(pos)
        return positions

    # ── CHAMBER_OF_FORTUNE ────────────────────────────────────────────────────
    def handle_chamber_of_fortune(self, screen):
        time.sleep(1)
        missed_chests = 0
        while self.running and self._chest_taps < 3:
            time.sleep(0.1)
            f = self.adb.current_screen()
            if f is None:
                continue

            pes = self.vision.find_template(f, "btn_give_up", threshold=0.70)
            if pes:
                print(f"[COF] Give up (1 buttons), tapping leftmost: {pes}")
                self.adb.tap(pes[0], pes[1])
                time.sleep(3)
                self._cof_tap_home()
                return

            sell = self.vision.find_template(f, "btn_sell", threshold=0.70)
            if sell:
                melt = self.vision.find_template(f, "btn_melt", threshold=0.92)
                gold_ref = self._gold_last if self._gold_last is not None else self._gold_start
                if melt and (gold_ref is None or gold_ref > self._melt_threshold):
                    print(f"[COF] Melt (gold={f'{gold_ref:,}' if gold_ref is not None else '?'}): {melt}")
                    self.adb.tap(melt[0], melt[1])
                else:
                    print(f"[COF] Sell: {sell}")
                    self.adb.tap(sell[0], sell[1])
                missed_chests = 0
                continue

            chests = self._find_chests(f)
            if not chests:
                missed_chests += 1
                if missed_chests == 2:
                    self._cof_tap_home()
                    return
                else:
                    continue

            target = chests[0]
            print(f"[COF] Opening chest 1 ({self._chest_taps + 1}/3)...")
            self.adb.tap(target[0], target[1])
            time.sleep(0.5)

            f2 = self.adb.current_screen()
            if f2 is not None:
                new_count = len(self._find_chests(f2))
                if new_count == 6 - (self._chest_taps + 1):
                    self._chest_taps += 1

        while self.running:
            time.sleep(0.1)
            f = self.adb.current_screen()
            if f is None:
                continue

            pes = self.vision.find_template(f, "btn_give_up", threshold=0.70)
            if pes:
                print(f"[COF] Give up (1 buttons), tapping leftmost: {pes}")
                self.adb.tap(pes[0], pes[1])
                time.sleep(2.25)
                self._cof_tap_home()
                return

            sell = self.vision.find_template(f, "btn_sell", threshold=0.70)
            if sell:
                melt = self.vision.find_template(f, "btn_melt", threshold=0.70)
                gold_ref = self._gold_last if self._gold_last is not None else self._gold_start
                if melt and (gold_ref is None or gold_ref > self._melt_threshold):
                    print(f"[COF] Melt (gold={f'{gold_ref:,}' if gold_ref is not None else '?'}): {melt}")
                    self.adb.tap(melt[0], melt[1])
                else:
                    print(f"[COF] Sell: {sell}")
                    self.adb.tap(sell[0], sell[1])
                time.sleep(2.25)
                self._cof_tap_home()
                return

            chest_count = len(self._find_chests(f))
            if chest_count == 0:
                self._cof_tap_home()
                time.sleep(1)
                return


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Royal Revolt 2 Farm Bot")
    parser.add_argument("--port", type=int, default=5555,
                        help="ADB port of the LDPlayer instance (default: 5555, the first instance). "
                             "Run 'adb devices' while the instance is running to confirm it.")
    parser.add_argument("--ld-index", type=int, default=0,
                        help="LDPlayer instance index, used for auto-restart via ldconsole (default: 0, the first instance)")
    parser.add_argument("--ldconsole", type=str, default=None,
                        help="Path to ldconsole.exe. Auto-detected under common LDPlayer install "
                             "locations if omitted; only needed if auto-detect fails.")
    parser.add_argument("--trophy-filter", type=int, default=600,
                        help="Trophy filter target (400-4000, default: 600)")
    parser.add_argument("--gold", type=int, default=1_000_000,
                        help="Melt threshold — melt if gold above this, sell otherwise (100000-32000000, default: 1000000)")
    parser.add_argument("--drop-trophies", choices=["drop_yes", "drop_no"], default="drop_no",
                        help="Drop trophies mode: drop_yes ends match after 3s, drop_no after 180s (default: drop_no)")
    args = parser.parse_args()

    # Validate ranges
    # Any valid TCP port — different emulators use very different ADB port
    # conventions (LDPlayer: 5555+2n per instance, MEmu: 21503+10n, etc.)
    if not (1 <= args.port <= 65535):
        parser.error(f"--port must be between 1 and 65535, got {args.port}")
    if not (400 <= args.trophy_filter <= 4000):
        parser.error(f"--trophy-filter must be between 400 and 4000, got {args.trophy_filter}")
    if not (100_000 <= args.gold <= 32_000_000):
        parser.error(f"--gold must be between 100000 and 32000000, got {args.gold}")

    trophy_filter  = args.trophy_filter
    melt_threshold = args.gold
    drop_trophies  = args.drop_trophies == "drop_yes"
    port           = args.port

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    template_dir = os.path.join(base, "En_Templates")

    ldconsole = args.ldconsole or find_ldconsole()
    print(f"Port: {port} | LD index: {args.ld_index} | ldconsole: {ldconsole or 'not found — pass --ldconsole'} | "
          f"Trophy filter: {trophy_filter} | Melt threshold: {melt_threshold:,} | Drop trophies: {'YES' if drop_trophies else 'NO'}")
    bot = RR2Bot(port=port, template_dir=template_dir, trophy_filter=trophy_filter,
                 melt_threshold=melt_threshold, drop_trophies=drop_trophies,
                 ld_index=args.ld_index, ldconsole=ldconsole)
    bot.loop()

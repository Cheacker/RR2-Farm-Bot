import time
import os
import sys
import csv
import glob
import argparse
import subprocess
import cv2
import numpy as np
from controller import ADBController
from vision import VisionInterpreter, FULL
from player_db import PlayerDB
from treasure import (TreasureBases, PathFollower, FrameRecorder, read_hidden_treasures, write_treasure_row,
                      FAV_ROW_TROPHIES, FAV_ROW_NAME)

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAIL_DEBUG_DIR = os.path.join(ROOT_DIR, "fail_debug")
os.makedirs(FAIL_DEBUG_DIR, exist_ok=True)

# Persistent run records (gitignored). Unlike session_log.txt these are appended
# across runs, so a 24h+ run can be reviewed afterwards with tools/report.py.
LOG_DIR        = os.path.join(ROOT_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)
MATCHES_CSV    = os.path.join(LOG_DIR, "matches.csv")   # one row per finished match
EVENTS_CSV     = os.path.join(LOG_DIR, "events.csv")    # restarts, popups, recoveries
HEARTBEAT_PATH = os.path.join(LOG_DIR, "heartbeat.txt") # watched by supervisor.py
TREASURES_CSV  = os.path.join(LOG_DIR, "treasures.csv") # treasure mode: one row per match
TREASURE_SHOTS = os.path.join(LOG_DIR, "treasure_results")  # result screens (catalog material)
TREASURE_FRAMES = os.path.join(LOG_DIR, "treasure_frames")  # every in-match frame (training data)
HEARTBEAT_EVERY = 2  # seconds between heartbeat writes
KEEP_RUN_LOGS   = 30 # bot_*.log files kept in logs/

CAPTURED_INACTIVES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "captured_inactives")
os.makedirs(CAPTURED_INACTIVES_DIR, exist_ok=True)

# Per-hour debug log: cleared at the start of every bot run (not accumulated
# across runs) -- meant for "what happened during this specific session",
# not a long-term history file.
SESSION_LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "session_log.txt")

RR2_PACKAGE            = "com.flaregames.rrtournament"
RESTART_GAME_WAIT      = 18  # seconds to wait after restart_game()/a HOME popup dismissal
                              # for the game to settle back onto a stable screen
CONNECT_POLL_TIMEOUT   = 60  # seconds to poll 'adb connect' at startup before giving up (no restart)

# No-frame recovery (see _handle_no_screen), measured from the last good screencap.
# This replaced the old "1 h runtime + 20 matches" gate on mid-run LDPlayer restarts:
# that gate protected against restarting over a transient hiccup, but it also left
# the bot blind forever when the emulator wedged early in a run. A full 5 minutes
# without a single frame is not a transient hiccup.
NO_FRAME_RECONNECT  = 90       # s → reconnect ADB + restart the game
NO_FRAME_LD_RESTART = 5 * 60   # s → restart LDPlayer

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
SECOND_TROOP_SLOT_COORDS      = (240, 800)
ATTACK_TEXT_REGION  = (1180, 744, 1305, 777)  # OCR region for yellow "ATTACK!" text
ATTACK_BTN_COORDS  = (1242, 760)              # center of the yellow attack button
ATTACK_BTN_COLOR_REGION = (1100, 740, 1380, 830)  # inside the ATTACK! button: yellow vs gray
MINUS_LEFT_COORDS  = (810, 226)
MINUS_RIGHT_COORDS = (1084, 226)
PLUS_LEFT_COORDS   = (985, 229)
PLUS_RIGHT_COORDS  = (1258, 226)
IN_GAME_TAP_COORDS = (10, 10) # (1130, 274) for real tap on map
GEAR_SET_1_COORDS  = (226, 818)
GEAR_SET_2_COORDS  = (298, 820)
GEAR_SET_3_COORDS  = (366, 818)
VIDEO_CLOSE_COORDS = (1522, 94)
POPUP_CLOSE_COORDS = (1520, 88)    # red X shared by every full-screen popup/offer and the Shop
SHOP_COORDS        = (1540, 508)
TRY_AGAIN_COORDS   = (810, 505)    # "Try again!" on the Break Time popup
UPDATE_DOWNLOAD_COORDS = (810, 522)  # "Download now!" on the forced-update popup
PLAY_STORE_PACKAGE = "com.android.vending"

# Break Time: the game stops you after ~5h of play for ~15 min (user's observation).
# The exact length varies (14-16 min), so wait most of it, then press "Try again!"
# every BREAK_POLL s until the popup is gone (pressing early is harmless).
BREAK_MIN_WAIT    = 12 * 60
BREAK_POLL        = 30
BREAK_MAX_WAIT    = 40 * 60  # give up polling; the next loop sees the popup again
UPDATE_TIMEOUT    = 10 * 60  # max wait for the Play Store to install a forced update
INTERRUPT_CHECK_EVERY_IN_GAME = 3  # seconds; full-screen popups are rare mid-match
# "Not enough Food?" (attacking costs food; 36 per match with the farming gear set 3).
# Farms empty and the Shop purchase didn't help either → wait for the farms to refill.
FOOD_WAIT         = 20 * 60
FOOD_SHOP_RETRY   = 30 * 60  # min time between two Shop purchase attempts

# Templates bot.py references that may legitimately be missing (no screenshot of
# that screen yet). Reported once at startup instead of failing silently.
OPTIONAL_TEMPLATES = ["btn_archer_boosted", "btn_attack_start_gray"]
GREEN_BACK_COORDS  = (1430, 85)    # back out of attack prep / loading screen
# Treasure mode navigation: HOME → Community → Leaderboard card → right arrow → Favorites
COMMUNITY_BTN_COORDS = (1535, 505)  # HOME, 2nd button from the top right (two smiling faces)
CARD_OPEN_Y          = 790          # OPEN button under each Community card
PAGE_NEXT_COORDS     = (1545, 520)  # right arrow: Leaderboard → Favorite Players
SCROLL_CONFIRM_COORDS = (896, 785) # ranked-list confirm after scroll — on the
                                   # attack-prep screen this is "New Opponent",
                                   # so never tap it without confirming the screen

# ── Drop-trophies mode (only used when --drop-trophies drop_yes) ─────────────
# All four are unset until captured — get_coords.py for the two tap coordinates
# and the home-return coordinate, and get_coords.py again (or just eyeball the
# region) for the OCR box around your own trophy count. Until all four are set,
# drop mode falls back to the old behavior: a plain timeout + full game restart.
OWN_TROPHY_REGION    = (1413, 112, 1504, 150)  # (x1, y1, x2, y2) OCR region showing your own trophy count
DROP_BUTTON_1_COORDS = (72, 266)  # tapped first, 2s after the match starts
DROP_BUTTON_2_COORDS = (605, 484)  # tapped right after button 1
DROP_HOME_COORDS     = (1114, 788)  # final tap that returns to HOME after dropping
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
    COMMUNITY          = "COMMUNITY"    # treasure mode only
    FAVORITES          = "FAVORITES"    # treasure mode only


class RR2Bot:
    def __init__(self, port=21503, template_dir=None, trophy_filter=600, melt_threshold=1_000_000,
                 drop_trophies=False, ld_index=0, ldconsole=None, capture_inactives=False,
                 mode="farm", treasure_max_trophies=2000):
        # Skip ADBController's own connect-on-init: connecting before the startup
        # restart even runs is wasted work whose result gets thrown away the
        # instant the restart happens -- only try after the restart completes.
        self._last_heartbeat = 0
        self._last_good_frame = time.time()  # startup counts as fresh
        self._no_screen_stage = 0            # 0 ok, 1 reconnected, 2 LDPlayer restarted
        self.state = State.HOME
        self._heartbeat(force=True)  # supervisor.py counts startup (LDPlayer restart) from here
        self.adb = ADBController(port=port, connect_on_init=False)
        self._ld_index = ld_index
        self._ldconsole = ldconsole or find_ldconsole()
        self._startup_restart_ldplayer()
        if not self.adb.device:
            print("ADB connection failed. Start LDPlayer manually and re-run.")
            self._event("startup_failed", "no ADB device after LDPlayer restart")
            sys.exit(1)
        self.adb.ensure_resolution(1600, 900)
        self.vision = VisionInterpreter(template_dir=template_dir)
        missing = [t for t in OPTIONAL_TEMPLATES if t not in self.vision.templates]
        if missing:
            print(f"[WARN] Optional templates not captured yet (their checks are skipped): "
                  f"{', '.join(missing)}")
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
        self._last_in_game_find_state = 0
        self._trophy_filter     = trophy_filter
        self._melt_threshold    = melt_threshold
        self._drop_trophies     = drop_trophies
        self._in_game_timeout   = 3 if drop_trophies else 180
        self._gold_start        = None
        self._pearl_start       = None
        self._gold_last         = None
        self._pearl_last        = None
        self._game_load_miss    = 0
        self._anchor_miss_streak = 0
        self._capture_inactives  = capture_inactives
        self._capture_wait_start = 0
        self._scroll_no_progress = 0
        self._scroll_attempts    = 0
        self._last_interrupt_check = 0
        self._last_food_shop     = 0
        self._match_outcome      = None  # "continue" / "retreat" / ... written to matches.csv
        self._hour_capture_count = 0
        self._hour_capture_secs  = 0.0
        self._hour_events        = 0
        self.db = PlayerDB()
        # Treasure mode (--mode treasure): favorite bases instead of the trophy list
        self._mode               = mode
        self._treasure_max       = treasure_max_trophies
        self.bases               = TreasureBases() if mode == "treasure" else None
        self.frames              = FrameRecorder(TREASURE_FRAMES) if mode == "treasure" else None
        self._follower           = None
        self._target_base        = None   # (base id, trophies) being attacked
        self._nav_miss           = 0
        self._fav_scrolls        = 0
        self._fav_seen           = set()
        self._fav_last_view      = None

        # Per-hour debug log — truncated (not appended) on every bot run, so it
        # always reflects only the current session.
        with open(SESSION_LOG_PATH, "w", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Session started "
                     f"(trophy_filter={trophy_filter}, melt_threshold={melt_threshold}, "
                     f"drop_trophies={drop_trophies})\n")
        self._session_log_hour_index      = 0
        self._session_log_hour_matches    = 0
        self._session_log_hour_loop_sum   = 0.0
        self._session_log_hour_pearl_start = None

    # ── Emulator connection ───────────────────────────────────────────────────
    def _startup_restart_ldplayer(self):
        """Always do one clean LDPlayer restart at bot startup, every run — even
        if ADB already sees a device. The instance itself can be left in a wedged
        state (rendering stalls, stuck input, etc) from the previous run that a
        healthy-looking connection wouldn't reveal, so a cold restart is the
        reliable baseline. Just one quit+launch, then poll 'adb connect' — no
        repeated resets, no adb kill-server/start-server."""
        if not self._ldconsole:
            print("[EMULATOR] ldconsole.exe not found — can't restart LDPlayer automatically, "
                  "trying to connect to whatever's already running instead.")
            self.adb._connect()
            return
        self._restart_ldplayer()
        deadline = time.time() + CONNECT_POLL_TIMEOUT
        while time.time() < deadline:
            self.adb._connect()
            if self.adb.device:
                if not self.adb.wait_boot_completed():
                    print("[EMULATOR] Android didn't report boot completed within 120s — continuing anyway.")
                return
            time.sleep(3)
        print(f"[EMULATOR] Still not connected after {CONNECT_POLL_TIMEOUT}s post-restart. "
              f"Giving up for now — the main loop will keep trying.")

    def _restart_ldplayer(self):
        """Just close and reopen the instance — no adb kill-server/start-server,
        no isrunning check, nothing else. Called once at every startup, and from
        the main loop's gated recovery (long runtime + many matches + a real,
        sustained disconnect)."""
        if not self._ldconsole:
            print("[EMULATOR] ldconsole.exe not found — can't restart LDPlayer automatically.")
            return
        print(f"[EMULATOR] Restarting LDPlayer instance --index {self._ld_index}...")
        subprocess.run([self._ldconsole, "quit", "--index", str(self._ld_index)], capture_output=True)
        time.sleep(3)
        subprocess.Popen([self._ldconsole, "launch", "--index", str(self._ld_index)])
        time.sleep(15)

    # ── Main loop ─────────────────────────────────────────────────────────────
    def loop(self):
        print("Bot started! Press Ctrl+C to stop.")
        self._event("bot_start", f"trophy_filter={self._trophy_filter} drop={self._drop_trophies}")
        self.adb.restart_game(RR2_PACKAGE, wait=RESTART_GAME_WAIT)
        while self.running:
            try:
                self._heartbeat()
                screen = self.adb.current_screen()
                if screen is None:
                    self._handle_no_screen()
                    time.sleep(0.1)
                    continue
                self._last_good_frame = time.time()
                self._no_screen_stage = 0

                # Full-screen interrupts (Break Time, forced update, stray Shop, asset
                # download) can appear on top of any state. Checked before the state
                # handler so no handler blind-taps its own coordinates onto them.
                # Each check is a small-region template match (~5-10 ms); mid-match it
                # only runs every few seconds to keep the in-game tap rhythm.
                now = time.time()
                if (self.state != State.IN_GAME
                        or now - self._last_interrupt_check >= INTERRUPT_CHECK_EVERY_IN_GAME):
                    self._last_interrupt_check = now
                    if self._check_interrupts(screen):
                        continue

                if   self.state == State.HOME:               self.handle_home(screen)
                elif self.state == State.TROPHY_MENU:        self.handle_trophy_menu(screen)
                elif self.state == State.FILTERED_RANKS:     self.handle_filtered_ranks(screen)
                elif self.state == State.ATTACK_PREP:
                    if self._capture_inactives:
                        self.handle_capture_inactives(screen)
                    else:
                        self.handle_attack_prep(screen)
                elif self.state == State.GAME_LOAD:          self.handle_game_load(screen)
                elif self.state == State.GOING_BACK:         self.handle_going_back(screen)
                elif self.state == State.IN_GAME:            self.handle_in_game(screen)
                elif self.state == State.CHAMBER_OF_FORTUNE: self.handle_chamber_of_fortune(screen)
                elif self.state == State.COMMUNITY:          self.handle_community(screen)
                elif self.state == State.FAVORITES:          self.handle_favorites(screen)
                # never leave a finger down outside a match
                if self._follower and self.state != State.IN_GAME:
                    self._follower.stop()
                    self._follower = None

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
                time.sleep(0.5)

    # ── No screen: time-based recovery ladder ────────────────────────────────
    def _handle_no_screen(self):
        """Called when a screencap failed. Escalates by how long it has been since the
        last good frame — not by a failure count, since a single failed capture can
        take anywhere from 0 s (no device) to 4 x 8 s (wedged screencap).

        Field case 2026-11-22: right after the startup LDPlayer restart, ADB stayed
        connected but every screencap timed out. The old ladder only reconnected (ADB
        looked fine) and restarted the game, and gated LDPlayer restarts behind 1 h of
        runtime + 20 matches — so the bot stayed blind indefinitely."""
        blind = time.time() - self._last_good_frame
        if self._no_screen_stage == 0 and blind >= NO_FRAME_RECONNECT:
            self._no_screen_stage = 1
            self._event("no_screen", f"{blind:.0f}s without a frame — reconnecting ADB")
            self.adb.device = None
            self.adb._connect()
            if self.adb.device:
                self.adb.ensure_resolution(1600, 900)
                self._restart_game("reconnected after screen loss")
                self.state = State.HOME
            return
        if self._no_screen_stage == 1 and blind >= NO_FRAME_LD_RESTART:
            self._no_screen_stage = 2
            self._event("ldplayer_restart", f"{blind:.0f}s without a frame")
            self._restart_ldplayer()
            deadline = time.time() + 90
            while time.time() < deadline:
                self._heartbeat()
                self.adb._connect()
                if self.adb.device:
                    break
                time.sleep(3)
            if self.adb.device:
                self.adb.wait_boot_completed()
                self.adb.ensure_resolution(1600, 900)
                self._restart_game("after LDPlayer restart (no frames)")
                self.state = State.HOME
            # Give the restarted emulator a fresh 5-minute window before escalating again;
            # if it never recovers, supervisor.py restarts the whole bot (FRAME_TIMEOUT).
            self._last_good_frame = time.time() - NO_FRAME_RECONNECT
            self._no_screen_stage = 1
            return
        if self.adb.device is None:
            # Disconnected: keep polling 'adb connect' (cheap, never resets the server).
            self.adb._connect()
            time.sleep(3)

    # ── Run bookkeeping: heartbeat, events, restarts ─────────────────────────
    def _heartbeat(self, force=False):
        """Touch logs/heartbeat.txt so supervisor.py can tell a live bot from a hung
        one. Written atomically (tmp + replace) so the supervisor never reads half a line."""
        now = time.time()
        if not force and now - self._last_heartbeat < HEARTBEAT_EVERY:
            return
        self._last_heartbeat = now
        try:
            tmp = HEARTBEAT_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                # "<now> <state> <last good frame>" — supervisor.py restarts the bot if
                # either the heartbeat itself or the last good frame gets too old.
                f.write(f"{now:.0f} {self.state} {self._last_good_frame:.0f}\n")
            os.replace(tmp, HEARTBEAT_PATH)
        except PermissionError:
            # Windows refuses the replace while supervisor.py has the file open for its
            # (millisecond) read. Harmless: the next write, 2 s later, goes through.
            self._last_heartbeat = 0
        except OSError as e:
            print(f"[HEARTBEAT] write failed: {e}")

    def _sleep(self, seconds):
        """Long wait that keeps the heartbeat alive (Break Time, update installs)."""
        end = time.time() + seconds
        while self.running and time.time() < end:
            # A deliberate wait counts as progress: keeps supervisor.py's
            # "no frame for 10 min" rule from killing the bot during Break Time.
            self._last_good_frame = time.time()
            self._heartbeat(force=True)
            time.sleep(min(10, max(0, end - time.time())))

    def _event(self, kind, detail=""):
        """Append one row to logs/events.csv — every recovery, popup and restart, so a
        long unattended run can be audited afterwards (tools/report.py)."""
        print(f"[EVENT] {kind}: {detail}")
        self._hour_events = getattr(self, "_hour_events", 0) + 1
        try:
            new = not os.path.exists(EVENTS_CSV)
            with open(EVENTS_CSV, "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(["time", "kind", "state", "match", "detail"])
                w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), kind, self.state,
                            getattr(self, "_match_count", 0), detail])
        except OSError as e:
            print(f"[EVENT] write failed: {e}")

    def _restart_game(self, reason=None):
        """restart_game + an events.csv row saying why (defaults to the calling handler)."""
        if reason is None:
            reason = sys._getframe(1).f_code.co_name
        shot = self._save_debug_screen(reason)
        self._event("game_restart", f"{reason}{f' [{os.path.basename(shot)}]' if shot else ''}")
        self.adb.restart_game(RR2_PACKAGE, wait=RESTART_GAME_WAIT)

    def _save_debug_screen(self, reason):
        """Save the screen that led to a recovery into fail_debug/, so every restart
        can be diagnosed afterwards (and turned into a template / test case)."""
        frame = self.adb.current_screen(retries=1) if self.adb.device else None
        if frame is None:
            return None
        slug = "".join(c if c.isalnum() else "_" for c in reason)[:50].strip("_")
        path = os.path.join(FAIL_DEBUG_DIR, f"{time.strftime('%Y%m%d_%H%M%S')}_{slug}.png")
        try:
            cv2.imwrite(path, frame)
            return path
        except Exception as e:
            print(f"[DEBUG] screenshot save failed: {e}")
            return None

    # ── Full-screen interrupts (any state) ───────────────────────────────────
    def _check_interrupts(self, screen):
        """Handle screens that can cover any state. Returns True if one was handled
        (the caller then grabs a fresh frame instead of running the state handler)."""
        if self.vision.find_template(screen, "popup_break_time", threshold=0.85):
            self._handle_break_time()
            return True
        if self.vision.find_template(screen, "popup_update", threshold=0.85):
            self._handle_forced_update()
            return True
        if self.vision.find_template(screen, "text_downloading", threshold=0.85):
            # Post-update asset download on the loading screen: progress, not a hang.
            # Reset the miss counters so HOME's 21-miss rule doesn't restart mid-download.
            print("[INTERRUPT] Game is downloading assets — waiting...")
            self._trophy_miss_count = 0
            self._anchor_miss_streak = 0
            self._sleep(5)
            return True
        king = self.vision.find_template(screen, "btn_king_claim", threshold=0.85)
        if king:
            # "I am the KING!" appears after ~2 min idle (user's observation) — e.g. after
            # a long wait. Claiming is harmless: tap at once.
            self._event("king_claim", f"claimed in {self.state}")
            self.adb.tap(*king)
            self._sleep(RESTART_GAME_WAIT)
            self.state = State.HOME
            self._trophy_miss_count = 0
            self._anchor_miss_streak = 0
            return True
        if self.vision.find_template(screen, "title_not_enough_food", threshold=0.85):
            self._handle_no_food(screen)
            return True
        if self.vision.find_template(screen, "title_shop", threshold=0.85):
            # Stray Shop (e.g. a mis-tap on HOME). Several of its buttons spend gems, so
            # close it right away instead of letting a handler tap its own coordinates here.
            self._event("shop_closed", f"Shop open while in {self.state}")
            self.adb.tap(*POPUP_CLOSE_COORDS)
            time.sleep(1)
            if self.state not in (State.HOME, State.IN_GAME):
                self.state = State.HOME
            return True
        return False

    def _handle_break_time(self):
        """'You've been playing for a while': wait most of the break, then press
        'Try again!' every BREAK_POLL s until the popup disappears."""
        start = time.time()
        self._event("break_time", f"waiting {BREAK_MIN_WAIT // 60} min, then retrying every {BREAK_POLL}s")
        self._sleep(BREAK_MIN_WAIT)
        while time.time() - start < BREAK_MAX_WAIT:
            self.adb.tap(*TRY_AGAIN_COORDS)
            self._sleep(3)
            f = self.adb.current_screen()
            if f is not None and not self.vision.find_template(f, "popup_break_time", threshold=0.85):
                print(f"[BREAK] Over after {(time.time() - start) / 60:.1f} min")
                break
            self._sleep(BREAK_POLL)
        self._sleep(RESTART_GAME_WAIT)
        self._trophy_miss_count = 0
        self._anchor_miss_streak = 0
        self.state = State.HOME

    def _handle_no_food(self, screen):
        """'Not enough Food?' after pressing ATTACK. In order of preference:
        1. 'Collect Food from Farms!' → COLLECT (free),
        2. farms empty (WATCH VIDEO / gems offer) → buy food in the Shop with tokens,
        3. that failed recently too → close and wait for the farms to refill.
        Never taps the gem/video offer itself."""
        collect = self.vision.find_template(screen, "btn_big_collect", threshold=0.85)
        if collect:
            self._event("food_collected", "Not enough Food → COLLECT from farms")
            self.adb.tap(*collect)
            self._sleep(3)
        elif (self.vision.find_template(screen, "btn_video", threshold=0.90)
                and time.time() - self._last_food_shop > FOOD_SHOP_RETRY):
            self._last_food_shop = time.time()
            self._event("food_shop", "farms empty → buying food in the Shop")
            self._buy_food_in_shop()
        else:
            self._event("food_empty", f"no food anywhere → waiting {FOOD_WAIT // 60} min")
            self.adb.tap(*POPUP_CLOSE_COORDS)
            self._sleep(FOOD_WAIT)
        # Where we land afterwards isn't fixed (attack prep, HOME...): resync.
        f = self.adb.current_screen()
        self.state = (self._find_state(f) if f is not None else None) or State.HOME
        self._trophy_miss_count = 0
        self._anchor_miss_streak = 0
        self._game_load_miss = 0
        if self.state == State.ATTACK_PREP:
            self._attack_prep_start = time.time()

    def _buy_food_in_shop(self):
        """From the food popup's gem/video variant: close it, open the Shop, tap the
        food offer (paid with tokens), close the Shop. Kept from the original bot."""
        self.adb.tap(*VIDEO_CLOSE_COORDS)
        time.sleep(1)
        self.adb.tap(*SHOP_COORDS)
        self._sleep(7)
        f = self.adb.current_screen()
        food_btn = self.vision.find_template(f, "btn_food", threshold=0.90) if f is not None else None
        if food_btn:
            print("[FOOD] Food offer found in the Shop, buying...")
            self.adb.tap(*food_btn)
            time.sleep(1)
        else:
            self._event("food_shop_failed", "food offer not found in the Shop")
            self._save_debug_screen("food_offer_not_found")
        f = self.adb.current_screen()
        close_btn = self.vision.find_template(f, "btn_close", threshold=0.70) if f is not None else None
        self.adb.tap(*close_btn) if close_btn else self.adb.tap(*VIDEO_CLOSE_COORDS)
        time.sleep(1)

    def _handle_forced_update(self):
        """'A brand new update' popup blocks the game entirely until updated."""
        old = self.adb.package_version(RR2_PACKAGE)
        self._event("forced_update", f"installed version {old}")
        self.adb.tap(*UPDATE_DOWNLOAD_COORDS)  # "Download now!" → opens the Play Store page
        self._play_store_update(old)

    def _play_store_update(self, old_version):
        """On the game's Play Store page: tap Update, wait for the new version, relaunch.
        Any failure ends in a plain game restart, never in a stuck Play Store."""
        deadline = time.time() + 20
        while time.time() < deadline and self.adb.focused_package() != PLAY_STORE_PACKAGE:
            self._sleep(2)
        tapped = False
        if self.adb.focused_package() == PLAY_STORE_PACKAGE:
            for _ in range(10):
                f = self.adb.current_screen()
                btn = self.vision.find_template(f, "btn_play_update", threshold=0.85, region=FULL)
                if btn:
                    self.adb.tap(*btn)
                    tapped = True
                    break
                self._sleep(2)
        if tapped:
            deadline = time.time() + UPDATE_TIMEOUT
            while self.running and time.time() < deadline:
                self._sleep(10)
                version = self.adb.package_version(RR2_PACKAGE)
                if version and version != old_version:
                    self._event("update_installed", f"{old_version} -> {version}")
                    break
            else:
                self._event("update_failed", f"no new version after {UPDATE_TIMEOUT // 60} min")
        else:
            self._event("update_failed", "Play Store 'Update' button not found")
        self.adb.force_stop(PLAY_STORE_PACKAGE)
        self._restart_game("after forced update")
        self.state = State.HOME

    def _check_foreground(self):
        """The game may not be on screen at all (crash to the Android launcher, Play
        Store or a browser opened by an offer). Costs one ADB call, so only used from
        HOME's periodic miss checks. Returns True if it acted."""
        pkg = self.adb.focused_package()
        if pkg is None or pkg == RR2_PACKAGE:
            return False
        if pkg == PLAY_STORE_PACKAGE:
            self._event("play_store_in_front", "trying the update flow")
            self._play_store_update(self.adb.package_version(RR2_PACKAGE))
        else:
            self._restart_game(f"game not in foreground ({pkg})")
            self.state = State.HOME
        return True

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
            if self._mode == "treasure":
                print("[HOME] Forge icon found, opening Community (treasure mode)...")
                self.adb.tap(*COMMUNITY_BTN_COORDS)
                self._nav_miss = 0
                self.state = State.COMMUNITY
                time.sleep(2)
                return
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
                self._restart_game("HOME: forge icon not found 21x")
                self.state = State.HOME
                return

            # Full-screen blocking popups that icon_forge can never see past. Checked
            # only every 10th miss, same cadence as the other rare popup checks below —
            # these are uncommon enough that scanning for them on every single miss
            # would just be wasted vision work.
            if self._trophy_miss_count % 10 == 0:
                if self._check_foreground():
                    self._trophy_miss_count = 0
                    self._anchor_miss_streak = 0
                    return
                # 'Connected another device' popup uses the same button/template as
                # GAME_LOAD's btn_take_me_back — checked here too since this popup can
                # also interrupt at HOME.
                take_me_back = self.vision.find_template(screen, "btn_take_me_back", threshold=0.85)
                if take_me_back:
                    print("[HOME] 'Connected another device' popup detected — dismissing...")
                    self.adb.tap(take_me_back[0], take_me_back[1])
                    time.sleep(RESTART_GAME_WAIT)
                    self._trophy_miss_count = 0
                    self._anchor_miss_streak = 0
                    return

            # Search for btn_close on every miss, not just every 6th — if the forge
            # icon is missing because a popup/shop leftover is covering the screen
            # (e.g. after the GAME_LOAD food-purchase flow didn't fully close out),
            # waiting up to 6 misses to even try closing it just prolongs being stuck.

            collect = self.vision.find_template(screen, "btn_collect", threshold=0.80)
            if collect:
                print(f"[HOME] {self._trophy_miss_count}th miss → pressing btn_collect...")
                self.adb.tap(collect[0], collect[1])
                time.sleep(3.5)
                self.adb.tap(1524, 86)

            close = self.vision.find_template(screen, "btn_close", threshold=0.57)
            if close:
                print("[HOME] Pressing btn_close...")
                self.adb.tap(close[0], close[1])
                time.sleep(0.5)

                close = self.vision.find_template(screen, "btn_close", threshold=0.57)
                if close:
                    print("[HOME] Pressing btn_close...")
                    self.adb.tap(close[0], close[1])
                    time.sleep(0.5)

            if self._trophy_miss_count % 2 == 0:
                self.adb.tap(10, 10)
                time.sleep(0.1)

            # "JOIN LEAGUE!" (league result at startup) has no X, only this button.
            # Region-limited now (~10 ms), so it's checked on every miss instead of
            # every 10th — it's part of the popup chain right after every game start.
            league = self.vision.find_template(screen, "btn_collect_league", threshold=0.80)
            if league:
                print(f"[HOME] {self._trophy_miss_count}th miss → pressing btn_collect_league...")
                self.adb.tap(league[0], league[1])
                time.sleep(1)

            if self._trophy_miss_count % 10 == 0:
                btn_big_collect = self.vision.find_template(screen, "btn_big_collect", threshold=0.80)
                if btn_big_collect:
                    print("[HOME] btn_big_collect found, tapping...")
                    self.adb.tap(btn_big_collect[0], btn_big_collect[1])
                    time.sleep(1)

    # ── Helper: identify the actual screen from its anchor template(s) ───────
    def _find_state(self, screen):
        """Probe every state's anchor template(s), independent of self.state. Used
        only as a resync fallback after a handler's expected anchor keeps missing —
        cheap enough to run occasionally, too much vision work to run every loop."""
        if screen is None:
            return None

        # Break Time is handled by _check_interrupts on every loop, before any handler.
        # "I am the KING!" (after a game start, when the account was used elsewhere).
        king = self.vision.find_template(screen, "btn_king_claim", threshold=0.85, region=FULL)
        if king:
            self._event("king_claim", "claimed")
            self.adb.tap(king[0], king[1])
            time.sleep(RESTART_GAME_WAIT)
            return State.HOME

        # This is the rare fallback after repeated misses, so it deliberately searches
        # the whole screen (region=FULL) — it must still work if a button has moved
        # out of its default region after a game update.
        if (self.vision.find_template(screen, "icon_forge", threshold=0.90, region=FULL)
                or self.vision.find_template(screen, "btn_take_me_back", threshold=0.85, region=FULL)):
            return State.HOME
        if self.vision.find_template(screen, "title_favorites", threshold=0.85, region=FULL):
            return State.FAVORITES
        if self.vision.find_template(screen, "title_community", threshold=0.85, region=FULL):
            return State.COMMUNITY
        if self.vision.find_template(screen, "btn_start_search", threshold=0.80, region=FULL):
            return State.TROPHY_MENU
        if self.vision.find_multiple_templates(screen, "area_top_opponent", threshold=0.90, region=FULL):
            return State.FILTERED_RANKS
        if self._on_attack_prep_screen(screen):
            return State.ATTACK_PREP
        if (self.vision.find_template(screen, "btn_archer", threshold=0.85, region=FULL)
                or self.vision.find_template(screen, "btn_archer_boosted", threshold=0.85, region=FULL)):
            return State.GAME_LOAD
        if (self.vision.find_template(screen, "btn_give_up", threshold=0.70, region=FULL)
                or self.vision.find_template(screen, "btn_sell", threshold=0.70, region=FULL)
                or self._find_chests(screen)):
            return State.CHAMBER_OF_FORTUNE
        return None

    # ── Helper: is the attack-prep screen showing? ────────────────────────────
    def _attack_button_color(self, screen):
        """'yellow', 'gray' or None from the mean HSV saturation inside the ATTACK!
        button. Measured on 12.9.0: yellow ~138, gray (inactive / unattackable
        opponent) 0. Text and shape can't tell them apart: OCR reads "ATTACK!" on
        both and the template scores the gray button 0.87 — which once made the bot
        press a dead gray button in an endless ATTACK_PREP ↔ GAME_LOAD loop."""
        x1, y1, x2, y2 = ATTACK_BTN_COLOR_REGION
        patch = screen[y1:y2, x1:x2]
        if patch.size == 0:
            return None
        sat = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)[:, :, 1].mean()
        if sat >= 80:
            return "yellow"
        if sat <= 20:
            return "gray"
        return None

    def _attack_button_present(self, screen):
        """The ATTACK! button (either colour) is on screen: region template first
        (~5 ms), OCR of its text (~300 ms) as the fallback."""
        if self.vision.find_template(screen, "btn_attack_start", threshold=0.80):
            return True
        return "attack" in self.vision.read_region_text(screen, *ATTACK_TEXT_REGION)

    def _has_yellow_attack(self, screen):
        """True if the ATTACK! button is visible AND yellow (opponent attackable)."""
        if screen is None:
            return False
        return self._attack_button_color(screen) == "yellow" and self._attack_button_present(screen)

    def _has_gray_attack(self, screen):
        """True if the ATTACK! button is visible but gray: "This player is inactive."
        or the trophy gap makes the opponent unattackable. Also accepts the older small
        btn_attack_start_gray template."""
        if screen is None:
            return False
        if self.vision.find_template(screen, "btn_attack_start_gray", threshold=0.90):
            return True
        return self._attack_button_color(screen) == "gray" and self._attack_button_present(screen)

    def _on_attack_prep_screen(self, screen):
        """True if either the yellow or gray attack button is visible."""
        if screen is None:
            return False
        return bool(self._has_yellow_attack(screen) or self._has_gray_attack(screen))

    # ── Helper: leave attack prep ─────────────────────────────────────────────
    def _leave_attack_prep(self, reason, permanent=False):
        """Navigate back to the ranked list. Never just flip state — if we stay on
        the attack-prep screen, FILTERED_RANKS blind-taps 'New Opponent'.

        permanent=True is for a confirmed-gray attack button: the trophy gap makes this
        opponent unattackable regardless of when we last saw them, so it's recorded
        permanently instead of the 15-minute active-player mark (which is only a "they
        might be online, try again later" guess, not "this matchup can never work")."""
        if self._mode == "treasure":
            print(f"[ATTACK_PREP] {reason} — back to Favorites")
            self.adb.tap(*GREEN_BACK_COORDS)
            self._target_base = None
            self._fav_reset()
            self.state = State.FAVORITES
            time.sleep(1)
            return
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
    def _scroll_list(self, times=1, before_screen=None):
        # Hard cap on repeated scrolling — a full-screen popup with no matching
        # template (e.g. a "take a break" reminder or similar) makes every anchor
        # score floor out at noise, which looks identical to "no opponents on this
        # part of the list" and used to make FILTERED_RANKS scroll into it forever.
        # After 7 scroll attempts, stop swiping blindly: try a generic close button,
        # then hand off to _find_state to actually figure out what's on screen.
        self._scroll_attempts += 1
        if self._scroll_attempts > 7:
            print(f"[SCROLL] {self._scroll_attempts} scroll attempts without finding opponents — "
                  f"checking for a blocking popup instead of scrolling again...")
            f = self.adb.current_screen()
            close = self.vision.find_template(f, "btn_close", threshold=0.57) if f is not None else None
            if close:
                print("[SCROLL] btn_close found — tapping to dismiss.")
                self.adb.tap(close[0], close[1])
                time.sleep(0.5)
                f = self.adb.current_screen()
            detected = self._find_state(f)
            self._scroll_attempts = 0
            if detected and detected != self.state:
                print(f"[SCROLL] Resynced to {detected}.")
                self.state = detected
            elif detected == self.state:
                print("[SCROLL] Screen still matches current state — continuing.")
            else:
                print("[SCROLL] Still unresolved — continuing to search from here.")
            return
        for _ in range(times):
            self.adb.swipe(650, 600, 650, 300, 300)
            time.sleep(2)
            self.adb.tap(800, 806)
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
        after = None
        while time.time() < deadline:
            time.sleep(0.3)
            after = self.adb.current_screen()
            if after is not None and self.vision.find_multiple_templates(after, "area_top_opponent", threshold=0.90):
                break
        print(f"List scrolled {times} time(s).")
        self._skip_top = 0

        # If the swipe produced a screen that's byte-for-byte identical to the one
        # before it, the list has hit its end and scrolling can't reveal anything
        # new — that's the "stuck scrolling forever, same score every time" failure
        # mode. Escape to TROPHY_MENU for a fresh search instead of scrolling into
        # the same dead end indefinitely.
        if (before_screen is not None and after is not None
                and before_screen.shape == after.shape
                and np.array_equal(before_screen, after)):
            self._scroll_no_progress += 1
            print(f"[SCROLL] Screen unchanged after swipe ({self._scroll_no_progress}x) — list may be exhausted.")
            if self._scroll_no_progress >= 2:
                print("[SCROLL] List appears exhausted — returning to TROPHY_MENU for a fresh search.")
                self.adb.tap(*GREEN_BACK_COORDS)
                time.sleep(0.5)
                self.state = State.TROPHY_MENU
                self._scroll_no_progress = 0
        else:
            self._scroll_no_progress = 0

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
            self._restart_game("TROPHY_MENU: search button not found 300x")
            self.state = State.HOME
            return
        time.sleep(0.15)

    # ── FILTERED_RANKS ────────────────────────────────────────────────────────
    def handle_filtered_ranks(self, screen):
        # 0.90, not the original 0.92 — confirmed live that real, changing swords in
        # the list were plateauing at 0.903-0.904 (vs. 0.921-0.927 for ones that did
        # match), same rendering-gap class of issue as btn_start_search. The noise
        # floor on unrelated screens is ~0.28-0.31, so there's still large margin here.
        opponents = self.vision.find_multiple_templates(screen, "area_top_opponent", threshold=0.90)
        if not opponents:
            self._no_opponent_count += 1
            self._anchor_miss_streak += 1
            if self._no_opponent_count >= 27:
                print(f"[FILTERED_RANKS] No sword found after {self._no_opponent_count} attempts — scanning anchors...")
                detected = None
                for attempt in range(10):
                    f = self.adb.current_screen() if attempt > 0 else screen
                    detected = self._find_state(f)
                    if detected:
                        break
                    time.sleep(0.5)
                self._no_opponent_count = 0
                if detected and detected != self.state:
                    print(f"[FILTERED_RANKS] Resynced to {detected}.")
                    self.state = detected
                elif detected == self.state:
                    print("[FILTERED_RANKS] Screen still matches FILTERED_RANKS — continuing to search.")
                else:
                    print("[FILTERED_RANKS] No anchor matched after retrying — continuing to search.")
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
                self._scroll_list(before_screen=screen)
            return

        self._no_opponent_count = 0
        self._anchor_miss_streak = 0
        self._scroll_attempts = 0
        opponents.sort(key=lambda pos: pos[1])
        for i, sword_pos in enumerate(opponents):
            is_last = (i == len(opponents) - 1)
            name = self.vision.read_player_name(screen, sword_pos[0], sword_pos[1])
            if not name:
                if is_last:
                    print("[FILTERED_RANKS] Last opponent name unreadable (partially visible) → scrolling")
                    self._scroll_list(before_screen=screen)
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
                    self._scroll_list(self._scroll_count, before_screen=screen)
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
        if self._has_gray_attack(screen):
            self._anchor_miss_streak = 0
            self._event("opponent_unattackable", f"gray ATTACK button ({self._current_target})")
            self._leave_attack_prep("Attack button is gray (inactive/unattackable)", permanent=True)
            return

        if self._has_yellow_attack(screen):
            self._anchor_miss_streak = 0
            print("[ATTACK_PREP] Attack button found (OCR), pressing → GAME_LOAD...")
            # gear set 3 = farming set, set 2 = treasure hunter set (user's setup)
            self.adb.tap(*(GEAR_SET_2_COORDS if self._mode == "treasure" else GEAR_SET_3_COORDS))
            time.sleep(0.1)
            self.adb.tap(*ATTACK_BTN_COORDS)
            self.state = State.GAME_LOAD

        elif time.time() - self._attack_prep_start > 12:
            self._leave_attack_prep("Button not found within 12s")
        else:
            self._anchor_miss_streak += 1
            print("[ATTACK_PREP] Waiting for attack button...")

    # ── ATTACK_PREP (capture-inactives mode) ──────────────────────────────────
    def handle_capture_inactives(self, screen):
        """Dedicated collection mode (--capture-inactives): stays on the attack-prep
        screen forever, using the static 'New Opponent' button (SCROLL_CONFIRM_COORDS)
        to reroll. An attackable (yellow) or already-recognized gray opponent gets
        rerolled immediately; an opponent where neither renders within 10s is the
        harder-to-find case — its screenshot is saved to CAPTURED_INACTIVES_DIR for
        you to crop offline later with crop_from_file.py, which doesn't touch the
        live device and so can't disrupt this loop while it keeps running."""
        yellow = self._has_yellow_attack(screen)
        gray   = self.vision.find_template(screen, "btn_attack_start_gray", threshold=0.85)
        if yellow:
            print("[CAPTURE_INACTIVES] Attackable — rerolling for another opponent...")
            self.adb.tap(*SCROLL_CONFIRM_COORDS)
            self._capture_wait_start = time.time()
            time.sleep(0.5)
            return
        if gray:
            print("[CAPTURE_INACTIVES] Gray button already recognized — rerolling for more...")
            self.adb.tap(*SCROLL_CONFIRM_COORDS)
            self._capture_wait_start = time.time()
            time.sleep(0.5)
            return
        if self._capture_wait_start == 0:
            self._capture_wait_start = time.time()
            return
        if time.time() - self._capture_wait_start >= 15:
            ts   = time.strftime('%Y%m%d_%H%M%S')
            path = os.path.join(CAPTURED_INACTIVES_DIR, f'{ts}_unrecognized_attack_prep.png')
            cv2.imwrite(path, screen)
            print(f"[CAPTURE_INACTIVES] Neither button recognized for 10s — saved {path}")
            self.adb.tap(*SCROLL_CONFIRM_COORDS)
            self._capture_wait_start = time.time()
            time.sleep(0.5)

    # ── GAME_LOAD ─────────────────────────────────────────────────────────────
    def handle_game_load(self, screen):
        time.sleep(0.1)
        self.adb.tap(*SECOND_TROOP_SLOT_COORDS)
        # "Not enough Food?" is handled by _check_interrupts → _handle_no_food.
        go_back = self.vision.find_template(screen, "btn_take_me_back", threshold=0.9)
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
        archer = (self.vision.find_template(screen, "btn_archer", threshold=0.9)
                  or self.vision.find_template(screen, "btn_archer_boosted", threshold=0.9))
        if archer:
            self._game_load_miss = 0
            self._anchor_miss_streak = 0
            print("[GAME_LOAD] Archer button visible, match started!")
            self._skip_top = 0
            if self._mode == "treasure":
                self._start_treasure_match()
                return
            self.adb.tap(*ARCHER_COORDS)
            time.sleep(0.3)
            self.adb.tap(*SECOND_TROOP_SLOT_COORDS)
            time.sleep(1)
            self.adb.tap(953, 566)
            self._in_game_start = time.time()
            self._last_in_game_find_state = time.time()
            time.sleep(2)
            self.adb.tap(*SECOND_TROOP_SLOT_COORDS)
            self.adb.tap(*ARCHER_COORDS)
            self.state = State.IN_GAME
            return 
        self._game_load_miss += 1
        self._anchor_miss_streak += 1
        if self._game_load_miss >= 15:
            # Don't restart blind — the match may already have finished (btn_archer can
            # be missed entirely if it renders differently than the template expects),
            # in which case a restart here tears down the app mid-loot-screen instead of
            # just noticing we're actually on COF/HOME/wherever and continuing from there.
            print(f"[GAME_LOAD] Nothing found for {self._game_load_miss} attempts — scanning anchors...")
            detected = self._find_state(screen)
            self._game_load_miss = 0
            if detected and detected != self.state:
                print(f"[GAME_LOAD] Resynced to {detected}.")
                self.state = detected
                return
            print(f"[GAME_LOAD] Still unresolved (detected={detected}) — restarting game...")
            self._restart_game("GAME_LOAD: nothing recognized 15x")
            self.state = State.HOME
            return
        print(f"[GAME_LOAD] Waiting... ({self._game_load_miss}/15)")

    # ── GOING_BACK ────────────────────────────────────────────────────────────
    def handle_going_back(self, screen):
        if self._mode == "treasure":
            print("[GOING_BACK] Tapping green back button → FAVORITES...")
            if self._target_base:
                self.bases.mark_busy(self._target_base[0])   # owner online: retry in 15 min
                self._event("treasure_base_busy", f"base {self._target_base[0]} owner online")
            self._target_base = None
            self._fav_reset()
            self.adb.tap(*GREEN_BACK_COORDS)
            self.state = State.FAVORITES
            time.sleep(1)
            return
        print("[GOING_BACK] Tapping green back button → FILTERED_RANKS...")
        self.adb.tap(*GREEN_BACK_COORDS)
        self._skip_top = 0
        self.state = State.FILTERED_RANKS
        time.sleep(0.5)

    # ── IN_GAME ───────────────────────────────────────────────────────────────
    def handle_in_game(self, screen):
        if self._mode == "treasure":
            return self.handle_in_game_treasure(screen)
        now = time.time()

        drop_coords_ready = DROP_BUTTON_1_COORDS and DROP_BUTTON_2_COORDS and DROP_HOME_COORDS
        if (self._drop_trophies and drop_coords_ready
                and self._in_game_start > 0 and now - self._in_game_start >= 2):
            print("[IN_GAME] Drop-trophies: 2s elapsed, tapping drop sequence...")
            self.adb.tap(*DROP_BUTTON_1_COORDS)
            time.sleep(0.5)
            self.adb.tap(*DROP_BUTTON_2_COORDS)
            time.sleep(5)
            for _ in range(3):
                self.adb.tap(*DROP_HOME_COORDS)
                time.sleep(1)
            self._in_game_start = 0
            self.state = State.HOME
            time.sleep(0.5)
            return

        if (screen is not None and self._in_game_start > 0
                and now - self._last_in_game_find_state >= 45):
            self._last_in_game_find_state = now
            detected = self._find_state(screen)
            if detected is not None and detected != State.IN_GAME:
                print(f"[IN_GAME] find_state detected '{detected}' instead of gameplay — resyncing")
                self._in_game_start = 0
                self.state = detected
                return

        if self._in_game_start > 0 and now - self._in_game_start > self._in_game_timeout:
            print(f"[IN_GAME] {self._in_game_timeout}s timeout — restarting game...")
            self._in_game_start = 0
            self._restart_game("IN_GAME: match timeout")
            self.state = State.HOME
            return
        if now - self._last_tap >= 1:
            self._last_tap = now
            self.adb.tap(*IN_GAME_TAP_COORDS)
            print(f"Tapped: {IN_GAME_TAP_COORDS}")

        if screen is not None and now - self._last_end_check >= 3:
            self._last_end_check = now
            continue_pos = self.vision.find_template(screen, "btn_continue", threshold=0.95)
            if continue_pos:
                print("[IN_GAME] Match ended, going to result screen...")
                if self._match_outcome is None:
                    self._match_outcome = "finished"
                self.adb.tap(continue_pos[0], continue_pos[1])
                self.state = State.CHAMBER_OF_FORTUNE
                time.sleep(1.25)
                return
            # Hero died: RESURRECT (36 gems) / USE TOKEN / RETREAT. Never resurrect —
            # RETREAT ends the match and leads to the normal result screen (SKIP →
            # CONTINUE), which the check above then picks up. Previously this screen
            # sat untouched until the 3-minute timeout restarted the whole game.
            retreat = self.vision.find_template(screen, "btn_retreat", threshold=0.85)
            if retreat:
                self._match_outcome = "hero_died"
                self._event("hero_died", "tapping RETREAT")
                self.adb.tap(*retreat)
                time.sleep(1.5)

    # ── Treasure mode ─────────────────────────────────────────────────────────
    def _treasure_nav_failed(self, where):
        """Navigation lost: close whatever is open and start over from HOME."""
        self._event("treasure_nav_lost", where)
        self.adb.tap(*POPUP_CLOSE_COORDS)
        time.sleep(1.5)
        self._nav_miss = 0
        self.state = State.HOME

    def handle_community(self, screen):
        """Community card carousel → Leaderboard card's OPEN → right arrow → Favorites.
        The carousel stops at varying positions after a swipe, so the Leaderboard card
        is located by its title instead of a fixed coordinate."""
        if self.vision.find_template(screen, "title_favorites", threshold=0.85):
            self.state = State.FAVORITES
            return
        card = self.vision.find_template(screen, "card_leaderboard", threshold=0.85)
        if card:
            self._anchor_miss_streak = 0
            print(f"[COMMUNITY] Leaderboard card at x={card[0]} → OPEN, then → Favorites")
            self.adb.tap(card[0], CARD_OPEN_Y)
            time.sleep(3)
            self.adb.tap(*PAGE_NEXT_COORDS)
            time.sleep(3)
            self._nav_miss = 0
            self._fav_reset()
            self.state = State.FAVORITES
            return
        self._nav_miss += 1
        if self._nav_miss > 8:
            return self._treasure_nav_failed("Community: Leaderboard card not found")
        if self.vision.find_template(screen, "title_community", threshold=0.85):
            # cards scroll horizontally; alternate a long left swipe and a short right one
            if self._nav_miss % 3 == 0:
                self.adb.swipe(500, 500, 1000, 500, 800)
            else:
                self.adb.swipe(1300, 500, 300, 500, 1500)
            time.sleep(1.5)
        else:
            self._anchor_miss_streak += 1
            time.sleep(1)

    def _fav_reset(self):
        """The Favorites list opens scrolled to the top: restart the scan."""
        self._fav_scrolls = 0
        self._fav_seen = set()
        self._fav_last_view = None

    def handle_favorites(self, screen):
        """Pick a favorite base: active (yellow swords), trophies ≤ --treasure-max-trophies,
        not banned (king died on its path), least recently attacked first."""
        if not self.vision.find_template(screen, "title_favorites", threshold=0.85):
            self._nav_miss += 1
            self._anchor_miss_streak += 1
            if self.vision.find_template(screen, "title_community", threshold=0.85):
                self.state = State.COMMUNITY
            elif self._nav_miss > 6:
                self._treasure_nav_failed("Favorites: list not found")
            time.sleep(1)
            return
        self._anchor_miss_streak = 0
        self._nav_miss = 0
        rows = []
        for x, y in sorted(self.vision.find_multiple_templates(screen, "btn_fav_attack", threshold=0.80),
                           key=lambda p: p[1]):
            if y < 215 or y > 845:
                continue      # row cut off by the list frame
            sat = cv2.cvtColor(screen[y - 30:y + 30, x - 35:x + 35], cv2.COLOR_BGR2HSV)[:, :, 1].mean()
            if sat < 60:
                continue      # gray swords: inactive player
            tx1, tdy1, tx2, tdy2 = FAV_ROW_TROPHIES
            trophies = self.vision.read_region_number(screen, tx1, y + tdy1, tx2, y + tdy2)
            nx1, ndy1, nx2, ndy2 = FAV_ROW_NAME
            bid = self.bases.identify(screen[y + ndy1:y + ndy2, nx1:nx2])
            self.bases.note_trophies(bid, trophies)
            rows.append((bid, trophies, x, y))
        view = tuple(r[0] for r in rows)
        eligible = [r for r in rows if r[1] and r[1] <= self._treasure_max and self.bases.available(r[0])]
        print(f"[FAVORITES] rows: {[(b, t) for b, t, _, _ in rows]} eligible: {[b for b, *_ in eligible]}")
        pick = min(eligible, key=lambda r: self.bases.last_attack(r[0])) if eligible else None
        # a known eligible base further down the list that waited longer than the pick?
        older_elsewhere = pick and any(
            bid not in view and bid not in self._fav_seen and self.bases.available(bid)
            and (m.get("trophies") or 99999) <= self._treasure_max
            and m.get("last_attack", 0) < self.bases.last_attack(pick[0])
            for bid, m in self.bases.meta.items())
        at_end = view == self._fav_last_view or self._fav_scrolls >= 8
        if pick and (not older_elsewhere or at_end):
            bid, trophies, x, y = pick
            print(f"[FAVORITES] Attacking base {bid} ({trophies} trophies)")
            self._target_base = (bid, trophies)
            self._current_target = None
            self.adb.tap(x, y)
            self._attack_prep_start = time.time()
            self.state = State.ATTACK_PREP
            time.sleep(1)
            return
        if at_end:
            self._event("treasure_no_target", f"no active favorite ≤ {self._treasure_max} trophies; waiting 10 min")
            self.adb.tap(*POPUP_CLOSE_COORDS)
            self._sleep(10 * 60)
            self.state = State.HOME
            return
        self._fav_seen.update(view)
        self._fav_last_view = view
        self._fav_scrolls += 1
        self.adb.swipe(800, 750, 800, 450, 800)
        time.sleep(1.5)

    def _start_treasure_match(self):
        bid = self._target_base[0] if self._target_base else None
        if bid:
            self.bases.attacked(bid)
        self._follower = PathFollower(self.adb.touch(), self.vision)
        self._follower.start()
        self.frames.start(bid or "unknown")
        self._in_game_start = time.time()
        self._last_in_game_find_state = time.time()
        self._match_outcome = None
        self.state = State.IN_GAME

    def handle_in_game_treasure(self, screen):
        now = time.time()
        f = self._follower
        if f is None:   # e.g. resynced into a running match: just take over
            self._follower = f = PathFollower(self.adb.touch(), self.vision)
            f.start()
            self._in_game_start = self._in_game_start or now
        if now - self._in_game_start > 200:
            f.stop()
            self._in_game_start = 0
            self._restart_game("IN_GAME (treasure): match timeout")
            self.state = State.HOME
            return
        cont = self.vision.find_template(screen, "btn_continue", threshold=0.95)
        if cont:
            f.stop()
            if self._match_outcome is None:
                self._match_outcome = "castle" if f.at_castle else "finished"
            time.sleep(3)   # reward counters animate; the panel scrolls to the treasures
            res = self.adb.current_screen()
            treasures = read_hidden_treasures(self.vision, res) if res is not None else []
            if res is not None:
                os.makedirs(TREASURE_SHOTS, exist_ok=True)
                cv2.imwrite(os.path.join(TREASURE_SHOTS, f"{time.strftime('%Y%m%d_%H%M%S')}.png"), res)
            bid, trophies = self._target_base or ("?", "")
            try:
                write_treasure_row(TREASURES_CSV, [time.strftime("%Y-%m-%d %H:%M:%S"), bid, trophies,
                                                   self._match_outcome, f.max_x or "",
                                                   f"{now - self._in_game_start:.0f}", " | ".join(treasures)])
            except OSError as e:
                print(f"[TREASURE] csv write failed: {e}")
            self.frames.finish(treasures, self._match_outcome)
            print(f"[TREASURE] base {bid}: {self._match_outcome}, treasures: {treasures or 'none'}")
            self.adb.tap(*cont)
            self._in_game_start = 0
            self.state = State.CHAMBER_OF_FORTUNE
            time.sleep(1.25)
            return
        retreat = self.vision.find_template(screen, "btn_retreat", threshold=0.85)
        if retreat:
            f.stop()
            bid = self._target_base[0] if self._target_base else None
            if f.at_castle:
                self._match_outcome = "castle"      # dying at the castle is fine (user)
                self._event("hero_died", "at the castle — counts as success")
            else:
                self._match_outcome = "died_on_path"
                self._event("hero_died", f"on the path (icon x={f.max_x}) — base {bid} banned")
                if bid:
                    self.bases.ban(bid, f"king died on the path at icon x={f.max_x}")
            self.adb.tap(*retreat)
            time.sleep(1.5)
            return
        f.step(screen)
        self.frames.save(screen, f.last_seen_x)

    # ── Session log helpers ───────────────────────────────────────────────────
    def _log_to_file(self, message):
        """Append a timestamped line to the per-run debug log (SESSION_LOG_PATH).
        Best-effort — never let a logging failure take down the bot."""
        try:
            with open(SESSION_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
        except Exception as e:
            print(f"[LOG] Failed to write to session log: {e}")

    def _flush_hour_log(self):
        """Write a summary line for the hour bucket that just ended: average match
        loop time and pearls earned during that hour. Debug aid for spotting which
        hours of a long run went slow or stopped earning."""
        avg_loop = (self._session_log_hour_loop_sum / self._session_log_hour_matches
                    if self._session_log_hour_matches else 0)
        if self._pearl_last is not None and self._session_log_hour_pearl_start is not None:
            pearl_str = str(self._pearl_last - self._session_log_hour_pearl_start)
        else:
            pearl_str = "?"
        caps = self.adb.capture_count - self._hour_capture_count
        cap_secs = self.adb.capture_seconds - self._hour_capture_secs
        cap_str = f"{cap_secs / caps * 1000:.0f} ms x {caps}" if caps else "?"
        self._log_to_file(
            f"Hour {self._session_log_hour_index + 1} summary — "
            f"matches: {self._session_log_hour_matches}, avg loop: {avg_loop:.0f}s, pearls: {pearl_str}, "
            f"screencap: {cap_str}, events: {self._hour_events}"
        )
        self._hour_capture_count = self.adb.capture_count
        self._hour_capture_secs  = self.adb.capture_seconds
        self._hour_events        = 0

    def _write_match_row(self, loop_dur):
        """One row per finished match in logs/matches.csv (kept across runs)."""
        try:
            new = not os.path.exists(MATCHES_CSV)
            with open(MATCHES_CSV, "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(["time", "match", "loop_s", "outcome", "gold", "pearls", "trophy_filter"])
                w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), self._match_count, f"{loop_dur:.1f}",
                            self._match_outcome or "unknown",
                            "" if self._gold_last is None else self._gold_last,
                            "" if self._pearl_last is None else self._pearl_last,
                            "treasure" if self._mode == "treasure" else self._trophy_filter])
        except OSError as e:
            print(f"[MATCHES] write failed: {e}")
        self._match_outcome = None

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
            hours = total_secs / 3600
            pearl_per_hour = pearl_gain / hours if hours > 0 else 0
            resources  = f" | Gold: + {gold_gain:,} | Pearls: +{pearl_gain} | Pearls/hr: {pearl_per_hour:.0f}"
        else:
            resources = ""
        print("--------------------------------------------------------------------")
        print(f"[COF] Match #{self._match_count} | Loop: {loop_dur:.0f}s | Total: {total_str} | Avg: {total_secs/self._match_count:.0f}s{resources}")
        print("--------------------------------------------------------------------")
        self._write_match_row(loop_dur)

        # Per-hour debug log: roll the bucket over whenever total elapsed time
        # crosses into a new hour, flushing a summary of the hour that just ended.
        hour_index = total_secs // 3600
        if self._session_log_hour_pearl_start is None:
            self._session_log_hour_pearl_start = self._pearl_last
        self._session_log_hour_matches  += 1
        self._session_log_hour_loop_sum += loop_dur
        if hour_index > self._session_log_hour_index:
            self._flush_hour_log()
            self._session_log_hour_index       = hour_index
            self._session_log_hour_matches     = 0
            self._session_log_hour_loop_sum    = 0.0
            self._session_log_hour_pearl_start = self._pearl_last

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
    # COF's inner loops run outside the main loop() iteration, so none of the
    # top-level safety nets (3h restart timer, screen-none counter, anchor-miss
    # resync) can see or recover a hang in here -- a wedged COF state (stale/weak
    # chest-template matches, an animation the vision keeps half-matching, etc.)
    # would otherwise spin on time.sleep(0.1) forever with zero escape. This is a
    # hard wall-clock ceiling per loop, independent of any per-iteration counter.
    _COF_LOOP_TIMEOUT = 90

    def handle_chamber_of_fortune(self, screen):
        time.sleep(1)
        missed_chests = 0
        loop1_start = time.time()
        while self.running and self._chest_taps < 3:
            if time.time() - loop1_start > self._COF_LOOP_TIMEOUT:
                print(f"[COF] Chest-opening loop stuck for {self._COF_LOOP_TIMEOUT}s — restarting game...")
                self._restart_game("COF: chest loop stuck 90s")
                self.state = State.HOME
                return
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
                self._cof_tap_home()
                return


            target = chests[0]
            print(f"[COF] Opening chest ({self._chest_taps + 1}/3)...")
            self.adb.tap(target[0], target[1])
            time.sleep(0.5)

            f2 = self.adb.current_screen()
            if f2 is not None:
                new_count = len(self._find_chests(f2))
                if new_count == 6 - (self._chest_taps + 1):
                    self._chest_taps += 1

        loop2_start = time.time()
        while self.running:
            if time.time() - loop2_start > self._COF_LOOP_TIMEOUT:
                print(f"[COF] Post-chest loop stuck for {self._COF_LOOP_TIMEOUT}s — restarting game...")
                self._restart_game("COF: post-chest loop stuck 90s")
                self.state = State.HOME
                return
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


class _TeeLog:
    """Mirror console output into logs/bot_<start>.log with a timestamp per line, so
    an unattended run (or one restarted by supervisor.py) leaves a readable record."""

    def __init__(self, console, path):
        self._console = console
        self._file = open(path, "a", encoding="utf-8", buffering=1)
        self._at_line_start = True

    def write(self, text):
        try:
            self._console.write(text)
        except UnicodeEncodeError:
            # A console/pipe in a legacy code page (cp1252) can't print "→" etc. —
            # this used to raise inside every print() and wedge the main loop.
            enc = getattr(self._console, "encoding", None) or "ascii"
            self._console.write(text.encode(enc, errors="replace").decode(enc))
        out = []
        for chunk in text.splitlines(keepends=True):
            if self._at_line_start:
                out.append(time.strftime("%H:%M:%S "))
            out.append(chunk)
            self._at_line_start = chunk.endswith("\n")
        self._file.write("".join(out))
        return len(text)

    def flush(self):
        self._console.flush()
        self._file.flush()


def setup_run_log():
    path = os.path.join(LOG_DIR, time.strftime("bot_%Y%m%d_%H%M%S.log"))
    for old in sorted(glob.glob(os.path.join(LOG_DIR, "bot_*.log")))[:-(KEEP_RUN_LOGS - 1)]:
        try:
            os.remove(old)
        except OSError:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.stdout = _TeeLog(sys.stdout, path)
    sys.stderr = _TeeLog(sys.stderr, path)
    print(f"[LOG] Console output is also written to {path}")


if __name__ == "__main__":
    setup_run_log()
    parser = argparse.ArgumentParser(description="Royal Revolt 2 Farm Bot")
    parser.add_argument("--port", type=int, default=5555,
                        help="ADB port of the LDPlayer instance (default: 5555, the first instance). "
                             "Run 'adb devices' while the instance is running to confirm it.")
    parser.add_argument("--ld-index", type=int, default=0,
                        help="LDPlayer instance index, used to check/launch/restart it via "
                             "ldconsole (default: 0, the first instance)")
    parser.add_argument("--ldconsole", type=str, default=None,
                        help="Path to ldconsole.exe. Auto-detected under common LDPlayer install "
                             "locations if omitted; only needed if auto-detect fails.")
    parser.add_argument("--trophy-filter", type=int, default=600,
                        help="Trophy filter target (400-4000, default: 600)")
    parser.add_argument("--gold", type=int, default=1_000_000,
                        help="Melt threshold — melt if gold above this, sell otherwise (100000-32000000, default: 1000000)")
    parser.add_argument("--drop-trophies", choices=["drop_yes", "drop_no"], default="drop_no",
                        help="Drop trophies mode: drop_yes ends match after 3s, drop_no after 180s (default: drop_no)")
    parser.add_argument("--mode", choices=["farm", "treasure"], default="farm",
                        help="farm: trophy-filtered attacks (default). treasure: attack favorite bases "
                             "with gear set 2 and walk the king along the path to collect treasures")
    parser.add_argument("--treasure-max-trophies", type=int, default=2000,
                        help="treasure mode: only favorites at or below this many trophies (default 2000)")
    parser.add_argument("--capture-inactives", action="store_true",
                        help="Collection mode: stays on the attack-prep screen rerolling opponents "
                             "forever via the static 'New Opponent' button, saving a screenshot to "
                             "captured_inactives/ whenever neither the yellow nor gray attack button "
                             "is recognized within 10s. Crop the results offline with crop_from_file.py.")
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
                 ld_index=args.ld_index, ldconsole=ldconsole,
                 capture_inactives=args.capture_inactives,
                 mode=args.mode, treasure_max_trophies=args.treasure_max_trophies)
    bot.loop()

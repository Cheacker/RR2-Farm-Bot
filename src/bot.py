import sys
import time
import os
import subprocess
import cv2
from controller import ADBController
from vision import VisionInterpreter
from player_db import PlayerDB

FAIL_DEBUG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fail_debug")
os.makedirs(FAIL_DEBUG_DIR, exist_ok=True)

RR2_PACKAGE            = "com.flaregames.rrtournament"
MEMU_EXE               = r"D:\Program Files\Microvirt\MEmu\MEmu.exe"
MEMU_RESTART_INTERVAL  = 3 * 3600  # restart MEmu every 3 hours

# ── Coordinates ──────────────────────────────────────────────────────────────
TROPHY_COORDS      = (0, 0)      # set with get_coords.py
BLUE_SEARCH_COORDS = (1436, 213)
ARCHER_COORDS      = (200, 800)
CANNON_COORDS      = (240, 800)
MINUS_LEFT_COORDS  = (810, 226)
MINUS_RIGHT_COORDS = (1084, 226)
PLUS_LEFT_COORDS   = (985, 229)
PLUS_RIGHT_COORDS  = (1258, 226)

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
    def __init__(self, port=21503, template_dir=None, trophy_filter=600):
        self.adb = ADBController(port=port)
        if not self.adb.device:
            print("[MEMU] No ADB device — launching MEmu...")
            subprocess.Popen([MEMU_EXE])
            print("[MEMU] Waiting for MEmu ADB to be ready...")
            time.sleep(15)
            deadline = time.time() + 75
            while time.time() < deadline:
                self.adb._connect()
                if self.adb.device:
                    break
                time.sleep(5)
        if not self.adb.device:
            print("ADB connection failed.")
            exit(1)
        self.vision = VisionInterpreter(template_dir=template_dir)
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
        self._in_game_start     = 0
        self._trophy_filter     = trophy_filter
        self._gold_start        = None
        self._pearl_start       = None
        self._gold_last         = None
        self._pearl_last        = None
        self._main_adb_fail     = 0
        self.db = PlayerDB()

    # ── MEmu restart ─────────────────────────────────────────────────────────
    def _restart_memu(self):
        print("[MEMU] Force-closing MEmu...")
        subprocess.run(["taskkill", "/F", "/IM", "MEmu.exe", "/T"], capture_output=True)
        time.sleep(5)
        print(f"[MEMU] Launching: {MEMU_EXE}")
        subprocess.Popen([MEMU_EXE])
        print("[MEMU] Waiting for MEmu ADB to be ready...")
        time.sleep(15)
        deadline = time.time() + 75
        while time.time() < deadline:
            self.adb._connect()
            if self.adb.device and self.adb.current_screen() is not None:
                print("[MEMU] MEmu is ready.")
                break
            time.sleep(5)
        else:
            print("[MEMU] Timeout — MEmu did not start within 90s.")
        self.db.set_last_memu_restart()
        self.adb.restart_game(RR2_PACKAGE)
        self.state = State.HOME
        self._main_adb_fail = 0
        print("[MEMU] Restart complete.")

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
        last_restart = self.db.get_last_memu_restart()
        if last_restart is None:
            # No record — MEmu state unknown; record now and just start the game
            self.db.set_last_memu_restart()
            self.adb.restart_game(RR2_PACKAGE)
        elif time.time() - last_restart >= MEMU_RESTART_INTERVAL:
            self._restart_memu()
        else:
            self.adb.restart_game(RR2_PACKAGE)
        while self.running:
            try:
                screen = self.adb.current_screen()
                if screen is None:
                    self._main_adb_fail += 1
                    if self._main_adb_fail >= 20:
                        print(f"[ADB] No screen for {self._main_adb_fail} attempts — restarting MEmu...")
                        self._restart_memu()
                        self._main_adb_fail = 0
                    elif self.state == State.IN_GAME:
                        self.handle_in_game(None)
                    time.sleep(0.5)
                    continue
                self._main_adb_fail = 0

                if self.state == State.HOME:
                    last_restart = self.db.get_last_memu_restart() or self._start_time
                    if time.time() - last_restart >= MEMU_RESTART_INTERVAL:
                        self._restart_memu()
                        continue

                if   self.state == State.HOME:               self.handle_home(screen)
                elif self.state == State.TROPHY_MENU:        self.handle_trophy_menu(screen)
                elif self.state == State.FILTERED_RANKS:     self.handle_filtered_ranks(screen)
                elif self.state == State.ATTACK_PREP:        self.handle_attack_prep(screen)
                elif self.state == State.GAME_LOAD:          self.handle_game_load(screen)
                elif self.state == State.GOING_BACK:         self.handle_going_back(screen)
                elif self.state == State.IN_GAME:            self.handle_in_game(screen)
                elif self.state == State.CHAMBER_OF_FORTUNE: self.handle_chamber_of_fortune(screen)

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
            gold  = self.vision.read_region_number(screen, 102, 29, 253, 72)
            pearl = self.vision.read_region_number(screen, 88, 194, 213, 228)
            if gold is not None and pearl is not None:
                if self._gold_start is None:
                    self._gold_start  = gold
                    self._pearl_start = pearl
                self._gold_last  = gold
                self._pearl_last = pearl
            print("[HOME] Forge icon found, tapping trophy...")
            self.adb.tap(*TROPHY_COORDS)
            self.state = State.TROPHY_MENU
            time.sleep(0.5)
        else:
            self._trophy_miss_count += 1
            if self._trophy_miss_count > 21:
                self._shutdown(f"home_trophy_miss_{self._trophy_miss_count}")
                return

            if self._trophy_miss_count % 2 == 0:
                self.adb.tap(10, 10)
                time.sleep(0.1)

            if self._trophy_miss_count % 6 == 0:
                close = self.vision.find_template(screen, "btn_close", threshold=0.57)
                if close:
                    print("[HOME] Pressing btn_close...")
                    self.adb.tap(close[0], close[1])
                    time.sleep(0.5)

            if self._trophy_miss_count % 5 == 0:
                collect = self.vision.find_template(screen, "btn_collect", threshold=0.80)
                if collect:
                    print(f"[HOME] {self._trophy_miss_count}th miss → pressing btn_collect...")
                    self.adb.tap(collect[0], collect[1])
                    time.sleep(1.5)
                    self.adb.tap(1524, 86)
                else:
                    close = self.vision.find_template(screen, "btn_close", threshold=0.80)
                    if close:
                        print("[HOME] No collect found, trying btn_close...")
                        self.adb.tap(close[0], close[1])

    # ── Helper: scroll ranked list ────────────────────────────────────────────
    def _scroll_list(self, times=1):
        for _ in range(times):
            self.adb.swipe(650, 600, 650, 300, 300)
            time.sleep(0.4)
        self.adb.tap(812, 832)
        time.sleep(3.5)
        print(f"List scrolled {times} time(s).")
        self._skip_top = 0

    # ── TROPHY_MENU ───────────────────────────────────────────────────────────
    def handle_trophy_menu(self, screen):
        yellow = self.vision.find_template(screen, "btn_start_search", threshold=0.95)
        if yellow:
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
        time.sleep(0.15)

    # ── FILTERED_RANKS ────────────────────────────────────────────────────────
    def handle_filtered_ranks(self, screen):
        opponents = self.vision.find_multiple_templates(screen, "area_top_opponent", threshold=0.92)
        if not opponents:
            self._no_opponent_count += 1
            if self._no_opponent_count >= 27:
                print(f"[FILTERED_RANKS] No sword found after {self._no_opponent_count} attempts, shutting down...")
                self.running = False
                return
            if self._no_opponent_count % 9 == 0:
                attack_btn = self.vision.find_template(screen, "btn_attack_start", threshold=0.5)
                if attack_btn:
                    print("[FILTERED_RANKS] Attack button visible → ATTACK_PREP")
                    self.state = State.ATTACK_PREP
                    self._attack_prep_start = time.time()
                    return
            if self._no_opponent_count % 3 == 0:
                print(f"[FILTERED_RANKS] No sword found after {self._no_opponent_count} attempts, scrolling...")
                self._scroll_list()
            return

        self._no_opponent_count = 0
        opponents.sort(key=lambda pos: pos[1])
        for sword_pos in opponents:
            name = self.vision.read_player_name(screen, sword_pos[0], sword_pos[1])
            if not name:
                print("[FILTERED_RANKS] Could not read player name → scroll")
                self._scroll_list()
                return
            active = self.db.is_active(name)
            info   = self.db.info_str(name)
            print(f"Player detected: [{name}], {info}")
            if active:
                self._skip_top += 1
                print(f"  → Active, skipping. skip={self._skip_top}")
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
        pos = self.vision.find_template(screen, "btn_attack_start", threshold=0.9)
        if pos:
            print("[ATTACK_PREP] Attack button found, pressing → GAME_LOAD...")
            self.adb.tap(pos[0], pos[1])
            self.state = State.GAME_LOAD
            time.sleep(0.2)
        elif time.time() - self._attack_prep_start > 12:
            print("[ATTACK_PREP] Button not found within 12s → FILTERED_RANKS")
            self.state = State.FILTERED_RANKS
        else:
            print("[ATTACK_PREP] Waiting for attack button...")

    # ── GAME_LOAD ─────────────────────────────────────────────────────────────
    def handle_game_load(self, screen):
        time.sleep(0.1)
        go_back = self.vision.find_template(screen, "btn_bring_me_back", threshold=0.9)
        if go_back:
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
        if time.time() - self._attack_prep_start > 10:
            big_collect = self.vision.find_template(screen, "big_collect", threshold=0.80)
            if big_collect:
                print("[GAME_LOAD] 10s → btn_collect found, tapping...")
                self.adb.tap(big_collect[0], big_collect[1])
                time.sleep(0.5)
                return
        print("[GAME_LOAD] Waiting...")

    # ── GOING_BACK ────────────────────────────────────────────────────────────
    def handle_going_back(self, screen):
        print("[GOING_BACK] Tapping green back button → FILTERED_RANKS...")
        self.adb.tap(1430, 85)
        self._skip_top = 0
        self.state = State.FILTERED_RANKS
        time.sleep(0.5)

    # ── IN_GAME ───────────────────────────────────────────────────────────────
    def handle_in_game(self, screen):
        now = time.time()

        if self._in_game_start > 0 and now - self._in_game_start > 180:
            print("[IN_GAME] 3-minute timeout, shutting down...")
            self._shutdown("in_game_timeout_3min")
            return
        if now - self._last_tap >= 0.65:
            self._last_tap = now
            self.adb.tap(10, 10)
            print("Tapped: (10, 10)")

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
        _adb_fail = 0
        while self._chest_taps < 3:
            time.sleep(0.3)
            f = self.adb.current_screen()
            if f is None:
                _adb_fail += 1
                if _adb_fail >= 20:
                    print("[COF] ADB lost — restarting MEmu...")
                    self._restart_memu()
                    return
                continue
            _adb_fail = 0

            pes = self.vision.find_template(f, "btn_give_up", threshold=0.70)
            if pes:
                print(f"[COF] Give up (1 buttons), tapping leftmost: {pes}")
                self.adb.tap(pes[0], pes[1])
                time.sleep(3)
                self._cof_tap_home()
                return

            sell = self.vision.find_template(f, "btn_sell", threshold=0.70)
            if sell:
                melt = self.vision.find_template(f, "btn_melt", threshold=0.70)
                if melt and self._gold_last is not None and self._gold_last > 1_000_000:
                    print(f"[COF] Melt (gold={self._gold_last:,}): {melt}")
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

        _adb_fail = 0
        while True:
            time.sleep(0.3)
            f = self.adb.current_screen()
            if f is None:
                _adb_fail += 1
                if _adb_fail >= 20:
                    print("[COF] ADB lost — restarting MEmu...")
                    self._restart_memu()
                    return
                continue
            _adb_fail = 0

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
                if melt and self._gold_last is not None and self._gold_last > 1_000_000:
                    print(f"[COF] Melt (gold={self._gold_last:,}): {melt}")
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
    args = sys.argv[1:]
    port          = 21503
    trophy_filter = 600

    for arg in args:
        if arg.isdigit():
            val = int(arg)
            if 600 <= val <= 3500:
                trophy_filter = val
            else:
                port = val

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    template_dir = os.path.join(base, "En_Templates")

    print(f"Trophy filter: {trophy_filter} (left min: {trophy_filter - 100})")
    bot = RR2Bot(port=port, template_dir=template_dir, trophy_filter=trophy_filter)
    bot.loop()

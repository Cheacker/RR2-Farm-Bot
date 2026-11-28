# Royal Revolt 2 Farm Bot

ADB-based farm bot for Royal Revolt 2 on LDPlayer 9. The user speaks Turkish (reply in the prompt's language).
Vision: OpenCV `TM_CCOEFF_NORMED` template matching (per region) + Tesseract OCR (numbers/names only).
Details: `HANDOVER.md` (read the relevant section before touching an area). Plan: `YOL_HARITASI.md`.
Build/run steps: `BUILD.md`.

## Commands

```powershell
uv sync
uv run python supervisor.py --trophy-filter 600 --gold 1000000   # supervised, for 24h+ runs
uv run python src/bot.py --trophy-filter 600                     # one-off run
uv run python src/bot.py --mode treasure                         # treasure mode (favorites, gear set 2)
uv run python tools/report.py --hours 24                         # run summary (logs/)
uv run python tools/check_regions.py fail_debug dev_tools/atlas_2026-11-21   # region check (no emulator)
uv run python tools/treasure_label.py                            # mark treasures on logs/treasure_frames
uv run python tools/treasure_dataset.py                          # crops from the marks -> dataset/treasure/
```

Options: `--port 5555`, `--ld-index 0`, `--ldconsole`, `--trophy-filter 600` (400-4000),
`--gold 1000000` (melt/sell threshold), `--drop-trophies drop_yes|drop_no`, `--capture-inactives`,
`--mode treasure`, `--treasure-max-trophies 2000`.

## Environment

- LDPlayer: `D:\LDPlayer\LDPlayer9\ldconsole.exe`, ADB `127.0.0.1:5555` (`emulator-5554` also shows up briefly).
- Game package `com.flaregames.rrtournament`, version 12.9.0 (2026-11-21). Device language `en-US` only.
- Resolution fixed at 1600x900: every template and coordinate assumes it.
- Python via `uv` (old `venv/` still around).

## Layout

```
src/bot.py              RR2Bot: state machine, interrupts, logs
src/treasure.py         treasure mode: favorites navigation, PathFollower, FrameRecorder, treasures.csv
src/treasure_vision.py  treasure detection helpers (glow map, HUD mask, crop, camera shift, beam tracks)
src/controller.py       ADB (every shell call has a timeout), real-finger touch()
src/vision.py           DEFAULT_REGIONS, find_template(region=None|FULL|box), OCR
src/player_db.py        player_data.json (active 15 min / permanently unattackable)
supervisor.py           starts the bot, watches heartbeat, restarts on crash/hang
tools/                  report.py, check_regions.py, treasure_label.py, treasure_dataset.py
En_Templates/           templates (.png)
logs/                   bot_*.log, matches.csv, events.csv, treasures.csv, treasure_frames/, heartbeat.txt (gitignored)
dataset/                generated crops (gitignored)
fail_debug/, dev_tools/ local only; dev_tools/atlas_2026-11-21 has player names, never committed
```

## State flow

`HOME -> TROPHY_MENU -> FILTERED_RANKS -> ATTACK_PREP -> GAME_LOAD -> IN_GAME -> CHAMBER_OF_FORTUNE -> HOME`
(active opponent: GAME_LOAD -> GOING_BACK -> FILTERED_RANKS). Each loop first runs `_check_interrupts`
(Break Time, forced update, Downloading, Not enough Food, Shop). Treasure mode: `HANDOVER.md` §5b.

## Rules

- Never tap a fixed coordinate without confirming the screen. Gem-spending areas: `HANDOVER.md` §5.
- Detect with region-limited templates, not OCR. Add region/context instead of lowering a threshold.
- Never run `adb kill-server`.
- Every game restart goes through `self._restart_game("reason")` (written to events.csv).
- Long waits use `self._sleep()` (keeps the heartbeat alive, otherwise the supervisor kills the bot).
- New template: add its region to `DEFAULT_REGIONS`, verify with `check_regions.py`.
- During exploration don't analyse frames while in a match (~1:40); script the inputs, record with `screenrecord`.
- Commit messages in English; stage changed files by name. Version: `pyproject.toml`, notes `CHANGELOG.md`.

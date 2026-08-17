# RR2 Farm Bot

An ADB-based bot that automatically plays [Royal Revolt 2](https://www.flaregames.com/games/royal-revolt-2/) inside the [LDPlayer](https://www.ldplayer.net/) Android emulator: it searches the ranked ladder for opponents, attacks, plays out the match, opens the post-match chests, and loops — indefinitely, unattended.

It works by taking screenshots of the emulator and matching them against a set of reference button images (template matching with OpenCV), plus OCR (Tesseract) to read trophy counts and player names. It does **not** modify the game client, read memory, or touch any game files — it only sends the same taps and swipes a human would, over ADB.

Everything runs locally on your PC. Nothing is uploaded anywhere, and the bot does not require or ask for your game account credentials.

> **Use at your own risk.** Automating a mobile game like this is against most games' Terms of Service. This project is shared for educational purposes (computer vision, state machines, ADB automation). You are responsible for what you do with it.

---

## What it does

- Opens the ranked ladder, sets a trophy filter, and searches for opponents.
- Skips opponents who are currently active (online) or confirmed unattackable, and remembers them so it doesn't waste time re-trying.
- Attacks, plays the match (archer + cannon), and opens the Chamber of Fortune (3 chests, then melts or sells the rest based on your gold).
- Recovers from most failure states on its own: a wrong/unexpected screen, a dropped ADB connection, a hung emulator, or a stuck Chamber of Fortune — restarting the game or the emulator itself as a last resort.
- Optional **drop-trophies mode**: deliberately loses matches quickly to lower your own trophy count instead of climbing it.
- Optional **inactive-opponent capture mode**: a data-collection tool to help you build a better "unattackable opponent" template (see [Troubleshooting](#troubleshooting)).

---

## Requirements

| Requirement | Notes |
|---|---|
| Windows PC | Tested on Windows 11. Uses `wm size` over ADB and Windows process management (`ldconsole.exe`), so it's Windows-specific as written. |
| [LDPlayer](https://www.ldplayer.net/) | The Android emulator the bot controls. Any recent version works. |
| [Python 3.10+](https://www.python.org/downloads/) | With `pip`. |
| [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) | Separate program, not just a Python package — used to read trophy counts and player names from screenshots. |
| ADB (`adb.exe`) | LDPlayer ships its own; see [Step 2](#step-2--make-sure-adb-is-available) if `adb` isn't on your `PATH`. |

---

## Installation

### Step 1 — Get the code

**Option A — download as ZIP** (no git needed): click the green **Code** button on the GitHub page → **Download ZIP** → extract it anywhere.

**Option B — clone with git**:
```
git clone https://github.com/Cheacker/RR2-Farm-Bot.git
cd RR2-Farm-Bot
```

### Step 2 — Make sure ADB is available

Open a terminal and run:
```
adb version
```
If that works, skip to Step 3.

If you get `'adb' is not recognized`, either:
- Add LDPlayer's install folder to your `PATH` (it bundles `adb.exe`, `ldconsole.exe`, etc. — see [Finding your LDPlayer install path](#finding-your-ldplayer-install-path) below), **or**
- Install [Android Platform Tools](https://developer.android.com/studio/releases/platform-tools) and add the extracted folder to your `PATH`.

### Step 3 — Install Python packages

```
pip install -r requirements.txt
```
This installs `adbutils` (talks to the emulator over ADB), `opencv-python` (template matching), `pytesseract` (OCR wrapper), and `numpy`.

### Step 4 — Install Tesseract OCR

1. Download the Windows installer from the [UB-Mannheim Tesseract wiki](https://github.com/UB-Mannheim/tesseract/wiki) (look for `tesseract-ocr-w64-setup-x.x.x.exe` near the bottom of the page).
2. Run it with default settings. Default install path is `C:\Program Files\Tesseract-OCR\tesseract.exe`.
3. If you installed it somewhere else, open `src/vision.py` and update this line near the top:
   ```python
   pytesseract.pytesseract.tesseract_cmd = r"C:\...\tesseract.exe"
   ```

### Step 5 — Install and configure LDPlayer

1. Download and install [LDPlayer](https://www.ldplayer.net/).
2. Start an instance and install Royal Revolt 2 inside it (from the Play Store, or however you normally would).
3. **Set the Android language to English.** The bot's reference images were captured with an English UI — a different language means most buttons won't be recognized at all.
   - Inside the emulator: **Settings** (gear icon) → search **Languages & input** → make **English** the primary language → restart the emulator so Royal Revolt 2 picks it up.
4. **Do not manually change the emulator resolution.** The bot forces it to `1600x900` itself on every start (via `wm size`) to match the reference images — you don't need to set this yourself, and fighting it by setting a different resolution in LDPlayer's own display settings isn't necessary.

### Step 6 — One-time in-game setup

**Place a boosted Archer in your FIRST troop slot.** The bot detects the archer button to know a match has started, and taps the first and second troop slots at match start. If slot 1 is empty, has a different unit, or isn't boosted, the bot will not function correctly.

That's it — you do **not** need to navigate to any particular screen or manually set a trophy filter. The bot closes and reopens the game itself and sets the filter automatically.

---

## Finding your ADB port

LDPlayer's first instance uses ADB port **`5555`** by default — this is also the bot's default, so most single-instance setups don't need to pass `--port` at all.

If you're running multiple LDPlayer instances, or want to confirm the port:
1. Start the instance.
2. Run `adb devices` in a terminal. You'll see something like `127.0.0.1:5555` or `emulator-5554` in the list — the port following the instance is what you pass to `--port`.
3. Multiple instances follow the pattern `5555 + 2×index` (instance 0 → `5555`, instance 1 → `5557`, instance 2 → `5559`, ...).

## Finding your LDPlayer install path

The bot needs `ldconsole.exe` (LDPlayer's command-line control tool) to restart the emulator when needed. It auto-detects this under common install locations (`C:\LDPlayer\...`, `D:\LDPlayer\...`, `C:\Program Files\LDPlayer\...`, etc. — any drive, any version folder). If your install is somewhere unusual, pass its path with `--ldconsole` (see below) — you'll get a clear `[WARN]` at startup if it couldn't be found.

---

## Running the bot

```
python src\bot.py
```

That's the whole command for a typical single-instance setup — port `5555`, trophy filter `600`, melt threshold `1,000,000` gold, drop-trophies off.

### Parameters

| Parameter | Default | Description |
|---|---|---|
| `--port` | `5555` | ADB port of the LDPlayer instance. |
| `--ld-index` | `0` | LDPlayer instance index (used for `ldconsole` restarts). |
| `--ldconsole` | auto-detected | Path to `ldconsole.exe`. Only needed if auto-detection fails. |
| `--trophy-filter` | `600` | Target trophy range for the ranked search (400–4000). |
| `--gold` | `1000000` | Melt threshold in Chamber of Fortune: melt if your gold is above this, sell otherwise (100,000–32,000,000). |
| `--drop-trophies` | `drop_no` | `drop_yes` deliberately ends matches quickly to lower your trophies; `drop_no` plays normally. |
| `--capture-inactives` | off | Data-collection mode — see [Troubleshooting](#capturing-more-unattackable-opponent-examples). |

Examples:
```
python src\bot.py --trophy-filter 900
python src\bot.py --gold 20000000
python src\bot.py --port 5557 --ld-index 1
python src\bot.py --drop-trophies drop_yes
```

Stop the bot any time with **Ctrl+C**. If it stops for any reason (crash, manual stop, power loss), just run the same command again — it restarts the game and continues from the beginning of the loop. Progress (which players are marked active/unattackable) is saved in `player_data.json` and survives restarts.

**While it's running:** don't click inside the LDPlayer window — it will interfere with the bot's taps. You can minimize the window; the bot keeps working.

---

## How it works (short version)

The bot is a state machine: `HOME → TROPHY_MENU → FILTERED_RANKS → ATTACK_PREP → GAME_LOAD → IN_GAME → CHAMBER_OF_FORTUNE → HOME`, looping forever. Each state has its own handler that looks for a specific "anchor" button (e.g. the forge icon means HOME, the archer button means a match has started) and acts accordingly.

If a handler's expected anchor goes missing for too long, the bot doesn't just keep guessing — it scans every state's anchor template to figure out what screen it's actually looking at and resyncs, rather than continuing to tap coordinates meant for a screen that isn't there anymore. If that still doesn't resolve things, it falls back to restarting the game, and if even the emulator itself seems hung, it restarts LDPlayer.

---

## Troubleshooting

### The bot can't find an obvious button (icon/button detection fails)

This is almost always a **template mismatch** — the reference image in `En_Templates/` doesn't match closely enough to what's rendering on your screen. This can happen because of subtle rendering differences between emulators, a game UI update, or a different game language/resolution.

Fix: recapture the specific template with `recrop.py`:
```
python recrop.py
```
It connects to the emulator, shows you a numbered menu, and lets you draw a box around the button on a live screenshot. Navigate to the relevant screen in the emulator first, then pick its number from the menu. It overwrites the existing `.png` in `En_Templates/`.

Console output tells you exactly what it's comparing and how close it got, e.g.:
```
[VISION] btn_start_search not found (max=0.842, threshold=0.95)
```
A score that's consistently *close* to the threshold (like `0.842` vs `0.95`, repeating across many frames) usually means the button genuinely renders slightly differently on your setup — recapture it. A score that's *far* off (like `0.28`) usually means you're looking at the wrong screen or a totally different bug.

### The bot taps the wrong coordinate

Coordinates in `src/bot.py` (things like `TROPHY_COORDS`, `ARCHER_COORDS`, etc., near the top of the file) are all tuned for `1600x900`. If your setup somehow ends up at a different resolution, or a button has genuinely moved, use `get_coords.py` to find the right pixel position:
```
python get_coords.py
```
Click where you want the coordinate, and it prints the `(x, y)` to the console.

### Capturing more unattackable-opponent examples

The bot detects an unattackable opponent by a gray (rather than yellow) attack button. If it's missing this too often, you can generate more example screenshots automatically:
```
python src\bot.py --capture-inactives
```
This mode parks on the attack-prep screen and rerolls forever, saving a screenshot to `captured_inactives/` any time neither the yellow nor gray attack button is confidently recognized for 10 seconds — these are exactly the cases the current template misses. Stop it (Ctrl+C) once you have a few, then crop the best one into a template **without touching the running emulator**:
```
python crop_from_file.py captured_inactives\<file>.png btn_attack_start_gray
```

### `--drop-trophies` isn't doing anything

Drop-trophies mode needs four coordinates specific to your setup, defined near the top of `src/bot.py`:
- `OWN_TROPHY_REGION` — the OCR box around your own trophy count (find corners with `get_coords.py`)
- `DROP_BUTTON_1_COORDS` / `DROP_BUTTON_2_COORDS` — the two buttons tapped in order, 2 seconds after a match starts, to end it
- `DROP_HOME_COORDS` — the final tap back to HOME

If any of these are unset, the bot prints a `[WARN]` at startup and falls back to a plain timeout-based drop instead.

### "No ADB devices found"

Is the LDPlayer instance actually running? Run `adb devices` — it should be listed. If it isn't, start the instance manually once, wait for it to fully boot, then run the bot again.

### `ModuleNotFoundError` / `tesseract is not installed`

Re-check [Step 3](#step-3--install-python-packages) and [Step 4](#step-4--install-tesseract-ocr) above.

---

## Files & folders

| Path | What it is |
|---|---|
| `src/bot.py` | Main state machine and all the per-screen handlers. |
| `src/controller.py` | ADB connection, screenshots, taps/swipes. |
| `src/vision.py` | Template matching and OCR. |
| `src/player_db.py` | Tracks which opponents are currently active or permanently unattackable. |
| `En_Templates/` | Reference button images used for template matching. |
| `recrop.py` | Recapture a template from a live emulator screenshot. |
| `get_coords.py` | Click on a live screenshot to get its pixel coordinates. |
| `crop_from_file.py` | Crop a template from an already-saved screenshot file (no live emulator needed). |
| `player_data.json` | Persisted state — which players are active/unattackable. Not committed to git. |
| `fail_debug/` | Screenshots saved automatically when the bot gets stuck, for diagnosing later. Not committed to git. |
| `captured_inactives/` | Screenshots saved by `--capture-inactives` mode. Not committed to git. |

---

## Verifying the code yourself

This project is fully open source and doesn't send anything over the network besides talking to your local emulator (`127.0.0.1`) over ADB. If you want a second opinion before running it: paste `src/bot.py`, `src/controller.py`, and `src/vision.py` into an AI assistant and ask it to explain what the code does and whether it sends any data anywhere.

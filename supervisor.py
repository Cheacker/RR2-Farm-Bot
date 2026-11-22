"""
Supervisor for unattended (24h+) runs.

Starts src/bot.py with the same arguments, watches logs/heartbeat.txt and
restarts the bot when it
  * exits for any reason (crash, sys.exit, Ctrl+C inside the bot's own window), or
  * stops updating its heartbeat for HEARTBEAT_TIMEOUT seconds (hung ADB call,
    endless inner loop...), or
  * keeps running but gets no screen frame for FRAME_TIMEOUT seconds (blind).
The whole process tree is killed first.

Every restart of the bot also restarts LDPlayer (bot.py does that on startup),
so a wedged emulator is reset as well.

Usage (all arguments after the script name are passed to bot.py unchanged):
  uv run python supervisor.py --trophy-filter 600 --gold 1000000
Stop with Ctrl+C in this window.
"""
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
BOT = os.path.join(ROOT, "src", "bot.py")
LOG_DIR = os.path.join(ROOT, "logs")
HEARTBEAT = os.path.join(LOG_DIR, "heartbeat.txt")
SUPERVISOR_LOG = os.path.join(LOG_DIR, "supervisor.log")

HEARTBEAT_TIMEOUT = 300   # s without a heartbeat → bot considered hung
FRAME_TIMEOUT = 10 * 60   # s without a good screen frame → bot considered blind
                          # (the bot itself restarts LDPlayer after 5 min; this is the backstop)
STARTUP_GRACE = 240       # s allowed for LDPlayer restart + first heartbeat
QUICK_EXIT = 120          # a run shorter than this counts as a failed start
MAX_BACKOFF = 15 * 60     # s; wait cap after repeated failed starts
POLL = 5


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(f"[SUPERVISOR] {line}", flush=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(SUPERVISOR_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def heartbeat_ages():
    """(heartbeat age, last-good-frame age) in seconds; None where unreadable.
    Heartbeat line: '<now> <state> <last good frame>'."""
    try:
        with open(HEARTBEAT, encoding="utf-8") as f:
            parts = f.read().split()
        now = time.time()
        beat = now - float(parts[0])
        frame = now - float(parts[2]) if len(parts) >= 3 else None
        return beat, frame
    except (OSError, ValueError, IndexError):
        return None, None


def kill_tree(proc):
    """Kill the bot and its children (adb shell / tesseract calls)."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        proc.kill()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        pass


def run_once(bot_args):
    """Run the bot until it exits or hangs. Returns (runtime_s, reason)."""
    start = time.time()
    # Remove the old heartbeat so a stale file can't mask a bot that never starts.
    try:
        os.remove(HEARTBEAT)
    except OSError:
        pass
    env = dict(os.environ, PYTHONIOENCODING="utf-8")  # bot prints "→"; cp1252 pipes can't
    proc = subprocess.Popen([sys.executable, "-u", BOT, *bot_args], cwd=ROOT, env=env)
    log(f"bot started (pid {proc.pid}) args={' '.join(bot_args) or '(defaults)'}")
    seen_heartbeat = False
    last_ok = start
    try:
        while True:
            time.sleep(POLL)
            code = proc.poll()
            if code is not None:
                return time.time() - start, f"exited with code {code}"
            age, frame_age = heartbeat_ages()
            elapsed = time.time() - start
            if age is None:
                # Unreadable can be transient (the bot is replacing the file right now).
                # Before the first heartbeat: startup grace. After: same limit as stale.
                if not seen_heartbeat and elapsed > STARTUP_GRACE:
                    kill_tree(proc)
                    return elapsed, f"no heartbeat {STARTUP_GRACE}s after start"
                if seen_heartbeat and time.time() - last_ok > HEARTBEAT_TIMEOUT:
                    kill_tree(proc)
                    return elapsed, f"heartbeat unreadable for {HEARTBEAT_TIMEOUT}s"
                continue
            seen_heartbeat = True
            last_ok = time.time()
            if age > HEARTBEAT_TIMEOUT:
                kill_tree(proc)
                return elapsed, f"heartbeat stale for {age:.0f}s (hung)"
            elif frame_age is not None and frame_age > FRAME_TIMEOUT:
                # Loop still running but blind (e.g. wedged screencap) and the bot's
                # own LDPlayer restart didn't fix it: start over from scratch.
                kill_tree(proc)
                return elapsed, f"no screen frame for {frame_age:.0f}s (blind)"
    except KeyboardInterrupt:
        kill_tree(proc)
        raise


def main():
    bot_args = sys.argv[1:]
    failures = 0
    log("supervisor started")
    try:
        while True:
            runtime, reason = run_once(bot_args)
            log(f"bot stopped after {runtime / 60:.1f} min: {reason}")
            failures = failures + 1 if runtime < QUICK_EXIT else 0
            wait = min(MAX_BACKOFF, 10 * (2 ** failures)) if failures else 10
            log(f"restarting in {wait}s (consecutive quick failures: {failures})")
            time.sleep(wait)
    except KeyboardInterrupt:
        log("supervisor stopped by user")


if __name__ == "__main__":
    main()

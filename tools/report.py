"""
Summarise an unattended run from logs/matches.csv, logs/events.csv and
logs/supervisor.log.

Usage:
  uv run python tools/report.py              # last 24 hours
  uv run python tools/report.py --hours 6
"""
import argparse
import csv
import os
import time
import sys
from collections import Counter
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(ROOT, "logs")
FMT = "%Y-%m-%d %H:%M:%S"


def rows(name, since):
    path = os.path.join(LOG_DIR, name)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if datetime.strptime(r["time"], FMT) >= since]


def to_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Turkish console = cp1254
    since = datetime.now() - timedelta(hours=args.hours)

    matches = rows("matches.csv", since)
    events = rows("events.csv", since)
    print(f"Window: {since.strftime(FMT)} → now ({args.hours:g} h)\n")

    if matches:
        first = datetime.strptime(matches[0]["time"], FMT)
        last = datetime.strptime(matches[-1]["time"], FMT)
        span_h = max((last - first).total_seconds() / 3600, 1e-9)
        loops = [float(m["loop_s"]) for m in matches]
        print(f"Matches: {len(matches)}  ({len(matches) / span_h:.1f}/h over {span_h:.1f} h of matches)")
        print(f"Loop time: avg {sum(loops) / len(loops):.0f}s, "
              f"min {min(loops):.0f}s, max {max(loops):.0f}s")
        print("Outcomes: " + ", ".join(f"{k} {v}" for k, v in Counter(m["outcome"] for m in matches).most_common()))
        pearls = [to_int(m["pearls"]) for m in matches if to_int(m["pearls"]) is not None]
        gold = [to_int(m["gold"]) for m in matches if to_int(m["gold"]) is not None]
        if len(pearls) >= 2:
            print(f"Pearls: {pearls[0]:,} → {pearls[-1]:,} ({pearls[-1] - pearls[0]:+,}, "
                  f"{(pearls[-1] - pearls[0]) / span_h:+,.0f}/h)")
        if len(gold) >= 2:
            print(f"Gold:   {gold[0]:,} → {gold[-1]:,} ({gold[-1] - gold[0]:+,})")
        # Longest gap between consecutive matches = longest stretch of not farming.
        times = [datetime.strptime(m["time"], FMT) for m in matches]
        gaps = sorted(((b - a).total_seconds(), a) for a, b in zip(times, times[1:]))
        if gaps:
            g, at = gaps[-1]
            print(f"Longest gap between matches: {g / 60:.1f} min (after {at.strftime(FMT)})")
    else:
        print("No matches in this window.")

    print()
    if events:
        print("Events:")
        for kind, n in Counter(e["kind"] for e in events).most_common():
            print(f"  {kind:22s} {n}")
        restarts = Counter(e["detail"] for e in events if e["kind"] == "game_restart")
        if restarts:
            print("Game restart reasons:")
            for reason, n in restarts.most_common():
                print(f"  {n:4d}  {reason}")
    else:
        print("No events in this window.")

    sup = os.path.join(LOG_DIR, "supervisor.log")
    if os.path.exists(sup):
        cutoff = time.mktime(since.timetuple())
        with open(sup, encoding="utf-8") as f:
            stops = [l.strip() for l in f
                     if "bot stopped" in l and time.mktime(time.strptime(l[:19], FMT)) >= cutoff]
        print(f"\nSupervisor restarts: {len(stops)}")
        for l in stops[-10:]:
            print(f"  {l}")


if __name__ == "__main__":
    main()

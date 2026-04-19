import time
import json
import os

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "player_data.json")

ACTIVE_DURATION = 900  # seconds a player is considered active (15 minutes)


class PlayerDB:
    def __init__(self):
        self._data = {}
        self._load()

    def _get(self, name):
        if name not in self._data:
            self._data[name] = {"active": False, "active_since": None}
        return self._data[name]

    def is_active(self, name):
        p = self._data.get(name)
        if not p or not p["active"]:
            return False
        if time.time() - p["active_since"] >= ACTIVE_DURATION:
            p["active"] = False
            p["active_since"] = None
            self._save()
            return False
        return True

    def mark_active(self, name):
        p = self._get(name)
        p["active"] = True
        p["active_since"] = time.time()
        self._save()

    def info_str(self, name):
        p = self._get(name)
        since = (time.strftime("%H:%M:%S", time.localtime(p["active_since"]))
                 if p["active_since"] else "never")
        return f"active: {p['active']} ({since})"

    def _save(self):
        with open(DB_PATH, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def _load(self):
        if not os.path.exists(DB_PATH):
            return
        try:
            with open(DB_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            # Keep only known fields — strips any legacy data
            for name, entry in raw.items():
                self._data[name] = {
                    "active":       entry.get("active", False),
                    "active_since": entry.get("active_since", None),
                }
            print(f"[DB] {len(self._data)} players loaded: {DB_PATH}")
        except Exception as e:
            print(f"[DB] Load failed: {e}")

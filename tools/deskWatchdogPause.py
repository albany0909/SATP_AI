# -*- coding: utf-8 -*-
"""Create or remove the desk watchdog pause flag. Does not start the desk."""
from pathlib import Path
import sys
import datetime as dt

REPO = Path(__file__).resolve().parents[1]
PAUSE = REPO / "config" / "data" / "desk_watchdog.pause"


def main(argv):
    cmd = (argv[1] if len(argv) > 1 else "status").strip().lower()
    if cmd in ("on", "pause", "set"):
        PAUSE.parent.mkdir(parents=True, exist_ok=True)
        PAUSE.write_text(
            "paused at %s KST\n"
            % dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            encoding="utf-8",
        )
        print("PAUSE_ON", PAUSE)
        return 0
    if cmd in ("off", "unpause", "clear"):
        if PAUSE.is_file():
            PAUSE.unlink()
        print("PAUSE_OFF", PAUSE)
        return 0
    if cmd in ("status", ""):
        print("PAUSE_ON" if PAUSE.is_file() else "PAUSE_OFF", PAUSE)
        return 0
    print("usage: deskWatchdogPause.py on|off|status")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

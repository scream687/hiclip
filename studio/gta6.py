"""GTA 6 auto-run: hands the whole job to the gta6-clipper skill when it is installed on this computer.

The skill (https://guide.nocodehack.io/gta6-clipper) is not part of hiclip and is not shipped with it. hiclip only
starts it, shows its log and serves the Shorts it made. Its three steps, run from hiclip's data folder:
  watch.py   finds new GTA 6 videos (Rockstar and big streamers) and fetches their audio and transcript
  nopick.py  picks the best moments without an AI
  batch.py   renders each one: <n>_<name>.mp4, a .txt with title, hashtags and credit, and a _cover.jpg
"""
import os
import re
import subprocess
import threading
import time
from pathlib import Path

from . import config

SKILL = Path(os.environ.get("HICLIP_GTA6_SKILL") or Path.home() / ".claude" / "skills" / "gta6-clipper")
WORK = config.DATA_DIR / "gta6"
OUT = WORK / "clips" / "out"
SAFE_NAME = re.compile(r"^[\w.-]+\.(mp4|jpg|txt)$")
_lock = threading.Lock()
state = {"running": False, "step": "", "log": [], "started": None, "finished": None, "error": None}


def python() -> Path:
    return SKILL / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def available() -> bool:
    return (SKILL / "batch.py").is_file() and python().is_file()


def steps(videos: int, shorts: int) -> list:
    py = str(python())
    return [("Finding new GTA 6 videos", [py, str(SKILL / "watch.py"), "GTA ?(6|VI)", str(videos)]),
            ("Picking the best moments", [py, str(SKILL / "nopick.py"), "clips", str(shorts)]),
            ("Rendering the Shorts", [py, str(SKILL / "batch.py"), "clips/picks.json", "clips/out"])]


def _log(line: str):
    state["log"] = (state["log"] + [line.rstrip()])[-200:]


def _run(videos: int, shorts: int):
    try:
        WORK.mkdir(parents=True, exist_ok=True)
        for label, cmd in steps(videos, shorts):
            state["step"] = label
            _log(f"== {label}")
            p = subprocess.Popen(cmd, cwd=WORK, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                 encoding="utf-8", errors="replace", env={**os.environ, "PYTHONUTF8": "1"})
            for line in p.stdout:
                _log(line)
            if p.wait() != 0:
                raise RuntimeError(f"{label} stopped (exit {p.returncode}). The log above says why.")
        state["step"] = "Done"
    except Exception as e:  # noqa: BLE001
        state["error"] = str(e)[:300]
        state["step"] = "Stopped"
    finally:
        state.update(running=False, finished=time.time())


def start(videos: int = 5, shorts: int = 10) -> dict:
    if not available():
        raise RuntimeError(f"The gta6-clipper skill is not installed at {SKILL} (or its .venv is missing).")
    with _lock:
        if state["running"]:
            raise RuntimeError("A GTA 6 run is already going.")
        state.update(running=True, step="Starting", log=[], started=time.time(), finished=None, error=None)
    threading.Thread(target=_run, args=(max(1, min(videos, 20)), max(1, min(shorts, 20))), daemon=True).start()
    return status()


def shorts() -> list:
    """The finished Shorts, newest first: video, cover and the post text the skill wrote."""
    out = []
    for mp4 in sorted(OUT.glob("*.mp4"), key=lambda f: f.stat().st_mtime, reverse=True):
        if mp4.stem.endswith("_b") or not SAFE_NAME.match(mp4.name):
            continue
        txt, cover = mp4.with_suffix(".txt"), mp4.with_name(mp4.stem + "_cover.jpg")
        out.append({"name": mp4.name, "cover": cover.name if cover.is_file() else None,
                    "text": txt.read_text(encoding="utf-8", errors="replace")[:3000] if txt.is_file() else ""})
    return out


def status() -> dict:
    return {"available": available(), "skill": str(SKILL), **{k: state[k] for k in ("running", "step", "started", "finished", "error")},
            "log": state["log"][-40:], "shorts": shorts()}


def file(name: str) -> Path:
    """A file the skill made, by plain name only (no paths), from the output folder."""
    if not SAFE_NAME.match(name or ""):
        raise FileNotFoundError(name)
    f = OUT / name
    if not f.is_file():
        raise FileNotFoundError(name)
    return f

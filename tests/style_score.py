"""Scores a finished clip out of 10 against the reference creator Shorts (docs/STYLE_REF.md).

usage: python tests/style_score.py CLIP.mp4 [CLIP.ass] [EDIT_INFO.json]
The .ass is the caption file written next to the clip; the info is the "edit" record render_clip returns."""
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio import edit  # noqa: E402


def probe(mp4):
    out = json.loads(subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(mp4)],
                                    capture_output=True, text=True).stdout)
    v = next(s for s in out["streams"] if s["codec_type"] == "video")
    a, b = v["avg_frame_rate"].split("/")
    r1, r2 = v["r_frame_rate"].split("/")
    return int(v["width"]), int(v["height"]), float(a) / float(b), float(r1) / float(r2), float(out["format"]["duration"])


def loudness(mp4):
    err = subprocess.run(["ffmpeg", "-nostats", "-i", str(mp4), "-af", "loudnorm=print_format=json", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    m = json.loads(re.findall(r"\{[^{}]*\}", err, re.S)[-1])
    return float(m["input_i"]), float(m["input_tp"])


def visual_changes(mp4):
    err = subprocess.run(["ffmpeg", "-nostats", "-i", str(mp4), "-vf", "scdet=threshold=10", "-an", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    return [float(x) for x in re.findall(r"lavfi\.scd\.time: ([\d.]+)", err)]


def ass_times(ass_text, style):
    out = []
    for line in ass_text.splitlines():
        if line.startswith("Dialogue:") and f",{style}," in line:
            p = line.split(",")
            to_s = lambda t: sum(float(x) * k for x, k in zip(t.split(":"), (3600, 60, 1)))   # noqa: E731
            out.append((to_s(p[1]), to_s(p[2])))
    return out


def score(mp4, ass_text="", info=None):
    info = info or {}
    w, h, fps, rfps, dur = probe(mp4)
    lufs, tp = loudness(mp4)
    db = edit.audio_db(mp4)
    hooks = ass_times(ass_text, "Hook") + ass_times(ass_text, "HookRed")
    caps = ass_times(ass_text, "Cap")
    events = sorted(set(visual_changes(mp4)) | set(info.get("zooms", [])) | ({info["payoff"]} if info.get("finale") else set()))
    marks = [0.0] + events + [dur]
    longest_still = max(b - a for a, b in zip(marks, marks[1:]))
    quiet, run = 0.0, 0
    floor = float(max(db.max() - 45, -99)) if len(db) else -99
    for v in db:
        run = run + 1 if v < floor else 0
        quiet = max(quiet, run / 20)
    sounds = set(info.get("sounds", []))
    checks = [
        ("heading on screen from the first second", any(a <= 0.2 and b >= min(1.0, dur) for a, b in hooks)),
        ("word captions on screen", len(caps) >= 3 or not ass_text),
        ("something changes on screen at least every 8 s", longest_still <= 8.0),
        ("no dead air over 0.6 s", quiet <= 0.6),
        ("meme sounds on the edit", len(sounds & {"boom", "glitch", "static"}) + info.get("clip_sfx", 0) >= 2),
        ("music bed under the voice", "music" in sounds),
        ("the payoff gets an emphasis (ending, zoom or flash)", bool(info.get("finale")) or info.get("payoff") in info.get("zooms", [])),
        ("ending fits: reaction for dark moments, else ends on the payoff",
         bool(info.get("finale")) or (info.get("dark") is False and info.get("payoff") in info.get("zooms", []))),
        ("loudness -14 +-1 LUFS, peak at or under -1 dB", abs(lufs + 14) <= 1.0 and tp <= -1.0),
        ("1080x1920 at a constant 30 or 60 fps", (w, h) == (1080, 1920) and abs(fps - rfps) < 0.5 and round(fps) in (30, 60)),
    ]
    return checks, {"lufs": lufs, "tp": tp, "fps": fps, "longest_still": round(longest_still, 2), "dead_air": quiet, "dur": dur}


if __name__ == "__main__":
    mp4 = Path(sys.argv[1])
    ass = Path(sys.argv[2]).read_text(encoding="utf-8") if len(sys.argv) > 2 else (mp4.with_suffix(".ass").read_text(encoding="utf-8") if mp4.with_suffix(".ass").exists() else "")
    info = json.loads(Path(sys.argv[3]).read_text()) if len(sys.argv) > 3 else {}
    checks, m = score(mp4, ass, info)
    for name, ok in checks:
        print(("PASS " if ok else "MISS ") + name)
    print(f"SCORE {sum(ok for _, ok in checks)}/10  {m}")

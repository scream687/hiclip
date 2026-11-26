"""The creator edit pass: switches, payoff, punch-ins, the reaction ending and loudness.

Runs against the code directly, offline (no meme sounds or emoji are fetched)."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ["HICLIP_DATA"] = tempfile.mkdtemp(prefix="pc-edit-")
sys.path.insert(0, ".")
import numpy as np  # noqa: E402

from studio import edit  # noqa: E402

fails = []


def check(n, ok):
    print(("PASS " if ok else "FAIL ") + n)
    ok or fails.append(n)


check("unknown edit switches are dropped", edit.fx_options({"punch": False, "evil": 1}) == dict(edit.FX_DEFAULT, punch=False))
check("edits off turns every switch off", not any(edit.fx_options(False).values()))

db = np.full(20 * 20, -40.0)
db[16 * 20: 17 * 20] = -5.0                   # a loud second at 16 s in a 20 s clip
p = edit.payoff(db, 20.0)
check("the payoff is the loud moment, landing as it hits", 15.4 <= p <= 16.0)
check("the payoff is never in the first 2 s", edit.payoff(np.r_[np.full(20, -1.0), np.full(380, -40.0)], 20.0) >= 2.0)

z = edit.fill_gaps(np.random.default_rng(1).normal(-30, 3, 400), [], 20.0)
marks = [0.0] + z + [20.0]
check("nothing is left still for longer than the references hold a shot", max(b - a for a, b in zip(marks, marks[1:])) <= edit.MAX_STILL)

words = [{"w": "he", "s": 10.0, "e": 10.3}, {"w": "really", "s": 10.4, "e": 12.0}, {"w": "jumped", "s": 12.1, "e": 15.2},
         {"w": "out.", "s": 15.3, "e": 15.6}]
stops = edit.sentence_stops(words, 0.0)
check("the reaction finishes the sentence being said", abs(edit.react_len(30.0, 11.0, stops) - (15.6 - 11.0 + 0.25)) < 0.01)
check("the reaction is the usual length when nobody is mid-sentence", edit.react_len(30.0, 20.0, stops) == edit.REACT_MAX)

check("the reaction ending is kept for creepy clips", edit.is_dark("KAI CENAT GETS JUMPSCARED") and edit.is_dark("creepy guy follows him"))
check("an ordinary clip gets no reaction ending", not edit.is_dark("SPEED FINDS KFC IN GTA 6")
      and not edit.plan(20.0, db, edit.fx_options(None), False, dark=False)["finale"])
work = Path(tempfile.mkdtemp(prefix="pc-edit-work-"))
expr = edit.zoom_expr([1.0, 2.5], hit=2.5)
r = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1080x1920:r=30:d=4", "-vf", expr, "-f", "null", "-"],
                   capture_output=True, text=True)
check("the punch-in zoom is a valid ffmpeg filter", r.returncode == 0)

# a quiet 10 s clip with a loud bang at 7 s, rendered through the whole edit
src = work / "c.mp4"
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1080x1920:r=30:d=10",
                "-f", "lavfi", "-i", "anoisesrc=d=10:c=pink:a=0.12", "-f", "lavfi", "-i", "sine=f=880:d=1,volume=0.9,adelay=7000|7000",
                "-filter_complex", "[1:a][2:a]amix=inputs=2:normalize=0[a]", "-map", "0:v", "-map", "[a]",
                "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(src)], check=True)
edit._sound = lambda *a, **k: None            # offline: no MyInstants
edit.make_emoji = lambda *a, **k: False
info = edit.finish(work, "c", 30, edit.fx_options(None), cam=(0, 400, 300, 170))
probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration,avg_frame_rate", "-of", "csv=p=0", str(src)],
                       capture_output=True, text=True).stdout.split()
got = {x.split(",")[0]: x.split(",") for x in probe}
vdur, adur = float(got["video"][-1]), float(got["audio"][-1])
check("the reaction ending was added at the bang", info["finale"] and 6.0 <= info["payoff"] <= 7.2)
check("the clip grows by the replay and outro", abs(vdur - info["length"]) < 0.15)
check("picture and sound stay the same length", abs(vdur - adur) < 0.15)
err = subprocess.run(["ffmpeg", "-nostats", "-i", str(src), "-af", "ebur128", "-f", "null", "-"], capture_output=True, text=True).stderr
lufs = float(err.rsplit("I:", 1)[1].split("LUFS")[0])
check(f"two-pass levelling lands at -14 LUFS (got {lufs})", abs(lufs + 14) <= 1.0)

print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)

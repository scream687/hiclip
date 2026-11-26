"""Voice and picture stay in step when a clip starts between keyframes of a stream piece.

Runs against the code directly, with its own empty data folder."""
import os, shutil, subprocess, sys, tempfile
os.environ["HICLIP_DATA"] = tempfile.mkdtemp(prefix="pc-sync-")
sys.path.insert(0, ".")
from pathlib import Path
from studio import render
fails = []
def check(n, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else f"   got {got!r}")); ok or fails.append(n)

work = Path(tempfile.mkdtemp(prefix="pc-sync-work-"))
try:
    # A live-stream piece: MPEG-TS, a keyframe every 2 s, timestamps not starting at 0.
    src = work / "piece.ts"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=20",
                    "-f", "lavfi", "-i", "sine=d=20", "-c:v", "libx264", "-g", "60", "-preset", "ultrafast",
                    "-c:a", "aac", "-output_ts_offset", "1.4", "-f", "mpegts", str(src)], check=True)
    out = render.render_clip(src, work, "c", {"start": 7.3, "end": 15.3, "hook": "", "layout": "crop", "captions": False, "fx": False},
                             [], 640, 360)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,start_time,duration",
                            "-of", "csv=p=0", str(work / out["file"])], capture_output=True, text=True).stdout.split()
    got = {p.split(",")[0]: (float(p.split(",")[1]), float(p.split(",")[2])) for p in probe}
    check("the video starts with the audio, not at the next keyframe", got["video"][0], 0.0)
    check("both tracks are the clip's length", abs(got["video"][1] - 8) < 0.1 and abs(got["audio"][1] - 8) < 0.1, True)
    # with the creator edits on (zooms, the reaction ending, music) the clip grows, but both tracks still match
    out = render.render_clip(src, work, "e", {"start": 7.3, "end": 15.3, "hook": "", "layout": "crop", "captions": False},
                             [], 640, 360)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,start_time,duration",
                            "-of", "csv=p=0", str(work / out["file"])], capture_output=True, text=True).stdout.split()
    got = {p.split(",")[0]: (float(p.split(",")[1]), float(p.split(",")[2])) for p in probe}
    check("with edits, picture and sound are the same length", abs(got["video"][1] - got["audio"][1]) < 0.1, True)
finally:
    shutil.rmtree(work, ignore_errors=True)

print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)

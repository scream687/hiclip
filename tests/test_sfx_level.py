"""Meme sounds sit under the voice, and hype sounds are skipped where the streamer already shouts (26 Nov)."""
import math
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")
from studio import sfx  # noqa: E402

fails = []


def check(n, ok):
    print(("PASS " if ok else "FAIL ") + n)
    ok or fails.append(n)


d = Path(tempfile.mkdtemp(prefix="pc-sfxlvl-"))
mk = lambda name, src: subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", src, str(d / name)], check=True)  # noqa: E731
mk("horn.wav", "sine=f=600:d=1.5,volume=0.95")                                   # a meme sound mastered hot
mk("voice.wav", "aevalsrc='0.05*sin(2*PI*220*t)*if(between(t,6,9),8,1)':d=12")   # quiet talk, shouting 6-9 s
voice = sfx.loudness(d / "voice.wav")
g = sfx.gain_for(d / "horn.wav", voice, 1.0)
under = voice - (sfx.loudness(d / "horn.wav") + 20 * math.log10(g))
check(f"a hot meme sound is brought {sfx.UNDER_VOICE_DB:.0f} dB under the voice (got {under:.1f})", abs(under - sfx.UNDER_VOICE_DB) < 0.5)
items = [{"time": 7.0, "name": "MLG Airhorn", "url": "x/airhorn.mp3"}, {"time": 2.0, "name": "MLG Airhorn", "url": "x/airhorn.mp3"},
         {"time": 7.5, "name": "Vine Boom", "url": "x/vine-boom.mp3"}, {"time": 0.1, "name": "Vine Boom", "url": "x/vine-boom.mp3"}]
kept = sfx.calm_spots(items, d / "voice.wav", 0.0, 12.0)
check("no air horn while the streamer is shouting", not any(x["time"] == 7.0 for x in kept))
check("an air horn in a calm stretch stays", any(x["time"] == 2.0 for x in kept))
check("other sounds are kept at the shout", any(x["time"] == 7.5 for x in kept))
check("nothing in the first half second", not any(x["time"] < 0.5 for x in kept))
print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)

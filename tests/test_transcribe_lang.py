"""Hindi and Hinglish speech goes to Whisper; English stays on the English model; Whisper failing never stops a video.

Runs against the code directly, with its own empty data folder. Whisper itself is replaced by a stand-in."""
import os, sys, tempfile, types
os.environ["HICLIP_DATA"] = tempfile.mkdtemp(prefix="pc-lang-")
sys.path.insert(0, ".")
import numpy as np
from studio import transcribe
fails = []
def check(n, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else f"   got {got!r}")); ok or fails.append(n)

audio = np.zeros(16000 * 3, dtype=np.int16)
seen = {}

def fake_whisper(samples, **kw):
    seen.update(kw, dtype=str(samples.dtype))
    return {"segments": [{"words": [{"word": " नमस्ते", "start": 0.12, "end": 0.5}, {"word": " ", "start": 0.5, "end": 0.6},
                                    {"word": " game", "start": 0.6, "end": 0.9}]}]}

sys.modules["mlx_whisper"] = types.SimpleNamespace(transcribe=fake_whisper)
got = transcribe.transcribe(audio, lang="hi")
check("Hindi speech goes to Whisper, set to Hindi", seen.get("language"), "hi")
check("Whisper is asked for word timings", seen.get("word_timestamps"), True)
check("Whisper gets the samples as numbers between -1 and 1", seen.get("dtype"), "float32")
check("its words come back in hiclip's shape, blanks left out",
      got, [{"w": "नमस्ते", "s": 0.12, "e": 0.5}, {"w": "game", "s": 0.6, "e": 0.9}])

seen.clear()
transcribe.transcribe(audio, lang="")   # silent audio: the English model skips it without loading anything
check("English speech never goes to Whisper", seen, {})

def broken(samples, **kw):
    raise RuntimeError("out of memory")
sys.modules["mlx_whisper"] = types.SimpleNamespace(transcribe=broken)
check("Whisper failing falls back to the English model, not an error", transcribe.transcribe(audio, lang="hi"), [])

print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)

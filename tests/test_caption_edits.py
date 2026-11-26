"""A creator's own caption words survive re-making the clip, stay on the speech, and are not redone by the AI."""
import os
import sys
import tempfile

os.environ["HICLIP_DATA"] = tempfile.mkdtemp(prefix="pc-capedit-")
sys.path.insert(0, ".")
from studio import jobs  # noqa: E402

fails = []


def check(name, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   got {got!r}"))
    if not ok:
        fails.append(name)


# what the transcript heard, as the captions had it: "so I lost my merch" said from 10 s to 13 s
HEARD = [{"w": w, "s": 10.0 + i * 0.6, "e": 10.5 + i * 0.6} for i, w in enumerate("so I lost my merch".split())]
clip = {"id": "c1", "start": 9.0, "end": 16.0, "hook": "H", "style": "bounce", "layout": "blur", "captions": True,
        "fixed_words": [dict(w) for w in HEARD], "fixed_for": [9.0, 16.0, "en2"]}
job = {"id": "j1", "clips": [clip], "options": {}, "title": "t"}
jobs._jobs["j1"] = job
jobs.save = lambda j: None
jobs._q.put = lambda item: None
ai_called = []
jobs.brain.english_captions = lambda raw, *a, **k: ai_called.append(1) or raw


def remake(**changes):
    jobs.request_render("j1", "c1", dict({"start": clip["start"], "end": clip["end"]}, **changes))
    return [w["w"] for w in jobs._caption_words(job, clip, HEARD, 0.0)]


check("saving with the captions unchanged changes nothing", remake(caption_text="so I lost my merch"),
      ["so", "I", "lost", "my", "merch"])
check("an edit is what the clip is made with", remake(caption_text="so I LOST my MERCH!"),
      ["so", "I", "LOST", "my", "MERCH!"])
check("and it is not sent to the AI to be redone", ai_called, [])
check("re-making again keeps the edit", remake(caption_text="so I LOST my MERCH!"), ["so", "I", "LOST", "my", "MERCH!"])
check("edited words stay where they were said", [w["s"] for w in clip["fixed_words"]], [w["s"] for w in HEARD])
check("fewer words still cover the same speech", (lambda w: (w[0]["s"], w[-1]["e"]))(
    jobs._place_words(["lost", "merch"], HEARD, 9.0, 16.0)), (10.0, HEARD[-1]["e"]))
check("trimming the clip keeps the edit, without the words trimmed away", remake(start=11.0, caption_text="so I LOST my MERCH!"),
      ["I", "LOST", "my", "MERCH!"])

print(f"{len(fails)} FAILED" if fails else "ALL PASSED")
sys.exit(1 if fails else 0)

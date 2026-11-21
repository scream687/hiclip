"""Gaming streams say which game they are. Knowing it changes the rules the AI reads and the tags every
post carries.

Runs against the code directly, with its own empty data folder."""
import os, sys, tempfile
os.environ["HICLIP_DATA"] = tempfile.mkdtemp(prefix="pc-games-")
sys.path.insert(0, ".")
from studio import brain, games, live
fails = []
def check(n, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else f"   got {got!r}")); ok or fails.append(n)


def _session(title, game=""):
    """A live session that is not recording anything: only the game reading is wanted."""
    d = tempfile.mkdtemp(prefix="pc-games-live-")
    s = live.Session({"title": title, "uploader": "someone", "dir": d, "creator": "", "ai": {},
                      "source": {"url": "", "name": title}},
                     {"game": game})
    return s

# ---- the game is read from the title
check("GTA 6 in a title is GTA VI", games.detect("CLICK THIS STREAM IF YOU WANT GTA 6!")[0], "Grand Theft Auto VI")
check("spelled out is GTA VI", games.detect("grand theft auto vi lets go")[0], "Grand Theft Auto VI")
check("GTA 5 is GTA V", games.detect("GTA V RP tonight")[0], "Grand Theft Auto V")
check("gta5 and gta online are GTA V", games.detect("gta5 gta online")[0], "Grand Theft Auto V")
check("another game is found", games.detect("Fortnite ranked")[0], "Fortnite")
check("a chat stream has no game", games.detect("just chatting with friends")[0], None)
check("GTA 60 seconds is not a game", games.detect("GTA 60 seconds of gameplay")[0], None)

# ---- the tags belong on the post
check("a GTA VI post carries the GTA VI tags", "GTA6" in games.tags_of("gta 6"), True)
check("the tags do not double up", games.tags_of("GTA6Launch") == games.tags_of("gta 6"), True)
check("an unknown game has no tags", games.tags_of("Hades II"), ["HadesII"])
# a hashtag cannot contain a space, so a game tag with one in it would arrive broken
tagged = brain._tidy({"hashtags": []}, 0, 1, game="cyberpunk 2077")["hashtags"]
check("every tag a post carries is one word", all(t and " " not in t and "#" not in t for t in tagged), True)

# ---- the tags go on every post, whatever the AI wrote
tidy = brain._tidy({"start": 1, "end": 9, "score": 80, "category": "clutch", "hook": "one vs three",
                     "title": "He got them all", "description": "three of them", "hashtags": ["Gaming", "FPS"],
                     "highlight_words": ["three"]}, 1, 9, game="gta 6")
check("the game's tags are added to the post", "GTA6" in tidy["hashtags"], True)
check("the AI's own tags are kept", "Gaming" in tidy["hashtags"] and "FPS" in tidy["hashtags"], True)
check("the game's tags are not written twice", tidy["hashtags"].count("GTA6"), 1)
plain = brain._tidy({"start": 1, "end": 9, "score": 80, "category": "clutch", "hook": "x", "title": "t",
                     "description": "d", "hashtags": ["Gaming"], "highlight_words": []}, 1, 9)
check("a stream with no game gets no game tags", "GTA6" in plain["hashtags"], False)

# ---- the AI is told which game it is looking at
rules = brain._game_rules("gta 6")
check("the game is named in the extra rules", "Grand Theft Auto VI" in rules, True)
check("the tags are named in the extra rules", "GTA6" in rules, True)
check("a stream with no game gets no extra rules", brain._game_rules(""), "")

# ---- a live session finds its game in the stream's title
check("a live session finds the game in its title", _session("CLICK THIS STREAM IF YOU WANT GTA 6!")._game(),
      "Grand Theft Auto VI")
check("the creator's own setting wins over the title", _session("just chatting", "gta 6")._game(), "Grand Theft Auto VI")
check("a live stream with no game in it says so", _session("morning everyone")._game(), "")

# ---- a short name that means something else is not a game
check("'rust' is not read as the game Rust", games.detect("Rust removal and restoration")[0], None)
check("'the finals' is not read as a game", games.detect("The finals of the world cup")[0], None)

# ---- chat hype is genuine hype, and one person spamming it is not a crowd
from studio.live import hype_label
check("W in chat is hype", hype_label("WWWW"), "W")
check("a question in chat is not hype", hype_label("what game is this"), None)
check("'no way out' is not NO WAY", hype_label("IT'S DONE NO WAY OUT"), None)
s = _session("morning everyone")
for _ in range(5):
    s._count_hype(5, "W", "spammer")
s._count_hype(5, "GG", "someone else")
check("one person spamming W counts once a second", s.hype[5], 2)

def _busy_chat(hyped: bool):
    """Three minutes of steady chat, then 10 s where chat triples: hype, or just people backseating."""
    s = _session("morning everyone")
    s.db = [-30.0] * 200                               # loudness stays the same throughout
    s.chat_on = True
    for t in range(200):
        for i in range(4 if t < 190 else 12):
            s.chat[t] += 1
            s._count_hype(t, ("LETS GOOO" if hyped and t >= 190 else "W" if i == 0 else "reload the game"), f"p{i}")
    s._watch_spikes()
    return s.spike
check("a burst of backseating does not clip", _busy_chat(False), None)
check("a burst of real hype is a jump", (_busy_chat(True) or {}).get("why", ""), lambda w: "chat hype" in w)
check("chat erupting is clipped without the words judge", (_busy_chat(True) or {}).get("crowd"), "LET'S GO")

s = _session("morning everyone")
s.job["clips"] = []
s.settings.update(max_len=60, min_gap_min=2, live_per_hour=0)
s._crowd_clip({"t": 300.0, "why": "chat hype +250%", "crowd": "W, GG"})
check("a crowd clip waits for the stretch before the burst and just after it", (s.manual[0]["start"], s.manual[0]["end"]), (210.0, 315.0))
s._crowd_clip({"t": 340.0, "why": "chat hype +250%", "crowd": "W"})
check("a second eruption right after is not clipped twice", len(s.manual), 1)
check("and says why", s.verdicts[-1]["result"], "Too close to the last clip")

# ---- the clip chat erupted over is cut on whole sentences, with its setup, at the length the story needs
def _words(*sentences):
    """Words spoken from t=200, one a second, a short pause after each sentence."""
    out, t = [], 200.0
    for sent in sentences:
        ws = sent.split()
        for i, w in enumerate(ws):
            out.append({"w": w + ("." if i == len(ws) - 1 else ""), "s": t, "e": t + 0.8})
            t += 1.0
        t += 1.5
    return out

def _crowd(words, judge):
    s = _session("morning everyone")
    s.job.update(clips=[], uploader="Kai Cenat", title="GTA 6")
    s.settings.update(min_len=15, max_len=60)
    s.words, s.samples, s.t_done = words, 400 * live.SR, 400.0
    live.brain.judge_transcript = judge
    live.brain.describe = lambda *a, **k: {"score": 0, "category": "other", "hook": "HOOK", "title": "t",
                                           "description": "", "hashtags": [], "highlight_words": []}
    return s._crowd_pick({"start": 168.0, "end": 273.0, "crowd_t": 258.0, "why": "Chat erupted (W)"})

story = _words("so I bought my own merch in the game today", "and I put it in the wardrobe last night",
               "now it is gone I cannot find it anywhere chat", "wait wait is that it right there",
               "oh my god there it is the yellow merch", "that is a banger dude what a fit", "anyway where was I going")
picked = {"start": 211.5, "end": 260.3, "score": 84, "hook": "MERCH", "reason": "Finds his merch."}  # setup to "banger"
c = _crowd(story, lambda *a, **k: ([dict(picked)], 10))
check("when the AI finds the story, that is the clip", (c["start"], c["end"], c["score"]), (211.5, 260.3, 84))
c = _crowd(story, lambda *a, **k: ([dict(picked, start=210.0, end=240.0)], 10))
ends = set(round(w["e"], 1) for w in story if w["w"].endswith("."))
check("a pick that misses the burst is not used: the fallback ends on a whole sentence", round(c["end"] - 0.35, 1) in ends
      or any(abs(c["end"] - e) < 0.4 for e in ends), True)
def _broken(*a, **k): raise RuntimeError("no AI")
c = _crowd(story, _broken)
check("with no AI the clip still ends on a whole sentence", any(abs(c["end"] - e) < 0.4 for e in ends), True)
check("and starts at the start of a line", any(abs(c["start"] + 0.15 - w["s"]) < 0.01 for w in story), True)

# ---- a slow AI answer must not stop the recording
import time as _time
s = _session("morning everyone")
s._ai_check = lambda *a, **k: _time.sleep(2)      # a model that takes its time
t0 = _time.time()
s._ask_ai()
check("asking the AI does not hold up the recording", _time.time() - t0 < 0.5, True)
t0 = _time.time()
s._ask_ai()                                       # a second ask while the first is still out
check("a second ask does not pile up", _time.time() - t0 < 0.5 and s.ai_busy > 0, True)

# ---- a hand-picked clip is made even when the AI cannot answer
from unittest.mock import patch
added = []
s = _session("morning everyone")
s.words = [{"w": "hello", "s": 0.5, "e": 0.9, "p": 0}]
s.t_done, s.samples = 10.0, 10 * 16000
s._add_clip = added.append
s.manual.append({"start": 0.0, "end": 2.0})
with patch.object(brain, "describe", side_effect=RuntimeError("The AI did not answer")):
    s._manual_step()
check("a hand-picked clip is made when the AI fails", len(added), 1)
check("it is made without a hook rather than not at all", added and added[0]["hook"], "")
check("the failure is remembered", "AI did not answer" in (s.job["ai"]["last_error"] or ""), True)

# ---- an eight-hour stream must not keep a name for every second of it
users, kept = {}, 0
for t in range(0, 3000):
    users.setdefault(t, set()).add(f"person{t}")
    kept = live._trim_names(users, t, kept)
check("the chat names of a whole stream are not kept", len(users) <= 700, True)
check("the last few minutes of them still are", 2999 in users and min(users) >= 2999 - 660, True)
check("the dict is not walked on every message", live._trim_names(users, 3005, 2999), 2999)

print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)

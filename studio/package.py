"""Title check: a post title and the clip's big heading text linted as one pairing, so the best of the AI's
titles goes first. Ported from title.py in Jake Schincariol's youtube-agent-skill (/yt-package, MIT licence).

The heading over the clip plays the thumbnail's part: it should say what the title doesn't."""
import re

DESKTOP, MOBILE, HARD = 60, 40, 100
VAGUE = {"amazing", "incredible", "insane", "crazy", "huge", "massive", "ultimate", "best", "powerful",
         "secret", "revolutionary", "mindblowing", "epic", "perfect", "complete", "everything"}
STOP = {"the", "a", "an", "of", "for", "to", "in", "on", "and", "or", "is", "are", "with", "your", "you", "my", "i",
        "this", "that", "it", "how", "what", "why", "he", "his", "at", "by"}


def _words(t: str) -> list:
    return re.findall(r"[a-z0-9']+", t.lower())


def check(title: str, heading: str = "") -> dict:
    """{'score': 0-100, 'issues': [(kind, why)], 'good': [why]} for one title."""
    t = (title or "").strip()
    n = len(t)
    issues, good = [], []
    if n > HARD:
        issues.append(("length", f"{n} characters: YouTube's limit is {HARD}"))
    elif n > DESKTOP:
        issues.append(("length", f"{n} characters: desktop search cuts near {DESKTOP}"))
    else:
        good.append(f"{n} characters, inside the desktop cut")
    if n > MOBILE:
        head = t[:MOBILE].rsplit(" ", 1)[0]
        good_head = [w for w in _words(head) if w not in STOP]
        if len(good_head) < 2:
            issues.append(("mobile", f'a phone feed shows about "{head}...": the subject is cut off'))
    caps = [w for w in t.split() if len(w) > 2 and w.isupper() and not w.isdigit()]
    if len(caps) > 2:
        issues.append(("shouting", f"{len(caps)} all-caps words: two is the ceiling before it reads as spam"))
    vague = sorted({w for w in _words(t) if w in VAGUE})
    if vague:
        issues.append(("vague", f"{', '.join(vague)}: a name, number or place says more"))
    if not re.search(r"\d", t) and not re.search(r"\b[A-Z][a-z]+\b", t[1:]):
        issues.append(("no-name", "no name or number: the most reliable single fix"))
    if not [w for w in _words(t)[:3] if w not in STOP]:
        issues.append(("front-load", "the first three words are all filler: move the subject forward"))
    if heading:
        shared = (set(_words(t)) - STOP) & (set(_words(heading)) - STOP) - {"gta", "6"}
        if len(shared) >= 2:
            issues.append(("duplicate", f"the heading repeats the title ({', '.join(sorted(shared))}): "
                                        "it should say what the title doesn't"))
    score = max(0, min(100, 100 - 14 * len(issues) + 4 * len(good)))
    return {"score": score, "issues": issues, "good": good}


def best_first(title: str, alts: list, heading: str = "") -> tuple:
    """(title, alts, swapped): the highest-scoring title first. The AI's own pick stays on a tie."""
    pool = [t for t in [title, *alts] if t]
    if not pool:
        return title, alts, False
    scores = [check(t, heading)["score"] for t in pool]
    best = max(range(len(pool)), key=lambda i: (scores[i], -i))
    if best == 0 or scores[best] <= scores[0]:
        return title, alts, False
    return pool[best], [t for i, t in enumerate(pool) if i != best][:4], True

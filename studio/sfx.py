"""Meme sounds and sound effects from MyInstants (https://www.myinstants.com/).
Searches, curates, caches, and automatically places meme audio/SFX in clips."""
import hashlib
import html
import json
import logging
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

from . import config

log = logging.getLogger("studio.sfx")

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
MYINSTANTS_BASE = "https://www.myinstants.com"

# Curated library of iconic meme sounds and sound effects from MyInstants
CURATED = [
    {
        "id": "vine-boom",
        "name": "Vine Boom",
        "url": "https://www.myinstants.com/media/sounds/vine-boom.mp3",
        "category": "punchline",
        "tags": ["vine boom", "boom", "punchline", "meme", "dramatic", "thud"],
        "default_vol": 0.7,
    },
    {
        "id": "bruh",
        "name": "Bruh Sound Effect",
        "url": "https://www.myinstants.com/media/sounds/movie_1.mp3",
        "category": "reaction",
        "tags": ["bruh", "fail", "meme", "reaction", "disbelief"],
        "default_vol": 0.75,
    },
    {
        "id": "emotional-damage",
        "name": "Emotional Damage",
        "url": "https://www.myinstants.com/media/sounds/emotional-damage-meme.mp3",
        "category": "punchline",
        "tags": ["emotional damage", "roast", "funny", "meme"],
        "default_vol": 0.75,
    },
    {
        "id": "dun-dun-dun",
        "name": "Dramatic Dun Dun Dun",
        "url": "https://www.myinstants.com/media/sounds/dun-dun-dun.mp3",
        "category": "shock",
        "tags": ["dun dun dun", "dramatic", "shock", "suspense", "reveal"],
        "default_vol": 0.7,
    },
    {
        "id": "metal-pipe",
        "name": "Metal Pipe Falling",
        "url": "https://www.myinstants.com/media/sounds/metal-pipe-falling-sound.mp3",
        "category": "shock",
        "tags": ["metal pipe", "loud", "meme", "funny", "fail"],
        "default_vol": 0.65,
    },
    {
        "id": "airhorn",
        "name": "MLG Airhorn",
        "url": "https://www.myinstants.com/media/sounds/airhorn.mp3",
        "category": "hype",
        "tags": ["airhorn", "mlg", "hype", "victory", "clutch", "cheer"],
        "default_vol": 0.65,
    },
    {
        "id": "anime-wow",
        "name": "Anime Wow",
        "url": "https://www.myinstants.com/media/sounds/anime-wow-sound-effect.mp3",
        "category": "hype",
        "tags": ["anime wow", "wow", "cute", "reaction", "victory"],
        "default_vol": 0.7,
    },
    {
        "id": "whoosh",
        "name": "Whoosh Transition",
        "url": "https://www.myinstants.com/media/sounds/whoosh-sound-effect.mp3",
        "category": "transition",
        "tags": ["whoosh", "swoosh", "transition", "fast", "hook", "intro"],
        "default_vol": 0.45,
    },
    {
        "id": "pop",
        "name": "Pop Sound",
        "url": "https://www.myinstants.com/media/sounds/pop-sound-effect.mp3",
        "category": "transition",
        "tags": ["pop", "bubble", "click", "intro", "transition"],
        "default_vol": 0.5,
    },
    {
        "id": "fart-reverb",
        "name": "Fart with Reverb",
        "url": "https://www.myinstants.com/media/sounds/fart-with-reverb.mp3",
        "category": "funny",
        "tags": ["fart", "funny", "goofy", "meme", "reverb"],
        "default_vol": 0.65,
    },
    {
        "id": "record-scratch",
        "name": "Record Scratch",
        "url": "https://www.myinstants.com/media/sounds/record-scratch_1.mp3",
        "category": "funny",
        "tags": ["record scratch", "freeze", "pause", "fail", "wait"],
        "default_vol": 0.6,
    },
    {
        "id": "ba-dum-tss",
        "name": "Ba Dum Tss",
        "url": "https://www.myinstants.com/media/sounds/ba-dum-tss.mp3",
        "category": "punchline",
        "tags": ["ba dum tss", "joke", "punchline", "rimshot", "comedy"],
        "default_vol": 0.7,
    },
    {
        "id": "spongebob-fail",
        "name": "Sad SpongeBob / Fail",
        "url": "https://www.myinstants.com/media/sounds/spongebob-fail.mp3",
        "category": "fail",
        "tags": ["spongebob", "sad", "fail", "trombone", "lose"],
        "default_vol": 0.7,
    },
    {
        "id": "what-the-hell",
        "name": "What The Hell Meme",
        "url": "https://www.myinstants.com/media/sounds/what-the-hell-meme.mp3",
        "category": "shock",
        "tags": ["what the hell", "shock", "meme", "reaction", "rage"],
        "default_vol": 0.7,
    },
    {
        "id": "huh-cat",
        "name": "Huh? Cat Meme",
        "url": "https://www.myinstants.com/media/sounds/huh.mp3",
        "category": "reaction",
        "tags": ["huh", "cat", "confusion", "question", "what"],
        "default_vol": 0.75,
    },
    {
        "id": "directed-by",
        "name": "Directed by Robert B. Weide",
        "url": "https://www.myinstants.com/media/sounds/directed-by-robert-b_c2whp.mp3",
        "category": "punchline",
        "tags": ["directed by", "robert b weide", "curb", "credits", "end", "fail"],
        "default_vol": 0.65,
    },
    {
        "id": "bell-ding",
        "name": "Bell Ding",
        "url": "https://www.myinstants.com/media/sounds/ding-sound-effect.mp3",
        "category": "transition",
        "tags": ["ding", "bell", "correct", "idea", "alert"],
        "default_vol": 0.55,
    },
    {
        "id": "cricket",
        "name": "Awkward Crickets",
        "url": "https://www.myinstants.com/media/sounds/cricket-sound-effect.mp3",
        "category": "funny",
        "tags": ["cricket", "awkward", "silence", "fail"],
        "default_vol": 0.55,
    },
    {
        "id": "are-baap-re",
        "name": "Are Baap Re (Indian Meme)",
        "url": "https://www.myinstants.com/media/sounds/are-baap-re-yaad-aya.mp3",
        "category": "funny",
        "tags": ["are baap re", "hindi", "hinglish", "meme", "shock"],
        "default_vol": 0.7,
    },
]

_PARSER_RE = re.compile(
    r'<div class="instant">.*?'
    r'<button\s+class="small-button"\s+onclick="play\(\'([^\']+)\',\s*\'([^\']*)\',\s*\'([^\']*)\'\)"[^>]*>.*?'
    r'<a\s+href="([^"]+)"\s+class="instant-link[^"]*">([^<]+)</a>',
    re.DOTALL
)


def _fetch_html(url: str, timeout: float = 6.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "ignore")
    except Exception as e:
        log.warning("Failed to fetch %s: %s", url, e)
        return ""


def search(query: str, limit: int = 18) -> list:
    """Searches MyInstants for sounds matching `query`. Combines curated results with live scrape."""
    query = (query or "").strip().lower()
    results = []
    seen = set()

    # Match in curated first
    if query:
        words = query.split()
        for item in CURATED:
            score = 0
            name_low = item["name"].lower()
            tags_low = " ".join(item.get("tags", [])).lower()
            if query in name_low:
                score += 10
            if any(w in name_low or w in tags_low for w in words):
                score += 5
            if score > 0:
                results.append((score, {
                    "id": item["id"],
                    "name": item["name"],
                    "url": item["url"],
                    "category": item.get("category", "meme"),
                    "source": "curated",
                }))
                seen.add(item["url"])

    # Live scrape on MyInstants
    if query:
        search_url = f"{MYINSTANTS_BASE}/en/search/?name={urllib.parse.quote(query)}"
        html_text = _fetch_html(search_url)
        if html_text:
            matches = _PARSER_RE.findall(html_text)
            for mp3, loader, sound_id, link, title in matches:
                full_mp3 = MYINSTANTS_BASE + mp3 if mp3.startswith("/") else mp3
                if full_mp3 in seen:
                    continue
                seen.add(full_mp3)
                clean_name = html.unescape(title).strip()
                results.append((1, {
                    "id": sound_id or f"sfx_{len(seen)}",
                    "name": clean_name,
                    "url": full_mp3,
                    "category": "myinstants",
                    "source": "myinstants",
                }))
                if len(results) >= limit + 10:
                    break

    # If no results (e.g. empty query or offline), return curated library
    if not results:
        for item in CURATED:
            results.append((0, {
                "id": item["id"],
                "name": item["name"],
                "url": item["url"],
                "category": item.get("category", "meme"),
                "source": "curated",
            }))

    # Sort by score descending and take up to limit
    results.sort(key=lambda x: x[0], reverse=True)
    return [r[1] for r in results[:limit]]


def trending(category: str = "memes", limit: int = 24) -> list:
    """Returns trending / category sounds from MyInstants."""
    cat_map = {
        "memes": "/en/categories/memes/",
        "sound-effects": "/en/categories/sound%20effects/",
        "reactions": "/en/categories/reactions/",
        "viral": "/en/categories/viral/",
        "games": "/en/categories/games/",
        "all": "/en/index/us/",
    }
    path = cat_map.get(category, "/en/categories/memes/")
    url = f"{MYINSTANTS_BASE}{path}"
    html_text = _fetch_html(url)
    results = []
    seen = set()

    if html_text:
        matches = _PARSER_RE.findall(html_text)
        for mp3, loader, sound_id, link, title in matches:
            full_mp3 = MYINSTANTS_BASE + mp3 if mp3.startswith("/") else mp3
            if full_mp3 in seen:
                continue
            seen.add(full_mp3)
            clean_name = html.unescape(title).strip()
            results.append({
                "id": sound_id or f"sfx_{len(seen)}",
                "name": clean_name,
                "url": full_mp3,
                "category": category,
                "source": "myinstants",
            })
            if len(results) >= limit:
                break

    # If scrape fails, return matching curated items
    if not results:
        for item in CURATED:
            results.append({
                "id": item["id"],
                "name": item["name"],
                "url": item["url"],
                "category": item.get("category", "meme"),
                "source": "curated",
            })
    return results[:limit]


def is_sound_url(url: str) -> bool:
    p = urllib.parse.urlparse(str(url))
    return (p.scheme == "https" and (p.hostname or "").lower().rstrip(".") == "www.myinstants.com"
            and p.path.startswith("/media/sounds/") and ".." not in p.path)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def ensure_cached(url: str, filename_hint: str = "") -> Path | None:
    """Downloads and caches an MP3 from MyInstants into config.SFX_DIR. Returns the local Path."""
    if not url or not is_sound_url(url):   # only MyInstants sound files: a stored URL is never a door to other hosts
        return None
    config.SFX_DIR.mkdir(parents=True, exist_ok=True)

    # Derive filename
    parsed = urllib.parse.urlparse(url)
    basename = Path(parsed.path).name
    if not basename.endswith(".mp3"):
        basename = (filename_hint or "sound") + ".mp3"

    url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    clean_base = re.sub(r"[^A-Za-z0-9_.-]", "_", basename)
    local_file = config.SFX_DIR / f"{url_hash}_{clean_base}"

    if local_file.exists() and local_file.stat().st_size > 200:
        return local_file

    tmp_file = config.SFX_DIR / f"tmp_{url_hash}_{clean_base}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=12) as resp:
            data = resp.read()
        if len(data) < 200:
            log.warning("SFX response too small for %s (%d bytes)", url, len(data))
            return None
        tmp_file.write_bytes(data)
        tmp_file.replace(local_file)
        return local_file
    except Exception as e:
        log.warning("Failed to download SFX %s: %s", url, e)
        if tmp_file.exists():
            tmp_file.unlink(missing_ok=True)
        return None


def find_sound(query: str) -> dict | None:
    """Finds the single best sound for a query, ensuring it is cached."""
    res = search(query, limit=5)
    if not res:
        return None
    for item in res:
        cached = ensure_cached(item["url"], item["name"])
        if cached:
            item["file"] = str(cached)
            return item
    return None


HYPE_WORDS = ("airhorn", "air horn", "mlg", "hype", "cheer", "victory", "crowd", "wow")
UNDER_VOICE_DB = 6.0      # a meme sound at vol 1.0 sits this far under the streamer's voice (user, 26 Nov: too loud)
_LOUD_CACHE: dict = {}


def loudness(path, start: float = None, dur: float = None, typical: bool = False) -> float:
    """Integrated loudness (LUFS) of a file or a stretch of it; for a sound too short to measure that way, its
    loudest 400 ms. typical=True: the median 400 ms loudness instead (normal talking level, not pulled up by a
    shout). -70 when silent or unreadable."""
    import statistics
    import subprocess
    key = (str(path), start, dur, typical, Path(path).stat().st_mtime if Path(path).exists() else 0)
    if key in _LOUD_CACHE:
        return _LOUD_CACHE[key]
    cmd = ["ffmpeg", "-nostats", "-hide_banner"]
    if start is not None:
        cmd += ["-ss", f"{max(0.0, start):.3f}"]
    if dur is not None:
        cmd += ["-t", f"{max(0.1, dur):.3f}"]
    cmd += ["-i", str(path), "-vn", "-af", "ebur128", "-f", "null", "-"]
    try:
        err = subprocess.run(cmd, capture_output=True, text=True, timeout=120).stderr
    except Exception:  # noqa: BLE001
        return -70.0
    i = re.findall(r"I:\s+(-?[\d.]+) LUFS", err)
    m = [float(x) for x in re.findall(r"M:\s*(-?[\d.]+)", err)]
    val = float(i[-1]) if i else -70.0
    if typical:
        heard = [x for x in m if x > -60]
        val = statistics.median(heard) if heard else -70.0
    elif val <= -60 and m:
        val = max(m)
    _LOUD_CACHE[key] = val
    return val


def gain_for(sound_path, voice_lufs: float, vol: float) -> float:
    """The linear gain that puts a sound UNDER_VOICE_DB under the voice at vol 1.0 (vol scales from there)."""
    have = loudness(sound_path)
    if have <= -60 or voice_lufs <= -60:
        return max(0.05, min(1.0, vol * 0.5))
    db = max(-40.0, min(12.0, voice_lufs - UNDER_VOICE_DB - have))
    return max(0.0, vol) * 10 ** (db / 20)


def is_hype(item: dict) -> bool:
    name = f"{item.get('name', '')} {item.get('url', '')} {item.get('search', '')}".lower()
    return any(w in name for w in HYPE_WORDS)


def calm_spots(items: list, src, start: float, dur: float) -> list:
    """Drops hype sounds (air horn and friends) where the streamer is already loud: they add nothing over a
    shout (user, 26 Nov). Also drops any sound in the first half second."""
    if not src or not Path(src).exists():
        return [x for x in items if x.get("time", 0) >= 0.5]
    talk = loudness(src, start, dur, typical=True)
    out = []
    for x in items:
        t = float(x.get("time", 0))
        if t < 0.5:
            continue
        if is_hype(x) and loudness(src, start + max(0.0, t - 0.75), 1.5) >= talk + 4.0:
            log.info("skipped %s at %.1fs: the streamer is already hyped there", x.get("name"), t)
            continue
        out.append(x)
    return out


def suggest_for_clip(clip: dict, words: list = None, game: str = "", title: str = "", src=None) -> list:
    """Suggests 1 to 3 meme sounds and SFX for a clip based on its mood, category, hook and timing."""
    start = clip.get("start", 0.0)
    end = clip.get("end", 0.0)
    dur = max(1.0, end - start)
    cat = (clip.get("category") or "other").lower()
    hook = clip.get("hook", "")
    clip_title = clip.get("title", "")
    hw = [w.lower() for w in clip.get("highlight_words", [])]

    # Try AI suggestion first if available and there are transcript words
    if words and len(words) >= 4:
        try:
            from . import ai as ai_mod, brain as brain_mod
            if ai_mod.ready():
                spoken = " ".join(f"[{w['s'] - start:.1f}s] {w['w']}" for w in words if start <= w.get("s", 0) <= end)
                if spoken.strip():
                    prompt = f"""You are an audio editor for short-form viral videos (Reels, TikTok, Shorts).
Given this vertical clip:
Title: {clip_title or title}
Hook: {hook}
Category: {cat}
Duration: {dur:.1f} seconds (from 0.0s to {dur:.1f}s)
Words spoken:
{spoken[:1200]}

Pick at most 2 moments that a meme sound makes funnier. Usually that is the pause right AFTER a punchline, a
fail or an awkward silence, not on top of the streamer talking. Rules:
- never in the first second; offset is the second the sound starts, read from the [seconds] above
- never a hype sound (airhorn, MLG, crowd cheer) where the streamer is already shouting or hyped: the voice is
  the hype; a sound there just covers it
- fewer is better: if nothing needs a sound, return []
- vol is 0.3 to 0.6
Sounds that work: vine boom, bruh, emotional damage, dun dun dun, metal pipe, record scratch, sad violin, anime wow.
Return a JSON array, e.g. [{{"offset": <seconds>, "search": "<sound>", "vol": 0.5}}]
"""
                    out, _, _ = brain_mod.ask_ai(prompt, purpose="Clip SFX suggestion", timeout=12)
                    if isinstance(out, list) and not out:
                        return []           # the AI found nothing that needs a sound: none is better than a random one
                    if isinstance(out, list) and len(out) > 0:
                        chosen = []
                        for item in out[:2]:
                            offset = float(item.get("offset") or item.get("start") or 0.0)
                            if 0.0 <= offset <= dur:
                                q = str(item.get("search") or item.get("hook") or "vine boom")
                                s_item = find_sound(q)
                                if s_item:
                                    vol = max(0.2, min(0.8, float(item.get("vol") or 0.5)))
                                    chosen.append({
                                        "time": round(offset, 2),
                                        "name": s_item["name"],
                                        "url": s_item["url"],
                                        "vol": round(vol, 2),
                                    })
                        if chosen:
                            if dur >= 6.0 and not any(c["time"] < 0.6 for c in chosen):
                                w_item = find_sound("whoosh")
                                if w_item:
                                    chosen.insert(0, {
                                        "time": 0.15,
                                        "name": "Whoosh Transition",
                                        "url": w_item["url"],
                                        "vol": 0.45,
                                    })
                            chosen = calm_spots(chosen, src, start, dur)
                            for s in chosen:
                                ensure_cached(s["url"], s["name"])
                            return chosen
        except Exception as e:
            log.warning("AI SFX suggestion skipped: %s", e)

    # Pre-select curated candidates for speed and guaranteed audio quality
    chosen = []


    # 1. Opening hook sound (whoosh or pop at 0.1s to grab attention)
    if dur >= 6.0:
        whoosh_item = find_sound("whoosh")
        if whoosh_item:
            chosen.append({
                "time": 0.15,
                "name": "Whoosh Transition",
                "url": whoosh_item["url"],
                "vol": 0.45,
            })

    # 2. Main punchline / reaction sound near the climax or key moment
    # Calculate moment timing: preferably near highlight word or around 65-80% of clip
    punch_t = round(min(dur - 1.2, max(1.5, dur * 0.72)), 2)

    # If we have timestamped words and highlight words, find exact timestamp of punch word
    if words and hw:
        for w in words:
            if start <= w.get("s", 0) <= end:
                clean_w = re.sub(r"[^a-z0-9]", "", w.get("w", "").lower())
                if clean_w in hw:
                    punch_t = round(max(0.5, min(dur - 0.8, w["s"] - start)), 2)
                    break

    # Pick sound based on category
    punch_query = "vine boom"
    if cat in ("funny", "fail"):
        punch_query = "vine boom" if ("dead" in clip_title.lower() or "new" in clip_title.lower()) else "bruh"
    elif cat in ("shock", "rage"):
        punch_query = "dun dun dun"
    elif cat in ("clutch", "hype"):
        punch_query = "airhorn"
    elif "drug" in clip_title.lower() or "reveal" in hook.lower():
        punch_query = "vine boom"

    punch_item = find_sound(punch_query) or find_sound("vine boom")
    if punch_item:
        chosen.append({
            "time": punch_t,
            "name": punch_item["name"],
            "url": punch_item["url"],
            "vol": 0.5,
        })

    chosen = calm_spots(chosen, src, start, dur)
    for s in chosen:
        ensure_cached(s["url"], s["name"])
    return chosen

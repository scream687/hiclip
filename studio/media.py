"""Getting video in: link download, probing, audio extraction, loudness."""
import contextlib
import functools
import json
import re
import subprocess
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from . import config

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# ---------------------------------------------------------------- when YouTube refuses this server
# YouTube sometimes refuses a server's downloads altogether ("sign in to confirm you're not a bot"). It is a
# refusal of the server, not of one video, so once it happens YouTube links are turned away at once for a few
# minutes, with a clear message, instead of each one waiting to fail. Then the next link simply tries again.
YT_PAUSE = 600
_yt_refused_until = 0.0
YT_BLOCKED = ("YouTube isn't letting hiclip download videos right now. Upload the video file instead, or use a "
              "Twitch or Kick link. YouTube links will be tried again in a few minutes.")
_BOT_CHECK = re.compile(r"not a bot|confirm you.?re not|sign in to confirm(?! your age)", re.I)


class YouTubeBlocked(RuntimeError):
    pass


def is_youtube(url: str) -> bool:
    from urllib.parse import urlparse
    host = (urlparse((url or "").strip()).hostname or "").lower()
    return host in ("youtube.com", "youtu.be") or host.endswith((".youtube.com", ".youtu.be"))


def youtube_paused() -> bool:
    return time.time() < _yt_refused_until


def check_youtube(url: str):
    """Raise YouTubeBlocked straight away if YouTube is refusing this server right now."""
    if is_youtube(url) and youtube_paused():
        raise YouTubeBlocked(YT_BLOCKED)


def note_refusal(url: str, err) -> bool:
    """If this error is YouTube refusing the server, pause YouTube links for a while. True if it was."""
    global _yt_refused_until
    if is_youtube(url) and _BOT_CHECK.search(str(err)):
        _yt_refused_until = time.time() + YT_PAUSE
        return True
    return False


@contextlib.contextmanager
def _youtube_guard(url):
    check_youtube(url)
    try:
        yield
    except YouTubeBlocked:
        raise
    except Exception as e:  # noqa: BLE001
        if note_refusal(url, e):
            raise YouTubeBlocked(YT_BLOCKED) from None
        raise


def site_allowed(url: str) -> bool:
    """Links are fetched by the server, so only known video sites are accepted (never arbitrary addresses)."""
    from urllib.parse import urlparse
    try:
        u = urlparse(url.strip())
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    return u.scheme in ("http", "https") and any(host == s or host.endswith("." + s) for s in config.ALLOWED_SITES)


def run(cmd, cwd=None, timeout=3600):
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", creationflags=NO_WINDOW, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{Path(str(cmd[0])).name} took too long and was stopped.")
    if p.returncode != 0:
        raise RuntimeError(f"{Path(str(cmd[0])).name} failed: {p.stderr[-1500:]}")
    return p.stdout


class _Quiet:
    def debug(self, m): pass
    def info(self, m): pass
    def warning(self, m): pass
    def error(self, m): pass


@functools.cache
def _browser_cookies_readable() -> bool:
    """Whether the browser named in settings can be read. A missing browser or one this computer won't let hiclip
    open (macOS privacy) would otherwise fail every download and recording, so hiclip carries on without it."""
    if not config.YT_COOKIES_FROM_BROWSER:
        return False
    try:
        from yt_dlp.cookies import extract_cookies_from_browser
        extract_cookies_from_browser(config.YT_COOKIES_FROM_BROWSER)
        return True
    except Exception as e:  # ponytail: checked once per run; restart hiclip after granting access
        print(f"hiclip: not using {config.YT_COOKIES_FROM_BROWSER} cookies ({e}); continuing without them")
        return False


def ydl_auth() -> dict:
    """yt-dlp options that sign in as the viewer in the browser named in settings, or nothing."""
    return {"cookiesfrombrowser": (config.YT_COOKIES_FROM_BROWSER,)} if _browser_cookies_readable() else {}


def ydl_auth_args() -> list:
    """The same, for yt-dlp run as a command."""
    return ["--cookies-from-browser", config.YT_COOKIES_FROM_BROWSER] if _browser_cookies_readable() else []


def _ydl(opts):
    import yt_dlp
    # a channel or playlist link is answered as "a list" straight away, never walked video by video
    return yt_dlp.YoutubeDL({"noplaylist": True, "quiet": True, "no_warnings": True, "logger": _Quiet(),
                             "extract_flat": "in_playlist", "playlistend": 1, "socket_timeout": 20,
                             "concurrent_fragment_downloads": 4, **ydl_auth(), **opts})


def _video_format(height):
    return f"bv*[height<={height}][vcodec^=avc1]+ba[ext=m4a]/bv*[height<={height}]+ba/b[height<={height}]/b"


def _biggest(job_dir: Path, stem: str) -> Path:
    files = sorted((p for p in job_dir.glob(stem + ".*") if p.suffix not in (".part", ".ytdl")),
                   key=lambda p: p.stat().st_size, reverse=True)
    if not files:
        raise RuntimeError("Download finished but no file was produced.")
    return files[0]


def info(url: str) -> dict:
    """Title, channel and length, without downloading anything."""
    with _youtube_guard(url), _ydl({}) as ydl:
        i = ydl.extract_info(url, download=False)
    return {"title": i.get("title") or "", "uploader": i.get("uploader") or i.get("channel") or "",
            "duration": float(i.get("duration") or 0), "is_live": bool(i.get("is_live")),
            "thumbnail": i.get("thumbnail") or "", "kind": i.get("_type") or "video"}


def youtube_words(url: str, pref_lang: str = ""):
    """YouTube's own transcript of a video, word by word: ([{'w', 's', 'e'}, ...], language), or None if the video
    has none. Only the transcript in the language that was spoken is used, never a translation."""
    if not is_youtube(url):
        return None
    import json
    with _youtube_guard(url), _ydl({}) as ydl:
        i = ydl.extract_info(url, download=False)
        auto = i.get("automatic_captions") or {}
        subs = i.get("subtitles") or {}
        vlang = (i.get("language") or "").lower()
        base_vlang = vlang.split("-")[0] if vlang else ""

        candidates = []
        if pref_lang:
            p = pref_lang.lower()
            candidates += [f"{p}-orig", p, f"{p.split('-')[0]}-orig", p.split("-")[0]]
            if p in ("hi", "hinglish"):
                candidates += ["hi-orig", "hi", "ur-orig", "ur"]
        if vlang:
            candidates += [f"{vlang}-orig", vlang]
        if base_vlang and base_vlang != vlang:
            candidates += [f"{base_vlang}-orig", base_vlang]

        key, group = None, auto
        for c in candidates:
            if c in auto:
                key, group = c, auto
                break
            if c in subs:
                key, group = c, subs
                break

        if not key:
            if base_vlang:
                for k in auto:
                    if k.endswith("-orig") and k.split("-")[0].lower() == base_vlang:
                        key, group = k, auto
                        break
            if not key:
                key = next((k for k in auto if k.endswith("-orig")), None)
            if not key and vlang in auto:
                key = vlang

        track = next((f for f in group.get(key) or [] if f.get("ext") == "json3" and f.get("url")), None) if key else None
        if not track:
            return None
        raw = ydl.urlopen(track["url"]).read(40_000_000)
    return _caption_words(json.loads(raw)), key.replace("-orig", "")


def youtube_english_words(url: str):
    """YouTube's English captions of a video spoken in another language: ones a person wrote if there are any,
    otherwise YouTube's own translation of its transcript. Word list like youtube_words, or None."""
    if not is_youtube(url):
        return None
    import json
    with _youtube_guard(url), _ydl({}) as ydl:
        i = ydl.extract_info(url, download=False)
        tracks = []
        for group in (i.get("subtitles") or {}, i.get("automatic_captions") or {}):
            for k in ("en", "en-US", "en-GB"):
                tracks.append(next((f for f in group.get(k) or [] if f.get("ext") == "json3" and f.get("url")), None))
        track = next((t for t in tracks if t), None)
        if not track:
            return None
        raw = ydl.urlopen(track["url"]).read(40_000_000)
    words = _caption_words(json.loads(raw))
    return words or None


def _caption_words(data: dict) -> list:
    out = []
    for ev in data.get("events") or []:
        segs = [x for x in ev.get("segs") or [] if (x.get("utf8") or "").strip()]
        if not segs:
            continue
        t0, dur = float(ev.get("tStartMs") or 0), float(ev.get("dDurationMs") or 0)
        timed = len(segs) > 1 or "tOffsetMs" in segs[0]
        for x in segs:
            parts = x["utf8"].split()
            for n, w in enumerate(parts):
                if re.fullmatch(r"[\[(].*[\])]", w) or w in ("[", "]", "__"):
                    continue                      # [Music], [Applause], and words YouTube blanked out
                # a line with no word timings: its words are spread evenly over the time the line was on screen
                at = t0 + float(x.get("tOffsetMs") or 0) if timed else t0 + dur * n / max(1, len(parts))
                out.append({"w": w, "s": round(at / 1000, 2)})
    out.sort(key=lambda w: w["s"])
    for n, w in enumerate(out):
        nxt = out[n + 1]["s"] if n + 1 < len(out) else w["s"] + 0.4
        w["e"] = round(max(w["s"] + 0.08, min(nxt, w["s"] + 0.6)), 2)
    return out


_PREVIEWS: dict = {}


def preview(url: str) -> dict:
    """What the user will get from a link, checked before any work starts. The thumbnail comes back inline
    (the page may only load images from this site). Cached, so retyping a link costs nothing."""
    url = url.strip()
    if url in _PREVIEWS:
        return _PREVIEWS[url]
    i = info(url)
    if i["kind"] != "video" or not i["title"]:
        raise ValueError("That link is a channel or playlist, not a single video.")
    thumb = ""
    t = i.get("thumbnail") or ""
    if t.startswith("https://"):
        import base64
        import urllib.request
        try:
            req = urllib.request.Request(t, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=6) as r:
                ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip()
                data = r.read(600_000 + 1)
            if ctype in ("image/jpeg", "image/png", "image/webp") and len(data) <= 600_000:
                thumb = f"data:{ctype};base64," + base64.b64encode(data).decode()
        except Exception:
            thumb = ""
    out = {"title": i["title"][:200], "uploader": i["uploader"][:120], "duration": i["duration"],
           "is_live": i["is_live"], "thumb": thumb}
    if len(_PREVIEWS) > 300:
        _PREVIEWS.pop(next(iter(_PREVIEWS)))
    _PREVIEWS[url] = out
    return out


def save_thumb(job_dir: Path, url: str = "", video: Path = None) -> bool:
    """A small picture for the video list: the link's own thumbnail, or a frame from an uploaded file."""
    import base64
    out = Path(job_dir) / "clips" / "thumb.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        if url:
            p = _PREVIEWS.get(url.strip()) or preview(url)
            if not p.get("thumb"):
                return False
            out.write_bytes(base64.b64decode(p["thumb"].split(",", 1)[1]))
            return True
        if video:
            d = probe(video).get("duration") or 0
            run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{min(3.0, d / 3):.2f}", "-i", str(video),
                 "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "4", str(out)])
            return out.is_file()
    except Exception:
        return False
    return False


def download_audio(url: str, job_dir: Path, on_progress=None) -> Path:
    """Audio only: a few seconds even for long videos, and all the AI needs to pick clips."""
    def hook(d):
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        if on_progress and d.get("status") == "downloading" and total:
            on_progress(d.get("downloaded_bytes", 0) / total)

    with _youtube_guard(url), _ydl({"format": "ba[ext=m4a]/ba/worst", "outtmpl": str(job_dir / "audio_src.%(ext)s"),
               "progress_hooks": [hook]}) as ydl:
        ydl.download([url])
    return _biggest(job_dir, "audio_src")


def download(url: str, job_dir: Path, height=720) -> Path:
    """The whole video. Used for shorter videos, where this is quicker than fetching pieces."""
    with _youtube_guard(url), _ydl({"format": _video_format(height), "merge_output_format": "mp4",
                                    "outtmpl": str(job_dir / "source.%(ext)s")}) as ydl:
        ydl.download([url])
    return _biggest(job_dir, "source")


def download_section(url: str, out_dir: Path, stem: str, start: float, end: float, height=720, exact=False) -> Path:
    """Just one stretch of the video. The cut lands on a keyframe, so find_offset() pins the true start; with
    exact=True the cut is made at exactly `start` instead (slower), for when there is nothing to match against."""
    from yt_dlp.utils import download_range_func
    out_dir.mkdir(parents=True, exist_ok=True)
    opts = {"format": _video_format(height), "merge_output_format": "mp4", "outtmpl": str(out_dir / (stem + ".%(ext)s")),
            "download_ranges": download_range_func(None, [(max(0.0, start), end)])}
    if exact:
        opts["force_keyframes_at_cuts"] = True
    with _youtube_guard(url), _ydl(opts) as ydl:
        ydl.download([url])
    return _biggest(out_dir, stem)


def find_offset(full: np.ndarray, part: np.ndarray, expected: float, search=20.0) -> float:
    """Where in the full audio (seconds) a downloaded piece really begins, by matching the sound itself."""
    n = min(len(part), 30 * 16000)
    a0 = max(0, int((expected - search) * 16000))
    hay = full[a0: int((expected + search) * 16000) + n].astype(np.float32)
    needle = part[:n].astype(np.float32)
    if n < 16000 or len(hay) < n or float(np.abs(needle).mean()) < 20:
        return expected  # too short or silent to match: trust the requested time
    size = 1 << (len(hay) + n).bit_length()
    corr = np.fft.irfft(np.fft.rfft(hay, size) * np.conj(np.fft.rfft(needle, size)), size)[: len(hay) - n + 1]
    return (a0 + int(np.argmax(corr))) / 16000


def probe(path: Path) -> dict:
    out = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)])
    data = json.loads(out)
    v = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    if not v:
        raise RuntimeError("This file has no video track.")
    if not any(s["codec_type"] == "audio" for s in data["streams"]):
        raise RuntimeError("This file has no audio track, so there is nothing to transcribe.")
    w, h = int(v["width"]), int(v["height"])
    if w * h > 3840 * 2160 or w < 16 or h < 16:
        raise RuntimeError("This video's resolution isn't supported (4K is the largest).")
    rot = str((v.get("side_data_list") or [{}])[0].get("rotation", "0"))
    if rot.lstrip("-") in ("90", "270"):
        w, h = h, w
    return {"width": w, "height": h, "duration": float(data["format"]["duration"])}


def extract_audio(src: Path, wav: Path):
    # first_pts=0 pads any gap before the audio starts, so second 0 of the wav is second 0 of the file
    # -t: never more audio than the longest video allowed, whatever the file claims about itself
    run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-t", str(int(config.MAX_VIDEO_HOURS * 3600) + 60),
         "-af", "aresample=async=1:first_pts=0", "-c:a", "pcm_s16le", str(wav)])


def load_audio(wav: Path) -> np.ndarray:
    data, sr = sf.read(str(wav), dtype="int16")
    assert sr == 16000
    return data


def loudness_per_second(audio: np.ndarray) -> np.ndarray:
    """RMS level in dB for each second of audio."""
    n = len(audio) // 16000
    if n == 0:
        return np.zeros(1, dtype=np.float32)
    x = audio[: n * 16000].reshape(n, 16000).astype(np.float32) / 32768.0
    rms = np.sqrt((x ** 2).mean(axis=1) + 1e-10)
    return (20 * np.log10(rms)).astype(np.float32)


def live_manifest(info: dict) -> tuple[str, str | None]:
    """HLS playlists of a running live stream (yt-dlp `info`): the best video up to 1080p, and the best audio-only
    playlist when YouTube/Twitch serves sound separately (None when the video playlist carries sound)."""
    hls = [f for f in info.get("formats", []) if "m3u8" in (f.get("protocol") or "") and f.get("url")]
    has = lambda f, key: f.get(key) not in (None, "none")
    best = lambda fs: max(fs, key=lambda f: (f.get("height") or 0, f.get("tbr") or 0, f.get("format_id") or ""))
    muxed = [f for f in hls if has(f, "vcodec") and has(f, "acodec") and (f.get("height") or 0) <= 1080]
    if muxed:
        return best(muxed)["url"], None
    video = [f for f in hls if has(f, "vcodec") and (f.get("height") or 0) <= 1080]
    audio = [f for f in hls if not has(f, "vcodec") and not f.get("height")]
    if not video:
        video = hls
    if not audio:
        return best(video)["url"], None
    return best(video)["url"], best(audio)["url"]


def segments(playlist: str) -> dict[int, tuple[float, str]]:
    """Sequence number -> (seconds, url) of each segment in an HLS media playlist."""
    seq = 0
    for line in playlist.splitlines():
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            seq = int(line.split(":", 1)[1])
            break
    out, length = {}, None
    for line in playlist.splitlines():
        if line.startswith("#EXTINF:"):
            length = float(line.split(":", 1)[1].split(",")[0])
        elif line and not line.startswith("#") and length is not None:
            out[seq] = (length, line.strip())
            seq, length = seq + 1, None
    return out


def window_playlists(lists: list[dict[int, tuple[float, str]]], window_s: float) -> tuple[list[str], float]:
    """Fixed playlists for the last `window_s` seconds that every input has, starting on the same segment number,
    so separate video and audio start in step."""
    common = sorted(set.intersection(*(set(s) for s in lists)))
    if not common:
        raise RuntimeError("The stream's video and audio have no segments in common yet; try again.")
    picked, total = [], 0.0
    for n in reversed(common):
        if total >= window_s or (picked and picked[-1] != n + 1):  # stop at window or gap
            break
        picked.append(n)
        total += lists[0][n][0]
    picked.reverse()
    body = lambda s: "".join(f"#EXTINF:{s[n][0]:.3f},\n{s[n][1]}\n" for n in picked)
    head = f"#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-PLAYLIST-TYPE:VOD\n#EXT-X-TARGETDURATION:{int(max(lists[0][n][0] for n in picked)) + 1}\n#EXT-X-MEDIA-SEQUENCE:{picked[0]}\n"
    return [head + body(s) + "#EXT-X-ENDLIST\n" for s in lists], total


def download_live_window(url: str, out: Path, window_s: float = 1800.0, log=print) -> Path:
    """The last `window_s` seconds of a stream that is still live, from the rewind buffer, without waiting for it to end."""
    import subprocess
    import urllib.request
    with _youtube_guard(url), _ydl({}) as ydl:
        info = ydl.extract_info(url, download=False)
    urls = [u for u in live_manifest(info) if u]
    lists = []
    for m_url in urls:
        req = urllib.request.Request(m_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            lists.append(segments(r.read(20_000_000).decode("utf-8", "replace")))
    playlists, covered = window_playlists(lists, window_s)
    if log:
        log(f"Live stream: capturing the last {round(covered / 60, 1)} minutes")
    partial = out.with_suffix(".download.mp4")
    inputs = []
    for n, text in enumerate(playlists):
        local = out.with_name(f"window{n}.m3u8")
        local.write_text(text, encoding="utf-8")
        inputs += ["-protocol_whitelist", "file,http,https,tcp,tls,crypto", "-i", str(local)]
    maps = ["-map", "0:v:0", "-map", "1:a:0"] if len(playlists) == 2 else []
    try:
        result = subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, *maps, "-c", "copy", "-movflags", "+faststart",
                                 str(partial)], capture_output=True, text=True, timeout=covered + 900)
    finally:
        for n in range(len(playlists)):
            out.with_name(f"window{n}.m3u8").unlink(missing_ok=True)
    if result.returncode != 0 or not partial.is_file() or partial.stat().st_size == 0:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"Could not capture the live stream: {result.stderr.strip()[-300:] or 'no data'}")
    partial.replace(out)
    return out

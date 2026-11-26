"""Live sessions: record a stream into a rolling buffer, transcribe as it goes, let the AI pick clips."""
import collections
import json
import re
import socket
import ssl
import subprocess
import threading
import time
from pathlib import Path

import numpy as np

from . import alerts, auth, brain, config, creators, ctx, games, jobs, load, media, roman, transcribe, usage

# ":username!user@host PRIVMSG #channel :the message"
_NICK = re.compile(r"(?m)^:([A-Za-z0-9_]{2,25})!")


def _youtube_video_id(url: str) -> str | None:
    m = re.search(r"(?:v=|\/live\/|\.be\/)([a-zA-Z0-9_-]{11})", url or "")
    return m.group(1) if m else None

SESSIONS: dict = {}
SEG_SEC = 6           # length of each buffer piece
PAD = 8               # seconds kept either side of a clip so it can be re-trimmed later
CHECK_EVERY = 180     # look at the stream every 3 minutes...
QUIET_MAX = 600       # ...but only ask the AI when something happened, or at most every 10 minutes when calm
WINDOW = 240          # seconds of transcript shown to the AI per check
CHAT_KEEP = 600       # seconds of chat names kept: only the last few minutes are ever read
SR = 16000


# Genuine hype: what a crowd types when something happens. Each message counts once, under the first label that
# matches. Plain chatter, questions and bot commands are chat, not hype.
HYPE_WORDS = [(label, re.compile(rx, re.I)) for label, rx in [
    ("W", r"(?<![\w'/])w+(?![\w'/])"), ("GG", r"\bg+gs?\b"), ("LET'S GO", r"\blet'?s+\s*go+\b|\blfg+\b"),
    ("NO WAY", r"\bno+\s*wa+y+\b(?!\s+out)"), ("OMG", r"\bomg+\b|\boh my god\b"), ("SHEESH", r"\bshe+sh\b"),
    ("POG", r"\bpog\w*\b"), ("CLIP IT", r"\bclip\s*(it|that)\b"), ("HOLY", r"\bholy\b"), ("FAH", r"\bfa+h+\b"), ("I WAS HERE", r"\bi\s*was\s*here\b"),
    ("OHHH", r"\bo+h{3,}\b|\bo{3,}h*\b|\bou+\b"), ("NOOO", r"\bno{3,}\b"), ("RIP", r"\brip\b"),
    ("L", r"(?<![\w'/])l+(?![\w'/])"),
    ("LMAO", r"\blma+o+\b|\blo+l+\b|\bkekw\b|\blul\b|\bicant\b|\b(ha){2,}h?\b|\bxd+\b"
             r"|\bj[aeiou](j[aeiou]){2,}\b|\bk{4,}\b|ه{3,}|哈{2,}|ㅋ{3,}"),  # haha, jajaja, kkkk, هههه, 哈哈, ㅋㅋㅋ
    ("LET'S GO", r"\bvamo+s?\b|\bbora+\b|\ballez\b|\bandiamo\b|\bchal+o+\b"),
    ("INSANE", r"\binsane\b|\bcrazy\b|\bwild\b|\bwtf\b|\bbro what\b|\bwhat the\b"),
    ("CINEMA", r"\bcinema\b|\bgoated\b|🐐|\bop\b|\bhype+\b"),
    ("KYA BAAT", r"\bkya\s*baat\b|\bgaza+b\b|\bjhakaa+s\b|\bbawaa*l\b|\bmast\b|\bbhaukaal\b|\bwa+h+\b|वाह|गज़ब|गजब"),  # not "goat", "fire", "peak": ordinary words too ("🔥", r"🔥"), ("😂", r"😂|🤣"),
    ("💀", r"💀|☠|🥀"), ("😭", r"😭"), ("😱", r"😱|😮|🤯|😳")]]


def _get_json(url: str, body: bytes | None = None) -> str:
    """A public JSON endpoint, asked like a browser would (Kick turns plain scripts away)."""
    import urllib.request
    hdr = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/131 Safari/537.36", "Accept": "application/json"}
    if body:
        hdr["Content-Type"] = "application/json"
    return urllib.request.urlopen(urllib.request.Request(url, body, hdr), timeout=20).read().decode("utf-8", "replace")


def _emoji_text(e: dict) -> str:
    """A chat emoji as text: the character itself, or a channel's own emote by its name (":snaxHype:"), not its id."""
    if e.get("isCustomEmoji"):
        return (e.get("shortcuts") or [""])[0]
    return e.get("emojiId") or ""


# Hindi or Hinglish in chat: Devanagari, or the small words Hinglish can't do without. A stream whose chat is
# this Hindi has a streamer speaking it, which the speech models can't be trusted to tell (Whisper hears Hinglish
# as English, and the English model spells it as nonsense).
HINDI_CHAT = re.compile(r"[\u0900-\u097F]|\b(hai|hain|kya|kyu|kyun|nahi|nhi|bhai|bhi|yaar|yrr|karo|raha|rahi|rahe|"
                        r"mein|toh|hoga|kaise|accha|acha|bahut|bohot|apna|tera|mera|aaj|abhi|dekh|haan|wala|wali)\b",
                        re.I)
CHAT_LANG_AFTER, CHAT_HINDI_SHARE = 100, 0.2   # messages read before deciding; share of them that means Hindi


def hype_label(text: str) -> str | None:
    """The hype this chat message shows, or None for ordinary chat."""
    if text.strip().startswith("!"):
        return None
    return next((label for label, rx in HYPE_WORDS if rx.search(text)), None)


class NotLive(Exception):
    pass


def _trim_names(users: dict, second: int, kept: int) -> int:
    """Drop the chat names older than CHAT_KEEP, and say when this was last done.

    Only the last few minutes are ever read, and a stream that runs for eight hours would otherwise hold
    a name for every second of it. Called under the session's lock, from the chat reader thread."""
    if second - kept < 60:
        return kept              # walking the whole dict on every message would cost more than it saves
    for t in [t for t in users if t < second - CHAT_KEEP]:
        del users[t]
    return second


def media_safe(e) -> str:
    from . import safe
    return safe.friendly(e, 300)


def normalize(url: str) -> str:
    url = url.strip()
    if not re.match(r"^https?://", url):
        url = "https://" + url
    m = re.match(r"^(https?://(?:www\.)?youtube\.com/(?:@[^/?#]+|channel/[^/?#]+|c/[^/?#]+))/?(?:[?#].*)?$", url)
    return m.group(1) + "/live" if m else url


class _Quiet:
    def debug(self, m): pass
    def info(self, m): pass
    def warning(self, m): pass
    def error(self, m): pass


def resolve(url: str) -> dict:
    """Ask yt-dlp for the stream's playable address. Raises NotLive if the channel is offline."""
    import yt_dlp
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "logger": _Quiet(),
            "format": "b[height<=720]/bv*[height<=720]+ba/b", **media.ydl_auth()}
    if media.is_youtube(url) and media.youtube_paused():
        raise NotLive(media.YT_BLOCKED)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:  # noqa: BLE001
        if media.note_refusal(url, e):
            raise NotLive(media.YT_BLOCKED) from e
        raise NotLive(str(e)[-200:]) from e
    if not info or not info.get("is_live"):
        raise NotLive("This channel is not live right now.")
    fmts = info.get("requested_formats") or [info]
    title = info.get("title") or ""
    if "(live)" in title and info.get("description"):  # Twitch puts the real stream title here
        title = str(info["description"]).splitlines()[0][:120]
    return {"urls": [f["url"] for f in fmts], "title": title,
            "uploader": info.get("uploader") or info.get("channel") or ""}


def active():
    return [s for s in SESSIONS.values() if not s.finished and not s.user_stopped]


def active_mine():
    u = ctx.uid()
    return [s for s in active() if s.job["user"] == u]


def active_for(cid):
    return next((s for s in active_mine() if s.job["creator"] == cid), None)


def mine(jid):
    """The signed-in user's recording with this id, or None."""
    s = SESSIONS.get(jid)
    return s if s and s.job["user"] == ctx.uid() else None


class Session:
    def __init__(self, job, settings):
        self.job, self.settings = job, settings
        self.dir = Path(job["dir"])
        self.buf = self.dir / "buffer"
        self.buf.mkdir(parents=True, exist_ok=True)
        (self.dir / "clips").mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.stop_flag = threading.Event()
        self.ended = False            # recording has stopped (stream over, stopped, or failed)
        self.finished = False         # everything is wrapped up
        self.end_note = "Stream ended"
        self.user_stopped = False     # Stop pressed: wrap up at once, no last AI check
        self.proc = None
        self.pending = bytearray()    # audio received but not yet transcribed
        self.clock_fix = 0.0          # how far the video-piece clock is behind the audio clock, last measured
        self.pending_base = 0         # sample index where `pending` starts
        self.samples = 0              # total audio samples received
        self.db = []                  # loudness per second
        self.words = []
        self.t_done = 0.0             # transcript is complete up to this second
        self.segments = []            # (path, start, end) on the session clock
        self.seg_clock = 0.0
        self.csv_seen = {}
        self.part = 0
        self.chat = collections.Counter()   # second -> messages
        self.chat_total = 0
        self.chat_on = False
        self.hype = collections.Counter()    # second -> hype messages (W, GG, LET'S GO, 🔥...)
        self.hype_said = collections.deque(maxlen=20000)   # (second, label), for the words chat is hyping with
        self.hype_users = {}                 # second -> who hyped then: one person spamming W counts once
        self.lang_seen = self.lang_hindi = 0    # chat messages of two words or more, and how many were Hindi
        self.hype_kept = 0
        self.last_check = 0.0
        self.words_at_check = 0
        self.checks = 0
        self.rejected = 0
        self.verdicts = []
        self.last_trigger = -999.0
        self.started_at = time.time()
        self.skipped = 0
        self.last_ai = 0.0
        self.tokens = 0
        self.manual = []
        self.n = max([0] + [int(c["id"].replace("clip", "")) for c in self.job.get("clips", []) if str(c.get("id", "")).startswith("clip") and c["id"].replace("clip", "").isdigit()])
        self.spike = None             # a jump in loudness or chat waiting for its transcript
        self.said = None              # someone said "clip": that moment, waiting for its transcript
        self.last_said = -999.0
        self.last_spike = -999.0      # when the last jump was sent to the AI (stream seconds)
        self.base = {}                # running averages the jumps are measured against, shown on the card
        self.spikes = 0
        self.game = ""                # which game this stream is, when it can be told
        self.ai_busy = 0.0            # when the AI check now running was started, 0 when none is

    def _ask_ai(self, window=None, why=None):
        """Ask the AI on its own thread, so a slow answer never stops the recording.

        While the AI thinks, the speech keeps being written and a "clip that" still works. One check
        runs at a time; if the running one is stuck far past its own timeout, a new one is let through.
        """
        now = time.time()
        if self.ai_busy and now - self.ai_busy < config.LIVE_AI_TIMEOUT + 60:
            return

        def work():
            try:
                self._ai_check(window, why)
            except Exception as e:  # noqa: BLE001
                self.job["ai"]["last_error"] = str(e)[-200:]
            finally:
                self.ai_busy = 0.0
        self.ai_busy = now
        threading.Thread(target=ctx.bind(work), daemon=True, name="ai_check").start()

    # ---------------------------------------------------------------- the game this stream is
    def _game(self):
        """The game, from what the creator set or from what the stream's title says.

        The stream title is the best place to look: on launch night it says the game."""
        if self.game:
            return self.game
        found = games.detect(self.settings.get("game"), self.job.get("title"), self.job.get("uploader"),
                             self.job.get("source", {}).get("name"))
        self.game = found[0] or ""
        if self.game:
            try:
                self.job["game"] = self.game
            except Exception:  # noqa: BLE001
                pass
        return self.game

    # ---------------------------------------------------------------- recording
    def start(self):
        SESSIONS[self.job["id"]] = self
        threading.Thread(target=ctx.bind(self._ingest), daemon=True).start()
        threading.Thread(target=ctx.bind(self._analyze), daemon=True).start()
        m = re.match(r"^https?://(?:www\.)?twitch\.tv/([A-Za-z0-9_]+)", self.job["source"]["url"])
        if m:
            threading.Thread(target=ctx.bind(self._chat), args=(m.group(1).lower(),), daemon=True).start()
        url = self.job["source"].get("url") or ""
        k = re.match(r"^https?://(?:www\.)?kick\.com/([A-Za-z0-9_-]+)", url)
        if k:
            threading.Thread(target=ctx.bind(self._kick_chat), args=(k.group(1).lower(),), daemon=True).start()
        yt_id = _youtube_video_id(url)
        if yt_id:
            threading.Thread(target=ctx.bind(self._yt_chat), args=(yt_id,), daemon=True).start()
        elif media.is_youtube(url):   # added by channel ("@name/live"): find the stream's video id first
            threading.Thread(target=ctx.bind(self._yt_chat_by_channel), args=(url,), daemon=True).start()

    def _yt_chat_by_channel(self, url):
        import yt_dlp
        try:   # just the page's metadata (no formats, no download): the live video's own id
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True, "logger": _Quiet(),
                                   **media.ydl_auth()}) as y:
                info = y.extract_info(url, download=False, process=False) or {}
        except Exception:  # noqa: BLE001 - offline or blocked: no chat, the recording goes on
            return
        vid = info.get("id") or info.get("display_id") or _youtube_video_id(
            info.get("webpage_url") or info.get("original_url") or "")
        if vid and re.fullmatch(r"[\w-]{11}", str(vid)):
            self._yt_chat(str(vid))

    def stop(self, note="Stopped by you"):
        """Instant for the user: the card and the creator show as stopped straight away. Clips already being made
        still finish in the background, but nothing new is transcribed or sent to the AI."""
        self.end_note = note
        self.user_stopped = True
        self.stop_flag.set()
        with self.lock:
            self.job.update(status="done", stage=note)
        jobs.save(self.job)
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    def _ingest(self):
        """Record the stream. However this ends, even when ffmpeg cannot start at all, the session is marked as
        ended, so it stops showing as recording and gives its place to the next one."""
        try:
            self._ingest_loop()
        except Exception as e:  # noqa: BLE001
            self.job["error"] = f"Recording stopped: {media_safe(e)}"
        finally:
            self.ended = True

    def _ingest_loop(self):
        fails = 0
        url = self.job["source"]["url"]
        while not self.stop_flag.is_set():
            try:
                info = resolve(url)
            except NotLive as e:
                fails += 1
                if self.samples == 0 or fails >= 3:
                    if self.samples == 0:
                        self.job["error"] = f"Could not start recording: {media_safe(e)}"
                    break
                self.stop_flag.wait(10)
                continue
            if not self.job.get("uploader"):
                self.job.update(title=info["title"], uploader=info["uploader"])
            self.part += 1
            with self.lock:
                self.seg_clock = max(self.seg_clock, self.samples / SR)
            p = f"p{self.part:02d}"
            cmd = ["ffmpeg", "-v", "error", "-reconnect", "1", "-reconnect_streamed", "1",
                   "-reconnect_delay_max", "10"]
            for u in info["urls"]:
                cmd += ["-i", u]
            a_in = "1:a:0" if len(info["urls"]) > 1 else "0:a:0"
            cmd += ["-map", "0:v:0", "-map", a_in, "-c", "copy", "-f", "segment", "-segment_time", str(SEG_SEC),
                    "-reset_timestamps", "1", "-segment_format", "mpegts", "-segment_list", f"{p}.csv",
                    "-segment_list_type", "csv", f"{p}_%05d.ts",
                    "-map", a_in, "-vn", "-ac", "1", "-ar", str(SR), "-f", "s16le", "pipe:1"]
            with open(self.dir / "ffmpeg.log", "ab") as errlog:
                self.proc = subprocess.Popen(cmd, cwd=self.buf, stdout=subprocess.PIPE, stderr=errlog,
                                             creationflags=media.NO_WINDOW)
                got = 0
                while True:
                    data = self.proc.stdout.read(SR * 2)  # one second of 16-bit mono
                    if not data:
                        break
                    data = data[: len(data) // 2 * 2]
                    got += len(data)
                    x = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
                    level = float(20 * np.log10(np.sqrt((x ** 2).mean() + 1e-10))) if len(x) else -100.0
                    with self.lock:
                        self.pending += data
                        self.samples += len(data) // 2
                        self.db.append(level)
                self.proc.wait()
            fails = 0 if got > SR * 2 * 20 else fails + 1
            if fails >= 3:
                break
            self.stop_flag.wait(3)
        self.ended = True

    def _scan_segments(self):
        """Pick up buffer pieces ffmpeg has finished writing."""
        with self.lock:
            for i in range(1, self.part + 1):
                f = self.buf / f"p{i:02d}.csv"
                if not f.exists():
                    continue
                rows = [r.split(",") for r in f.read_text(encoding="utf-8", errors="ignore").splitlines() if r.count(",") >= 2]
                for name, a, b in (r[:3] for r in rows[self.csv_seen.get(i, 0):]):
                    try:
                        dur = max(0.0, float(b) - float(a))
                    except ValueError:
                        continue
                    self.segments.append((self.buf / name, self.seg_clock, self.seg_clock + dur))
                    self.seg_clock += dur
                self.csv_seen[i] = len(rows)
            keep_from = self.seg_clock - self.settings["buffer_min"] * 60
            active_starts = [
                min(c["start"], c["start"] - self.clock_fix) - PAD - 15
                for c in self.job.get("clips", [])
                if c.get("status") in ("queued", "rendering") and not c.get("src") and "start" in c
                and (not c.get("made_at") or time.time() - c["made_at"] < 900)
            ]
            active_starts += [
                min(m["start"], m["start"] - self.clock_fix) - PAD - 15
                for m in self.manual
                if "start" in m
            ]
            if active_starts:
                keep_from = min(keep_from, min(active_starts))
            while self.segments and self.segments[0][2] < keep_from:
                self.segments.pop(0)[0].unlink(missing_ok=True)

    # ---------------------------------------------------------------- chat: Twitch, Kick, YouTube
    def _chat(self, channel):
        while not self.stop_flag.is_set() and not self.ended:
            try:
                raw = socket.create_connection(("irc.chat.twitch.tv", 6697), timeout=20)
                s = ssl.create_default_context().wrap_socket(raw, server_hostname="irc.chat.twitch.tv")
                s.sendall(f"NICK justinfan{int(time.time()) % 90000 + 10000}\r\nJOIN #{channel}\r\n".encode())
                s.settimeout(30)
                rest = b""
                self.chat_on = True
                while not self.stop_flag.is_set() and not self.ended:
                    try:
                        data = s.recv(8192)
                    except socket.timeout:
                        continue
                    if not data:
                        break
                    *lines, rest = (rest + data).split(b"\r\n")
                    for line in lines:
                        if line.startswith(b"PING"):
                            s.sendall(b"PONG :tmi.twitch.tv\r\n")
                        elif b" PRIVMSG #" in line:
                            raw = line.decode("utf-8", "replace")
                            who = _NICK.findall(raw)
                            self._on_chat(raw.split(" PRIVMSG #", 1)[1].partition(" :")[2], who[0] if who else "")
                s.close()
            except Exception:  # noqa: BLE001
                pass
            self.chat_on = False
            self.stop_flag.wait(5)

    def _kick_chat(self, slug):
        """Kick chat over its public Pusher websocket, read-only and with no login. Kick hands out the Pusher key per
        request (it changes), so it is asked for again on every reconnect."""
        import websocket
        while not self.stop_flag.is_set() and not self.ended:
            try:
                d = json.loads(_get_json(f"https://kick.com/api/v2/channels/{slug}"))
                room, cid = d["chatroom"]["id"], d["id"]
                body = json.dumps({"client": {"id": "hiclip", "type": "web"},
                                   "capabilities": {"accepted_providers": [{"provider": "pusher"}]}}).encode()
                c = json.loads(_get_json(f"https://web.kick.com/api/v1/realtime/channels/{cid}/chat/connection",
                                         body))["data"]["connections"][0]["credentials"]
                ws = websocket.create_connection(
                    f"wss://ws-{c['cluster']}.pusher.com/app/{c['app_key']}?protocol=7&client=js&version=8.4.0&flash=false",
                    timeout=30, origin="https://kick.com")
                ws.send(json.dumps({"event": "pusher:subscribe", "data": {"auth": "", "channel": f"chatrooms.{room}.v2"}}))
                self.chat_on = True
                while not self.stop_flag.is_set() and not self.ended:
                    try:
                        f = json.loads(ws.recv())
                    except websocket.WebSocketTimeoutException:
                        continue
                    if f.get("event") == "pusher:ping":
                        ws.send(json.dumps({"event": "pusher:pong", "data": {}}))
                    elif "ChatMessageEvent" in f.get("event", ""):
                        m = json.loads(f["data"]) if isinstance(f.get("data"), str) else f.get("data") or {}
                        self._on_chat(m.get("content", ""), (m.get("sender") or {}).get("username", ""))
                ws.close()
            except Exception:  # noqa: BLE001 - offline, blocked or the stream ended: the recording goes on
                pass
            self.chat_on = False
            self.stop_flag.wait(5)

    def _yt_chat(self, video_id):
        """YouTube live chat through yt-dlp (YouTube refuses plain requests for the chat page): yt-dlp writes each
        message to a file as it arrives, and every new line is counted the moment it lands."""
        import subprocess
        import sys
        out = Path(self.job["dir"]) / "chat"
        out.mkdir(parents=True, exist_ok=True)
        for f in out.glob("*.live_chat.json*"):
            f.unlink(missing_ok=True)
        proc = subprocess.Popen([sys.executable, "-m", "yt_dlp", "--quiet", "--no-warnings", "--skip-download", *media.ydl_auth_args(),
                                 "--write-subs", "--sub-langs", "live_chat", "-o", str(out / "chat.%(ext)s"),
                                 f"https://www.youtube.com/watch?v={video_id}"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        pos, path = 0, None
        try:
            while not self.stop_flag.is_set() and not self.ended:
                # yt-dlp appends to chat.live_chat.json.part while the stream runs (the -FragN files come and go)
                path = next((f for f in (out / "chat.live_chat.json.part", out / "chat.live_chat.json") if f.exists()), None)
                if path:
                    with open(path, "rb") as f:
                        f.seek(pos)
                        chunk = f.read()
                    end = chunk.rfind(b"\n") + 1           # only whole lines; the rest is read next time
                    pos += end
                    second = int(self.samples / SR)
                    for line in chunk[:end].splitlines():
                        try:
                            acts = json.loads(line).get("replayChatItemAction", {}).get("actions", [])
                        except ValueError:
                            continue
                        for act in acts:
                            r = act.get("addChatItemAction", {}).get("item", {}).get("liveChatTextMessageRenderer")
                            if not r:
                                continue
                            text = "".join(x.get("text") or _emoji_text(x.get("emoji") or {})
                                           for x in r.get("message", {}).get("runs", []))
                            if text.strip().startswith("!"):   # bot commands (!giveaway): a raffle, not hype
                                continue
                            author = r.get("authorExternalChannelId") or (r.get("authorName") or {}).get("simpleText", "")
                            self.chat_on = True
                            self._on_chat(text, author, second)
                if proc.poll() is not None and not path:
                    return                                # yt-dlp could not open this stream's chat
                self.stop_flag.wait(2.0)
        finally:
            proc.terminate()
            self.chat_on = False

    # ---------------------------------------------------------------- analysis
    def _transcribe_step(self):
        with self.lock:
            n = len(self.pending) // 2
            hurry = self.ended or bool(self.manual)
            if n < (SR if hurry else SR * 7):   # about every 7 s, so "what was just said" really is live
                return False
            audio = np.frombuffer(bytes(self.pending[: n * 2]), dtype=np.int16)
            base = self.pending_base
        cut = n
        if not hurry or n > SR * 24:
            # cut at the quietest moment between 4 and 6.5 s, so no word is split; the rest waits for the next pass
            _, cut = next(transcribe._chunks(audio, 4.0, 6.5))
        words = transcribe.transcribe(audio[:cut], lang=self._speech_lang())
        off = base / SR
        for w in words:
            w["s"], w["e"] = round(w["s"] + off, 2), round(w["e"] + off, 2)
        with self.lock:
            del self.pending[: cut * 2]
            self.pending_base += cut
            self.words += words
            self.t_done = self.pending_base / SR
        self._triggers(words)
        (self.dir / "words.json").write_text(json.dumps(self.words), encoding="utf-8")
        return True

    AUTO_BEFORE, AUTO_AFTER, AUTO_GAP = 30, 20, 60   # seconds of stream sent around a jump; least time between jumps
    JUMP = 0.6                                        # 60% louder or busier than the last three minutes
    HYPE_MIN = 0.5                                    # hype messages a second before a jump counts (30 a minute)
    # Chat this far above its normal has voted: clip it without waiting for the words judge, which cannot see a
    # cutscene or a streamer going "woahhh". ponytail: one fixed bar; a per-creator setting if streams need different.
    CROWD_X = 2.0
    CROWD_BEFORE, CROWD_AFTER = 25, 8                 # chat reacts late: most of the clip is before the burst
    CROWD_LOOK, CROWD_WAIT = 90, 15                   # the AI reads this much before the burst, and after it

    def _watch_spikes(self):
        """Always on. Keep a running average of loudness and chat over the last three minutes (leaving out the
        last few seconds), and mark a moment when the last five seconds jump a set percentage above it. Nothing is
        sent yet: the AI gets that moment once its transcript is ready, with the half minute before it."""
        with self.lock:
            n = len(self.db)
            if n < 60:
                self.base = {"warming": True}
                return
            db = np.array(self.db[max(0, n - 190):], dtype=np.float32)
            # genuine hype (W, GG, LET'S GO...), not message volume: backseating and questions are busy, not hype
            chat = [self.hype.get(t, 0) for t in range(max(0, n - 190), n)] if (self.chat_on or self.chat_total) else None
        amp = 10 ** (db / 20)                       # loudness as plain level, so a percentage means something
        base, cur = float(amp[:-10].mean()), float(amp[-5:].mean())
        loud_jump = cur / max(base, 1e-6) - 1
        pct = self.JUMP
        why = []
        if loud_jump >= pct:
            why.append(f"loudness +{round(loud_jump * 100)}%")
        chat_base = chat_now = None
        crowd = ""
        if chat is not None:
            chat_base = sum(chat[:-10]) / max(1, len(chat) - 10)
            chat_now = sum(chat[-10:]) / 10        # YouTube hands chat over every few seconds: look a little longer
            if chat_now >= max(self.HYPE_MIN, chat_base * (1 + pct)):
                why.append(f"chat hype +{round((chat_now / max(chat_base, 0.01) - 1) * 100)}%")
                if chat_now >= chat_base * self.CROWD_X:
                    with self.lock:
                        top = collections.Counter(w for t, w in self.hype_said if t >= n - 10).most_common(3)
                    crowd = ", ".join(w for w, _ in top) or "hype"
        self.base = {"loud_db": round(20 * np.log10(max(base, 1e-6)), 1), "loud_now": round(loud_jump * 100),
                     "chat": round(chat_base, 2) if chat_base is not None else None,
                     "chat_now": round(chat_now, 2) if chat_now is not None else None, "pct": round(pct * 100)}
        if why and not self.spike and n - self.last_spike >= self.AUTO_GAP:
            self.spike = {"t": float(n - 3), "why": ", ".join(why), "crowd": crowd}
            self.last_spike = n
            self.spikes += 1

    def _ai_check(self, window=None, why=None):
        """Judge 1 reads only the words. If it finds a moment, judge 2 reads only the loudness and chat activity
        for the same stretch, without seeing the words. A clip is made when both say yes (or as set in Setup)."""
        st = self.settings
        every = int(st.get("ai_every_min", 3)) or 3
        if window:                    # just the stretch around a jump, or around someone saying "clip"
            ws, now = max(0.0, window[0]), window[1]
        else:
            now = self.t_done
            ws = max(0.0, now - max(every, 3) * 60 - 90)  # each check overlaps the last by 90 s, so nothing falls between
        self.last_check = self.t_done
        win = [w for w in self.words if ws <= w["s"] <= now]
        recent = [c for c in self.job["clips"] if c.get("score", 0) > 0 and time.time() - c.get("made_at", 0) < 3600]
        last_clip = max((c.get("made_at", 0) for c in self.job["clips"]), default=self.started_at)
        must = st["must_clip_min"] > 0 and time.time() - last_clip >= st["must_clip_min"] * 60
        verdict = {"t": round(now), "transcript": None, "reaction": None, "trigger": why}
        if len(win) < 8:
            self.skipped += 1
            return self._verdict(verdict, "Skipped: nobody talking")
        if st["live_per_hour"] and len(recent) >= st["live_per_hour"] and not must:  # 0 = no hourly limit
            self.skipped += 1
            return self._verdict(verdict, f"Skipped: {st['live_per_hour']} clips this hour already")
        usage.set_context(self.job["creator"], self.job["title"] or "Live stream")
        title, up = self.job["title"], self.job["uploader"]
        try:
            cands, tok = brain.judge_transcript(win, ws, now, title, up, st["min_len"], st["max_len"], self.job["clips"],
                                                n=1 if every <= 2 else 2, must=must, game=self._game(),
                                                timeout=config.LIVE_AI_TIMEOUT)  # Auto (0) asks for one
        except Exception as e:  # noqa: BLE001
            self.job["ai"]["last_error"] = str(e)[-200:]
            return self._verdict(verdict, "AI error: " + str(e)[-80:])
        self.checks += 1
        self.tokens += tok
        keep = [c for c in cands if c["score"] >= (40 if must else st["min_score"])]
        verdict["transcript"] = max((c["score"] for c in cands), default=0)
        if not keep:
            return self._verdict(verdict, "Words judge: nothing clip-worthy")
        z = brain.loud_zscore(np.array(self.db, dtype=np.float32))
        lo, hi = int(ws), int(now) + 1
        with self.lock:
            chat = [self.hype.get(t, 0) for t in range(lo, hi)] if (self.chat_on or self.chat_total) else None
        unit = "hype"
        try:
            sig, tok2 = brain.judge_signals(z[lo:hi], chat, lo, now, unit=unit, timeout=config.LIVE_AI_TIMEOUT)
            self.checks += 1
            self.tokens += tok2
        except Exception as e:  # noqa: BLE001
            sig = []
            self.job["ai"]["last_error"] = str(e)[-200:]
        added = 0
        for c in keep:
            match = [m for m in sig if m["end"] >= c["start"] - 10 and m["start"] <= c["end"] + 10 and m["score"] >= 50]
            rs = max((m["score"] for m in match), default=0)
            verdict["reaction"] = rs
            both = bool(match)
            ok = both or (st["decide_mode"] == "either" and c["score"] >= 90) or (must and added == 0)
            if not ok:
                self.rejected += 1
                self._verdict(verdict, "Reaction judge said no, not clipped")
                continue
            if any(abs(c["start"] - k["start"]) < st["min_gap_min"] * 60 for k in self.job["clips"]):
                self._verdict(verdict, "Too close to the last clip")
                continue
            why = "Both judges agreed" if both else ("Words judge was very sure" if c["score"] >= 90 and
                                                     st["decide_mode"] == "either" else "Forced: no clip for a while")
            c["judges"] = {"words": c["score"], "reaction": rs or None, "why": why}
            c["reason"] = f"{c['reason']} ({why.lower()}.)"
            self._add_clip(c)
            self._verdict(verdict, "Clipped: " + why.lower())
            added += 1
            must = False
            if added >= (1 if every <= 2 else 2):
                break

    def _crowd_clip(self, sp):
        """Chat erupted (CROWD_X over its normal): it will be clipped, whatever the words judge would score it.
        The clip itself is chosen once the transcript has caught up (_crowd_pick). The creator's gap between clips
        and clips-an-hour limit still hold."""
        st = self.settings
        t = sp["t"]
        start = max(0.0, t - self.CROWD_BEFORE)
        verdict = {"t": round(t), "transcript": None, "reaction": None, "trigger": "Jump: " + sp["why"]}
        hour = [c for c in self.job["clips"] if time.time() - c.get("made_at", 0) < 3600]
        if st["live_per_hour"] and len(hour) >= st["live_per_hour"]:
            self.skipped += 1
            return self._verdict(verdict, f"Skipped: {st['live_per_hour']} clips this hour already")
        taken = [k["crowd_t"] - self.CROWD_BEFORE if "crowd_t" in k else k["start"] for k in self.job["clips"] + self.manual]
        if any(abs(start - k) < st["min_gap_min"] * 60 for k in taken):
            return self._verdict(verdict, "Too close to the last clip")
        self.manual.append({"start": max(0.0, t - self.CROWD_LOOK), "end": t + self.CROWD_WAIT, "crowd_t": t,
                            "why": f"Chat erupted ({sp['crowd']})"})
        self._verdict(verdict, f"Clipping: chat erupted ({sp['crowd']})")

    def _verdict(self, v, result):
        self.verdicts.append(dict(v, result=result))
        del self.verdicts[:-6]

    CLIP_WORDS = re.compile(r"\bclip(\s+(it|that|this))?\b", re.I)
    SAID_BEFORE, SAID_AFTER, SAID_GAP = 60, 15, 45

    def _triggers(self, new_words):
        """Built in: when someone says "clip" ("clip it", "clip that"), the AI looks at that moment as soon as its
        transcript is ready; people say it right after something worth clipping. Then the creator's own trigger
        phrases from Setup, which clip straight away with no AI check."""
        for w in new_words or []:
            if self.CLIP_WORDS.search(re.sub(r"[^a-z' ]", "", w["w"].lower())) and w["s"] - self.last_said > self.SAID_GAP \
                    and not self.said:
                self.said = {"t": w["s"], "why": 'Someone said "clip"'}
                self.last_said = w["s"]
        phrases = [p.strip().lower() for p in self.settings.get("trigger_words", "").split(",") if p.strip()]
        if not phrases or not new_words:
            return
        key = [re.sub(r"[^a-z0-9']", "", w["w"].lower()) for w in new_words]
        for p in phrases:
            parts = [re.sub(r"[^a-z0-9']", "", x) for x in p.split()]
            for i in range(len(key) - len(parts) + 1):
                if key[i:i + len(parts)] == parts:
                    t = new_words[i]["s"]
                    if t - self.last_trigger > 45:
                        self.last_trigger = t
                        self.manual.append({"start": max(0.0, t - 30), "end": t + 2, "why": f'Someone said "{p}"'})

    def _crowd_pick(self, req):
        """The clip chat erupted over. The AI reads the stretch before the burst and picks where the story starts
        and where the payoff ends, in whole sentences. With too few words (a cutscene, a streamer only going
        "woahhh") or no AI, the moment just before the burst is taken, still cut on whole sentences."""
        st, t = self.settings, req["crowd_t"]
        end_at = min(req["end"], self.samples / SR)
        win = [w for w in self.words if req["start"] <= w["s"] <= end_at]
        try:
            cands, tok = brain.judge_transcript(win, req["start"], end_at, self.job["title"], self.job["uploader"],
                                                st["min_len"], st["max_len"], self.job["clips"], n=1, must=True,
                                                game=self._game(), timeout=config.LIVE_AI_TIMEOUT, chat_at=t)
            self.checks += 1
            self.tokens += tok
            # it must hold what chat reacted to: the seconds just before the burst
            c = next((c for c in cands if c["start"] <= t - 3 and c["end"] >= t - self.CROWD_AFTER), None)
            if c:
                c["reason"] = f"{req['why']}. {c.get('reason', '')}".strip()
                return c
        except Exception as e:  # noqa: BLE001
            self.job["ai"]["last_error"] = str(e)[-200:]
        start = max(0.0, t - self.CROWD_BEFORE)
        lines = brain.make_lines(win)
        s, e = (brain._snap({"start": start, "end": t + self.CROWD_AFTER}, lines, win, st["min_len"], st["max_len"],
                            end_at) if lines else (start, min(t + self.CROWD_AFTER, end_at)))
        c = self._describe(" ".join(w["w"] for w in win if s <= w["s"] <= e))
        c.update(start=s, end=e, score=0, reason=req["why"])
        return c

    def _describe(self, text):
        """Hook and title for a clip that is being made whatever its score. A slow or broken AI costs it its hook,
        not the clip."""
        usage.set_context(self.job["creator"], self.job["title"] or "Live stream")
        try:
            return brain.describe(text, self.job["title"], self.job["uploader"], game=self._game(),
                                  timeout=config.LIVE_AI_TIMEOUT)
        except Exception as e:  # noqa: BLE001
            self.job["ai"]["last_error"] = str(e)[-200:]
            return {"score": 0, "category": "other", "hook": "", "title": self.job["title"],
                    "description": "", "hashtags": [], "highlight_words": []}

    def _manual_step(self):
        for req in [m for m in self.manual if m["end"] <= self.t_done or self.ended]:
            if req.get("crowd_t") is not None:
                usage.set_context(self.job["creator"], self.job["title"] or "Live stream")
                c = self._crowd_pick(req)
                self.manual.remove(req)
                self._add_clip(c)
                continue
            c = self._describe(" ".join(w["w"] for w in self.words if req["start"] <= w["s"] <= req["end"]))
            self.manual.remove(req)
            c.update(start=round(req["start"], 2), end=round(min(req["end"], self.samples / SR), 2), score=0)
            if req.get("why"):
                c["reason"] = req["why"]
            self._add_clip(c)

    def _add_clip(self, c):
        self.n += 1
        c.update(id=f"clip{self.n:03d}", style=self.settings.get("style", "punch"),
                 layout=self.settings.get("layout", "band"),
                 captions=self.job.get("options", {}).get("captions", True),
                 hinglish=self.job.get("options", {}).get("hinglish", False),
                 status="queued", version=0, made_at=time.time())
        with self.lock:
            self.job["clips"].append(c)
        jobs.save(self.job)
        jobs.submit_live(lambda: self._render(c))

    def _render(self, clip):
        """Runs on the render worker: copy the needed buffer pieces, then make the clip."""
        deadline = time.time() + 45
        target_wait_end = max(clip["end"], clip["end"] - self.clock_fix)
        while time.time() < deadline:
            self._scan_segments()
            if self.ended or (self.segments and self.segments[-1][2] >= target_wait_end + 1):
                break
            time.sleep(1)
        pad = max(PAD, 15) + abs(self.clock_fix)
        t_start = min(clip["start"], clip["start"] - self.clock_fix) - pad
        t_end = max(clip["end"], clip["end"] - self.clock_fix) + pad
        with self.lock:
            segs = [s for s in self.segments if s[2] > t_start and s[1] < t_end and s[0].exists()]
        if not segs:
            clip.update(status="error", error="That part of the stream is no longer in the buffer.")
            jobs.save(self.job)
            return
        total_dur = segs[-1][2] - segs[0][1]
        if total_dur < 3.0:
            clip.update(status="error", error="That part of the stream is no longer in the buffer.")
            jobs.save(self.job)
            return
        if "width" not in self.job:
            meta = media.probe(segs[0][0])
            self.job.update(width=meta["width"], height=meta["height"])
        lst = self.dir / "clips" / f"{clip['id']}.txt"
        lst.write_text("".join(f"file '{s[0].as_posix()}'\n" for s in segs), encoding="utf-8")
        src = self.dir / "clips" / f"{clip['id']}.src.ts"
        try:
            media.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                       "-c", "copy", str(src)])
        except Exception as e:  # noqa: BLE001
            clip.update(status="error", error=media_safe(e))
            jobs.save(self.job)
            return
        finally:
            lst.unlink(missing_ok=True)
        dur = segs[-1][2] - segs[0][1]
        offset = self._align(src, segs[0][1] + self.clock_fix, dur)
        self.clock_fix = offset - segs[0][1]  # the two clocks drift ~4 s an hour; start the next clip from here
        clip.update(src=str(src), src_offset=round(offset, 2), src_end=round(offset + dur, 2))
        clip["start"] = max(clip["start"], clip["src_offset"])
        clip["end"] = min(clip["end"], clip["src_end"])
        if clip["end"] - clip["start"] < 3.0:
            if clip["src_end"] - clip["src_offset"] >= 3.0:
                clip["end"] = min(clip["src_end"], clip["start"] + min(30.0, clip["src_end"] - clip["src_offset"]))
                if clip["end"] - clip["start"] < 3.0:
                    clip["start"] = max(clip["src_offset"], clip["end"] - 3.0)
            else:
                clip.update(status="error", error="That part of the stream is no longer in the buffer.")
                jobs.save(self.job)
                return
        jobs.render_one(self.job, clip, list(self.words))

    def _speech_lang(self):
        """"hi" when this creator speaks Hindi or Hinglish: their speech then goes to Whisper."""
        return "hi" if self.job.get("options", {}).get("hinglish") else ""

    def _align(self, src, guess, dur):
        """Where `src` really starts on the transcript clock. The video pieces and the audio feed keep separate
        clocks that drift apart, so transcribe the clip's own audio and line its words up with the live words.
        Local speech model only: no AI calls."""
        key = lambda w: re.sub(r"[^a-z0-9]", "", roman.to_roman(w).lower())   # Hindi script matches too
        try:
            wav = src.with_suffix(".wav")
            media.extract_audio(src, wav)
            heard = transcribe.transcribe(media.load_audio(wav), lang=self._speech_lang())
            wav.unlink(missing_ok=True)
            with self.lock:
                near = [w for w in self.words if guess - 15 <= w["s"] <= guess + dur + 15]
            diffs = []
            for h in heard:
                k = key(h["w"])
                if len(k) < 3:
                    continue
                m = [w["s"] for w in near if key(w["w"]) == k and abs(w["s"] - (guess + h["s"])) < 8]
                if m:
                    diffs.append(min(m, key=lambda s: abs(s - (guess + h["s"]))) - h["s"])
            if len(diffs) >= 4:
                return float(np.median(diffs))
        except Exception:  # noqa: BLE001 - fall back to the piece clock
            pass
        return guess

    def clip_last(self, seconds):
        end = self.samples / SR
        if end < 5:
            raise ValueError("Nothing has been recorded yet.")
        self.manual.append({"start": max(0.0, end - seconds), "end": end})

    HYPE_NOW = 30      # seconds of chat that count as "now": YouTube hands chat over in batches every few seconds
    HYPE_NORMAL = 180  # the stretch before that, which counts as "normal" for this stream

    def _on_chat(self, text: str, who: str = "", second: int | None = None):
        """One chat message, from any site: counted for chat activity and, when it is, for hype."""
        with self.lock:
            second = int(self.samples / SR) if second is None else second
            self.chat[second] += 1
            self.chat_total += 1
            self._count_hype(second, text, who.lower())
            self._chat_language(text)

    def _chat_language(self, text: str):
        """Once enough chat has been read: a Hindi chat switches this recording's speech to Hindi (Whisper), so what
        the streamer says is heard. Only ever switches on; the creator's own setting wins when it is on already.
        Called under the lock."""
        if self.lang_seen > CHAT_LANG_AFTER or len(text.split()) < 2:
            return
        self.lang_seen += 1
        self.lang_hindi += bool(HINDI_CHAT.search(text))
        opts = self.job.setdefault("options", {})
        if self.lang_seen == CHAT_LANG_AFTER and not opts.get("hinglish") and self.lang_hindi >= CHAT_HINDI_SHARE * CHAT_LANG_AFTER:
            opts["hinglish"] = True
            self.job["lang_auto"] = "hi"
            if self.job.get("creator"):
                creators.log(self.job["creator"], f"Chat is {self.lang_hindi}% Hindi: listening for Hindi speech")

    def _count_hype(self, second: int, text: str, who: str = ""):
        """Count one chat message as hype when it is, once per person per second. Called under the lock, from the
        chat reader threads."""
        label = hype_label(text)
        if not label:
            return
        if who:
            seen = self.hype_users.setdefault(second, set())
            if who in seen:
                return
            seen.add(who)
            self.hype_kept = _trim_names(self.hype_users, second, self.hype_kept)
        self.hype[second] += 1
        self.hype_said.append((second, label))

    def _hype(self, sec: int) -> dict | None:
        """Genuine hype in chat now (W, GG, LET'S GO, 🔥...) against this stream's normal, in messages a minute,
        with the words chat is using. Called under the lock."""
        if not (self.chat_on or self.chat_total):
            return None
        a = max(0, sec - self.HYPE_NOW)
        now = sum(self.hype.get(t, 0) for t in range(a, sec)) * 60 / self.HYPE_NOW
        said = sum(self.chat.get(t, 0) for t in range(a, sec))
        top = collections.Counter(label for t, label in self.hype_said if t >= a).most_common(5)
        bars = [sum(self.hype.get(t, 0) for t in range(b, b + 5)) for b in range(max(0, sec - 60), sec, 5)]
        out = {"now": round(now), "normal": None, "x": None, "share": round(100 * now * self.HYPE_NOW / 60 / said) if said else 0,
               "top": [[label, n] for label, n in top], "bars": bars}
        if a >= 60:                                    # enough of the stream to know its normal
            b = max(0, a - self.HYPE_NORMAL)
            normal = sum(self.hype.get(t, 0) for t in range(b, a)) * 60 / (a - b)
            out.update(normal=round(normal), x=round(now / normal, 1) if normal >= 1 else None)
        return out

    def _publish(self):
        elapsed = self.samples / SR
        lines = brain.make_lines([w for w in self.words if w["s"] >= self.t_done - 45])[-5:]
        line_texts = [roman.to_roman(l["text"]) if self.job.get("options", {}).get("hinglish") else l["text"] for l in lines]
        with self.lock:
            sec = int(elapsed)
            self.job.update(
                duration=elapsed,
                live={"elapsed": round(elapsed), "buffer_sec": round(self.segments[-1][2] - self.segments[0][1]) if self.segments else 0,
                      "lines": line_texts, "level": [round(x) for x in self.db[-60:]],
                      "chat": [self.chat.get(t, 0) for t in range(max(0, sec - 60), sec)] if self.chat_on or self.chat_total else None,
                      "hype": self._hype(sec),
                      "checks": self.checks, "skipped": self.skipped, "tokens": self.tokens, "words": len(self.words),
                      "rejected": self.rejected, "verdicts": list(reversed(self.verdicts)),
                      "every_min": int(self.settings.get("ai_every_min", 3)) or 3,
                      "auto": None,
                      "pending_clips": len(self.manual)})
        jobs.save(self.job)

    def _analyze(self):
        cid = self.job["creator"]
        started = False
        while True:
            busy = False
            try:
                if not self.user_stopped and not self.ended:      # a recording cannot run for ever
                    if time.time() - self.started_at > config.MAX_LIVE_HOURS * 3600:
                        self.stop(f"Stopped after {config.MAX_LIVE_HOURS:g} hours, the longest one recording can run")
                self._scan_segments()
                busy = self._transcribe_step()
                if not started and self.samples:
                    started = True
                    self.job.update(status="live", stage="Recording")
                    creators.log(cid, f"Recording started: {self.job['title']}")
                self._manual_step()
                # Three ways the AI is asked: on the creator's schedule (every N minutes), and always, when the
                # stream suddenly gets louder or chat busier, or when someone says "clip".
                every = int(self.settings.get("ai_every_min", 3)) or 3
                self._watch_spikes()
                sp, said = self.spike, self.said
                if self.user_stopped:
                    pass
                elif said and (self.t_done >= said["t"] + self.SAID_AFTER or (self.ended and not busy)):
                    self.said = None
                    self._ask_ai((said["t"] - self.SAID_BEFORE, said["t"] + self.SAID_AFTER), said["why"])
                elif sp and (self.t_done >= sp["t"] + self.AUTO_AFTER or (self.ended and not busy)):
                    self.spike = None
                    if sp.get("crowd"):
                        self._crowd_clip(sp)
                    else:
                        self._ask_ai((sp["t"] - self.AUTO_BEFORE, sp["t"] + self.AUTO_AFTER), "Jump: " + sp["why"])
                elif self.t_done - self.last_check >= every * 60 or (self.ended and not busy and self.t_done > self.last_check):
                    self._ask_ai()
                self._publish()
            except Exception as e:  # noqa: BLE001
                self.job["ai"]["last_error"] = str(e)[-200:]
            # Leaving here must not happen while an AI check is still running: it may still be adding a
            # clip, and cleanup() deletes the buffer that clip is cut from. That is true after a stop as
            # well as at the end of a stream, so both wait for it. Stopping is still instant on the page:
            # stop() has already written the status.
            if ((self.user_stopped and not self.manual and not self.ai_busy)
                    or (self.ended and not busy and not self.ai_busy
                        and len(self.pending) < SR * 2 and not self.manual)):
                break
            if not busy:
                time.sleep(2)
        failed = bool(self.job.get("error")) and not started
        self.job.update(status="error" if failed else "done", stage="Could not record" if failed else self.end_note)
        jobs.save(self.job)
        creators.log(cid, f"Recording finished ({self.end_note.lower()}): {len(self.job['clips'])} clips, "
                          f"{round(self.samples / SR / 60)} min" if not failed else f"Recording failed: {self.job['error']}")

        if failed:
            alerts.notify(f"recording failed for {self.job['source']['name']}: {self.job['error']}")
        else:
            alerts.notify(f"recording finished for {self.job['source']['name']}: {len(self.job['clips'])} clips, "
                          f"{round(self.samples / SR / 60)} min.")

        def cleanup():  # runs after any queued renders, then frees the buffer
            for f in self.buf.glob("*"):
                f.unlink(missing_ok=True)
            self.finished = True
            SESSIONS.pop(self.job["id"], None)

        jobs.submit_live(cleanup)


def recover(job):
    """After a restart: finish the clips of an interrupted recording from the stream pieces still on disk."""
    c = creators.get(job["creator"]) or dict(creators.DEFAULTS)
    s = Session(job, c)
    s.ended, s.end_note = True, "Recording stopped when the studio closed"
    s.part = len(list(s.buf.glob("p*.csv")))
    s.seg_clock = 0.0
    s.csv_seen = {}
    with s.lock:
        for i in range(1, s.part + 1):
            f = s.buf / f"p{i:02d}.csv"
            rows = [r.split(",") for r in f.read_text(encoding="utf-8", errors="ignore").splitlines() if r.count(",") >= 2]
            for name, a, b in (r[:3] for r in rows):
                try:
                    dur = max(0.0, float(b) - float(a))
                except ValueError:
                    continue
                s.segments.append((s.buf / name, s.seg_clock, s.seg_clock + dur))
                s.seg_clock += dur
            s.csv_seen[i] = len(rows)
    try:
        s.words = json.loads((s.dir / "words.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        s.words = []
    s.n = len(job["clips"])
    todo = [k for k in job["clips"] if k.get("status") == "queued"]
    for k in todo:
        jobs.submit_live(lambda k=k: s._render(k))

    def cleanup():
        for f in s.buf.glob("*"):
            f.unlink(missing_ok=True)
        job.pop("recover", None)
        jobs.save(job)
        creators.log(job["creator"], f"Finished {len(todo)} clip(s) from a recording interrupted by a restart")

    jobs.submit_live(cleanup)


def start_session(creator: dict, url=None) -> dict:
    url = normalize(url or creator.get("channel") or "")
    if not re.match(r"^https?://[^/]+\.[^/]+/", url + "/"):
        raise ValueError("Add this creator's channel link first.")
    if active_for(creator["id"]):
        raise ValueError("This creator is already being recorded.")
    if not media.site_allowed(url):
        raise ValueError("Channel links must be from YouTube, Twitch or Kick.")
    if media.is_youtube(url) and media.youtube_paused():
        raise ValueError(media.YT_BLOCKED.replace("download videos", "record YouTube streams")
                         .replace("Upload the video file instead, or use a Twitch or Kick link. YouTube links",
                                  "Twitch and Kick streams still work. YouTube"))
    if len(active_mine()) >= config.MAX_LIVE_PER_USER:
        raise ValueError(f"You're already recording {len(active_mine())} streams, the most this server allows at once. Stop one first.")
    if len(active()) >= config.MAX_LIVE_TOTAL or (active() and load.busy()):
        raise ValueError("hiclip is recording as many streams as it can right now. Try again in a little while.")
    from . import tools
    if tools.missing():
        raise ValueError(tools.HELP)
    opts = {
        "style": creator.get("style", "punch"),
        "layout": creator.get("layout", "band"),
        "captions": creator.get("captions", True),
        "hinglish": creator.get("hinglish", False),
        "min_len": creator.get("min_len", 15),
        "max_len": creator.get("max_len", 60),
    }
    job = jobs.create({"type": "live", "url": url, "name": f"{creator['name']} live"}, opts, enqueue=False,
                      creator=creator["id"], kind="live")
    job.update(status="live", stage="Connecting to the stream")
    jobs.save(job)
    Session(job, creator).start()
    return job


def start_stream_session(job: dict, url: str) -> "Session":
    """Start an ongoing live monitoring and clipping session for a job."""
    url = normalize(url)
    if not media.site_allowed(url):
        raise ValueError("Channel links must be from YouTube, Twitch or Kick.")
    if media.is_youtube(url) and media.youtube_paused():
        raise ValueError(media.YT_BLOCKED)
    if len(active_mine()) >= config.MAX_LIVE_PER_USER:
        raise ValueError(f"You're already recording {len(active_mine())} streams, the most this server allows at once. Stop one first.")
    if len(active()) >= config.MAX_LIVE_TOTAL or (active() and load.busy()):
        raise ValueError("hiclip is recording as many streams as it can right now. Try again in a little while.")
    from . import tools
    if tools.missing():
        raise ValueError(tools.HELP)
    settings = dict(creators.DEFAULTS)
    settings.update(job.get("options") or {})
    if not settings.get("name"):
        settings["name"] = job.get("uploader") or job.get("title") or "Live Stream"
    settings["channel"] = url
    session = Session(job, settings)
    session.start()
    return session


def _watch_user(uid):
    """Start recording any of this user's auto-record creators who have gone live."""
    from . import profiles
    with ctx.as_user(uid):
        try:
            todo = [c for c in creators.all_creators() if c["auto_watch"] and c["channel"]]
            if not todo:
                return
        except Exception:  # noqa: BLE001
            return
        for c in todo:
            _watch_one(c)


def check_now(cid):
    """Auto-record was just turned on: look now, on a thread, instead of waiting for the next round."""
    uid = ctx.uid()

    def run():
        from . import profiles
        with ctx.as_user(uid):
            c = creators.get(cid)
            if not c or not c["auto_watch"] or not c["channel"]:
                return
            for url in c.get("channels", {}).values():
                profiles._live.pop(url.rstrip("/").lower(), None)     # a fresh answer, not one from two minutes ago
            _watch_one(c)
            profiles.refresh(cid, force=True)
    threading.Thread(target=run, daemon=True).start()


def _watch_one(c):
    """Start recording this auto-record creator if they are live on any of their channels."""
    from . import profiles
    try:
        if active_for(c["id"]) or len(active_mine()) >= config.MAX_LIVE_PER_USER or len(active()) >= config.MAX_LIVE_TOTAL or (active() and load.busy()):
            return
        # the chosen channel first, then their other platforms
        urls = [c["channel"]] + [u for u in c.get("channels", {}).values() if u != c["channel"]]
        for url in urls:
            # one shared "are they live?" answer per channel, however many people follow it
            if not profiles.live_shared(profiles.platform_of(url), url).get("live"):
                continue
            if media.is_youtube(url):
                # check YouTube will hand over the stream before making a recording that could only fail
                if media.youtube_paused():
                    continue
                try:
                    resolve(normalize(url))
                except NotLive:
                    continue
            start_session(c, url)
            where = creators.platform_key(url).title()
            creators.log(c["id"], f"Went live on {where}: recording started automatically")
            alerts.notify(f"{c['name']} went live on {where}, recording started.")
            break
    except NotLive:
        pass
    except Exception as e:  # noqa: BLE001
        creators.log(c["id"], f"Auto-record check failed: {str(e)[-150:]}")


def watch_loop():
    """Every two minutes, start recording any creator set to auto-record who has gone live. Covers every user,
    several at a time, so the round stays short however many accounts there are."""
    from concurrent.futures import ThreadPoolExecutor
    time.sleep(20)
    pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="watch")
    while True:
        t0 = time.time()
        try:
            list(pool.map(_watch_user, auth.user_ids()))
        except Exception:  # noqa: BLE001
            pass
        time.sleep(max(15, 120 - (time.time() - t0)))


def _resume_recordings(paused):
    """Recordings that were running when the server stopped: start recording the same channel again, if the
    stream is still on. Tried for a few minutes, since the server may come back a little before the network."""
    time.sleep(10)
    for _ in range(6):
        left = []
        for uid, cid, url in paused:
            with ctx.as_user(uid):
                try:
                    c = creators.get(cid)
                    if not c or active_for(cid):
                        continue
                    resolve(normalize(url))      # raises NotLive if the stream has ended: no empty recording is made
                    start_session(c, url)
                    creators.log(cid, "Recording picked up again after a server update")
                except NotLive:
                    continue                  # the stream ended while the server was away
                except Exception:  # noqa: BLE001 - try again shortly
                    left.append((uid, cid, url))
        if not left:
            return
        paused = left
        time.sleep(30)


def start():
    paused = []
    for j in jobs.all_jobs():
        if j.get("recover"):
            with ctx.as_user(j["user"]):
                recover(j)
        if j.get("was_live") and j.get("creator"):
            paused.append((j["user"], j["creator"], j["source"]["url"]))
            j.pop("was_live", None)
            jobs.save(j)
    if paused:
        threading.Thread(target=_resume_recordings, args=(paused,), daemon=True).start()
    threading.Thread(target=watch_loop, daemon=True).start()

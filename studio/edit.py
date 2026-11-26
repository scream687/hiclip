"""The edit pass, run on a rendered clip: punch-in zooms on the loud moments and the creator-style ending the
reference Shorts use (glitch, black-and-white reaction with the facecam blown up, emoji, slow-mo replay, TV-bars
outro), meme sounds, a ducked music bed, and loudness levelled in two passes so every clip lands at -14 LUFS."""
import json
import logging
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np

from .media import NO_WINDOW

log = logging.getLogger("studio.edit")

W, H = 1080, 1920
FX_DEFAULT = {"punch": True, "finale": True, "outro": True, "music": True, "emoji": True}
FX_KEYS = tuple(FX_DEFAULT)
LUFS, TP, LRA = -14, -1.5, 11
# the reference Shorts are compressed (loudness range 1.6-7 LU): this evens bangs and whispers before levelling,
# so the linear (clean) levelling can reach -14 without its peak limit stopping it short
COMPRESS = "acompressor=threshold=-22dB:ratio=3.5:attack=8:release=220:makeup=3dB"

ZOOM, ZIN, ZOUT, HOLD = 0.10, 0.18, 0.30, 0.45   # punch-in: +10 % in 0.18 s, held, eased back over 0.3 s
REPLAY_BACK, REPLAY_FWD, REPLAY_SPEED = 1.6, 0.4, 0.55
BASS_HIT = 9            # dB of low end on the payoff hit, the references' bass-boosted reaction
OUTRO = 0.45
REACT_MAX = 3.6                                   # the reaction after the payoff, as long as the references hold it
GLITCH = 0.28
MUSIC_VOL = 0.22                                 # under the voice before ducking; ducking takes it lower while people talk

EMOJI = {"funny": "😭", "fail": "💀", "shock": "😳", "rage": "😤", "clutch": "🔥", "hype": "🔥", "wholesome": "🥹",
         "sad": "😭", "emotional": "🥹", "scary": "😱"}   # all bundled in studio/emoji (Fluent 3D, from PeakCut)
MUSIC = {"funny": "funny sad music", "fail": "spongebob fail", "sad": "sad violin", "emotional": "sad violin",
         "shock": "suspense", "scary": "suspense", "rage": "phonk", "clutch": "phonk", "hype": "phonk"}
EMOJI_FONTS = ("/System/Library/Fonts/Apple Color Emoji.ttc", "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
               "/usr/share/fonts/noto/NotoColorEmoji.ttf", "C:/Windows/Fonts/seguiemj.ttf")


def fx_options(raw) -> dict:
    """The clip's edit switches, only known keys, each on or off."""
    out = dict(FX_DEFAULT)
    if isinstance(raw, dict):
        out.update({k: bool(v) for k, v in raw.items() if k in FX_DEFAULT})
    elif raw is False:
        out = {k: False for k in FX_DEFAULT}
    return out


def _run(cmd, cwd, timeout=900):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       creationflags=NO_WINDOW, timeout=timeout)
    if p.returncode:
        raise RuntimeError(p.stderr[-1500:])
    return p


def audio_db(path, cwd=None) -> np.ndarray:
    """Loudness per 50 ms."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                         capture_output=True, cwd=cwd, creationflags=NO_WINDOW, timeout=300).stdout
    x = np.frombuffer(raw[: len(raw) // 800 * 800], np.int16).astype(np.float32).reshape(-1, 400)
    return 10 * np.log10((x ** 2).mean(1) + 1) if len(x) else np.zeros(0)


def payoff(db, dur) -> float:
    """The loudest second in the back half of the clip, never in the first 2 s or the last 0.6 s."""
    if len(db) < 40:
        return max(0.5, dur * 0.7)
    sm = np.convolve(db, np.ones(20) / 20, "same")
    lo, hi = int(max(dur * 0.5, 2.0) * 20), int(max(dur - 0.6, 2.5) * 20)
    hi = min(max(hi, lo + 1), len(sm))
    if lo >= hi:
        return max(0.5, dur * 0.7)
    # the onset of the loud second, not its middle: the cut lands as the moment hits
    i = int(np.argmax(sm[lo:hi])) + lo
    a = max(0, i - 20)
    j = a + int(np.argmax(db[a:i + 1] >= db[a:i + 1].max() - 6))
    return round(max(0.5, j / 20 - 0.1), 2)


def peaks(db, dur, before, n=2, gap=4.0) -> list:
    """Reaction moments (150 ms spots 6 dB over the clip's typical level) to punch in on, before the payoff."""
    if len(db) < 40:
        return []
    sm, base, out = np.convolve(db, np.ones(3) / 3, "same"), float(np.median(db)), []
    for i in np.argsort(-sm):
        t = i / 20
        if sm[i] < base + 6 or len(out) == n:
            break
        if 1.5 <= t <= min(before - 1.0, dur - 1.0) and all(abs(t - u) >= gap for u in out):
            out.append(round(t, 2))
    return sorted(float(t) for t in out)


MAX_STILL = 7.0   # the references never hold one framing longer than this


def fill_gaps(db, zooms, end) -> list:
    """Extra punch-ins on the loudest spot of any stretch longer than MAX_STILL with nothing happening."""
    out = sorted(zooms)
    for _ in range(10):
        marks = [0.0] + out + [end]
        gaps = [(a, b) for a, b in zip(marks, marks[1:]) if b - a > MAX_STILL]
        if not gaps or len(db) < 40:
            return out
        a, b = gaps[0]
        lo, hi = int((a + 2.0) * 20), int(min(b - 2.0, len(db) / 20) * 20)
        t = (lo + int(np.argmax(db[lo:hi]))) / 20 if hi > lo else (a + b) / 2
        out = sorted(out + [round(float(t), 2)])
    return out


def zoom_expr(ts, hit=None) -> str:
    """Eased punch-ins as one scale+crop on whole pixels (no zoompan, so no wobble). `hit` snaps harder."""
    if not ts:
        return ""
    u = lambda x: f"clip({x},0,1)"                                                        # noqa: E731
    ease = lambda x: f"({u(x)}*{u(x)}*(3-2*{u(x)}))"                                     # noqa: E731
    z = "(" + "+".join(f"{1.6 if t == hit else 1}*{ease(f'(t-{t:.3f})/{0.07 if t == hit else ZIN}')}"
                       f"*(1-{ease(f'(t-{t + HOLD:.3f})/{ZOUT}')})" for t in ts) + ")"
    d = lambda n: f"{n}+4*round({n}*{ZOOM}*{z}/4)"                                       # noqa: E731
    return (f"scale=w='{d(W)}':h='{d(H)}':eval=frame:flags=bicubic,"
            f"crop={W}:{H}:'({d(W)}-{W})/2':'round(0.4*({d(H)}-{H}))'")


def make_emoji(e, path, px=230) -> bool:
    """A colour emoji as a PNG (libass can't draw colour glyphs): the bundled 3D set, a system emoji font, else
    Twemoji from the web."""
    from PIL import Image, ImageDraw, ImageFont
    own = Path(__file__).parent / "emoji" / ("-".join(f"{ord(c):x}" for c in e if ord(c) != 0xFE0F) + ".png")
    if own.exists():
        Image.open(own).convert("RGBA").resize((px, px), Image.LANCZOS).save(path)
        return True
    for f in EMOJI_FONTS:
        if not Path(f).exists():
            continue
        for size in (160, 109, 137):   # colour emoji fonts only draw at their own bitmap sizes
            try:
                font = ImageFont.truetype(f, size)
            except OSError:
                continue
            im = Image.new("RGBA", (320, 320), (0, 0, 0, 0))
            ImageDraw.Draw(im).text((20, 20), e, font=font, embedded_color=True)
            if im.getbbox():
                im = im.crop(im.getbbox()).resize((px, px), Image.LANCZOS)
                im.save(path)
                return True
    try:
        import urllib.request
        code = "-".join(f"{ord(c):x}" for c in e if ord(c) != 0xFE0F)
        url = f"https://cdn.jsdelivr.net/gh/jdecked/twemoji@15.1.0/assets/72x72/{code}.png"
        with urllib.request.urlopen(url, timeout=8) as r:
            Path(path).write_bytes(r.read())
        Image.open(path).convert("RGBA").resize((px, px), Image.LANCZOS).save(path)
        return True
    except Exception as e2:  # noqa: BLE001 - no emoji is better than no clip
        log.warning("emoji %s unavailable: %s", e, e2)
        return False


def _sound(query, work: Path, name: str):
    """A MyInstants sound, cached, copied into the work folder. None when offline."""
    try:
        from . import sfx
        item = sfx.find_sound(query)
        if item and item.get("file"):
            dst = work / f"{name}.mp3"
            shutil.copyfile(item["file"], dst)
            return dst.name
    except Exception as e:  # noqa: BLE001
        log.warning("sound %r unavailable: %s", query, e)
    return None


def sentence_stops(words, start) -> list:
    """Clip-relative times where speech pauses: a sentence ends or a breath of 0.45 s follows."""
    ws = sorted((w for w in words or [] if w.get("w")), key=lambda w: w["s"])
    return [round(w["e"] - start, 2) for i, w in enumerate(ws)
            if w["w"].rstrip()[-1:] in ".?!" or i + 1 == len(ws) or ws[i + 1]["s"] - w["e"] >= 0.45]


def react_len(dur, p, stops) -> float:
    """How long the reaction runs: REACT_MAX, stretched to finish the sentence being said (never past 7 s)."""
    want = min(dur - p, REACT_MAX)
    after = [t for t in stops if t >= p + want - 0.3]
    if after and after[0] > p + want:
        want = min(dur - p, after[0] - p + 0.25, 7.0)
    return want


DARK = re.compile(r"\b(creep\w*|scar(y|ed|iest)|spook\w*|horror|haunt\w*|ghost\w*|jump ?scare\w*|terrif\w*|"
                  r"dark(est)?|cursed|demon\w*|nightmare\w*|freak(y|ed|ing)|eerie|chilling|disturb\w*|"
                  r"killer|murder\w*|dead body|blood\w*)\b", re.I)


def is_dark(text: str) -> bool:
    """The black-and-white reaction ending is for dark or creepy moments only (user, 26 Nov): anything else ends
    on the payoff with a punch-in."""
    return bool(DARK.search(text or ""))


def plan(dur, db, fx, has_audio_hit, stops=(), dark=True) -> dict:
    """When things happen: the payoff, the punch-ins before it, and whether the ending fits."""
    p = payoff(db, dur)
    finale = fx["finale"] and dark and dur >= 6 and p >= 2.0
    zooms = peaks(db, dur, p if finale else dur, n=2) if fx["punch"] else []
    if fx["punch"]:
        zooms = fill_gaps(db, zooms, p if finale else dur)
    if fx["punch"] and not finale and p not in zooms and p <= dur - 1.0:
        zooms = sorted(zooms + [p])
    return {"payoff": p, "finale": finale, "zooms": zooms, "boom": not has_audio_hit, "react": react_len(dur, p, stops)}


def _a(label):
    return f"aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[{label}]"


def _cover(chain: str, out: str, clean_h) -> list:
    """`chain` ending in [out], with the caption strip under clean_h covered by a blurred mirror of the picture just
    above it: the reaction and replay in the references have no captions, and ours are already burned in."""
    if not clean_h or not H // 2 <= clean_h < H:
        return [f"{chain}[{out}]"]
    sh = H - clean_h
    return [f"{chain},split[{out}a][{out}b]",
            f"[{out}b]crop={W}:{sh}:0:{clean_h - sh},vflip,gblur=sigma=24,eq=brightness=-0.18[{out}c]",
            f"[{out}a][{out}c]overlay=0:{clean_h}[{out}]"]


def graphs(dur, fps, pl, fx, cam, inputs, clean_h=None, gains=None) -> tuple:
    """(video graph, audio graph, total length). `inputs` maps 'emoji','glitch','boom','static','music' to input
    numbers when present. Video ends in [vout], audio in [amix] (loudness is applied after)."""
    P, finale = pl["payoff"], pl["finale"]
    norm = f",fps={fps},format=yuv420p,setsar=1"
    zoom = zoom_expr(pl["zooms"], hit=P if P in pl["zooms"] else None)
    flash = (f",drawbox=x=0:y=0:w=iw:h=ih:color=white@0.35:t=fill:enable='between(t,{P:.3f},{P + 0.1:.3f})'"
             if P in pl["zooms"] else "")
    v, a = [], []
    if not finale:
        v.append(f"[0:v]{zoom + ',' if zoom else ''}null{flash}{norm}[vout]")
        a.append(f"[0:a]{_a('acat')}")
        total = dur
    else:
        r0, r1 = max(0.0, P - REPLAY_BACK), min(dur, P + REPLAY_FWD)
        react = pl.get("react", min(dur - P, REACT_MAX))
        segs = [("s1", "x1")]
        v.append("[0:v]split=3[va][vb][vc]")
        a.append("[0:a]asplit=3[aa][ab][ac]")
        v.append(f"[va]trim=start=0:end={P:.3f},setpts=PTS-STARTPTS{',' + zoom if zoom else ''}{norm}[s1]")
        a.append(f"[aa]atrim=start=0:end={P:.3f},asetpts=PTS-STARTPTS,{_a('x1')}")
        emo = inputs.get("emoji")
        if emo is not None:
            v.append(f"[{emo}:v]format=rgba,split=2[e1][e2]")
        if react >= 0.5:
            glitch = (f"rgbashift=rh=-30:bh=30:gh=6:enable='lt(t,{GLITCH})',"
                      f"boxblur=10:1:enable='lt(t,{GLITCH})'")
            if cam:
                cx, cy, cw, ch = (int(x) for x in cam)
                v.append(f"[vb]trim=start={P:.3f}:end={P + react:.3f},setpts=PTS-STARTPTS,split[rb][rc]")
                v.append(f"[rc]crop={cw}:{ch}:{cx}:{cy},scale=640:-2,hue=s=0,pad=iw+12:ih+12:6:6:white[cam]")
                v.extend(_cover("[rb]hue=s=0", "rb2", clean_h))
                v.append("[rb2]gblur=sigma=14,eq=brightness=-0.08[bgr]")
                v.append(f"[bgr][cam]overlay=(W-w)/2:(H-h)/2-120,{glitch}[r0]")
            else:
                v.append(f"[vb]trim=start={P:.3f}:end={P + react:.3f},setpts=PTS-STARTPTS,hue=s=0,eq=contrast=1.18,"
                         f"scale=w='2*trunc({W}*(1+0.08*min(t/2,1))/2)':h='2*trunc({H}*(1+0.08*min(t/2,1))/2)':eval=frame,"
                         f"crop={W}:{H},{glitch}[r0]")
            if emo is not None:
                v.append(f"[r0][e1]overlay=W-w-70:1500:shortest=1:enable='gte(t,0.12)'{norm}[s2]")
            else:
                v.append(f"[r0]null{norm}[s2]")
            a.append(f"[ab]atrim=start={P:.3f}:end={P + react:.3f},asetpts=PTS-STARTPTS,"
                     f"bass=g={BASS_HIT}:f=90:enable='lt(t,0.45)',{_a('x2')}")
            segs.append(("s2", "x2"))
        else:
            v.append("[vb]nullsink")
            a.append("[ab]anullsink")
            if emo is not None:
                v.append("[e1]nullsink")
        rp = f"[vc]trim=start={r0:.3f}:end={r1:.3f},setpts=(PTS-STARTPTS)/{REPLAY_SPEED},hue=s=0,eq=contrast=1.2"
        v.extend(_cover(rp, "r3", clean_h))
        if emo is not None:
            v.append(f"[r3][e2]overlay=W-w-70:1500:shortest=1{norm}[s3]")
        else:
            v.append(f"[r3]null{norm}[s3]")
        a.append(f"[ac]atrim=start={r0:.3f}:end={r1:.3f},asetpts=PTS-STARTPTS,atempo={REPLAY_SPEED},lowpass=f=3500,volume=0.9,{_a('x3')}")
        segs.append(("s3", "x3"))
        total = P + max(0.0, react if react >= 0.5 else 0.0) + (r1 - r0) / REPLAY_SPEED
        if fx["outro"]:
            v.append(f"smptehdbars=s={W}x{H}:r={fps}:d={OUTRO},noise=alls=45:allf=t+u,boxblur=4:1{norm}[s4]")
            st = inputs.get("static")
            if st is not None:
                a.append(f"[{st}:a]atrim=start=0:end={OUTRO},volume=0.55,apad,atrim=start=0:end={OUTRO},{_a('x4')}")
            else:
                a.append(f"anoisesrc=d={OUTRO}:c=white:a=0.18:r=48000,{_a('x4')}")
            segs.append(("s4", "x4"))
            total += OUTRO
        n = len(segs)
        v.append("".join(f"[{s}]" for s, _ in segs) + f"concat=n={n}:v=1:a=0[vout]")
        a.append("".join(f"[{x}]" for _, x in segs) + f"concat=n={n}:v=0:a=1[acat]")
    # one-shot sounds over the edit
    hits, g = [], gains or {}
    if finale and inputs.get("glitch") is not None:
        hits.append((inputs["glitch"], P, g.get("glitch", 0.6)))
    if pl["boom"] and inputs.get("boom") is not None:
        hits.append((inputs["boom"], P + (GLITCH if finale else 0.0), g.get("boom", 0.8)))
    for k, (idx, t, vol) in enumerate(hits):
        ms = int(t * 1000)
        a.append(f"[{idx}:a]volume={vol:.4f},adelay={ms}|{ms},{_a(f'h{k}')}")
    mix_in = "[acat]" + "".join(f"[h{k}]" for k in range(len(hits)))
    if inputs.get("music") is not None:
        a.append(f"{mix_in}amix=inputs={len(hits) + 1}:normalize=0:duration=first,asplit=2[voice][sc]")
        a.append(f"[{inputs['music']}:a]atrim=start=0:end={total:.3f},asetpts=PTS-STARTPTS,volume={g.get('music', MUSIC_VOL):.4f},"
                 f"afade=t=out:st={max(0.0, total - 0.8):.3f}:d=0.8,{_a('bed')}")
        a.append("[bed][sc]sidechaincompress=threshold=0.03:ratio=8:attack=15:release=350[duck]")
        a.append("[voice][duck]amix=inputs=2:normalize=0:duration=first[amix]")
    else:
        a.append(f"{mix_in}amix=inputs={len(hits) + 1}:normalize=0:duration=first[amix]")
    return ";".join(v), ";".join(a), total


def _measure(base_inputs, agraph, work) -> dict:
    p = _run(["ffmpeg", "-hide_banner", "-nostats", *base_inputs, "-filter_complex",
              agraph + f";[amix]{COMPRESS},loudnorm=I={LUFS}:TP={TP}:LRA={LRA}:print_format=json[ln]", "-map", "[ln]",
              "-f", "null", "-"], work)
    return json.loads(re.findall(r"\{[^{}]*\}", p.stderr, re.S)[-1])


def finish(work: Path, name: str, fps: int, fx: dict, cam=None, category: str = "", sfx_times=(), stops=(), clean_h=None, mood_text: str = None) -> dict:
    """Applies the edit to work/<name>.mp4 in place. Returns what was done (for the clip record)."""
    src = work / f"{name}.mp4"
    out = work / f"{name}.fx.mp4"
    dur = float(_run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", src.name],
                     work).stdout.strip() or 0)
    db = audio_db(src.name, cwd=work)
    any_fx = any(fx.get(k) for k in ("punch", "finale", "music"))
    p_guess = payoff(db, dur)
    dark = True if mood_text is None else is_dark(mood_text)
    pl = plan(dur, db, fx, has_audio_hit=any(abs(t - p_guess) < 1.0 for t in sfx_times), stops=stops, dark=dark)
    inputs, paths, args, n = {}, {}, ["-i", src.name], 1
    cat = (category or "").lower()

    def add(key, extra, path):
        nonlocal n
        if path:
            args.extend([*extra, "-i", path])
            inputs[key], paths[key] = n, path
            n += 1

    if pl["finale"] and fx.get("emoji"):
        add("emoji", ["-loop", "1"], "emo.png" if make_emoji(EMOJI.get(cat, "💀"), work / "emo.png") else None)
    if pl["finale"]:
        add("glitch", [], _sound("glitch transition", work, "glitch"))
        if fx.get("outro"):
            add("static", [], _sound("tv static", work, "static"))
    if pl["boom"] and (pl["finale"] or pl["zooms"]):
        add("boom", [], _sound("vine boom", work, "boom"))
    if fx.get("music"):
        m = _sound(MUSIC.get(cat, "phonk"), work, "music")
        add("music", ["-stream_loop", "-1"], m)
    # every added sound is levelled against this clip's voice (MyInstants files are mastered very loud)
    from . import sfx as sfx_mod
    voice = sfx_mod.loudness(work / src.name)
    rel = {"glitch": 0.45, "boom": 0.55, "music": 0.3}    # vol 1.0 = 6 dB under the voice; music sits ~16 dB under
    gains = {k: sfx_mod.gain_for(work / paths[k], voice, rel[k]) for k in inputs if k in rel}
    vg, ag, total = graphs(dur, fps, pl, fx, cam, inputs, clean_h, gains)
    m = _measure(args, ag, work)
    ln = (f"loudnorm=I={LUFS}:TP={TP}:LRA={LRA}:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
          f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    tail = ["-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart", out.name]
    if any_fx:
        cmd = ["ffmpeg", "-y", "-v", "error", *args, "-filter_complex", f"{vg};{ag};[amix]{COMPRESS},{ln}[aout]",
               "-map", "[vout]", "-map", "[aout]", "-t", f"{total:.3f}", "-fps_mode", "cfr", "-r", str(fps),
               "-c:v", "libx264", "-crf", "20", "-preset", "veryfast", "-pix_fmt", "yuv420p", *tail]
    else:   # nothing to edit: the picture is copied, only the sound is levelled
        cmd = ["ffmpeg", "-y", "-v", "error", *args, "-filter_complex", f"{ag};[amix]{COMPRESS},{ln}[aout]",
               "-map", "0:v", "-map", "[aout]", "-c:v", "copy", *tail]
    _run(cmd, work)
    out.replace(src)
    return {"payoff": pl["payoff"], "zooms": [float(z) for z in pl["zooms"]], "finale": pl["finale"],
            "length": round(total, 2), "sounds": sorted(inputs), "dark": dark,
            "clip_sfx": len(sfx_times)}

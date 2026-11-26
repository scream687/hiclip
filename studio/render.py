"""Turns a time range into a vertical clip: 9:16 layout, bouncy word captions, hook title, cover."""
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import config, roman
from .media import NO_WINDOW, run

W, H = config.OUT_W, config.OUT_H

# ASS colours are &HBBGGRR. `size` is the ASS font size (line height); _Font turns it into real pixels.
STYLES = {
    "bounce": {"label": "Bounce", "font": "Poppins ExtraBold", "size": 132, "base": "FFFFFF", "active": "00F0FF",
               "strong": "FFFFFF", "outline": 7, "pop": 112, "upper": True, "per": 3},
    "hype": {"label": "Hype", "font": "Anton", "size": 152, "base": "FFFFFF", "active": "3C3CFF",
             "strong": "00D7FF", "outline": 7, "pop": 114, "upper": True, "per": 2},
    "clean": {"label": "Clean", "font": "Poppins ExtraBold", "size": 100, "base": "FFFFFF", "active": "6BD7FF",
              "strong": "FFFFFF", "outline": 5, "pop": 106, "upper": False, "per": 4},
    # the reference creators: one big word at a time, white, key words yellow (Snax Gaming)
    "punch": {"label": "One word", "font": "Archivo Black", "size": 150, "base": "FFFFFF", "active": "00F2FF",
              "strong": "00F2FF", "outline": 9, "pop": 116, "upper": True, "per": 1},
    # 2-3 words, white, key words hot pink (GTA guide channels)
    "pink": {"label": "Pink keys", "font": "Archivo Black", "size": 112, "base": "FFFFFF", "active": "D23CFF",
             "strong": "D23CFF", "outline": 8, "pop": 110, "upper": True, "per": 3},
}
# Heading looks: a white box (default), or bare red slanted text over the black band like the reference creators.
HOOK_STYLES = {"box": "White box", "red": "Red slanted text"}
HOOK_RED = {"font": "Anton", "size": 108, "ink": "1E1EF0", "outline": 4}
HOOK = {"font": "Poppins ExtraBold", "size": 96, "ink": "14100E", "box": "FFFFFF", "pad_x": 36, "pad_y": 22, "radius": 26,
        "max_w": 900}
FONT_FILES = {"Poppins ExtraBold": "Poppins-ExtraBold.ttf", "Anton": "Anton-Regular.ttf", "Archivo Black": "ArchivoBlack-Regular.ttf"}
CAP_MAX_W = 960   # captions never run wider than this, whatever is said


class _Font:
    """Measures text exactly as the renderer will draw it, so every word can be placed by hand: the highlighted
    word can grow without pushing its neighbours, and the heading box fits its text."""
    _cache: dict = {}

    def __init__(self, name):
        import struct
        path = Path(__file__).resolve().parent / "fonts" / FONT_FILES.get(name, FONT_FILES["Poppins ExtraBold"])
        d = path.read_bytes()
        tables = {}
        for i in range(struct.unpack(">H", d[4:6])[0]):
            tag, _, off, _ = struct.unpack(">4sIII", d[12 + 16 * i: 28 + 16 * i])
            tables[tag] = off
        self.upm = struct.unpack(">H", d[tables[b"head"] + 18: tables[b"head"] + 20])[0]
        o = tables[b"OS/2"]
        self.asc, self.desc = struct.unpack(">HH", d[o + 74: o + 78])      # what the renderer sizes the font by
        self.cap = struct.unpack(">h", d[o + 88: o + 90])[0] or int(self.upm * 0.7)
        self.path = str(path)
        self._pil = {}

    @classmethod
    def get(cls, name):
        if name not in cls._cache:
            cls._cache[name] = cls(name)
        return cls._cache[name]

    def em(self, size):
        return size * self.upm / (self.asc + self.desc)

    def width(self, text, size):
        """Pixels wide at this ASS size."""
        try:
            from PIL import ImageFont
            if "f" not in self._pil:
                self._pil["f"] = ImageFont.truetype(self.path, 200)
            return self._pil["f"].getlength(text) * self.em(size) / 200
        except Exception:  # noqa: BLE001 - no measuring library: a fair estimate keeps clips rendering
            return len(text) * self.em(size) * 0.62

    def cap_shift(self, size):
        """How far below the anchor the middle of a capital letter sits when text is centred on a point."""
        return ((self.asc - self.desc) / 2 - self.cap / 2) * size / (self.asc + self.desc)


CAPTION_LEAD = 0.0  # measured: word stamps are within 0.15 s of the voice, so no shift is needed
def install_fonts():
    """Copy the bundled caption fonts next to the data, so captions look the same on every machine and server."""
    src = Path(__file__).resolve().parent / "fonts"
    for f in src.glob("*.ttf"):
        if not (config.FONTS_DIR / f.name).exists():
            shutil.copyfile(f, config.FONTS_DIR / f.name)


LAYOUTS = {"blur": "Full frame + blurred fill", "zoom": "Zoomed in + blurred fill", "crop": "Fill the screen (centre crop)",
           "gameplay": "Gameplay fills the screen, facecam in the corner",
           "stack": "Facecam on top, gameplay below",
           "band": "Zoomed gameplay band, facecam in its corner (creator style)"}
CAM_LAYOUTS = ("gameplay", "stack", "band")   # the layouts that need the webcam found
BAND_TOP, BAND_H = 400, 980   # the zoomed picture sits a little above centre, like the reference creators'
BAND_PIP_W = 300


def _ts(t):
    t = max(0.0, t)
    h, m = int(t // 3600), int(t % 3600 // 60)
    return f"{h}:{m:02d}:{t % 60:05.2f}"


def _clean(text):
    return re.sub(r"[\x00-\x1f\x7f]+", " ", re.sub(r"[{}\\]", "", str(text))).strip()


def _key(word):
    return re.sub(r"[^a-z0-9']", "", word.lower())


def _groups(words, per):
    out, cur = [], []
    for w in words:
        if cur and (len(cur) >= per or sum(len(x["w"]) for x in cur) + len(w["w"]) > 16
                    or w["s"] - cur[-1]["e"] > 0.45 or cur[-1]["w"][-1:] in ".?!"):
            out.append(cur)
            cur = []
        cur.append(w)
    if cur:
        out.append(cur)
    return out


def geometry(src_w, src_h, layout):
    """Where the video sits in the 1080x1920 frame, and where hook and captions go."""
    if src_h >= src_w and layout != "crop":
        layout = "crop"  # already vertical or square: nothing to gain from a blurred fill
    if layout == "blur":
        fg_h = min(H, int(round(W * src_h / src_w / 2)) * 2)
    elif layout in ("zoom", "band"):
        fg_h = BAND_H
    else:
        fg_h = H
    top = (H - fg_h) // 2
    if layout in ("zoom", "band"):
        return layout, fg_h, BAND_TOP - 70, BAND_TOP + BAND_H + 10   # heading over the band, words on its bottom edge
    if layout in ("crop", "gameplay", "stack"):
        return layout, fg_h, 300, 1380
    return layout, fg_h, max(230, top - 190), min(1640, top + fg_h + 180)


HOOK_EMOJI = 96   # the emoji after a red heading, in pixels


def red_hook_layout(hook, y):
    """(lines, size, centre of each line, where the emoji after the last line goes). The text is held narrower than
    the frame so the emoji fits beside it, as on the reference creators' headings."""
    words = hook.split()
    f, size = _Font.get(HOOK_RED["font"]), HOOK_RED["size"]
    room = 900 - HOOK_EMOJI
    lines = [" ".join(words).upper()]
    if f.width(lines[0], size) > room and len(words) > 1:
        cut = min(range(1, len(words)), key=lambda i: max(f.width(" ".join(words[:i]), size), f.width(" ".join(words[i:]), size)))
        lines = [" ".join(words[:cut]).upper(), " ".join(words[cut:]).upper()]
    widest = max(f.width(l, size) for l in lines)
    if widest > room:
        size = max(40, int(size * room / widest))
    line_h = f.em(size) * 1.05
    last_w = f.width(lines[-1], size)
    shift = (HOOK_EMOJI + 14) / 2       # the line and its emoji are centred together
    ys = [((W / 2 - shift) if i == len(lines) - 1 else W / 2, y - line_h * (len(lines) - 1 - i)) for i in range(len(lines))]
    lx, ly = ys[-1]
    return lines, size, ys, (int(lx + last_w / 2 + 14), int(ly - HOOK_EMOJI / 2 - 4))


def _hook_events(hook, dur, y, look="box"):
    """The heading: dark text in a white rounded box that fits it, on one line or two balanced ones; or bare red
    slanted capitals with a dark outline (look="red")."""
    words = hook.split()
    if not words:
        return []
    if look == "red":
        lines, size, ys, _ = red_hook_layout(hook, y)
        span = f"{_ts(0)},{_ts(dur)}"
        return [f"Dialogue: 4,{span},HookRed,,0,0,0,,{{\\an5\\pos({x:.0f},{cy:.0f})\\fs{size}\\i1\\fad(160,0)}}{line}"
                for line, (x, cy) in zip(lines, ys)]
    f, size = _Font.get(HOOK["font"]), HOOK["size"]
    lines = [" ".join(words)]
    if f.width(lines[0], size) > HOOK["max_w"] and len(words) > 1:      # two lines, split where they come out most even
        cut = min(range(1, len(words)), key=lambda i: max(f.width(" ".join(words[:i]), size), f.width(" ".join(words[i:]), size)))
        lines = [" ".join(words[:cut]), " ".join(words[cut:])]
    widest = max(f.width(l, size) for l in lines)
    if widest > HOOK["max_w"]:
        size = max(40, int(size * HOOK["max_w"] / widest))
        widest = max(f.width(l, size) for l in lines)
    line_h = f.em(size) * 1.16
    bw, bh = widest + 2 * HOOK["pad_x"], line_h * len(lines) + 2 * HOOK["pad_y"]
    x0, y0, r = (W - bw) / 2, y - bh / 2, min(HOOK["radius"], bh / 2)
    x1, y1 = x0 + bw, y0 + bh
    n = lambda v: f"{v:.0f}"                                                         # noqa: E731
    box = (f"m {n(x0 + r)} {n(y0)} l {n(x1 - r)} {n(y0)} b {n(x1)} {n(y0)} {n(x1)} {n(y0)} {n(x1)} {n(y0 + r)} "
           f"l {n(x1)} {n(y1 - r)} b {n(x1)} {n(y1)} {n(x1)} {n(y1)} {n(x1 - r)} {n(y1)} "
           f"l {n(x0 + r)} {n(y1)} b {n(x0)} {n(y1)} {n(x0)} {n(y1)} {n(x0)} {n(y1 - r)} "
           f"l {n(x0)} {n(y0 + r)} b {n(x0)} {n(y0)} {n(x0)} {n(y0)} {n(x0 + r)} {n(y0)}")
    span = f"{_ts(0)},{_ts(dur)}"
    ev = [f"Dialogue: 3,{span},Hook,,0,0,0,,{{\\an7\\pos(0,0)\\fad(160,0)\\c&H{HOOK['box']}&\\p1}}{box}{{\\p0}}"]
    for i, line in enumerate(lines):
        cy = y - line_h * (len(lines) - 1) / 2 + i * line_h - f.cap_shift(size)
        ev.append(f"Dialogue: 4,{span},Hook,,0,0,0,,{{\\an5\\pos({W // 2},{cy:.0f})\\fad(160,0)\\fs{size}}}{line}")
    return ev


def _watermark_events(text, dur):
    """The creator's channel tag in the top-left corner, a small red play badge and the name, like the references."""
    text = _clean(text)[:30]
    if not text:
        return []
    span = f"{_ts(0)},{_ts(dur)}"
    badge = "m 0 4 b 0 0 0 0 4 0 l 34 0 b 38 0 38 0 38 4 l 38 22 b 38 26 38 26 34 26 l 4 26 b 0 26 0 26 0 22"
    return [f"Dialogue: 5,{span},Mark,,0,0,0,,{{\\an7\\pos(26,26)\\c&H0000FF&\\bord0\\shad0\\p1}}{badge}{{\\p0}}",
            f"Dialogue: 6,{span},Mark,,0,0,0,,{{\\an7\\pos(40,32)\\c&HFFFFFF&\\bord0\\shad0\\p1}}m 0 0 l 13 7 l 0 14{{\\p0}}",
            f"Dialogue: 5,{span},Mark,,0,0,0,,{{\\an4\\pos(74,39)}}{text}"]


def build_ass(words, start, end, hook, style_name, highlight, hook_y, cap_y, hook_look="box", watermark="") -> str:
    st = STYLES.get(style_name, STYLES["bounce"])
    words = [dict(w, s=w["s"] - CAPTION_LEAD, e=w["e"] - CAPTION_LEAD) for w in words]
    strong = {_key(h) for h in highlight if _key(h)}
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{st['font']},{st['size']},&H00{st['base']},&H00{st['base']},&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,{st['outline']},0,5,0,0,0,1
Style: Hook,{HOOK['font']},{HOOK['size']},&H00{HOOK['ink']},&H00{HOOK['ink']},&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1
Style: Mark,Poppins ExtraBold,40,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,7,0,0,0,1
Style: HookRed,{HOOK_RED['font']},{HOOK_RED['size']},&H00{HOOK_RED['ink']},&H00{HOOK_RED['ink']},&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,{HOOK_RED['outline']},0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    dur = end - start
    ev = _hook_events(_clean(hook), dur, hook_y, hook_look) + _watermark_events(watermark, dur)
    f = _Font.get(st["font"])
    inside = [dict(w, w=_clean(w["w"])) for w in words if w["s"] >= start - 0.05 and w["s"] < end]
    inside = sorted((w for w in inside if w["w"]), key=lambda w: w["s"])
    for i, w in enumerate(inside[:-1]):
        w["e"] = max(w["s"], min(w["e"], inside[i + 1]["s"]))
    groups = _groups(inside, st["per"])
    for gi, g in enumerate(groups):
        g_start = max(0.0, g[0]["s"] - start)
        g_end = min(g[-1]["e"] + 0.25, groups[gi + 1][0]["s"] if gi + 1 < len(groups) else end) - start
        if g_end - g_start < 0.02:
            continue
        texts = []
        for x in g:
            t = re.sub(r"[.,;:]+$", "", x["w"])
            texts.append(t.upper() if st["upper"] else t)
        if not all(roman.drawable(t) for t in texts):
            # A script the caption font cannot draw (Japanese, Thai, Chinese...): its words cannot be measured, so
            # they are not placed one by one. The line is drawn whole and centred, in a font the system has for it.
            line = " ".join(texts)
            fs = max(44, min(int(st["size"] * 0.7), int(1500 / max(1, len(line)) * 1.6)))
            ev.append(f"Dialogue: 1,{_ts(g_start)},{_ts(g_end)},Cap,,60,60,0,,{{\\an5\\pos({W // 2},{cap_y:.0f})\\q0\\fs{fs}}}{line}")
            continue
        size = st["size"]
        gap = f.width("n n", size) - 2 * f.width("n", size) + (2 * st["outline"] + 6) * size / st["size"]   # a space, plus room for both outlines
        total = sum(f.width(t, size) for t in texts) + gap * (len(texts) - 1)
        if total > CAP_MAX_W:                                  # a long phrase: shrink it to fit, never run off the frame
            size = max(36, int(size * CAP_MAX_W / total))
            gap = f.width("n n", size) - 2 * f.width("n", size) + (2 * st["outline"] + 6) * size / st["size"]   # a space, plus room for both outlines
            total = sum(f.width(t, size) for t in texts) + gap * (len(texts) - 1)
        cy = cap_y - f.cap_shift(size)
        x = (W - total) / 2
        p = st["pop"]
        for i, (w, t) in enumerate(zip(g, texts)):
            wd = f.width(t, size)
            p = max(104, min(st["pop"], 100 + int(2200 / max(wd, 1))))   # long words grow less, so they never touch their neighbours
            pos = f"\\an5\\pos({x + wd / 2:.0f},{cy:.0f})\\fs{size}"
            x += wd + gap
            a = max(g_start, w["s"] - start)
            b = (g[i + 1]["s"] - start) if i + 1 < len(g) else g_end
            base = st["strong"] if _key(w["w"]) in strong else st["base"]
            if a - g_start >= 0.02:                                                   # waiting to be said
                ev.append(f"Dialogue: 1,{_ts(g_start)},{_ts(a)},Cap,,0,0,0,,{{{pos}\\c&H{base}&}}{t}")
            if b - a >= 0.02:                                                         # being said: colour and a quick pop, in front
                ev.append(f"Dialogue: 2,{_ts(a)},{_ts(b)},Cap,,0,0,0,,{{{pos}\\c&H{st['active']}&\\fscx94\\fscy94"
                          f"\\t(0,90,\\fscx{p}\\fscy{p})\\t(90,200,\\fscx{p - 5}\\fscy{p - 5})}}{t}")
            if g_end - b >= 0.02:                                                     # said
                ev.append(f"Dialogue: 1,{_ts(b)},{_ts(g_end)},Cap,,0,0,0,,{{{pos}\\c&H{base}&\\fscx{p - 5}\\fscy{p - 5}"
                          f"\\t(0,90,\\fscx100\\fscy100)}}{t}")
    return head + "\n".join(ev) + "\n"


PIP_W = 380            # how wide the facecam is in the 1080-wide frame
PIP_MARGIN = 26
STACK_TOP = (0.30, 0.45)  # stack: the share of the height the webcam may take; the game gets the rest


def _stack(facecam):
    """(top height, the webcam crop x, y, w, h): the webcam at its own shape across the top, kept between
    30% and 45% of the height, its crop trimmed around the centre to that shape so nothing is stretched."""
    fx, fy, fw, fh = (int(v) for v in facecam)
    top = int(min(max(W * fh / fw, STACK_TOP[0] * H), STACK_TOP[1] * H)) // 2 * 2
    aspect = W / top
    cw, ch = (fh * aspect, fh) if fw / fh > aspect else (fw, fw / aspect)
    cw, ch = int(cw) // 2 * 2, int(ch) // 2 * 2
    return top, fx + (fw - cw) // 2, fy + (fh - ch) // 2, cw, ch


def _band_pip(facecam):
    fx, fy, fw, fh = (int(v) for v in facecam)
    return BAND_PIP_W, max(2, int(round(BAND_PIP_W * fh / fw)) // 2 * 2)


def cam_box(layout, facecam):
    """Where the webcam sits in the finished 1080x1920 frame (x, y, w, h), or None."""
    if not (facecam and len(facecam) == 4 and facecam[2] > 8 and facecam[3] > 8):
        return None
    if layout == "gameplay":
        return (W - PIP_W - PIP_MARGIN, PIP_MARGIN, PIP_W, max(2, int(round(PIP_W * facecam[3] / facecam[2]))))
    if layout == "stack":
        return (0, 0, W, _stack(facecam)[0])
    if layout == "band":
        return (0, BAND_TOP) + _band_pip(facecam)
    return None


def _filter(layout, fg_h, facecam=None, fps=None):
    fps = fps or config.OUT_FPS
    if layout == "band" and facecam and facecam[2] > 8 and facecam[3] > 8:
        # The reference creators' frame: the game zoomed into a band above centre, the webcam in its top-left
        # corner, the game blurred behind, the heading over the band.
        fx, fy, fw, fh = (int(v) for v in facecam)
        pw, ph = _band_pip(facecam)
        return (f"[0:v]fps={fps},split=3[a][b][fc];"
                f"[a]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=6:2,"
                f"scale={W}:{H},eq=brightness=-0.34:saturation=1.0[bg];"
                f"[b]scale={W}:{fg_h}:force_original_aspect_ratio=increase,crop={W}:{fg_h}[fg];"
                f"[fc]crop={fw}:{fh}:{fx}:{fy},scale={pw}:{ph},setsar=1[p];"
                f"[bg][fg]overlay=0:{BAND_TOP}[m];[m][p]overlay=0:{BAND_TOP},setsar=1[v0];")
    if layout == "gameplay" and facecam:
        # The game fills the screen, and the webcam sits in the top corner of it, so a viewer sees
        # the play and the face at the same time.
        fx, fy, fw, fh = (int(v) for v in facecam)
        if fw > 8 and fh > 8:
            pip_h = max(2, int(round(PIP_W * fh / fw)))
            return (f"[0:v]fps={fps},split=2[base][fc];"
                    f"[base]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1[b];"
                    f"[fc]crop={fw}:{fh}:{fx}:{fy},scale={PIP_W}:{pip_h},setsar=1[p];"
                    f"[b][p]overlay={W - PIP_W - PIP_MARGIN}:{PIP_MARGIN}[v0];")
    if layout == "stack" and facecam and facecam[2] > 8 and facecam[3] > 8:
        # The webcam across the top at its own shape, the game below filling the rest.
        top, cx, cy, cw, ch = _stack(facecam)
        return (f"[0:v]fps={fps},split=2[fc][game];"
                f"[fc]crop={cw}:{ch}:{cx}:{cy},scale={W}:{top},setsar=1[cam];"
                f"[game]scale={W}:{H - top}:force_original_aspect_ratio=increase,crop={W}:{H - top},setsar=1[low];"
                f"[cam][low]vstack[v0];")
    if layout == "crop" or (layout in ("gameplay", "stack") and not facecam):
        return f"[0:v]fps={fps},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1[v0];"
    y = BAND_TOP if layout in ("zoom", "band") else "(H-h)/2"
    return (f"[0:v]fps={fps},split=2[a][b];"
            f"[a]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=6:2,"
            f"scale={W}:{H},eq=brightness=-0.14:saturation=1.15[bg];"
            f"[b]scale={W}:{fg_h}:force_original_aspect_ratio=increase,crop={W}:{fg_h}[fg];"
            f"[bg][fg]overlay=0:{y},setsar=1[v0];")


def _hook_y(layout, hook_y, facecam=None):
    """Where the heading goes. In the gameplay layout the webcam sits in the top corner, so a heading
    across the top would run under it: it goes just below the picture instead."""
    if layout == "gameplay" and facecam and len(facecam) == 4 and facecam[2] > 8:
        return PIP_MARGIN + int(round(PIP_W * facecam[3] / facecam[2])) + 32
    if layout == "stack" and facecam and len(facecam) == 4 and facecam[2] > 8 and facecam[3] > 8:
        return _stack(facecam)[0] + 32   # just under the seam: never over the streamer's face
    return hook_y


_qsv_ok = None
PREROLL = 6.0            # seconds read before a clip so the video has a keyframe to start from (streams: ~2 s apart)
RENDER_SECONDS = 600      # one clip's video encode; far beyond normal, so a frozen encoder is stopped, not waited on


def render_clip(src: Path, out_dir: Path, name: str, clip: dict, words: list, src_w: int, src_h: int) -> dict:
    """Writes <name>.mp4, <name>.jpg and <name>.ass into out_dir. Returns file names."""
    out_dir.mkdir(parents=True, exist_ok=True)
    start, end = clip["start"], clip["end"]
    layout, fg_h, hook_y, cap_y = geometry(src_w, src_h, clip.get("layout", "blur"))
    face = clip.get("facecam")
    if layout in CAM_LAYOUTS and not (face and len(face) == 4):
        # no webcam found: the band keeps its zoomed picture; the others let the game fill the screen
        layout = "zoom" if layout == "band" else "crop"
    hook_y = _hook_y(layout, hook_y, face)
    look = clip.get("hook_style") if clip.get("hook_style") in HOOK_STYLES else ("red" if layout in ("zoom", "band") else "box")
    ass = build_ass(words if clip.get("captions", True) else [], start, end, clip.get("hook", ""), clip.get("style", "bounce"),
                    clip.get("highlight_words", []), hook_y, cap_y, look, clip.get("watermark", ""))
    fps = source_fps(src)
    hook_emoji = None
    if look == "red" and _clean(clip.get("hook", "")).split() and clip.get("hook_emoji", True):
        from .edit import EMOJI
        hook_emoji = (EMOJI.get((clip.get("category") or "").lower(), "💀"), red_hook_layout(_clean(clip["hook"]), hook_y)[3])
    from . import edit
    fx = edit.fx_options(clip.get("fx"))
    edit_info = {"fps": fps, "fx": fx, "cam": cam_box(layout, face), "category": clip.get("category", ""),
                 "sfx_times": [float(x.get("time", 0)) for x in clip.get("sfx") or []],
                 "mood": " ".join(str(clip.get(k) or "") for k in ("hook", "title", "description", "category")),
                 "clean_h": int(cap_y) - 120 if clip.get("captions", True) else None,
                 "stops": edit.sentence_stops([w for w in words if start - 0.05 <= w.get("s", -1) < end], start)}
    # The clip is made in a short work folder and moved into place afterwards. The caption step of ffmpeg cannot
    # open files whose full path is longer than 260 characters on Windows, which a data folder deep inside other
    # folders easily reaches. Inside the work folder every name is short and relative (a drive colon would also
    # break the filter syntax).
    work = Path(tempfile.mkdtemp(prefix="pk"))
    try:
        return _render_in(work, src, out_dir, name, start, end, layout, fg_h, ass, face=face, sfx_list=clip.get("sfx"),
                          edit_info=edit_info, hook_emoji=hook_emoji)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def source_fps(src) -> int:
    """60 for 50/60 fps sources (the reference Shorts keep it), else the usual 30."""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=avg_frame_rate",
                            "-of", "csv=p=0", str(src)], capture_output=True, text=True, timeout=30,
                           creationflags=NO_WINDOW).stdout.strip()
        a, b = (r.split("/") + ["1"])[:2]
        return 60 if float(a) / float(b or 1) >= 49 else config.OUT_FPS
    except Exception:  # noqa: BLE001
        return config.OUT_FPS


def _render_in(work: Path, src: Path, out_dir: Path, name: str, start, end, layout, fg_h, ass: str, face=None, sfx_list=None,
               edit_info=None, hook_emoji=None) -> dict:
    global _qsv_ok
    (work / f"{name}.ass").write_text(ass, encoding="utf-8")
    (work / "f").mkdir()
    for f in config.FONTS_DIR.glob("*.ttf"):
        shutil.copyfile(f, work / "f" / f.name)
    # Reading starts a little early and both tracks are trimmed to the clip inside the filters. Seeking straight to
    # the start lands between keyframes in a stream piece: the video then begins at the next keyframe, up to ~2 s
    # after the audio, and players that line both up at 0 put the voice out of step with the picture.
    pre = min(start, PREROLL)
    fc = (f"[0:v]trim=start={pre:.3f}:duration={end - start:.3f},setpts=PTS-STARTPTS[vin];"
          + _filter(layout, fg_h, face, (edit_info or {}).get("fps")).replace("[0:v]", "[vin]", 1) + f"[v0]ass={name}.ass:fontsdir=f[v]")
    trim_a = f"atrim=start={pre:.3f}:duration={end - start:.3f},asetpts=PTS-STARTPTS"

    inputs = ["ffmpeg", "-y", "-v", "error", "-ss", f"{start - pre:.2f}", "-t", f"{end - start + pre:.2f}", "-i", str(src)]
    first = 1   # input number of the first sound effect
    if hook_emoji:
        from . import edit
        if edit.make_emoji(hook_emoji[0], work / "hemo.png", px=HOOK_EMOJI):
            x, y = hook_emoji[1]
            inputs.extend(["-i", "hemo.png"])
            fc = fc.replace(f"ass={name}.ass:fontsdir=f[v]", f"ass={name}.ass:fontsdir=f[vs];[vs][1:v]overlay={x}:{y}[v]")
            first = 2

    # Fetch and stage any meme/sound effects
    valid_sfx = []
    if sfx_list:
        from . import sfx as sfx_mod
        for idx, item in enumerate(sfx_list):
            url = item.get("url")
            local_path = sfx_mod.ensure_cached(url, item.get("name", "")) if url else None
            if local_path and local_path.exists():
                dst = work / f"sfx_{len(valid_sfx)}.mp3"
                shutil.copyfile(local_path, dst)
                inputs.extend(["-i", f"sfx_{len(valid_sfx)}.mp3"])
                valid_sfx.append(item)

    if valid_sfx:
        af_parts = [
            f"[0:a:0]{trim_a},aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[a0];"
        ]
        for idx, item in enumerate(valid_sfx):
            t_ms = int(max(0.0, float(item.get("time", 0.0))) * 1000)
            vol = max(0.05, min(2.5, float(item.get("vol", 0.7))))
            af_parts.append(
                f"[{idx + first}:a]aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
                f"volume={vol:.2f},adelay={t_ms}|{t_ms}[sfx{idx + 1}];"
            )
        mix_inputs = "[a0]" + "".join(f"[sfx{i + 1}]" for i in range(len(valid_sfx)))
        af_parts.append(
            f"{mix_inputs}amix=inputs={len(valid_sfx) + 1}:duration=first:dropout_transition=0,"
            f"loudnorm=I=-14:TP=-1.5:LRA=11[aout]"
        )
        audio_filter = "".join(af_parts)
        full_fc = fc + ";" + audio_filter
        audio_maps = ["-map", "[aout]"]
    else:
        full_fc = fc + f";[0:a:0]{trim_a},loudnorm=I=-14:TP=-1.5:LRA=11[aout]"
        audio_maps = ["-map", "[aout]"]

    base = inputs + ["-filter_complex", full_fc, "-map", "[v]"] + audio_maps + [
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart", "-pix_fmt"
    ]
    encoders = []
    if _qsv_ok is not False:
        encoders.append(("qsv", ["nv12", "-c:v", "h264_qsv", "-global_quality", "23", "-preset", "fast"]))
    encoders.append(("x264", ["yuv420p", "-c:v", "libx264", "-crf", "21", "-preset", "veryfast"]))
    err = None
    for label, enc in encoders:
        p = subprocess.run(base + enc + [f"{name}.mp4"], cwd=work, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", creationflags=NO_WINDOW, timeout=RENDER_SECONDS)
        if p.returncode == 0:
            if label == "qsv":
                _qsv_ok = True
            break
        err = p.stderr[-1200:]
        if label == "qsv":
            _qsv_ok = False
    else:
        raise RuntimeError(f"Render failed: {err}")
    info = {}
    if edit_info:
        from . import edit
        try:
            info = edit.finish(work, name, edit_info["fps"], edit_info["fx"], cam=edit_info["cam"],
                               category=edit_info["category"], sfx_times=edit_info["sfx_times"],
                               stops=edit_info.get("stops", ()), clean_h=edit_info.get("clean_h"),
                               mood_text=edit_info.get("mood"))
        except Exception as e:  # noqa: BLE001 - the plain clip is still a good clip
            import logging
            logging.getLogger("studio.render").warning("edit pass skipped for %s: %s", name, e)
    cover_t = min(1.2, (end - start) / 2)
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{cover_t:.2f}", "-i", f"{name}.mp4", "-frames:v", "1",
         "-q:v", "3", f"{name}.jpg"], cwd=work)
    for ext in ("mp4", "jpg", "ass"):
        shutil.move(str(work / f"{name}.{ext}"), str(out_dir / f"{name}.{ext}"))
    return {"file": f"{name}.mp4", "cover": f"{name}.jpg", "layout_used": layout, "edit": info}

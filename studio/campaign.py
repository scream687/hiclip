"""A clipping campaign set on a creator: the brief pasted from the campaign, the @name each platform's posts must
start with, and the post text built from them. Ported in spirit from PeakCut's campaigns (read_brief/brief_tags).

Campaigns like Foltyn's say "Must TAG Foltyn on ALL POSTS (first line)" with a different handle per platform
(IG: @realfoltyn, YT: @FoltynFamily, TT: @klfoltyn). Those lines are read from the brief; a handle typed on the
creator wins over the one read."""
import re

POST_PLATFORMS = ("instagram", "youtube", "tiktok")
_NAMES = {"instagram": ("ig", "insta", "instagram"), "youtube": ("yt", "youtube"), "tiktok": ("tt", "tiktok", "tik tok")}
_HANDLE = r"@([A-Za-z0-9_][A-Za-z0-9_.]{0,29})"
BRIEF_MAX = 4000


def clean_brief(text) -> str:
    s = re.sub(r"[\x00-\x09\x0b-\x1f\x7f]+", " ", str(text or "").replace("\r\n", "\n").replace("\r", "\n"))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(x.strip() for x in s.split("\n"))).strip()[:BRIEF_MAX]


def handle(v) -> str:
    """'@name', 'name' or a profile link -> '@name'; anything else -> ''."""
    v = str(v or "").strip().rstrip("/")
    v = v.rsplit("/", 1)[-1] if "/" in v else v
    m = re.fullmatch(r"@?([A-Za-z0-9_][A-Za-z0-9_.]{0,29})", v)
    return "@" + m.group(1).rstrip(".") if m else ""


def read_brief(text: str) -> dict:
    """{'tags': {platform: '@name'}, 'hashtags': [...]} the brief asks for. A line like 'IG: @realfoltyn' names
    the handle for that platform; hashtags are kept in the brief's order ('#1' and '&#39;' are not hashtags)."""
    text = str(text or "")
    tags = {}
    for line in text.splitlines():
        m = re.match(r"\s*([A-Za-z ]{2,10})\s*[:\-–]\s*" + _HANDLE, line)
        if not m:
            continue
        word = m.group(1).strip().lower()
        for plat, names in _NAMES.items():
            if word in names and plat not in tags:
                tags[plat] = "@" + m.group(2).rstrip(".")
    hashtags = []
    for h in re.findall(r"(?<![\w&/])#([A-Za-z][A-Za-z0-9_]{1,40})", text):
        if h.lower() not in [x.lower() for x in hashtags]:
            hashtags.append(h)
    return {"tags": tags, "hashtags": hashtags[:12]}


def tags_for(creator: dict) -> dict:
    """The @name per platform: typed on the creator first, else read from the brief."""
    read = read_brief(creator.get("campaign_brief", ""))["tags"]
    typed = {p: handle(v) for p, v in (creator.get("campaign_tags") or {}).items() if p in POST_PLATFORMS}
    return {p: typed.get(p) or read.get(p, "") for p in POST_PLATFORMS if typed.get(p) or read.get(p)}


def post_text(clip: dict, creator: dict, platform: str, with_title: bool = True) -> str:
    """The whole post: the campaign's @name on the first line, then title, description, hashtags (the clip's and
    the brief's; #Shorts last on YouTube) and the creator's footer line."""
    have = list(clip.get("hashtags") or [])
    for h in read_brief(creator.get("campaign_brief", ""))["hashtags"]:
        if h.lower() not in {x.lower() for x in have}:
            have.append(h)
    if platform == "youtube" and "shorts" not in {h.lower() for h in have}:
        have.append("Shorts")
    parts = [tags_for(creator).get(platform, ""),
             (clip.get("title") or clip.get("hook") or "") if with_title else "",
             clip.get("description", ""), " ".join("#" + h for h in have), creator.get("post_footer", "")]
    return "\n\n".join(x for x in parts if x)


if __name__ == "__main__":
    brief = "DO\nMust TAG Foltyn on ALL POSTS (first line)\nIG: @realfoltyn\nYT: @FoltynFamily \nTT: @klfoltyn\nuse #gta6 &#39;"
    r = read_brief(brief)
    assert r["tags"] == {"instagram": "@realfoltyn", "youtube": "@FoltynFamily", "tiktok": "@klfoltyn"}, r
    assert r["hashtags"] == ["gta6"], r
    c = {"campaign_brief": brief, "campaign_tags": {"tiktok": "https://www.tiktok.com/@other"}}
    assert tags_for(c)["tiktok"] == "@other" and tags_for(c)["youtube"] == "@FoltynFamily"
    t = post_text({"title": "T", "description": "D", "hashtags": ["GTA6"]}, c, "youtube")
    assert t.split("\n")[0] == "@FoltynFamily" and t.endswith("#GTA6 #Shorts"), t
    assert post_text({"title": "T"}, {}, "instagram") == "T"
    print("campaign ok")

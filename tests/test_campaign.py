"""A campaign brief on a creator: the @name each platform must tag goes first in every post."""
import os
import sys
import tempfile

os.environ["HICLIP_DATA"] = tempfile.mkdtemp(prefix="pc-camp-")
sys.path.insert(0, ".")
from studio import campaign, creators  # noqa: E402

fails = []


def check(n, ok):
    print(("PASS " if ok else "FAIL ") + n)
    ok or fails.append(n)


BRIEF = """Foltyn GTA Clipper Guide
DO
Must TAG Foltyn on ALL POSTS (first line)
IG: @realfoltyn
YT: @FoltynFamily
TT: @klfoltyn
DON'T
DO NOT use external logos on posts"""
creator = {**creators.DEFAULTS, "campaign_brief": BRIEF, "post_footer": "Clipped from Foltyn"}
clip = {"title": "Foltyn finds the GTA 6 map edge", "description": "He kept driving.", "hashtags": ["GTA6"]}
check("each platform's handle is read from the brief",
      campaign.tags_for(creator) == {"instagram": "@realfoltyn", "youtube": "@FoltynFamily", "tiktok": "@klfoltyn"})
for plat, tag in (("instagram", "@realfoltyn"), ("tiktok", "@klfoltyn"), ("youtube", "@FoltynFamily")):
    check(f"the {plat} post starts with {tag}", campaign.post_text(clip, creator, plat).split("\n")[0] == tag)
check("a YouTube description ends its hashtags with #Shorts",
      "#GTA6 #Shorts" in campaign.post_text(clip, creator, "youtube", with_title=False))
check("a typed handle (even a profile link) wins over the brief",
      campaign.tags_for({**creator, "campaign_tags": {"tiktok": "https://tiktok.com/@other"}})["tiktok"] == "@other")
clean = creators._clean({"campaign_brief": BRIEF + "\r\n\x07", "campaign_tags": {"instagram": "@ok", "myspace": "@x", "tiktok": "no spaces allowed"}})
check("a saved brief keeps its lines", clean["campaign_brief"].count("\n") == BRIEF.count("\n"))
check("only known platforms and real handles are saved", clean["campaign_tags"] == {"instagram": "@ok"})
check("no campaign: the post is as before", campaign.post_text(clip, creators.DEFAULTS, "tiktok").split("\n")[0] == clip["title"])
print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)

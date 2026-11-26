"""The title check (ported from /yt-package): flags long and repeated titles, puts the cleanest title first."""
import sys

sys.path.insert(0, ".")
from studio import brain, package  # noqa: E402

fails = []


def check(n, ok):
    print(("PASS " if ok else "FAIL ") + n)
    ok or fails.append(n)


long = "Kai Cenat Cannot Believe What Happens When He Drives To The Very Edge Of The Map 😱"
check("a title past the desktop cut is flagged", any(k == "length" for k, _ in package.check(long)["issues"]))
check("a heading that repeats the title is flagged",
      any(k == "duplicate" for k, _ in package.check("Kai Cenat Gets Jumpscared 😱", "KAI GETS JUMPSCARED")["issues"]))
check("a heading with its own words is fine",
      not any(k == "duplicate" for k, _ in package.check("Kai Cenat Gets Jumpscared 😱", "HE WAS NOT READY")["issues"]))
t, alts, swapped = package.best_first("THE MOST INSANE CRAZY EPIC MOMENT EVER", ["Kai Cenat Finds the Edge of the GTA 6 Map 😱"])
check("the cleanest title goes first", swapped and t.startswith("Kai Cenat") and alts[0].startswith("THE MOST"))
t, alts, swapped = package.best_first("Kai Cenat Crashes in GTA 6 💀", ["Kai Cenat Flips in GTA 6 💀"])
check("the AI's own title stays on a tie", not swapped and t == "Kai Cenat Crashes in GTA 6 💀")
c = brain._tidy({"score": 80, "hook": "HE WAS NOT READY", "title": "THE MOST INSANE CRAZY EPIC MOMENT EVER",
                 "alt_titles": ["Kai Cenat Gets Jumpscared in GTA 6 😱"], "title_why": "x", "hashtags": []}, 1, 20)
check("picks from the AI are re-ordered and scored", c["title"].startswith("Kai Cenat") and c["title_score"] >= 80
      and c["title_why"].startswith("Picked by the title check"))
print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)

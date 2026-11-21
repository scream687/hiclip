"""Small helpers for what is safe to show a user."""
import re

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")   # terminal colour codes yt-dlp adds when run in a terminal
_PATH = re.compile(r"(?:[A-Za-z]:)?[\\/][^\s'\"]*[\\/][^\s'\"]+")


def friendly(e, limit=600) -> str:
    """An error message for the user: trimmed, with any server file paths removed. A missing ffmpeg, which Windows
    reports only as "the system cannot find the file specified", is said in plain words with what to do."""
    from . import tools
    return tools.explain(e) or _PATH.sub("[file]", _ANSI.sub("", str(e)).strip()[-limit:])


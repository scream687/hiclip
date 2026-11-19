"""When the AI service in Settings fails (rate limit, no credit, down), the same question goes down a chain of other
AIs this computer can already use, so clipping never stops:

  gemini-cli  Google's Gemini CLI, signed in with your Google account (its own free quota)
  claude      Claude Code, signed in with your Claude subscription
  codex       Codex CLI, signed in with your ChatGPT plan
  ollama      a model running on this machine: no limits, no account (HICLIP_OLLAMA_MODEL, default qwen2.5:7b)

HICLIP_AI_FALLBACKS sets the order (comma-separated; default claude,ollama, see ORDER); empty turns it off. A tool that is not
installed or not signed in is skipped. The prompt carries text from other people's videos, so every tool runs in a
new empty folder with its tools switched off (or read-only): it can only answer, never act.
"""
import json
import os
import shutil
import subprocess
import tempfile

import httpx

from . import ai

# Default: only the two that cannot touch this computer at all (Claude with every tool off, and Ollama, which has none).
# gemini-cli and codex can still READ files in their safest modes, so a video whose words carry instructions could
# get a private file copied into a description: add them to HICLIP_AI_FALLBACKS only if you accept that.
ORDER = [x.strip() for x in os.environ.get("HICLIP_AI_FALLBACKS", "claude,ollama").split(",") if x.strip()]
OLLAMA_URL = os.environ.get("HICLIP_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("HICLIP_OLLAMA_MODEL", "qwen2.5:7b")


def _full(prompt: str, schema: dict) -> str:
    return (prompt + "\n\nAnswer with JSON only (no prose, no code fence), matching this JSON Schema:\n"
            + json.dumps(ai._json_schema(schema)))


def _run(cmd: list, text: str, timeout: float, out_file: str | None = None) -> str:
    with tempfile.TemporaryDirectory(prefix="hiclip-ai-") as empty:
        r = subprocess.run(cmd, input=text, cwd=empty, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, env={**os.environ, "NO_COLOR": "1"})
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout).strip()[-300:] or f"exit {r.returncode}")
        if out_file:
            path = os.path.join(empty, out_file)
            return open(path, encoding="utf-8").read() if os.path.exists(path) else r.stdout
        return r.stdout


def _gemini_cli(text, timeout):
    return _run(["gemini", "--skip-trust", "--approval-mode", "plan", "-p", "Answer the request given as input."], text, timeout)


def _claude(text, timeout):
    return _run(["claude", "-p", "--tools", "", "--strict-mcp-config", "--setting-sources", "", "--disable-slash-commands",
                 "--model", "sonnet"], text, timeout)


def _codex(text, timeout):
    return _run(["codex", "exec", "--skip-git-repo-check", "--sandbox", "read-only", "-c", 'model_reasoning_effort="low"',
                 "-o", "answer.txt", "-"], text, timeout, out_file="answer.txt")


def _ollama(text, timeout):
    r = httpx.post(f"{OLLAMA_URL}/api/chat", timeout=timeout, json={
        "model": OLLAMA_MODEL, "stream": False, "format": "json", "messages": [{"role": "user", "content": text}]})
    if r.status_code != 200:
        raise RuntimeError(f"Ollama: {r.text[:200]}")
    return r.json()["message"]["content"]


TOOLS = {"gemini-cli": ("gemini", _gemini_cli), "claude": ("claude", _claude), "codex": ("codex", _codex),
         "ollama": (None, _ollama)}


def available() -> list:
    return [n for n in ORDER if n in TOOLS and (TOOLS[n][0] is None or shutil.which(TOOLS[n][0]))]


def ask(prompt: str, schema: dict, timeout: float | None = None):
    """(answer, name of the tool that gave it). Raises with every tool's reason when none could answer."""
    text, why = _full(prompt, schema), []
    for name in available():
        try:
            out = ai._parse(TOOLS[name][1](text, max(timeout or 0, 120)))
            if isinstance(out, dict) and schema.get("type") == "ARRAY":   # one item where a list was asked for
                out = next((v for v in out.values() if isinstance(v, list)), [out])
            if out is not None:
                return out, name
            why.append(f"{name}: no JSON in the answer")
        except Exception as e:  # noqa: BLE001 - not signed in, rate-limited too, timed out: try the next one
            why.append(f"{name}: {str(e)[-120:]}")
    raise RuntimeError("; ".join(why) or "no fallback AI is installed")

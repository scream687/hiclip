#!/bin/sh
# Start hiclip at http://127.0.0.1:8765 (or HICLIP_PORT)
cd "$(dirname "$0")" || exit 1
export DYLD_FALLBACK_LIBRARY_PATH="/opt/homebrew/lib${DYLD_FALLBACK_LIBRARY_PATH:+:$DYLD_FALLBACK_LIBRARY_PATH}"
exec .venv/bin/python -m studio.server

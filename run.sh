#!/usr/bin/env bash
# Development server with auto-reload. For the desktop app run `uv run cvmaker` instead.
cd "$(dirname "$0")"
# The copy that starts at login (Settings → Start at login) uses this port and the same Telegram bot.
systemctl --user stop cvmaker.service 2>/dev/null || true
exec ~/.local/bin/uv run uvicorn cvmaker.app:app --host 127.0.0.1 --port "${PORT:-8000}" --reload --reload-dir cvmaker "$@"

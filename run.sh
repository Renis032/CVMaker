#!/usr/bin/env bash
cd "$(dirname "$0")"
exec ~/.local/bin/uv run uvicorn cvmaker.app:app --port "${PORT:-8000}" --reload --reload-dir cvmaker "$@"

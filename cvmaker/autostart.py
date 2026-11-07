"""Start CVMaker when you log in, without a window, so scheduled searches and the Telegram bot keep working.

Linux only for now: a systemd user service that runs the desktop launcher (desktop.py) on a fixed port, so the address
stays the same and `cvmaker` opens the running copy instead of starting a second one. run.sh stops the service first.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import settings

UNIT = "cvmaker.service"
PORT = 8000


def available() -> bool:
    return sys.platform == "linux" and shutil.which("systemctl") is not None


def _unit_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user" / UNIT


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=30, check=False)


def _quoted(value: str) -> str:
    """A value for a systemd unit file ('%' starts a specifier there)."""
    return '"' + value.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"') + '"'


def unit_text() -> str:
    # systemd doesn't read your shell profile: keep this PATH so Chrome and Claude Code are found.
    path = os.environ.get("PATH", "")
    return f"""[Unit]
Description=CVMaker: job search, scheduled searches and the Telegram bot

[Service]
WorkingDirectory={str(settings.ROOT).replace('%', '%%')}
Environment={_quoted(f'PATH={path}')}
Environment=CVMAKER_NO_WINDOW=1
Environment=PORT={PORT}
ExecStart={_quoted(sys.executable)} -m cvmaker.desktop
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
"""


def enabled() -> bool:
    return _systemctl("is-enabled", UNIT).stdout.strip() == "enabled"


def enable() -> None:
    """Takes effect at the next login. It doesn't start now, because this copy of the app is already running."""
    path = _unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(unit_text())
    _systemctl("daemon-reload")
    result = _systemctl("enable", UNIT)
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[:300] or "systemctl enable failed")


def disable() -> None:
    """Stops future starts. A running copy keeps running, since this request may be served by it."""
    _systemctl("disable", UNIT)
    _unit_path().unlink(missing_ok=True)
    _systemctl("daemon-reload")

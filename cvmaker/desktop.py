"""The desktop app: `cvmaker` (or `python -m cvmaker.desktop`).

Starts CVMaker on this computer only (127.0.0.1, a free port) and opens it in its own window:
- with pywebview installed (`pip install 'cvmaker[desktop]'`): a native window;
- else Chrome / Chromium / Edge in app mode (a window without the browser's tabs and address bar);
- else the default browser.
Closing the window (pywebview) or pressing Ctrl+C stops it. A second start just opens the running one.

Data goes to the per-user data folder of the system (Linux ~/.local/share/CVMaker, macOS ~/Library/Application
Support/CVMaker, Windows %APPDATA%\\CVMaker), unless CVMAKER_DATA says otherwise or this is a source checkout that
already has a data/ folder. Everyone brings their own Anthropic API key (Settings → API keys).
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

APP_NAME = "CVMaker"


def user_data_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support" / APP_NAME
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / APP_NAME


def _choose_data_dir() -> Path:
    if os.environ.get("CVMAKER_DATA"):
        return Path(os.environ["CVMAKER_DATA"]).expanduser()
    checkout = Path(__file__).resolve().parent.parent / "data"
    if checkout.exists() and not getattr(sys, "frozen", False):
        return checkout
    return user_data_dir()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _alive(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/healthz", timeout=2) as r:
            return r.status == 200
    except OSError:
        return False


def _browser_app(url: str, data: Path) -> bool:
    """Open the URL as an app window in a Chromium-based browser, with its own profile so it opens a new window."""
    for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge", "brave-browser", "chrome", "msedge"):
        exe = shutil.which(name)
        if exe:
            profile = data / "window-profile"
            subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
                              "--window-size=1440,960"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
    mac_chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if mac_chrome.exists():
        subprocess.Popen([str(mac_chrome), f"--app={url}", f"--user-data-dir={data / 'window-profile'}", "--no-first-run"])
        return True
    return False


def main() -> None:
    data = _choose_data_dir()
    data.mkdir(parents=True, exist_ok=True)
    os.environ["CVMAKER_DATA"] = str(data)
    os.environ["CVMAKER_MODE"] = "desktop"
    # A source checkout's .env still counts (e.g. CVMAKER_BACKEND=claude-code for development); else the API.
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    os.environ.setdefault("CVMAKER_BACKEND", "api")
    lock = data / ".running.json"

    # Already running? Open that one.
    if lock.exists():
        try:
            running = json.loads(lock.read_text())["url"]
            if _alive(running):
                if not os.environ.get("CVMAKER_NO_WINDOW") and not _browser_app(running, data):
                    webbrowser.open(running)
                print(f"{APP_NAME} is already running at {running}")
                return
        except (ValueError, KeyError, OSError):
            pass

    port = int(os.environ.get("PORT") or _free_port())
    url = f"http://127.0.0.1:{port}"
    os.environ["CVMAKER_PUBLIC_URL"] = url

    import uvicorn

    from .app import app

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if _alive(url):
            break
        time.sleep(0.1)
    lock.write_text(json.dumps({"url": url, "pid": os.getpid()}))
    print(f"{APP_NAME} is running at {url} (data in {data}). Press Ctrl+C to stop.")
    try:
        try:
            import webview  # pywebview: a native window; closing it stops the app
        except ImportError:
            webview = None
        if webview is not None and not os.environ.get("CVMAKER_NO_WINDOW"):
            webview.create_window(APP_NAME, url, width=1440, height=960, min_size=(900, 600))
            webview.start(private_mode=False, storage_path=str(data / "window-profile"))
        else:
            if not os.environ.get("CVMAKER_NO_WINDOW") and not _browser_app(url, data):
                webbrowser.open(url)
            while thread.is_alive():
                thread.join(1)
    except KeyboardInterrupt:
        pass
    finally:
        server.should_exit = True
        lock.unlink(missing_ok=True)
        thread.join(5)


if __name__ == "__main__":
    main()

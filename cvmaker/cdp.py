"""Headless Chrome driven over the DevTools protocol (a pipe, no network port): PDFs and JavaScript-built pages.

Chrome's one-shot command-line modes (--print-to-pdf, --dump-dom) hang in some Chromium browsers (Brave 157 never
exits), so the browser is started with --remote-debugging-pipe and told what to do: open the page, wait until it
has loaded (and, for career pages, until its scripts are done), then print it or read its HTML. Messages are JSON,
each ended by a NUL byte; the browser reads them on its fd 3 and answers on fd 4. POSIX only: on Windows the
command-line modes are used (web_fetch / render)."""

import base64
import json
import os
import select
import subprocess
import time
from pathlib import Path

AVAILABLE = os.name == "posix"


class CDPError(RuntimeError):
    pass


class _Browser:
    def __init__(self, args: list[str], timeout: float):
        self.deadline = time.monotonic() + timeout
        to_browser_r, self._to_browser = os.pipe()
        self._from_browser, from_browser_w = os.pipe()

        def fds():  # in the child: the pipe ends become fd 3 (commands in) and fd 4 (answers out)
            os.dup2(to_browser_r, 3)
            os.dup2(from_browser_w, 4)

        self.proc = subprocess.Popen(
            [*args, "--remote-debugging-pipe", "about:blank"], preexec_fn=fds, pass_fds=(3, 4, to_browser_r, from_browser_w),  # 3 and 4 stay open after fds()
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        os.close(to_browser_r)
        os.close(from_browser_w)
        self._buf = b""
        self._next = 0
        self.events: list[dict] = []

    def _read(self) -> dict:
        while b"\0" not in self._buf:
            left = self.deadline - time.monotonic()
            if left <= 0:
                raise subprocess.TimeoutExpired("chrome", 0)
            ready, _, _ = select.select([self._from_browser], [], [], left)
            if not ready:
                continue
            chunk = os.read(self._from_browser, 1 << 20)
            if not chunk:
                err = self.proc.stderr.read().decode("utf-8", "replace")[-300:] if self.proc.stderr else ""
                raise CDPError(f"The browser closed: {err}")
            self._buf += chunk
        raw, self._buf = self._buf.split(b"\0", 1)
        return json.loads(raw)

    def send(self, method: str, params: dict | None = None, session: str = "") -> dict:
        self._next += 1
        msg = {"id": self._next, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        os.write(self._to_browser, json.dumps(msg).encode() + b"\0")
        while True:
            reply = self._read()
            if reply.get("id") == self._next:
                if "error" in reply:
                    raise CDPError(f"{method}: {reply['error'].get('message', reply['error'])}")
                return reply.get("result", {})
            if "method" in reply:
                self.events.append(reply)

    def wait_event(self, method: str, session: str = "") -> dict:
        while True:
            for i, e in enumerate(self.events):
                if e["method"] == method and (not session or e.get("sessionId") == session):
                    return self.events.pop(i)
            self.events.append(self._read())

    def close(self) -> None:
        try:
            if self.proc.poll() is None:
                try:
                    self.deadline = time.monotonic() + 5
                    self.send("Browser.close")
                except (OSError, CDPError, subprocess.TimeoutExpired, ValueError):
                    pass
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait()
        finally:
            for fd in (self._to_browser, self._from_browser):
                try:
                    os.close(fd)
                except OSError:
                    pass
            if self.proc.stderr:
                self.proc.stderr.close()


def _open(args: list[str], url: str, timeout: float, virtual_time_ms: int = 0) -> tuple[_Browser, str]:
    """A browser with `url` loaded; returns it and the page's session id."""
    browser = _Browser(args, timeout)
    try:
        target = browser.send("Target.createTarget", {"url": "about:blank"})["targetId"]
        session = browser.send("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
        browser.send("Page.enable", session=session)
        browser.send("Page.navigate", {"url": url}, session=session)
        browser.wait_event("Page.loadEventFired", session)
        if virtual_time_ms:
            # Like --virtual-time-budget: run the page's timers and requests as fast as possible, up to this much
            # page time, so lists built by JavaScript are there when the HTML is read.
            browser.send("Emulation.setVirtualTimePolicy", {"policy": "pauseIfNetworkFetchesPending",
                                                            "budget": virtual_time_ms}, session=session)
            browser.wait_event("Emulation.virtualTimeBudgetExpired", session)
        return browser, session
    except BaseException:
        browser.close()
        raise


def print_pdf(args: list[str], url: str, out_path: Path, timeout: float = 90) -> Path:
    """Print the page at `url` (its own @page size and margins, backgrounds included) to `out_path`."""
    browser, session = _open(args, url, timeout)
    try:
        browser.send("Runtime.evaluate", {"expression": "document.fonts.ready.then(() => true)", "awaitPromise": True},
                     session=session)
        pdf = browser.send("Page.printToPDF", {"printBackground": True, "preferCSSPageSize": True,
                                               "displayHeaderFooter": False}, session=session)
    finally:
        browser.close()
    out_path.write_bytes(base64.b64decode(pdf["data"]))
    return out_path


def dump_dom(args: list[str], url: str, virtual_time_ms: int = 15000, timeout: float = 90) -> str:
    """The page's HTML after its scripts ran (like --dump-dom with --virtual-time-budget)."""
    browser, session = _open(args, url, timeout, virtual_time_ms)
    try:
        result = browser.send("Runtime.evaluate", {"expression": "document.documentElement.outerHTML",
                                                   "returnByValue": True}, session=session)
    finally:
        browser.close()
    return result.get("result", {}).get("value", "") or ""

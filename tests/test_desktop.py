"""Desktop mode (run in a separate process, since the mode is fixed at start-up): no login, only localhost,
your own API key."""

import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

SCRIPT = textwrap.dedent('''
    import os
    from fastapi.testclient import TestClient
    from cvmaker.app import app
    from cvmaker import keys, people, settings, usage

    assert settings.DESKTOP
    c = TestClient(app, base_url="http://127.0.0.1:8000", follow_redirects=False)
    # no login: straight in, the local account
    r = c.get("/settings")
    assert r.status_code == 200, r.status_code
    # without a key the pages say how to add one
    assert "Add your Anthropic API key" in c.get("/").text
    # only localhost answers (DNS rebinding)
    assert TestClient(app, base_url="http://attacker.example").get("/").status_code == 421
    assert TestClient(app, base_url="http://localhost:1234").get("/healthz").status_code == 200
    # another web site can't make the local app do things
    assert c.post("/people/add", data={"name": "X"}, headers={"Origin": "http://attacker.example"}).status_code == 403
    # local scripts (no Origin) and the app's own pages can
    assert c.post("/settings/keys", data={"anthropic": "sk-ant-desktop-key-0123456789"}, headers={"Origin": "http://127.0.0.1:8000"}).status_code == 303
    assert "Add your Anthropic API key" not in c.get("/").text
    assert keys.get("anthropic") == "sk-ant-desktop-key-0123456789"
    # desktop use is never limited
    usage.record(10**9, 10**9)
    usage.check()
    # sign-up pages don't exist on the desktop
    assert c.get("/login").status_code == 303
    print("DESKTOP OK")
''')


def test_desktop_mode():
    with tempfile.TemporaryDirectory(prefix="cvmaker-desktop-") as data:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("CVMAKER_", "STRIPE_", "DATABASE_URL", "ANTHROPIC"))}
        env.update({"CVMAKER_MODE": "desktop", "CVMAKER_DATA": data, "CVMAKER_BACKEND": "api", "CVMAKER_TASK_WORKERS": "0",
                    "ANTHROPIC_API_KEY": ""})
        r = subprocess.run([sys.executable, "-c", SCRIPT], cwd=Path(__file__).parent.parent, env=env, capture_output=True, text=True, timeout=120)
        assert "DESKTOP OK" in r.stdout, r.stdout + r.stderr
        # the secret key was made once, readable only by the user
        secret = Path(data) / ".secret_key"
        assert secret.exists() and oct(secret.stat().st_mode)[-3:] == "600"

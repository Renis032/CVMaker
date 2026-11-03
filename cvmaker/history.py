"""History of your CV: snapshots of the profile and the CV editor's layout (versions, style, design), so any change
can be undone later, even one made weeks ago.

A snapshot is the state *before* a change. Edits close together share one snapshot (at most one per
SNAPSHOT_EVERY seconds), so a long editing session is one restore point, but anything that removes versions or
replaces the CV wholesale always gets its own.
"""

import json
import shutil
import time
from datetime import datetime
from pathlib import Path

from . import settings

SNAPSHOT_EVERY = 10 * 60
KEEP = 200
FILES = ("profile.json", "editor.json")


def _dir() -> Path:
    return settings.DATA_DIR / "history"


def _files() -> dict[str, Path]:
    return {"profile.json": settings.PROFILE_PATH, "editor.json": settings.DATA_DIR / "editor.json"}


def snapshot(reason: str, force: bool = False) -> str | None:
    """Save the current profile and editor layout. Returns the snapshot id, or None when skipped (a recent snapshot
    already covers this editing session, or there is nothing to save yet)."""
    sources = {name: path for name, path in _files().items() if path.exists()}
    if not sources:
        return None
    entries = list_snapshots()
    if not force and entries and time.time() - entries[0]["created"] < SNAPSHOT_EVERY:
        return None
    if entries and all(_same(entries[0]["id"], name, path) for name, path in sources.items()) and set(sources) == set(entries[0]["files"]):
        return None  # nothing changed since the last snapshot
    snap_id = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    folder = _dir() / snap_id
    folder.mkdir(parents=True, exist_ok=True)
    for name, path in sources.items():
        shutil.copy2(path, folder / name)
    (folder / "meta.json").write_text(json.dumps({"reason": reason, "created": time.time(), "files": sorted(sources)}))
    for old in list_snapshots()[KEEP:]:
        shutil.rmtree(_dir() / old["id"], ignore_errors=True)
    return snap_id


def _same(snap_id: str, name: str, path: Path) -> bool:
    saved = _dir() / snap_id / name
    return saved.exists() and saved.read_bytes() == path.read_bytes()


def list_snapshots() -> list[dict]:
    """Newest first, each with a short summary of what it holds."""
    if not _dir().is_dir():
        return []
    out = []
    for folder in _dir().iterdir():
        meta = folder / "meta.json"
        if not meta.is_file():
            continue
        try:
            info = json.loads(meta.read_text())
        except ValueError:
            continue
        out.append({"id": folder.name, "reason": info.get("reason", ""), "created": info.get("created", 0), "files": info.get("files", [])})
    return sorted(out, key=lambda e: e["created"], reverse=True)


def summary(snap_id: str) -> dict:
    """What a snapshot contains, for the history list: version names, counts, and the summary's first words."""
    folder = _dir() / Path(snap_id).name
    out: dict = {"versions": [], "jobs": 0, "projects": 0, "skills": 0, "summary": "", "free": 0}
    try:
        layout = json.loads((folder / "editor.json").read_text())
        out["versions"] = [v.get("name", "") for v in layout.get("variants", [])]
        out["free"] = len(layout.get("design", {}).get("free", []))
    except (OSError, ValueError):
        pass
    try:
        profile = json.loads((folder / "profile.json").read_text())
        out["jobs"] = len(profile.get("experience", []))
        out["projects"] = len(profile.get("projects", []))
        out["skills"] = sum(len(g.get("items", g.get("skills", [])) or []) for g in profile.get("skills", []) if isinstance(g, dict))
        out["summary"] = (profile.get("summary") or "")[:140]
    except (OSError, ValueError):
        pass
    return out


def restore(snap_id: str) -> None:
    """Put a snapshot back. The current state is snapshotted first, so a restore can itself be undone."""
    folder = _dir() / Path(snap_id).name
    if not (folder / "meta.json").is_file():
        raise ValueError("That restore point no longer exists.")
    snapshot("Before restoring an earlier version", force=True)
    for name, path in _files().items():
        saved = folder / name
        if saved.exists():
            tmp = path.with_suffix(".restore-tmp")
            shutil.copy2(saved, tmp)
            tmp.replace(path)

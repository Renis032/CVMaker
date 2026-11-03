"""One-file backups: everything of the current person (profile, CV editor design and uploads, jobs database, CVs,
letters, packs, chat files, history) in a zip, and restoring from one."""

import io
import shutil
import sqlite3
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath

from . import people, settings

# Never part of a person's backup: other people's folders (they live inside the default person's folder), the
# backups themselves and half-written temp files.
SKIP_TOP = {"people", "people.json", "backups"}
KEEP_BACKUPS = 8
DB_NAME = "cvmaker.db"


def backups_dir() -> Path:
    path = settings.DATA_DIR / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _copy_db(dest: Path) -> None:
    """A consistent copy of the live database, even while the app writes to it."""
    src = sqlite3.connect(f"file:{settings.DB_PATH}?mode=ro", uri=True)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()


def make(label: str = "") -> Path:
    """Write a backup zip into data/…/backups/ and return its path."""
    root = settings.DATA_DIR
    name = f"cvmaker-backup-{people.current()}-{time.strftime('%Y%m%d-%H%M%S')}{('-' + label) if label else ''}.zip"
    out = backups_dir() / name
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        if Path(settings.DB_PATH).exists():
            db_copy = Path(tmp) / DB_NAME
            _copy_db(db_copy)
            z.write(db_copy, DB_NAME)
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(root)
            if rel.parts[0] in SKIP_TOP or path.is_dir() or path.suffix in (".tmp", ".restore-tmp"):
                continue
            if rel.as_posix() in (DB_NAME, f"{DB_NAME}-journal", f"{DB_NAME}-wal", f"{DB_NAME}-shm"):
                continue
            z.write(path, rel.as_posix())
    for old in sorted(backups_dir().glob("cvmaker-backup-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)[KEEP_BACKUPS:]:
        old.unlink(missing_ok=True)
    return out


def _safe_names(z: zipfile.ZipFile) -> list[str]:
    names = []
    for info in z.infolist():
        if info.is_dir():
            continue
        p = PurePosixPath(info.filename)
        if p.is_absolute() or ".." in p.parts or not p.parts or "\\" in info.filename:
            raise ValueError(f"The backup contains an unsafe path: {info.filename}")
        if p.parts[0] in SKIP_TOP:
            continue
        names.append(info.filename)
    return names


def restore(data: bytes) -> list[str]:
    """Replace the current person's data with a backup's. A backup of the current state is made first.
    Returns a short report."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise ValueError("That file is not a CVMaker backup (not a zip file).") from e
    with z:
        names = _safe_names(z)
        if "profile.json" not in names and DB_NAME not in names:
            raise ValueError("That zip is not a CVMaker backup (no profile.json or cvmaker.db in it).")
        safety = make("before-restore")
        root = settings.DATA_DIR
        for name in names:
            if name == DB_NAME:
                continue
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(name) as src, open(target.with_suffix(target.suffix + ".restore-tmp"), "wb") as dst:
                shutil.copyfileobj(src, dst)
            target.with_suffix(target.suffix + ".restore-tmp").replace(target)
        if DB_NAME in names:
            with tempfile.TemporaryDirectory() as tmp:
                z.extract(DB_NAME, tmp)
                src = sqlite3.connect(Path(tmp) / DB_NAME)
                dst = sqlite3.connect(settings.DB_PATH, timeout=30)
                try:
                    src.backup(dst)  # replaces the live database's contents in one step
                finally:
                    src.close()
                    dst.close()
    return [f"Restored {len(names)} files.", f"Your data before the restore is saved as {safety.name} (Backups on My profile)."]


def list_backups() -> list[dict]:
    return [
        {"name": p.name, "size": p.stat().st_size, "created": p.stat().st_mtime}
        for p in sorted(backups_dir().glob("cvmaker-backup-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    ]

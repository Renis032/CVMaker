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
# backups themselves, half-written temp files, uploads waiting for a task, and what belongs to the installation
# (the central database, the secret key, the account's encrypted API keys, other accounts).
SKIP_TOP = {"people", "people.json", "backups", "incoming", "keys.json", ".secret_key", "central.db", "central.db-wal",
            "central.db-shm", "accounts", "deleted", "telegram.json", "telegram_bot.json"}
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


# --- automatic backups and the full export ---------------------------------------------------------------------------


def scheduled() -> int:
    """Back up every person of every account whose last automatic backup is older than BACKUP_EVERY_HOURS, and copy
    it to object storage when S3 is set up. Returns how many backups were made."""
    from . import central, storage

    if settings.BACKUP_EVERY_HOURS <= 0:
        return 0
    made = 0
    for account_id in people.all_accounts():
        with people.in_account(account_id):
            for person in people.load():
                key = f"backup:{account_id}:{person.id}"
                last = float(central.get_meta(key) or 0)
                if time.time() - last < settings.BACKUP_EVERY_HOURS * 3600:
                    continue
                with people.acting_as(person.id):
                    path = make("auto")
                    if storage.enabled():
                        storage.put(f"backups/{account_id}/{person.id}/{path.name}", path)
                central.set_meta(key, str(time.time()))
                made += 1
    return made


def export_account() -> Path:
    """Everything the account holds, for the person to take away: one backup zip per person inside one zip, plus
    the account details and AI usage."""
    import json

    from . import accounts, central

    account_id = people.account()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = people.account_root() / "backups"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"cvmaker-export-{stamp}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        info = {"exported": stamp, "people": [p.model_dump() for p in people.load()]}
        if settings.SERVER:
            account = accounts.get(account_id) or {}
            info["account"] = {k: account.get(k) for k in ("email", "name", "plan", "plan_until", "created", "verified")}
            with central.connect() as c:
                info["ai_usage"] = c.all("SELECT month, input_tokens, output_tokens, calls, own_key_calls FROM usage WHERE account_id = ?", (account_id,))
        z.writestr("account.json", json.dumps(info, indent=2, ensure_ascii=False))
        for person in people.load():
            with people.acting_as(person.id):
                path = make("export")
                z.write(path, f"people/{person.id}/{path.name}")
    return out

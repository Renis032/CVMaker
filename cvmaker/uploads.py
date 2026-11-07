"""Checking files people upload: size limits, the real file type (from the content, not the name), and images
decoded and saved again by Pillow, so what is stored is a plain picture whatever the upload contained.
"""

import io
import secrets
import zipfile
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from . import settings

# Decoding a huge image can eat all memory: refuse anything above ~50 megapixels.
Image.MAX_IMAGE_PIXELS = 50_000_000
MAX_SIDE = 4000

CV_KINDS = {"pdf", "docx", "txt", "image"}
DOC_KINDS = {"pdf", "docx", "txt", "image"}
# A zip (backup) may unpack to at most this much.
MAX_UNZIPPED_MB = 500


class UploadError(ValueError):
    pass


def max_bytes() -> int:
    return int(settings.MAX_UPLOAD_MB * 1024 * 1024)


def check_size(data: bytes, limit: int | None = None) -> None:
    limit = limit or max_bytes()
    if len(data) > limit:
        raise UploadError(f"That file is too big (the limit is {limit // (1024 * 1024)} MB).")
    if not data:
        raise UploadError("That file is empty.")


async def read(upload, kinds: set[str] | None = None, limit: int | None = None) -> bytes:
    """Read an uploaded file without taking more than the limit into memory, and check its type."""
    limit = limit or max_bytes()
    chunks, size = [], 0
    while chunk := await upload.read(1024 * 1024):
        size += len(chunk)
        if size > limit:
            raise UploadError(f"That file is too big (the limit is {limit // (1024 * 1024)} MB).")
        chunks.append(chunk)
    data = b"".join(chunks)
    check_size(data, limit)
    if kinds is not None and sniff(data) not in kinds:
        raise UploadError("That file type isn't supported here.")
    return data


def sniff(data: bytes) -> str:
    """The kind of file from its first bytes: pdf, docx, zip, image, svg, txt or unknown."""
    head = data[:16]
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                return "docx" if "word/document.xml" in z.namelist() else "zip"
        except zipfile.BadZipFile:
            return "unknown"
    if head.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a")) or (head[:4] == b"RIFF" and head[8:12] == b"WEBP"):
        return "image"
    start = data[:512].lstrip().lower()
    if start.startswith((b"<svg", b"<?xml")) and b"<svg" in data[:4096].lower():
        return "svg"
    try:
        data[:65536].decode("utf-8")
        return "txt"
    except UnicodeDecodeError:
        return "unknown"


def clean_image(data: bytes, keep_format: bool = True) -> tuple[bytes, str]:
    """Decode the image and encode it again (PNG for pictures with transparency, else JPEG; GIF/WebP become PNG),
    capped at MAX_SIDE pixels. Returns (bytes, extension)."""
    check_size(data)
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            fmt = (img.format or "").upper()
            if fmt not in {"PNG", "JPEG", "GIF", "WEBP"}:
                raise UploadError("Use a PNG, JPEG, GIF or WebP picture.")
            img = img.convert("RGBA" if img.mode in ("RGBA", "LA", "P") else "RGB")
            if max(img.size) > MAX_SIDE:
                img.thumbnail((MAX_SIDE, MAX_SIDE))
            out = io.BytesIO()
            if fmt == "JPEG" and keep_format and img.mode == "RGB":
                img.save(out, "JPEG", quality=92)
                return out.getvalue(), ".jpg"
            img.save(out, "PNG", optimize=True)
            return out.getvalue(), ".png"
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError) as e:
        raise UploadError("That picture couldn't be read - try a PNG or JPEG.") from e


def check_zip(data: bytes) -> None:
    """Refuse zip files that unpack to far more than they weigh (zip bombs)."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            total = sum(i.file_size for i in z.infolist())
    except zipfile.BadZipFile as e:
        raise UploadError("That is not a zip file.") from e
    if total > MAX_UNZIPPED_MB * 1024 * 1024:
        raise UploadError(f"That zip unpacks to more than {MAX_UNZIPPED_MB} MB.")


# --- holding an upload until a background task reads it -----------------------------------------------------------


def _incoming() -> Path:
    path = settings.DATA_DIR / "incoming"
    path.mkdir(parents=True, exist_ok=True)
    return path


def stash(data: bytes, name: str) -> str:
    stored = f"{secrets.token_hex(8)}{Path(name).suffix.lower()[:8]}"
    (_incoming() / stored).write_bytes(data)
    return stored


def unstash(stored: str) -> bytes:
    path = _incoming() / Path(stored).name
    if not path.is_file():
        raise UploadError("The uploaded file is gone - upload it again.")
    return path.read_bytes()


def drop(stored: str) -> None:
    (_incoming() / Path(stored).name).unlink(missing_ok=True)

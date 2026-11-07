"""Object storage (S3 or an S3-compatible store such as Hetzner, Scaleway, Cloudflare R2 or MinIO) for copies of
backups away from the server. Off unless S3_BUCKET is set; needs the "server" extra (boto3).

Credentials come the usual boto3 way (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY, or an instance role).
"""

from pathlib import Path

from . import settings

_client = None


def enabled() -> bool:
    return bool(settings.S3_BUCKET)


def _s3():
    global _client
    if _client is None:
        try:
            import boto3
        except ImportError as e:  # pragma: no cover - depends on the install
            raise RuntimeError("S3_BUCKET is set: install the server extra (pip install 'cvmaker[server]')") from e
        _client = boto3.client("s3", region_name=settings.S3_REGION, endpoint_url=settings.S3_ENDPOINT or None)
    return _client


def _key(name: str) -> str:
    return f"{settings.S3_PREFIX.strip('/')}/{name.lstrip('/')}" if settings.S3_PREFIX else name.lstrip("/")


def put(name: str, path: Path) -> str:
    """Upload a file (server-side encrypted). Returns its key."""
    key = _key(name)
    _s3().upload_file(str(path), settings.S3_BUCKET, key, ExtraArgs={"ServerSideEncryption": "AES256"} if not settings.S3_ENDPOINT else None)
    return key


def get(name: str, path: Path) -> Path:
    _s3().download_file(settings.S3_BUCKET, _key(name), str(path))
    return path


def delete_prefix(prefix: str) -> int:
    """Delete everything under a prefix (an account's backups when the account is deleted)."""
    s3 = _s3()
    deleted = 0
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=settings.S3_BUCKET, Prefix=_key(prefix)):
        objects = [{"Key": o["Key"]} for o in page.get("Contents", [])]
        if objects:
            s3.delete_objects(Bucket=settings.S3_BUCKET, Delete={"Objects": objects})
            deleted += len(objects)
    return deleted

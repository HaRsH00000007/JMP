"""Object storage abstraction (local filesystem for dev; S3-compatible for production). Keys are relative
paths such as "pdfs/2026/09/DAN-JMP-PB-001_<id>.pdf" — never exposed to clients directly."""

from __future__ import annotations

import shutil
from datetime import datetime  # noqa: TC003  (used in date_prefix's annotation)
from pathlib import Path
from typing import BinaryIO, Protocol

from app.errors import ErrorCode, JmpError
from app.settings import settings


class Storage(Protocol):
    def put_bytes(self, key: str, data: bytes, content_type: str) -> str: ...
    def put_file(self, key: str, path: Path, content_type: str) -> str: ...
    def get_bytes(self, key: str) -> bytes: ...
    def open(self, key: str) -> BinaryIO: ...
    def exists(self, key: str) -> bool: ...
    def delete(self, key: str) -> None: ...
    def size(self, key: str) -> int: ...


def date_prefix(when: "datetime | None" = None) -> str:
    """Folder for a run's outputs: "<YYYY>/<DD_MM_YY>", e.g. 2026/23_09_26.

    Grouping by day keeps one bulk run's documents together and makes retention/cleanup obvious.
    Stored paths are absolute in the database, so changing this only affects new files.
    """
    from datetime import datetime, timezone

    d = when or datetime.now(timezone.utc)
    return f"{d:%Y}/{d:%d_%m_%y}"


def _safe_key(key: str) -> str:
    k = key.replace("\\", "/").lstrip("/")
    if ".." in k.split("/"):
        raise JmpError("Invalid storage key", code=ErrorCode.VALIDATION_ERROR)
    return k


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _p(self, key: str) -> Path:
        p = (self.root / _safe_key(key)).resolve()
        if self.root not in p.parents and p != self.root:
            raise JmpError("Invalid storage key", code=ErrorCode.VALIDATION_ERROR)
        return p

    def put_bytes(self, key: str, data: bytes, content_type: str) -> str:
        p = self._p(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(p)  # atomic on the same filesystem
        return _safe_key(key)

    def put_file(self, key: str, path: Path, content_type: str) -> str:
        p = self._p(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, p)
        return _safe_key(key)

    def get_bytes(self, key: str) -> bytes:
        return self._p(key).read_bytes()

    def open(self, key: str) -> BinaryIO:
        return open(self._p(key), "rb")

    def exists(self, key: str) -> bool:
        return self._p(key).exists()

    def delete(self, key: str) -> None:
        p = self._p(key)
        if p.exists():
            p.unlink()

    def size(self, key: str) -> int:
        return self._p(key).stat().st_size


class S3Storage:  # pragma: no cover - requires S3 credentials
    def __init__(self) -> None:
        import boto3

        s = settings()
        self.bucket = s.s3_bucket
        self.client = boto3.client(
            "s3", region_name=s.s3_region, endpoint_url=s.s3_endpoint_url,
            aws_access_key_id=s.s3_access_key_id.get_secret_value() if s.s3_access_key_id else None,
            aws_secret_access_key=s.s3_secret_access_key.get_secret_value() if s.s3_secret_access_key else None)

    def put_bytes(self, key: str, data: bytes, content_type: str) -> str:
        k = _safe_key(key)
        self.client.put_object(Bucket=self.bucket, Key=k, Body=data, ContentType=content_type,
                               ServerSideEncryption="AES256")
        return k

    def put_file(self, key: str, path: Path, content_type: str) -> str:
        k = _safe_key(key)
        self.client.upload_file(str(path), self.bucket, k, ExtraArgs={"ContentType": content_type,
                                                                      "ServerSideEncryption": "AES256"})
        return k

    def get_bytes(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=_safe_key(key))["Body"].read()

    def open(self, key: str) -> BinaryIO:
        return self.client.get_object(Bucket=self.bucket, Key=_safe_key(key))["Body"]  # type: ignore[no-any-return]

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=_safe_key(key))
            return True
        except Exception:  # noqa: BLE001
            return False

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=_safe_key(key))

    def size(self, key: str) -> int:
        return int(self.client.head_object(Bucket=self.bucket, Key=_safe_key(key))["ContentLength"])


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        _storage = S3Storage() if settings().storage_backend == "s3" else LocalStorage(settings().storage_root)
    return _storage


def reset_storage() -> None:
    global _storage
    _storage = None

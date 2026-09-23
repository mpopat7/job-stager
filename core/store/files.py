"""Where uploaded resumes live.

A resume carries a full name, phone number and often a home address, so it is stored
per user and never where another account could reach it.

A personal install keeps resumes on its own disk under `resumes/user_<id>/`, and a
profile stores the file's path. A shared deployment has no disk that outlives a restart
(Koyeb's free instance has none), so it keeps them in a private Backblaze B2 bucket and
a profile stores a `b2://<bucket>/<key>` reference instead. Setting `B2_KEY_ID`,
`B2_APPLICATION_KEY` and `B2_BUCKET_NAME` switches new uploads to B2; references already
stored keep resolving through whichever backend wrote them.

This talks to B2's native API over httpx rather than pulling in an S3 SDK: the four
calls it needs are a few lines each.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

B2_PREFIX = "b2://"
LOCAL_ROOT = Path("resumes")
AUTH_URL = "https://api.backblazeb2.com/b2api/v4/b2_authorize_account"

CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def clean_filename(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", name)


def content_type(name: str) -> str:
    return CONTENT_TYPES.get(Path(name).suffix.lower(), "application/octet-stream")


def is_remote(ref: Optional[str]) -> bool:
    return bool(ref) and ref.startswith(B2_PREFIX)


def b2_configured() -> bool:
    return all(os.getenv(v, "").strip() for v in ("B2_KEY_ID", "B2_APPLICATION_KEY", "B2_BUCKET_NAME"))


class B2Error(RuntimeError):
    pass


class B2Bucket:
    """One private bucket, reached with a key scoped to it."""

    def __init__(self, key_id: str, app_key: str, bucket_name: str, client: Optional[httpx.Client] = None):
        self.key_id = key_id
        self.app_key = app_key
        self.bucket_name = bucket_name
        self.client = client or httpx.Client(timeout=60.0)
        self._auth: Optional[dict] = None
        self._bucket_id: Optional[str] = os.getenv("B2_BUCKET_ID", "").strip() or None
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls) -> "B2Bucket":
        return cls(
            os.environ["B2_KEY_ID"].strip(),
            os.environ["B2_APPLICATION_KEY"].strip(),
            os.environ["B2_BUCKET_NAME"].strip(),
        )

    def _authorize(self) -> dict:
        res = self.client.get(AUTH_URL, auth=(self.key_id, self.app_key))
        if res.status_code != 200:
            raise B2Error(f"B2 refused the key ({res.status_code}): {res.text[:200]}")
        body = res.json()
        api = body["apiInfo"]["storageApi"]
        if not self._bucket_id:
            for bucket in (api.get("allowed") or {}).get("buckets") or []:
                if bucket.get("name") == self.bucket_name:
                    self._bucket_id = bucket["id"]
        return {
            "token": body["authorizationToken"],
            "account": body["accountId"],
            "api": api["apiUrl"],
            "download": api["downloadUrl"],
        }

    def _session(self, refresh: bool = False) -> dict:
        with self._lock:
            if self._auth is None or refresh:
                self._auth = self._authorize()
            return self._auth

    def _call(self, name: str, payload: dict) -> dict:
        """A JSON API call, re-authorizing once when the day-long token has expired."""
        for attempt in (0, 1):
            auth = self._session(refresh=attempt == 1)
            res = self.client.post(
                f"{auth['api']}/b2api/v4/{name}",
                headers={"Authorization": auth["token"]},
                json=payload,
            )
            if res.status_code == 401 and attempt == 0:
                continue
            if res.status_code != 200:
                raise B2Error(f"{name} failed ({res.status_code}): {res.text[:200]}")
            return res.json()
        raise B2Error(f"{name} failed after re-authorizing")

    def _bucket(self) -> str:
        # Authorizing a bucket-scoped key names its bucket, which usually saves the lookup.
        auth = self._session()
        if not self._bucket_id:
            found = self._call(
                "b2_list_buckets", {"accountId": auth["account"], "bucketName": self.bucket_name}
            ).get("buckets") or []
            if not found:
                raise B2Error(f"Bucket {self.bucket_name!r} is not visible to this key")
            self._bucket_id = found[0]["bucketId"]
        return self._bucket_id

    def put(self, key: str, data: bytes, mime: str) -> None:
        target = self._call("b2_get_upload_url", {"bucketId": self._bucket()})
        res = self.client.post(
            target["uploadUrl"],
            headers={
                "Authorization": target["authorizationToken"],
                "X-Bz-File-Name": quote(key, safe="/"),
                "Content-Type": mime,
                "X-Bz-Content-Sha1": hashlib.sha1(data).hexdigest(),
            },
            content=data,
        )
        if res.status_code != 200:
            raise B2Error(f"Upload of {key} failed ({res.status_code}): {res.text[:200]}")

    def get(self, key: str) -> bytes:
        for attempt in (0, 1):
            auth = self._session(refresh=attempt == 1)
            res = self.client.get(
                f"{auth['download']}/file/{self.bucket_name}/{quote(key, safe='/')}",
                headers={"Authorization": auth["token"]},
            )
            if res.status_code == 401 and attempt == 0:
                continue
            if res.status_code != 200:
                raise B2Error(f"Download of {key} failed ({res.status_code})")
            return res.content
        raise B2Error(f"Download of {key} failed after re-authorizing")

    def delete(self, key: str) -> None:
        """Remove every stored version, so an old resume does not linger behind a new one."""
        listing = self._call(
            "b2_list_file_versions",
            {"bucketId": self._bucket(), "startFileName": key, "prefix": key, "maxFileCount": 100},
        )
        for version in listing.get("files", []):
            if version["fileName"] == key:
                self._call(
                    "b2_delete_file_version",
                    {"fileName": key, "fileId": version["fileId"]},
                )


_bucket: Optional[B2Bucket] = None


def _b2() -> B2Bucket:
    global _bucket
    if _bucket is None:
        _bucket = B2Bucket.from_env()
    return _bucket


def _split(ref: str) -> tuple[str, str]:
    bucket, _, key = ref[len(B2_PREFIX):].partition("/")
    return bucket, key


def _cache_dir(ref: str) -> Path:
    return Path(tempfile.gettempdir()) / "jobstager-resumes" / hashlib.sha256(ref.encode()).hexdigest()[:16]


def save_resume(user_id: int, grad_year: int, filename: str, data: bytes) -> str:
    """Store an upload and return the reference a profile keeps for it."""
    name = f"{grad_year}_{clean_filename(filename)}"
    if b2_configured():
        bucket = _b2()
        key = f"user_{user_id}/{name}"
        # B2 keeps every version of a name, so clear the old one rather than stack a second
        # copy of someone's personal details behind the new upload.
        bucket.delete(key)
        bucket.put(key, data, content_type(filename))
        ref = f"{B2_PREFIX}{bucket.bucket_name}/{key}"
        # A re-upload under the same name would otherwise keep serving the old copy.
        shutil.rmtree(_cache_dir(ref), ignore_errors=True)
        return ref
    upload_dir = (LOCAL_ROOT / f"user_{user_id}").resolve()
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / name
    dest.write_bytes(data)
    return str(dest)


def delete_resume(user_id: int, ref: Optional[str]) -> None:
    """Delete a replaced upload -- but only one this module stored for this user.

    A personal profile can point at a resume anywhere on disk (profile.yaml does), and
    that file is the person's own, not ours to remove.
    """
    if not ref:
        return
    try:
        if is_remote(ref):
            _bucket_name, key = _split(ref)
            if key.startswith(f"user_{user_id}/"):
                _b2().delete(key)
            return
        path = Path(ref).expanduser().resolve()
        if path.parent == (LOCAL_ROOT / f"user_{user_id}").resolve() and path.is_file():
            path.unlink()
    except Exception as err:  # a failed cleanup must not fail the upload that replaced it
        logger.warning(f"Could not delete replaced resume {ref}: {err}")


def available(ref: Optional[str]) -> bool:
    """Whether a reference points at a resume, without a network round trip for B2 --
    references are written only after the upload succeeded."""
    if not ref:
        return False
    if is_remote(ref):
        return True
    return Path(ref).expanduser().exists()


def read_resume(ref: str) -> bytes:
    if is_remote(ref):
        return _b2().get(_split(ref)[1])
    return Path(ref).expanduser().read_bytes()


def local_copy(ref: Optional[str]) -> Optional[Path]:
    """A path on this machine holding the resume, for a browser upload control.

    A B2 resume is downloaded into a private temp directory keyed by its reference, so
    the file keeps its own name -- the name is what an ATS shows the recruiter.
    """
    if not ref:
        return None
    if not is_remote(ref):
        path = Path(ref).expanduser().resolve()
        return path if path.exists() else None
    cache = _cache_dir(ref)
    dest = cache / Path(_split(ref)[1]).name
    if dest.exists():
        return dest
    try:
        data = read_resume(ref)
    except B2Error as err:
        logger.error(f"Could not fetch resume {ref}: {err}")
        return None
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    dest.write_bytes(data)
    dest.chmod(0o600)
    return dest

"""Public hosting for videos. The Instagram API fetches `video_url` itself, so the file must be publicly reachable.

Backends (MEDIA_HOST = supabase | r2 | none):
  supabase - Supabase Storage (free tier, no card needed). Secrets: SUPABASE_URL, SUPABASE_SERVICE_KEY,
             SUPABASE_BUCKET (a PUBLIC bucket; default "reels").
  r2       - Cloudflare R2 via boto3. Secrets: R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY,
             R2_BUCKET, R2_PUBLIC_BASE_URL.
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

from ..net import Http
from ..settings import env

log = logging.getLogger(__name__)


class HostError(RuntimeError):
    pass


class MediaHost:
    name = "base"

    def new_key(self, story_id: str) -> str:
        return f"queue/{story_id}-{uuid.uuid4().hex[:10]}.mp4"

    def upload(self, local: Path, key: str) -> str:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError

    def check_public(self, url: str, http: Http) -> None:
        response = http.head(url)
        if response.status_code != 200:
            raise HostError(f"public URL check failed: HTTP {response.status_code} for {url}")
        content_type = response.headers.get("Content-Type", "")
        if "video" not in content_type and "octet-stream" not in content_type:
            raise HostError(f"public URL has unexpected Content-Type {content_type!r}")


class SupabaseHost(MediaHost):
    name = "supabase"

    def __init__(self, base_url: str, key: str, bucket: str, http: Http) -> None:
        self.base = base_url.rstrip("/")
        self.key = key
        self.bucket = bucket
        self.http = http

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.key}", "apikey": self.key}

    def upload(self, local: Path, key: str) -> str:
        with local.open("rb") as handle:
            response = self.http.post(f"{self.base}/storage/v1/object/{self.bucket}/{key}",
                                      headers={**self._headers(), "Content-Type": "video/mp4", "x-upsert": "true"},
                                      data=handle, timeout=300)
        if response.status_code not in (200, 201):
            raise HostError(f"Supabase upload HTTP {response.status_code}: {response.text[:200]}")
        return f"{self.base}/storage/v1/object/public/{self.bucket}/{key}"

    def delete(self, key: str) -> None:
        response = self.http.delete(f"{self.base}/storage/v1/object/{self.bucket}/{key}", headers=self._headers())
        if response.status_code not in (200, 204, 404):
            log.warning("Supabase delete HTTP %s for %s", response.status_code, key)


class R2Host(MediaHost):
    name = "r2"

    def __init__(self, account_id: str, access_key: str, secret_key: str, bucket: str, public_base: str) -> None:
        try:
            import boto3
        except ImportError as exc:
            raise HostError("boto3 is required for R2 (pip install boto3)") from exc
        self.bucket = bucket
        self.public_base = public_base.rstrip("/")
        self.client = boto3.client("s3", endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
                                   aws_access_key_id=access_key, aws_secret_access_key=secret_key,
                                   region_name="auto")

    def upload(self, local: Path, key: str) -> str:
        self.client.upload_file(str(local), self.bucket, key, ExtraArgs={"ContentType": "video/mp4"})
        return f"{self.public_base}/{key}"

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)


def get_host(http: Http) -> MediaHost | None:
    choice = (env("MEDIA_HOST") or ("supabase" if env("SUPABASE_URL") else "r2" if env("R2_ACCOUNT_ID") else "none")).lower()
    if choice == "supabase" and env("SUPABASE_URL") and env("SUPABASE_SERVICE_KEY"):
        return SupabaseHost(env("SUPABASE_URL"), env("SUPABASE_SERVICE_KEY"), env("SUPABASE_BUCKET", "reels"), http)
    if choice == "r2" and all(env(k) for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY",
                                                 "R2_BUCKET", "R2_PUBLIC_BASE_URL")):
        return R2Host(env("R2_ACCOUNT_ID"), env("R2_ACCESS_KEY_ID"), env("R2_SECRET_ACCESS_KEY"),
                      env("R2_BUCKET"), env("R2_PUBLIC_BASE_URL"))
    return None

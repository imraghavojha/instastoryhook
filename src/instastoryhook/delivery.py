"""HTTP delivery and optional media archiving, without Instagram credentials."""

import hashlib
import hmac
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx


def validate_webhook(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.fragment:
        raise ValueError("Webhook must be an absolute HTTP(S) URL without a fragment")
    if parsed.username or parsed.password:
        raise ValueError("Use STORYHOOK_WEBHOOK_TOKEN instead of credentials in the URL")
    if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("Use HTTPS for non-local webhooks")


class Webhook:
    def __init__(self, url, secret, token=None, max_attempts=12, client=None):
        validate_webhook(url)
        if not secret:
            raise ValueError("Set STORYHOOK_WEBHOOK_SECRET to sign deliveries")
        self.url, self.secret, self.token = url, secret, token
        self.max_attempts = max_attempts
        self.client = client or httpx.Client(timeout=15, follow_redirects=False)

    def drain(self, store, now=None):
        now = time.time() if now is None else now
        sent = 0
        for row in store.due(now):
            body = row["payload"].encode()
            timestamp = str(int(time.time()))
            digest = hmac.new(
                self.secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256
            )
            headers = {
                "Content-Type": "application/json",
                "Idempotency-Key": row["id"],
                "X-Storyhook-Timestamp": timestamp,
                "X-Storyhook-Signature": "sha256=" + digest.hexdigest(),
            }
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
            retry_after = 0
            try:
                # Stream so a receiver cannot force an unbounded response body into memory.
                with self.client.stream(
                    "POST", self.url, content=body, headers=headers
                ) as response:
                    if 200 <= response.status_code < 300:
                        store.delivered(row["id"])
                        sent += 1
                        continue
                    error = f"HTTP {response.status_code}"
                    value = response.headers.get("retry-after", "")
                    if value.isdigit():
                        retry_after = int(value)
            except httpx.HTTPError as exc:
                error = type(exc).__name__
            store.failed(row, now, error, self.max_attempts, retry_after)
        return sent

    def close(self):
        self.client.close()


def archive(event, folder: Path, client=None):
    media = event["story"]["media"]
    url = media.get("url")
    parsed = urlsplit(url or "")
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not any(
        host.endswith("." + d)
        for d in (
            "cdninstagram.com",
            "fbcdn.net",
            "instagram.com",
        )
    ):
        media["download_error"] = "missing_or_unrecognized_media_url"
        return
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    suffix = ".mp4" if media["type"] == "video" else ".jpg"
    path = folder / (event["story"]["id"] + suffix)
    temporary = path.with_suffix(suffix + ".part")
    own_client = client is None
    client = client or httpx.Client(timeout=30, follow_redirects=False)
    try:
        size = 0
        with client.stream("GET", url) as response:
            response.raise_for_status()
            with temporary.open("wb") as f:
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 100 * 1024 * 1024:
                        raise ValueError("Media exceeds 100 MiB")
                    f.write(chunk)
                f.flush()
                os.fsync(f.fileno())
        os.replace(temporary, path)
        media["local_path"] = str(path)
        media["bytes"] = size
    except (httpx.HTTPError, OSError, ValueError) as exc:
        media["download_error"] = type(exc).__name__
    finally:
        temporary.unlink(missing_ok=True)
        if own_client:
            client.close()

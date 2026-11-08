"""Thin Battle.net API client with a token cache and an on-disk conditional-GET cache."""

from __future__ import annotations

import gzip
import json
import time
from pathlib import Path
from typing import Any

import httpx

from .config import CACHE_DIR, REGIONS, load_credentials

TOKEN_URL = "https://oauth.battle.net/token"


class ApiError(RuntimeError):
    pass


class Blizzard:
    def __init__(self, region: str, cache_dir: Path = CACHE_DIR):
        self.region = region
        self.locale = REGIONS[region]
        self.base = f"https://{region}.api.blizzard.com"
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.http = httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0), headers={"User-Agent": "wowf-auction-prices"})
        self._token: str | None = None

    # -- auth ---------------------------------------------------------------
    def _token_file(self) -> Path:
        return self.cache_dir / "token.json"

    def token(self, force: bool = False) -> str:
        if self._token and not force:
            return self._token
        tf = self._token_file()
        if not force:
            try:
                data = json.loads(tf.read_text())
                if data["expires_at"] > time.time() + 300:
                    self._token = data["access_token"]
                    return self._token
            except (OSError, ValueError, KeyError):
                pass
        cid, secret = load_credentials()
        try:
            r = self.http.post(TOKEN_URL, data={"grant_type": "client_credentials"}, auth=(cid, secret))
        except httpx.HTTPError as e:
            raise ApiError(f"could not reach Battle.net OAuth: {e}") from e
        if r.status_code != 200:
            raise ApiError(f"OAuth token request failed ({r.status_code}); check your client ID/secret")
        data = r.json()
        self._token = data["access_token"]
        tf.write_text(json.dumps({"access_token": self._token, "expires_at": time.time() + data.get("expires_in", 3600)}))
        tf.chmod(0o600)
        return self._token

    # -- requests -----------------------------------------------------------
    def _get(self, path: str, namespace: str, params: dict | None = None, headers: dict | None = None) -> httpx.Response:
        q = {"namespace": f"{namespace}-{self.region}", "locale": self.locale, **(params or {})}
        for attempt in range(3):
            h = {"Authorization": f"Bearer {self.token(force=attempt > 0)}", **(headers or {})}
            try:
                r = self.http.get(self.base + path, params=q, headers=h)
            except httpx.HTTPError as e:
                if attempt == 2:
                    raise ApiError(f"request to {path} failed: {e}") from e
                time.sleep(1 + attempt)
                continue
            if r.status_code == 401:
                continue  # token expired or revoked: refresh and retry
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(1 + attempt * 2)
                continue
            return r
        return r

    def get_json(self, path: str, namespace: str, params: dict | None = None) -> Any:
        r = self._get(path, namespace, params)
        if r.status_code != 200:
            raise ApiError(f"GET {path} -> HTTP {r.status_code}")
        return r.json()

    def get_cached(
        self, key: str, path: str, namespace: str, max_age: float = 300, need_body: bool = True
    ) -> tuple[Any, str | None, bool]:
        """GET with a disk cache revalidated via If-Modified-Since.

        Returns (data, last_modified, changed). Within `max_age` seconds of the last check
        the cached copy is used without touching the network. With need_body=False an
        unchanged resource returns data=None (saves parsing a large cached body).
        """
        body_f = self.cache_dir / f"{key}.json.gz"
        meta_f = self.cache_dir / f"{key}.meta.json"
        meta: dict = {}
        if body_f.exists():
            try:
                meta = json.loads(meta_f.read_text())
            except (OSError, ValueError):
                meta = {}
        if meta and time.time() - meta.get("checked_at", 0) < max_age:
            return (_read_gz(body_f) if need_body else None), meta.get("last_modified"), False

        headers = {"If-Modified-Since": meta["last_modified"]} if meta.get("last_modified") else None
        r = self._get(path, namespace, headers=headers)
        if r.status_code == 304 and body_f.exists():
            meta["checked_at"] = time.time()
            meta_f.write_text(json.dumps(meta))
            return (_read_gz(body_f) if need_body else None), meta.get("last_modified"), False
        if r.status_code != 200:
            if body_f.exists():  # serve stale rather than fail
                return _read_gz(body_f), meta.get("last_modified"), False
            raise ApiError(f"GET {path} -> HTTP {r.status_code}")
        body_f.write_bytes(gzip.compress(r.content, compresslevel=3))
        lm = r.headers.get("last-modified")
        meta_f.write_text(json.dumps({"last_modified": lm, "checked_at": time.time()}))
        return r.json(), lm, True


def _read_gz(path: Path) -> Any:
    return json.loads(gzip.decompress(path.read_bytes()))

"""Thin async REST client over Splunk management port (8089).

Supports two auth modes:
  - Bearer token (preferred): pass `token=...`
  - HTTP Basic:                pass `username=...` and `password=...`

Routing logic lives elsewhere; this is just typed httpx with auth.
"""
from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

import httpx

log = logging.getLogger(__name__)


class SplunkRESTError(Exception):
    def __init__(self, status: int, body: str, url: str):
        super().__init__(f"Splunk REST {status} on {url}: {body[:300]}")
        self.status = status
        self.body = body
        self.url = url


class SplunkClient:
    """One client per Splunk instance. Picks Bearer or Basic auth based on args."""

    def __init__(
        self,
        base_url: str,
        *,
        token: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        verify_ssl: bool = True,
        timeout: float = 30.0,
    ):
        if not token and not (username and password):
            raise ValueError("SplunkClient needs token, or both username and password.")

        self.base_url = base_url.rstrip("/")

        headers = {"Accept": "application/json"}
        auth = None

        if token:
            headers["Authorization"] = f"Bearer {token}"
            self._auth_mode = "bearer"
        else:
            auth = (username, password)
            self._auth_mode = "basic"

        self._client = httpx.AsyncClient(
            verify=verify_ssl,
            timeout=timeout,
            auth=auth,
            headers=headers,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "SplunkClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    def _url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        if not path.startswith("/"):
            path = "/" + path
        return self.base_url + path

    async def get(
        self,
        path: str,
        params: Mapping[str, Any] | None = None,
    ) -> dict:
        params = {**(params or {}), "output_mode": "json"}
        url = self._url(path)
        log.debug("GET %s params=%s", url, params)
        r = await self._client.get(url, params=params)
        if r.status_code >= 400:
            raise SplunkRESTError(r.status_code, r.text, url)
        return r.json()

    async def post(
        self,
        path: str,
        data: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> dict:
        params = {**(params or {}), "output_mode": "json"}
        url = self._url(path)
        log.debug("POST %s data_keys=%s", url, list((data or {}).keys()))
        r = await self._client.post(url, data=data or {}, params=params)
        if r.status_code >= 400:
            raise SplunkRESTError(r.status_code, r.text, url)
        if r.headers.get("content-type", "").startswith("application/json"):
            return r.json()
        return {"status": r.status_code, "text": r.text}

    async def server_info(self) -> dict:
        data = await self.get("/services/server/info")
        return data["entry"][0]["content"]

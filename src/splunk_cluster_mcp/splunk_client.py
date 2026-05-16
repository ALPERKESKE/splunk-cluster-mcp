"""Thin async REST client over Splunk management port (8089).

Knows nothing about cluster roles — just a typed wrapper over httpx with auth.
Routing logic lives in topology.py and tool implementations.
"""
from __future__ import annotations

import logging
from typing import Any, Mapping
from urllib.parse import urljoin

import httpx

log = logging.getLogger(__name__)


class SplunkRESTError(Exception):
    def __init__(self, status: int, body: str, url: str):
        super().__init__(f"Splunk REST {status} on {url}: {body[:300]}")
        self.status = status
        self.body = body
        self.url = url


class SplunkClient:
    """One client per Splunk instance (one URL+credentials)."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        verify_ssl: bool = False,
        timeout: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self._auth = (username, password)
        self._client = httpx.AsyncClient(
            verify=verify_ssl,
            timeout=timeout,
            auth=self._auth,
            headers={"Accept": "application/json"},
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
        log.debug("POST %s data=%s", url, data)
        r = await self._client.post(url, data=data or {}, params=params)
        if r.status_code >= 400:
            raise SplunkRESTError(r.status_code, r.text, url)
        # Some POSTs (e.g., job creation) return JSON; others 201 no body
        if r.headers.get("content-type", "").startswith("application/json"):
            return r.json()
        return {"status": r.status_code, "text": r.text}

    async def server_info(self) -> dict:
        """Return /services/server/info content of the entry."""
        data = await self.get("/services/server/info")
        return data["entry"][0]["content"]

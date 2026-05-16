"""Runtime config — built from environment, .env, or cluster_connect tool args.

The config is *optional* at startup. If env vars aren't set, the gateway is
"disconnected" until cluster_connect() is called.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    bootstrap_url: str            # Cluster Manager URL — required
    shc_bootstrap_url: Optional[str]  # Any SHC member URL — optional
    username: str
    password: str
    verify_ssl: bool
    topology_ttl: int
    search_default_earliest: str
    search_default_latest: str
    log_level: str
    source: str                   # 'env' | 'connect_tool' — for debugging

    @classmethod
    def from_env(cls) -> Optional[Config]:
        """Try to build Config from environment variables. Return None if required vars missing."""
        url = os.environ.get("SPLUNK_BOOTSTRAP_URL", "").rstrip("/")
        user = os.environ.get("SPLUNK_USERNAME", "")
        pw = os.environ.get("SPLUNK_PASSWORD", "")
        if not (url and user and pw):
            return None
        return cls(
            bootstrap_url=url,
            shc_bootstrap_url=(os.environ.get("SPLUNK_SHC_BOOTSTRAP_URL") or "").rstrip("/") or None,
            username=user,
            password=pw,
            verify_ssl=os.environ.get("SPLUNK_VERIFY_SSL", "false").lower() == "true",
            topology_ttl=int(os.environ.get("TOPOLOGY_CACHE_TTL", "60")),
            search_default_earliest=os.environ.get("SEARCH_DEFAULT_EARLIEST", "-15m"),
            search_default_latest=os.environ.get("SEARCH_DEFAULT_LATEST", "now"),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            source="env",
        )

    @classmethod
    def from_args(
        cls,
        *,
        bootstrap_url: str,
        username: str,
        password: str,
        shc_url: Optional[str] = None,
        verify_ssl: bool = False,
        topology_ttl: int = 60,
        log_level: str = "INFO",
    ) -> Config:
        return cls(
            bootstrap_url=bootstrap_url.rstrip("/"),
            shc_bootstrap_url=(shc_url or "").rstrip("/") or None,
            username=username,
            password=password,
            verify_ssl=verify_ssl,
            topology_ttl=topology_ttl,
            search_default_earliest="-15m",
            search_default_latest="now",
            log_level=log_level,
            source="connect_tool",
        )

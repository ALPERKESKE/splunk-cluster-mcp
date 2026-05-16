"""Runtime config — built from environment, .env, or cluster_connect tool args.

Two auth modes supported:
  - Bearer token (preferred): set SPLUNK_TOKEN
  - HTTP Basic (fallback):   set SPLUNK_USERNAME + SPLUNK_PASSWORD

Token auth is preferred — tokens can be scoped per-role, revoked
individually, and expire on a schedule. Create one in Splunk via:
  Web UI: Settings → Tokens → New Token
  REST:   POST /services/authorization/tokens

The config is optional at startup. If no auth is configured, the
gateway is "disconnected" until cluster_connect() is called.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    bootstrap_url: str
    shc_bootstrap_url: Optional[str]
    # Auth: token preferred; username+password is fallback
    token: Optional[str]
    username: Optional[str]
    password: Optional[str]
    verify_ssl: bool
    topology_ttl: int
    search_default_earliest: str
    search_default_latest: str
    log_level: str
    source: str

    @property
    def auth_mode(self) -> str:
        if self.token:
            return "bearer"
        if self.username and self.password:
            return "basic"
        return "none"

    @classmethod
    def from_env(cls) -> Optional[Config]:
        """Build Config from env. Returns None if nothing usable is set."""
        url = os.environ.get("SPLUNK_BOOTSTRAP_URL", "").rstrip("/")
        if not url:
            return None

        token = os.environ.get("SPLUNK_TOKEN") or None
        user = os.environ.get("SPLUNK_USERNAME") or None
        pw = os.environ.get("SPLUNK_PASSWORD") or None

        # Need either token OR (user + pw)
        if not token and not (user and pw):
            return None

        return cls(
            bootstrap_url=url,
            shc_bootstrap_url=(os.environ.get("SPLUNK_SHC_BOOTSTRAP_URL") or "").rstrip("/") or None,
            token=token,
            username=user if not token else None,
            password=pw if not token else None,
            verify_ssl=os.environ.get("SPLUNK_VERIFY_SSL", "true").lower() == "true",
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
        token: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        shc_url: Optional[str] = None,
        verify_ssl: bool = True,
        topology_ttl: int = 60,
        log_level: str = "INFO",
    ) -> Config:
        if not token and not (username and password):
            raise ValueError(
                "cluster_connect requires either token, or both username and password."
            )
        return cls(
            bootstrap_url=bootstrap_url.rstrip("/"),
            shc_bootstrap_url=(shc_url or "").rstrip("/") or None,
            token=token,
            username=username if not token else None,
            password=password if not token else None,
            verify_ssl=verify_ssl,
            topology_ttl=topology_ttl,
            search_default_earliest="-15m",
            search_default_latest="now",
            log_level=log_level,
            source="connect_tool",
        )

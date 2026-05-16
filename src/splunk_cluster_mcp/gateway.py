"""Gateway — shared runtime state used by all tools.

Picks auth mode from Config (token preferred, basic as fallback) and
hands clients to tools via client_for(url).
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

from .config import Config
from .splunk_client import SplunkClient
from .topology import Topology, TopologyDiscoverer

log = logging.getLogger(__name__)


class NotConnectedError(RuntimeError):
    """Raised when a tool needs cluster credentials but none are configured."""


_NOT_CONNECTED_MSG = (
    "The Splunk cluster is not configured. Call cluster_connect() with bootstrap_url "
    "and either a token (recommended) or username + password. Optionally pass shc_url "
    "and verify_ssl. Credentials are kept in memory only for this MCP session."
)


class Gateway:
    def __init__(self):
        self.cfg: Optional[Config] = None
        self.topology_disc: Optional[TopologyDiscoverer] = None
        self._connect_lock = asyncio.Lock()
        env_cfg = Config.from_env()
        if env_cfg is not None:
            self.cfg = env_cfg
            self.topology_disc = TopologyDiscoverer(env_cfg)
            log.info(
                "Gateway initialised from environment (bootstrap=%s, auth=%s)",
                env_cfg.bootstrap_url, env_cfg.auth_mode,
            )
        else:
            log.info("Gateway started without credentials. cluster_connect() required.")

    @property
    def is_connected(self) -> bool:
        return self.cfg is not None

    def _ensure_connected(self) -> Config:
        if self.cfg is None:
            raise NotConnectedError(_NOT_CONNECTED_MSG)
        return self.cfg

    async def topology(self, force: bool = False) -> Topology:
        self._ensure_connected()
        assert self.topology_disc is not None
        return await self.topology_disc.get(force=force)

    @asynccontextmanager
    async def client_for(self, url: str):
        cfg = self._ensure_connected()
        c = SplunkClient(
            url,
            token=cfg.token,
            username=cfg.username,
            password=cfg.password,
            verify_ssl=cfg.verify_ssl,
        )
        try:
            yield c
        finally:
            await c.aclose()

    async def connect(
        self,
        bootstrap_url: str,
        *,
        token: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        shc_url: Optional[str] = None,
        verify_ssl: bool = True,
    ) -> Topology:
        """Validate credentials, replace runtime config, refresh topology."""
        async with self._connect_lock:
            new_cfg = Config.from_args(
                bootstrap_url=bootstrap_url,
                token=token,
                username=username,
                password=password,
                shc_url=shc_url,
                verify_ssl=verify_ssl,
            )
            try:
                async with SplunkClient(
                    new_cfg.bootstrap_url,
                    token=new_cfg.token,
                    username=new_cfg.username,
                    password=new_cfg.password,
                    verify_ssl=new_cfg.verify_ssl,
                    timeout=10.0,
                ) as probe:
                    info = await probe.server_info()
                    log.info(
                        "cluster_connect probe OK: server=%s version=%s auth=%s",
                        info.get("serverName"), info.get("version"),
                        new_cfg.auth_mode,
                    )
            except Exception as e:
                raise RuntimeError(
                    f"Could not validate Splunk connection to {new_cfg.bootstrap_url}: {e}. "
                    "Check URL (must end with :8089), credentials/token, and reachability."
                ) from e
            self.cfg = new_cfg
            self.topology_disc = TopologyDiscoverer(new_cfg)
            return await self.topology_disc.get(force=True)

"""Gateway — shared runtime state used by all tools.

Holds Config (optionally), TopologyDiscoverer, and a factory for SplunkClient.
Config is set either at startup (from env) or at runtime via cluster_connect().
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
    "The Splunk cluster is not configured. Call cluster_connect() with at least "
    "bootstrap_url, username, password. Optionally pass shc_url (any SHC member URL) "
    "and verify_ssl. Credentials are kept in memory only for this MCP session."
)


class Gateway:
    def __init__(self):
        self.cfg: Optional[Config] = None
        self.topology_disc: Optional[TopologyDiscoverer] = None
        self._connect_lock = asyncio.Lock()
        # Try env auto-load
        env_cfg = Config.from_env()
        if env_cfg is not None:
            self.cfg = env_cfg
            self.topology_disc = TopologyDiscoverer(env_cfg)
            log.info("Gateway initialised from environment (bootstrap=%s)", env_cfg.bootstrap_url)
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
        assert self.topology_disc is not None  # for type narrowing
        return await self.topology_disc.get(force=force)

    @asynccontextmanager
    async def client_for(self, url: str):
        cfg = self._ensure_connected()
        c = SplunkClient(
            url, cfg.username, cfg.password,
            verify_ssl=cfg.verify_ssl,
        )
        try:
            yield c
        finally:
            await c.aclose()

    async def connect(
        self,
        bootstrap_url: str,
        username: str,
        password: str,
        shc_url: Optional[str] = None,
        verify_ssl: bool = False,
    ) -> Topology:
        """Validate credentials, replace runtime config, and refresh topology.

        On failure: cfg is left untouched and a clear exception is raised.
        """
        async with self._connect_lock:
            new_cfg = Config.from_args(
                bootstrap_url=bootstrap_url,
                username=username,
                password=password,
                shc_url=shc_url,
                verify_ssl=verify_ssl,
            )
            # Probe: server/info on bootstrap URL — must succeed
            try:
                async with SplunkClient(
                    new_cfg.bootstrap_url, new_cfg.username, new_cfg.password,
                    verify_ssl=new_cfg.verify_ssl, timeout=10.0,
                ) as probe:
                    info = await probe.server_info()
                    log.info(
                        "cluster_connect probe OK: server=%s version=%s",
                        info.get("serverName"), info.get("version"),
                    )
            except Exception as e:
                raise RuntimeError(
                    f"Could not validate Splunk connection to {new_cfg.bootstrap_url}: {e}. "
                    "Check URL (must end with :8089), credentials, and that the node is reachable."
                ) from e
            # Commit
            self.cfg = new_cfg
            self.topology_disc = TopologyDiscoverer(new_cfg)
            return await self.topology_disc.get(force=True)

"""Cluster topology discovery & cache.

Two bootstrap endpoints needed:
  - Cluster Manager (cm1) → indexer peers, license URI
  - Any SHC member (e.g., sh1) → SHC members + captain

The CM does NOT know about SHC composition; an SHC member must be queried.

A Topology snapshot is cached for TTL seconds and refreshed lazily.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from .config import Config
from .splunk_client import SplunkClient

log = logging.getLogger(__name__)


@dataclass
class Node:
    """A single Splunk node in the cluster."""
    name: str            # human label (e.g. "sf-student01-idx2")
    role: str            # 'cluster_manager' | 'indexer' | 'search_head' | 'shc_captain' | 'license_manager'
    mgmt_url: str        # https://host:8089
    status: Optional[str] = None
    extra: dict = field(default_factory=dict)


@dataclass
class Topology:
    cluster_manager: Node
    indexers: list[Node]
    search_heads: list[Node]
    shc_captain: Optional[Node] = None
    license_manager: Optional[Node] = None
    refreshed_at: float = field(default_factory=time.time)

    def by_role(self, role: str) -> list[Node]:
        if role == "cluster_manager":
            return [self.cluster_manager]
        if role == "indexer":
            return list(self.indexers)
        if role == "search_head":
            return list(self.search_heads)
        if role == "shc_captain":
            return [self.shc_captain] if self.shc_captain else []
        if role == "license_manager":
            return [self.license_manager] if self.license_manager else []
        return []

    def find_node(self, name_or_url: str) -> Optional[Node]:
        for n in [self.cluster_manager, *self.indexers, *self.search_heads]:
            if n.name == name_or_url or n.mgmt_url == name_or_url:
                return n
        if self.license_manager and (
            self.license_manager.name == name_or_url
            or self.license_manager.mgmt_url == name_or_url
        ):
            return self.license_manager
        return None

    def summary(self) -> dict:
        return {
            "cluster_manager": {"name": self.cluster_manager.name, "url": self.cluster_manager.mgmt_url},
            "indexers": [
                {"name": n.name, "url": n.mgmt_url, "status": n.status,
                 "bucket_count": n.extra.get("bucket_count"),
                 "is_searchable": n.extra.get("is_searchable")}
                for n in self.indexers
            ],
            "search_heads": [
                {"name": n.name, "url": n.mgmt_url, "status": n.status} for n in self.search_heads
            ],
            "shc_captain": self.shc_captain.name if self.shc_captain else None,
            "license_manager": self.license_manager.name if self.license_manager else None,
            "refreshed_at": self.refreshed_at,
        }


class TopologyDiscoverer:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._cache: Optional[Topology] = None
        self._lock = asyncio.Lock()

    async def get(self, force: bool = False) -> Topology:
        async with self._lock:
            now = time.time()
            if (
                not force
                and self._cache is not None
                and (now - self._cache.refreshed_at) < self.cfg.topology_ttl
            ):
                return self._cache
            log.info("Discovering topology from CM=%s", self.cfg.bootstrap_url)
            self._cache = await self._discover()
            return self._cache

    async def _discover_indexers(self, cm: SplunkClient) -> tuple[Node, list[Node], Optional[Node]]:
        cm_info = await cm.server_info()
        cluster_manager = Node(
            name=cm_info.get("serverName", "cm"),
            role="cluster_manager",
            mgmt_url=self.cfg.bootstrap_url,
            status="up",
            extra={"version": cm_info.get("version"), "guid": cm_info.get("guid")},
        )

        peers_resp = await cm.get("/services/cluster/manager/peers", params={"count": 0})
        indexers: list[Node] = []
        for entry in peers_resp.get("entry", []):
            c = entry.get("content", {})
            host_port = c.get("host_port_pair") or ""
            mgmt = f"https://{host_port}" if host_port else ""
            indexers.append(Node(
                name=c.get("label", entry.get("name", "")),
                role="indexer",
                mgmt_url=mgmt,
                status=c.get("status"),
                extra={
                    "bucket_count": c.get("bucket_count"),
                    "is_searchable": c.get("is_searchable"),
                    "site": c.get("site"),
                    "version": c.get("splunk_version"),
                },
            ))

        # License manager URI — Splunk 9.x renamed master_uri -> manager_uri
        lm_node: Optional[Node] = None
        try:
            lic_props = await cm.get("/services/properties/server/license")
            entries = {e.get("name"): e.get("content") for e in lic_props.get("entry", [])}
            # Try both keys for cross-version compatibility
            lm_uri = entries.get("manager_uri") or entries.get("master_uri")
            if lm_uri and lm_uri != "self":
                lm_node = Node(
                    name="license_manager",
                    role="license_manager",
                    mgmt_url=str(lm_uri).rstrip("/"),
                    status="up",
                )
        except Exception as e:
            log.debug("license URI discovery failed: %s", e)

        return cluster_manager, indexers, lm_node

    async def _discover_shc(self) -> tuple[list[Node], Optional[Node]]:
        """Discover SHC members and captain via SHC bootstrap URL (if configured)."""
        if not self.cfg.shc_bootstrap_url:
            log.info("SPLUNK_SHC_BOOTSTRAP_URL not set; skipping SHC discovery")
            return [], None
        try:
            async with SplunkClient(
                self.cfg.shc_bootstrap_url,
                token=self.cfg.token,
                username=self.cfg.username,
                password=self.cfg.password,
                verify_ssl=self.cfg.verify_ssl,
                timeout=10.0,
            ) as sh:
                members_resp = await sh.get("/services/shcluster/member/members", params={"count": 0})
                members: list[Node] = []
                for entry in members_resp.get("entry", []):
                    c = entry.get("content", {})
                    members.append(Node(
                        name=c.get("label") or entry.get("name", ""),
                        role="search_head",
                        mgmt_url=(c.get("mgmt_uri") or "").rstrip("/"),
                        status=c.get("status"),
                        extra={"id": entry.get("name")},
                    ))

                captain: Optional[Node] = None
                try:
                    cap_resp = await sh.get("/services/shcluster/captain/info")
                    c = cap_resp.get("entry", [{}])[0].get("content", {})
                    if c.get("label"):
                        captain = Node(
                            name=c["label"],
                            role="shc_captain",
                            mgmt_url=(c.get("mgmt_uri") or c.get("peer_scheme_host_port") or "").rstrip("/"),
                            status="captain",
                            extra={"dynamic_captain": c.get("dynamic_captain")},
                        )
                except Exception as e:
                    log.debug("captain/info failed: %s", e)
                return members, captain
        except Exception as e:
            log.warning("SHC discovery failed via %s: %s", self.cfg.shc_bootstrap_url, e)
            return [], None

    async def _discover(self) -> Topology:
        async with SplunkClient(
            self.cfg.bootstrap_url,
            token=self.cfg.token,
            username=self.cfg.username,
            password=self.cfg.password,
            verify_ssl=self.cfg.verify_ssl,
        ) as cm:
            cluster_manager, indexers, lm_node = await self._discover_indexers(cm)

        search_heads, shc_captain = await self._discover_shc()

        return Topology(
            cluster_manager=cluster_manager,
            indexers=indexers,
            search_heads=search_heads,
            shc_captain=shc_captain,
            license_manager=lm_node,
            refreshed_at=time.time(),
        )

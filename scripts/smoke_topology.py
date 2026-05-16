"""Smoke test — discover topology and pretty-print it.

Run: uv run python scripts/smoke_topology.py
"""
from __future__ import annotations

import asyncio
import json
import logging

from splunk_cluster_mcp.config import Config
from splunk_cluster_mcp.topology import TopologyDiscoverer


async def main() -> None:
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = Config.from_env()
    print(f"Bootstrap: {cfg.bootstrap_url}  user={cfg.username}  verify_ssl={cfg.verify_ssl}")
    disc = TopologyDiscoverer(cfg)
    topo = await disc.get()
    print(json.dumps(topo.summary(), indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())

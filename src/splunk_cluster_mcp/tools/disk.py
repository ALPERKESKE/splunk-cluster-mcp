"""Disk/partition tools — fan-out to every node, return per-node usage."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from ..gateway import Gateway
from ..topology import Node

log = logging.getLogger(__name__)


def _node_universe(topo) -> list[Node]:
    """All known Splunk nodes (CM + indexers + search heads + license manager)."""
    nodes: list[Node] = [topo.cluster_manager, *topo.indexers, *topo.search_heads]
    seen = {n.mgmt_url for n in nodes}
    if topo.license_manager and topo.license_manager.mgmt_url not in seen:
        nodes.append(topo.license_manager)
    return nodes


async def _disk_one(gw: Gateway, node: Node) -> dict[str, Any]:
    try:
        async with gw.client_for(node.mgmt_url) as cli:
            resp = await cli.get("/services/server/status/partitions-space", params={"count": 0})
        partitions = []
        for entry in resp.get("entry", []):
            c = entry.get("content", {})
            cap = c.get("capacity")
            free = c.get("free")
            try:
                pct = round(100 * (1 - (float(free) / float(cap))), 1) if cap and free is not None else None
            except (TypeError, ValueError, ZeroDivisionError):
                pct = None
            partitions.append({
                "fs": c.get("fs_type"),
                "mount": c.get("mount_point") or c.get("mount") or entry.get("name"),
                "capacity_mb": cap,
                "free_mb": free,
                "used_percent": pct,
            })
        return {"node": node.name, "role": node.role, "ok": True, "partitions": partitions}
    except Exception as e:
        return {"node": node.name, "role": node.role, "ok": False, "error": str(e)}


async def node_disk(
    gw: Gateway,
    node: Optional[str] = None,
    role: Optional[str] = None,
) -> dict[str, Any]:
    """Return partition-space stats per node. Filter by exact node name or by role.

    Args:
        node: Exact label (e.g., 'sf-student01-idx2'). If set, returns just that node.
        role: One of cluster_manager, indexer, search_head, license_manager.
              If set (and `node` is None), filter to that role.
    """
    topo = await gw.topology()
    all_nodes = _node_universe(topo)
    if node:
        targets = [n for n in all_nodes if n.name == node]
        if not targets:
            return {"error": f"Node '{node}' not found in topology", "known_nodes": [n.name for n in all_nodes]}
    elif role:
        targets = [n for n in all_nodes if n.role == role]
        if not targets:
            return {"error": f"No nodes with role '{role}'"}
    else:
        targets = all_nodes

    results = await asyncio.gather(*[_disk_one(gw, n) for n in targets], return_exceptions=False)

    # Summary: max used percent across all partitions, who has it
    hottest = {"used_percent": -1, "node": None, "mount": None}
    for r in results:
        if not r.get("ok"):
            continue
        for p in r.get("partitions", []):
            up = p.get("used_percent")
            if up is not None and up > hottest["used_percent"]:
                hottest = {"used_percent": up, "node": r["node"], "mount": p["mount"]}
    if hottest["used_percent"] < 0:
        hottest = None  # no data

    return {
        "queried_count": len(targets),
        "successful": sum(1 for r in results if r.get("ok")),
        "hottest_partition": hottest,
        "by_node": results,
    }

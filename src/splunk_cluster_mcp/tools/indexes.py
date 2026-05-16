"""Index tools — list/inspect Splunk indexes (cluster-wide view via CM)."""
from __future__ import annotations

import logging
from typing import Any

from ..gateway import Gateway

log = logging.getLogger(__name__)


async def list_indexes(gw: Gateway, include_internal: bool = False) -> dict[str, Any]:
    """List indexes with size, event count, retention, hot/warm bucket counts.

    Routes to the cluster manager which has the cluster-wide view of bucket
    state across all peers. Optionally include _internal/_audit/_introspection.
    """
    topo = await gw.topology()

    async with gw.client_for(topo.cluster_manager.mgmt_url) as cm:
        resp = await cm.get("/services/cluster/manager/indexes", params={"count": 0})

    indexes = []
    skipped_internal = 0
    for entry in resp.get("entry", []):
        name = entry.get("name", "")
        c = entry.get("content", {})
        if not include_internal and name.startswith("_"):
            skipped_internal += 1
            continue
        size_bytes = c.get("index_size")
        try:
            size_mb = round(int(size_bytes) / (1024 * 1024), 2) if size_bytes is not None else None
        except (TypeError, ValueError):
            size_mb = None
        indexes.append({
            "name": name,
            "index_size_mb": size_mb,
            "num_buckets": c.get("num_buckets"),
            "is_searchable": bool(c.get("is_searchable")),
            "total_excess_bucket_copies": c.get("total_excess_bucket_copies"),
            "buckets_with_excess_copies": c.get("buckets_with_excess_copies"),
            "buckets_with_excess_searchable_copies": c.get("buckets_with_excess_searchable_copies"),
        })

    # Sort by index_size_mb desc (largest first)
    indexes.sort(key=lambda x: (x["index_size_mb"] or 0), reverse=True)

    return {
        "cluster_manager": topo.cluster_manager.name,
        "index_count": len(indexes),
        "skipped_internal": skipped_internal,
        "indexes": indexes,
    }


async def index_detail(gw: Gateway, name: str) -> dict[str, Any]:
    """Deep info on a single index from the cluster manager perspective."""
    topo = await gw.topology()
    async with gw.client_for(topo.cluster_manager.mgmt_url) as cm:
        try:
            resp = await cm.get(f"/services/cluster/manager/indexes/{name}")
        except Exception as e:
            return {"error": f"Could not fetch index '{name}': {e}"}
        if not resp.get("entry"):
            return {"error": f"Index '{name}' not found"}
        c = resp["entry"][0].get("content", {})
        return {
            "name": name,
            "is_searchable": c.get("is_searchable"),
            "search_factor_met": c.get("search_factor_met"),
            "num_buckets": c.get("num_buckets"),
            "buckets_with_data": c.get("buckets_with_data"),
            "replicated_copies_tracker": c.get("replicated_copies_tracker"),
            "searchable_copies_tracker": c.get("searchable_copies_tracker"),
            "total_excess_bucket_copies": c.get("total_excess_bucket_copies"),
            "raw": c,  # keep full payload for inspection
        }

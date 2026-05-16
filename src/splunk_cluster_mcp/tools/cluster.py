"""Cluster-level tools — route to Cluster Manager."""
from __future__ import annotations

from typing import Any

from ..gateway import Gateway


async def cluster_health(gw: Gateway) -> dict[str, Any]:
    """Aggregate cluster health: peer count, searchable count, bundle, fixups, generation."""
    topo = await gw.topology()
    async with gw.client_for(topo.cluster_manager.mgmt_url) as cm:
        info_resp = await cm.get("/services/cluster/manager/info")
        cm_info = info_resp.get("entry", [{}])[0].get("content", {})

        # Fixup tasks by level (each level is a separate endpoint per Splunk 9.x)
        fixup_total = 0
        fixup_breakdown: dict[str, int] = {}
        for level in ("streaming", "replication_factor", "search_factor", "generation"):
            try:
                resp = await cm.get(
                    "/services/cluster/manager/fixup",
                    params={"level": level, "count": 0},
                )
                n = len(resp.get("entry", []))
                fixup_breakdown[level] = n
                fixup_total += n
            except Exception:
                fixup_breakdown[level] = -1  # unavailable

    indexers = topo.indexers
    active_bundle = cm_info.get("active_bundle") or {}
    return {
        "cluster_manager": topo.cluster_manager.name,
        "splunk_version": topo.cluster_manager.extra.get("version"),
        "rolling_restart": cm_info.get("rolling_restart_flag"),
        "service_ready": cm_info.get("service_ready_flag"),
        "indexing_ready": cm_info.get("indexing_ready_flag"),
        "available_sites": cm_info.get("available_sites"),
        "site_replication_factor": cm_info.get("site_replication_factor"),
        "site_search_factor": cm_info.get("site_search_factor"),
        "peer_count": len(indexers),
        "peers_up": sum(1 for p in indexers if p.status == "Up"),
        "peers_searchable": sum(1 for p in indexers if p.extra.get("is_searchable")),
        "active_bundle_id": active_bundle.get("checksum"),
        "active_bundle_timestamp": active_bundle.get("timestamp"),
        "fixup_tasks_total": fixup_total,
        "fixup_tasks_breakdown": fixup_breakdown,
        "search_heads_count": len(topo.search_heads),
        "shc_captain": topo.shc_captain.name if topo.shc_captain else None,
        "license_manager": topo.license_manager.mgmt_url if topo.license_manager else None,
    }


async def list_peers(gw: Gateway, detailed: bool = False) -> list[dict[str, Any]]:
    """List indexer cluster peers with key health fields.

    Args:
        detailed: If True, include extended fields (replication_count, primary_count, last_heartbeat).
    """
    topo = await gw.topology(force=True)
    rows = []
    for p in topo.indexers:
        row = {
            "name": p.name,
            "mgmt_url": p.mgmt_url,
            "status": p.status,
            "site": p.extra.get("site"),
            "bucket_count": p.extra.get("bucket_count"),
            "is_searchable": p.extra.get("is_searchable"),
            "version": p.extra.get("version"),
        }
        if detailed:
            # Re-query peer details directly from CM for richer fields
            async with gw.client_for(topo.cluster_manager.mgmt_url) as cm:
                resp = await cm.get("/services/cluster/manager/peers", params={"count": 0})
                for entry in resp.get("entry", []):
                    c = entry.get("content", {})
                    if c.get("label") == p.name:
                        row.update({
                            "replication_count": c.get("replication_count"),
                            "primary_count": c.get("primary_count"),
                            "last_heartbeat": c.get("last_heartbeat"),
                            "pending_job_count": c.get("pending_job_count"),
                            "indexing_disk_space_bytes": c.get("indexing_disk_space"),
                        })
                        break
        rows.append(row)
    return rows

"""Search Head Cluster tools — query SHC member(s) for membership, captain, sync state."""
from __future__ import annotations

import logging
from typing import Any, Optional

from ..gateway import Gateway

log = logging.getLogger(__name__)


async def shc_status(gw: Gateway) -> dict[str, Any]:
    """Return SHC composition: members with their state, captain detail, sync info.

    Queries the SHC bootstrap URL (or first known SH). The SHC member endpoint
    returns each member's last_heartbeat, replication_count, peer_scheme_host_port,
    and `mgmt_uri`. The captain endpoint provides election state.
    """
    topo = await gw.topology()
    if not topo.search_heads:
        return {"error": "No search heads known. Set SPLUNK_SHC_BOOTSTRAP_URL or pass shc_url to cluster_connect."}

    # Prefer captain so we get fresh captain info; otherwise first member
    target_url = topo.shc_captain.mgmt_url if topo.shc_captain else topo.search_heads[0].mgmt_url
    target_name = topo.shc_captain.name if topo.shc_captain else topo.search_heads[0].name

    async with gw.client_for(target_url) as sh:
        members_resp = await sh.get("/services/shcluster/member/members", params={"count": 0})
        captain_resp = await sh.get("/services/shcluster/captain/info")
        info_resp = await sh.get("/services/shcluster/member/info")

    members = []
    for entry in members_resp.get("entry", []):
        c = entry.get("content", {})
        members.append({
            "id": entry.get("name"),
            "label": c.get("label"),
            "mgmt_uri": c.get("mgmt_uri"),
            "status": c.get("status"),
            "is_registered": c.get("is_registered"),
            "last_heartbeat": c.get("last_heartbeat"),
            "host_port_pair": c.get("host_port_pair"),
            "peer_scheme_host_port": c.get("peer_scheme_host_port"),
            "replication_count": c.get("replication_count"),
            "advertise_restart_required": c.get("advertise_restart_required"),
        })

    captain_content = captain_resp.get("entry", [{}])[0].get("content", {})
    info_content = info_resp.get("entry", [{}])[0].get("content", {})

    return {
        "queried_node": target_name,
        "queried_node_url": target_url,
        "captain": {
            "label": captain_content.get("label"),
            "mgmt_uri": captain_content.get("mgmt_uri"),
            "id": captain_content.get("id"),
            "dynamic_captain": captain_content.get("dynamic_captain"),
            "last_heartbeat": captain_content.get("last_heartbeat"),
            "peer_scheme_host_port": captain_content.get("peer_scheme_host_port"),
            "service_ready": captain_content.get("service_ready_flag"),
            "rolling_restart": captain_content.get("rolling_restart_flag"),
            "min_peers_joined": captain_content.get("min_peers_joined_flag"),
        },
        "this_node": {
            "label": info_content.get("label"),
            "status": info_content.get("status"),
            "is_registered": info_content.get("is_registered"),
            "out_of_sync_node": info_content.get("out_of_sync_node"),
            "preferred_captain": info_content.get("preferred_captain"),
        },
        "members": members,
        "member_count": len(members),
        "members_up": sum(1 for m in members if m["status"] == "Up"),
    }

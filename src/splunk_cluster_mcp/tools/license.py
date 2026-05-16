"""License-related tools — route to License Manager."""
from __future__ import annotations

from typing import Any

from ..gateway import Gateway


async def license_status(gw: Gateway) -> dict[str, Any]:
    """Return license stack(s) + per-pool usage + recent violation messages.

    Always routes to the cluster's license manager (auto-discovered from CM).
    """
    topo = await gw.topology()
    if not topo.license_manager:
        return {"error": "license_manager not discoverable from cluster manager config"}

    async with gw.client_for(topo.license_manager.mgmt_url) as lm:
        stacks_resp = await lm.get("/services/licenser/stacks", params={"count": 0})
        pools_resp = await lm.get("/services/licenser/pools", params={"count": 0})
        messages_resp = await lm.get("/services/licenser/messages", params={"count": 0})
        local_resp = await lm.get("/services/licenser/localpeer")

        stacks = [
            {
                "name": entry.get("name"),
                "type": entry["content"].get("type"),
                "quota_bytes": entry["content"].get("quota"),
                "label": entry["content"].get("label"),
                "is_unlimited": entry["content"].get("is_unlimited"),
                "max_violations": entry["content"].get("max_violations"),
                "window_period_days": entry["content"].get("window_period"),
            }
            for entry in stacks_resp.get("entry", [])
        ]
        pools = [
            {
                "name": entry.get("name"),
                "stack_id": entry["content"].get("stack_id"),
                "quota": entry["content"].get("quota"),
                "effective_quota_bytes": entry["content"].get("effective_quota"),
                "used_bytes": entry["content"].get("used_bytes"),
                "peers_count": len(entry["content"].get("peers", []) or []),
            }
            for entry in pools_resp.get("entry", [])
        ]
        recent_messages = [
            {
                "name": entry.get("name"),
                "severity": entry["content"].get("severity"),
                "category": entry["content"].get("category"),
                "description": entry["content"].get("description"),
                "create_time": entry["content"].get("create_time"),
            }
            for entry in messages_resp.get("entry", [])[:10]
        ]
        local_peer = local_resp.get("entry", [{}])[0].get("content", {})

    return {
        "license_manager_url": topo.license_manager.mgmt_url,
        "stacks": stacks,
        "pools": pools,
        "recent_messages_count": len(recent_messages),
        "recent_messages": recent_messages,
        "this_peer": {
            "guid": local_peer.get("guid"),
            "license_keys_count": len(local_peer.get("license_keys", []) or []),
            "last_master_contact_sec_ago": local_peer.get("last_master_contact_sec_ago"),
        },
    }

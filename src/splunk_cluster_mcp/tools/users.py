"""User and role inspection — read-only.

Auth config (users + roles) is per-instance but typically synced via
config bundle or external auth backend. We query the cluster manager
since it's always present.
"""
from __future__ import annotations

import logging
from typing import Any

from ..gateway import Gateway

log = logging.getLogger(__name__)


async def list_users(gw: Gateway, detailed: bool = False) -> dict[str, Any]:
    """List Splunk users with roles, optionally including role detail.

    Args:
        detailed: If True, also return the role catalog with capabilities and
                  index visibility filters.
    """
    topo = await gw.topology()

    async with gw.client_for(topo.cluster_manager.mgmt_url) as cm:
        users_resp = await cm.get(
            "/services/authentication/users", params={"count": 0}
        )
        roles_resp = None
        if detailed:
            roles_resp = await cm.get(
                "/services/authorization/roles", params={"count": 0}
            )

    users = []
    for entry in users_resp.get("entry", []):
        c = entry.get("content", {})
        users.append({
            "username": entry.get("name"),
            "realname": c.get("realname"),
            "email": c.get("email"),
            "type": c.get("type"),
            "roles": c.get("roles", []) or [],
            "default_app": c.get("defaultApp"),
            "tz": c.get("tz"),
            "locked_out": bool(c.get("locked_out")),
            "force_change_pass": bool(c.get("force_change_pass")),
            "last_successful_login": c.get("last_successful_login"),
        })

    result: dict[str, Any] = {
        "queried_node": topo.cluster_manager.name,
        "user_count": len(users),
        "users": users,
    }

    if detailed and roles_resp is not None:
        roles = []
        for entry in roles_resp.get("entry", []):
            c = entry.get("content", {})
            roles.append({
                "name": entry.get("name"),
                "imported_roles": c.get("imported_roles", []) or [],
                "capabilities": c.get("capabilities", []) or [],
                "imported_capabilities": c.get("imported_capabilities", []) or [],
                "srch_indexes_allowed": c.get("srchIndexesAllowed", []) or [],
                "srch_indexes_default": c.get("srchIndexesDefault", []) or [],
                "srch_filter": c.get("srchFilter"),
                "srch_time_win": c.get("srchTimeWin"),
                "srch_disk_quota_mb": c.get("srchDiskQuota"),
                "srch_jobs_quota": c.get("srchJobsQuota"),
                "rt_srch_jobs_quota": c.get("rtSrchJobsQuota"),
                "cumulative_srch_jobs_quota": c.get("cumulativeSrchJobsQuota"),
            })
        result["role_count"] = len(roles)
        result["roles"] = roles

    return result

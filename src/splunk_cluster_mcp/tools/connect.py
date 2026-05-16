"""cluster_connect — runtime credential setup for the gateway."""
from __future__ import annotations

from typing import Any, Optional

from ..gateway import Gateway


async def cluster_connect(
    gw: Gateway,
    bootstrap_url: str,
    username: str,
    password: str,
    shc_url: Optional[str] = None,
    verify_ssl: bool = False,
) -> dict[str, Any]:
    """Test credentials, discover topology, and store config in memory."""
    topo = await gw.connect(
        bootstrap_url=bootstrap_url,
        username=username,
        password=password,
        shc_url=shc_url,
        verify_ssl=verify_ssl,
    )
    return {
        "status": "connected",
        "config_source": gw.cfg.source if gw.cfg else "unknown",
        "topology": topo.summary(),
    }


async def cluster_connection_status(gw: Gateway) -> dict[str, Any]:
    """Report whether the gateway has credentials and the current target."""
    if not gw.is_connected:
        return {
            "connected": False,
            "hint": (
                "Call cluster_connect(bootstrap_url, username, password, [shc_url], [verify_ssl=false])."
            ),
        }
    cfg = gw.cfg
    return {
        "connected": True,
        "bootstrap_url": cfg.bootstrap_url,
        "shc_bootstrap_url": cfg.shc_bootstrap_url,
        "username": cfg.username,
        "verify_ssl": cfg.verify_ssl,
        "topology_ttl_seconds": cfg.topology_ttl,
        "config_source": cfg.source,
    }

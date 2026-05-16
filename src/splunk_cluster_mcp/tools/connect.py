"""cluster_connect — runtime credential setup for the gateway."""
from __future__ import annotations

from typing import Any, Optional

from ..gateway import Gateway


async def cluster_connect(
    gw: Gateway,
    bootstrap_url: str,
    *,
    token: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
    shc_url: Optional[str] = None,
    verify_ssl: bool = True,
) -> dict[str, Any]:
    """Authenticate, discover topology, and store config in memory.

    Auth: prefer token. If no token provided, falls back to username + password.
    """
    if not token and not (username and password):
        return {
            "error": "cluster_connect needs either token (recommended) or both username and password.",
        }
    topo = await gw.connect(
        bootstrap_url=bootstrap_url,
        token=token,
        username=username,
        password=password,
        shc_url=shc_url,
        verify_ssl=verify_ssl,
    )
    return {
        "status": "connected",
        "auth_mode": gw.cfg.auth_mode if gw.cfg else "?",
        "config_source": gw.cfg.source if gw.cfg else "?",
        "topology": topo.summary(),
    }


async def cluster_connection_status(gw: Gateway) -> dict[str, Any]:
    """Report whether the gateway has credentials and the current target."""
    if not gw.is_connected:
        return {
            "connected": False,
            "hint": (
                "Call cluster_connect(bootstrap_url, [token | username, password], [shc_url], [verify_ssl=true])."
            ),
        }
    cfg = gw.cfg
    return {
        "connected": True,
        "bootstrap_url": cfg.bootstrap_url,
        "shc_bootstrap_url": cfg.shc_bootstrap_url,
        "auth_mode": cfg.auth_mode,
        "auth_principal": cfg.username if cfg.auth_mode == "basic" else "(token)",
        "verify_ssl": cfg.verify_ssl,
        "topology_ttl_seconds": cfg.topology_ttl,
        "config_source": cfg.source,
    }

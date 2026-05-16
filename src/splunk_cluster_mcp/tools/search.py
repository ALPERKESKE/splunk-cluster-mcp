"""SPL search tool — routes to SHC captain (or any SH as fallback).

Splunk search REST flow:
    1. POST   /services/search/jobs              → returns SID
    2. GET    /services/search/jobs/{sid}        → poll until isDone=1
    3. GET    /services/search/jobs/{sid}/results → JSON events
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from ..gateway import Gateway
from ..topology import Node

log = logging.getLogger(__name__)

# How often to poll job status, with mild back-off
_POLL_INTERVALS = [0.2, 0.3, 0.5, 0.8, 1.0]  # last value repeats


def _pick_search_target(gw_topology) -> Optional[Node]:
    """Prefer SHC captain. Fallback to first SHC member. Else None."""
    if gw_topology.shc_captain:
        return gw_topology.shc_captain
    if gw_topology.search_heads:
        return gw_topology.search_heads[0]
    return None


def _normalize_spl(spl: str) -> str:
    """Splunk requires SPL to start with a generating command. Prepend `search` if missing."""
    s = spl.strip()
    if s.startswith("|") or s.lower().startswith("search "):
        return s
    return "search " + s


async def search(
    gw: Gateway,
    spl: str,
    earliest: Optional[str] = None,
    latest: Optional[str] = None,
    max_results: int = 100,
    timeout_s: int = 60,
) -> dict[str, Any]:
    """Execute an SPL search and return events.

    Routes to the SHC captain so the search runs cluster-wide. If no captain is
    elected yet, falls back to the first SHC member.

    Args:
        spl: SPL query. If it doesn't start with `|` or `search `, `search ` is prepended.
        earliest: Relative or absolute time. Default from config (`-15m`).
        latest: Default `now`.
        max_results: Cap result count (default 100).
        timeout_s: Max seconds to wait for job to finish.

    Returns:
        Dict with sid, search_head, spl, time range, counts, run_duration, and events.
    """
    topo = await gw.topology()
    target = _pick_search_target(topo)
    if target is None:
        return {"error": "No search head available. Configure SPLUNK_SHC_BOOTSTRAP_URL or pass shc_url to cluster_connect."}

    cfg = gw.cfg
    earliest = earliest or (cfg.search_default_earliest if cfg else "-15m")
    latest = latest or (cfg.search_default_latest if cfg else "now")
    spl_normalized = _normalize_spl(spl)

    is_captain = topo.shc_captain is not None and target.name == topo.shc_captain.name

    log.info("search target=%s (captain=%s) spl=%r earliest=%s latest=%s",
             target.name, is_captain, spl_normalized, earliest, latest)

    async with gw.client_for(target.mgmt_url) as sh:
        # 1) submit
        post_resp = await sh.post(
            "/services/search/jobs",
            data={
                "search": spl_normalized,
                "earliest_time": earliest,
                "latest_time": latest,
                "max_count": max_results,
                "exec_mode": "normal",
            },
        )
        sid = post_resp.get("sid")
        if not sid:
            return {"error": "Search job submission returned no sid", "response": post_resp}

        # 2) poll
        start = time.time()
        content: dict = {}
        attempt = 0
        while True:
            if (time.time() - start) > timeout_s:
                # Cancel the job to free resources
                try:
                    await sh.post(f"/services/search/jobs/{sid}/control", data={"action": "cancel"})
                except Exception:
                    pass
                return {"error": f"Search timed out after {timeout_s}s", "sid": sid, "spl": spl_normalized}
            status_resp = await sh.get(f"/services/search/jobs/{sid}")
            content = status_resp.get("entry", [{}])[0].get("content", {})
            if content.get("isDone"):
                break
            interval = _POLL_INTERVALS[min(attempt, len(_POLL_INTERVALS) - 1)]
            attempt += 1
            await asyncio.sleep(interval)

        # 3) results
        results_resp = await sh.get(
            f"/services/search/jobs/{sid}/results",
            params={"count": max_results},
        )

    return {
        "sid": sid,
        "search_head": target.name,
        "search_head_role": "shc_captain" if is_captain else "shc_member",
        "search_head_url": target.mgmt_url,
        "spl": spl_normalized,
        "earliest": earliest,
        "latest": latest,
        "result_count": content.get("resultCount", 0),
        "event_count": content.get("eventCount", 0),
        "scan_count": content.get("scanCount", 0),
        "run_duration_seconds": content.get("runDuration", 0),
        "is_failed": content.get("isFailed", False),
        "messages": content.get("messages", []),
        "results": results_resp.get("results", []),
    }

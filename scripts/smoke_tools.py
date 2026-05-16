"""Direct tool smoke test — bypasses MCP layer to verify logic against live cluster."""
from __future__ import annotations

import asyncio
import json
import logging
import sys

from splunk_cluster_mcp.gateway import Gateway
from splunk_cluster_mcp.tools import cluster as cluster_tools
from splunk_cluster_mcp.tools import license as license_tools
from splunk_cluster_mcp.tools import search as search_tools


async def main() -> None:
    logging.basicConfig(level="INFO", stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    gw = Gateway()

    print("\n=== cluster_health ===")
    print(json.dumps(await cluster_tools.cluster_health(gw), indent=2, default=str))

    print("\n=== list_peers (basic) ===")
    print(json.dumps(await cluster_tools.list_peers(gw), indent=2, default=str))

    print("\n=== license_status (truncated) ===")
    lic = await license_tools.license_status(gw)
    print(json.dumps({k: lic[k] for k in ["license_manager_url", "stacks", "recent_messages_count"]}, indent=2, default=str))

    print("\n=== search: '| rest /services/server/info | head 1' ===")
    r = await search_tools.search(gw, "| rest /services/server/info | head 1", earliest="-1m", latest="now")
    print(json.dumps({
        "search_head": r.get("search_head"),
        "search_head_role": r.get("search_head_role"),
        "result_count": r.get("result_count"),
        "scan_count": r.get("scan_count"),
        "run_duration_seconds": r.get("run_duration_seconds"),
        "first_result_keys": list(r.get("results", [{}])[0].keys())[:8] if r.get("results") else [],
    }, indent=2, default=str))

    print("\n=== search: 'index=_internal sourcetype=splunkd ERROR | head 5' ===")
    r2 = await search_tools.search(gw, 'index=_internal sourcetype=splunkd ERROR', earliest="-1h", latest="now", max_results=5)
    print(json.dumps({
        "search_head": r2.get("search_head"),
        "result_count": r2.get("result_count"),
        "scan_count": r2.get("scan_count"),
        "run_duration_seconds": r2.get("run_duration_seconds"),
        "first_event_preview": (r2.get("results", [{}])[0].get("_raw") or "")[:200] if r2.get("results") else "(no results)",
    }, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())

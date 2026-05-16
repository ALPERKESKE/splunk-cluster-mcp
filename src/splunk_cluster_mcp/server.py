"""FastMCP server entry — splunk-cluster gateway.

Run via stdio:
    uv run python -m splunk_cluster_mcp.server

Config priority:
    1. SPLUNK_BOOTSTRAP_URL etc. environment variables (auto-loaded at startup)
    2. .env file in working dir (auto-loaded by python-dotenv)
    3. cluster_connect() tool at runtime

If neither (1) nor (2) is set, the gateway starts disconnected and other tools
return a helpful error until cluster_connect() is called.
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Optional

from fastmcp import FastMCP

from .gateway import Gateway, NotConnectedError
from .tools import cluster as cluster_tools
from .tools import connect as connect_tools
from .tools import disk as disk_tools
from .tools import indexes as indexes_tools
from .tools import license as license_tools
from .tools import logs as logs_tools
from .tools import scenario as scenario_tools
from .tools import search as search_tools
from .tools import shc as shc_tools


def _setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )


def _err(e: Exception) -> dict:
    """Format an error as a JSON-friendly dict for tool callers."""
    return {"error": str(e), "type": type(e).__name__}


def build_app() -> FastMCP:
    gw = Gateway()
    _setup_logging(gw.cfg.log_level if gw.cfg else os.environ.get("LOG_LEVEL", "INFO"))

    mcp = FastMCP(
        name="splunk-cluster",
        instructions=(
            "Federation gateway for a distributed Splunk deployment. Tools auto-route to "
            "the right node (cluster manager, license manager, SHC captain, indexer). "
            "If you see a 'not connected' error, call cluster_connect first with the "
            "Cluster Manager URL plus admin credentials. Credentials live in memory only "
            "for this MCP session."
        ),
    )

    # ---- Connection management ----------------------------------------

    @mcp.tool(
        name="cluster_connect",
        description=(
            "Establish a connection to a Splunk cluster. Validates the URL+credentials "
            "by calling /services/server/info, then auto-discovers cluster topology "
            "(indexer peers, search head cluster members, license manager URL). "
            "Required: bootstrap_url (Cluster Manager URL like https://cm.example.com:8089), "
            "username, password. Optional: shc_url (any SHC member URL, used to find SHC "
            "captain), verify_ssl (default false for lab self-signed certs). "
            "Credentials are kept in memory only — they are NOT written to disk."
        ),
    )
    async def cluster_connect(
        bootstrap_url: str,
        username: str,
        password: str,
        shc_url: Optional[str] = None,
        verify_ssl: bool = False,
    ) -> dict:
        try:
            return await connect_tools.cluster_connect(
                gw, bootstrap_url=bootstrap_url, username=username, password=password,
                shc_url=shc_url, verify_ssl=verify_ssl,
            )
        except Exception as e:
            return _err(e)

    @mcp.tool(
        name="cluster_connection_status",
        description=(
            "Check whether the gateway is currently configured with cluster credentials, "
            "and which Splunk Cluster Manager URL it points to. Useful for diagnosing 'not "
            "connected' errors and confirming you've connected to the right cluster."
        ),
    )
    async def cluster_connection_status() -> dict:
        return await connect_tools.cluster_connection_status(gw)

    # ---- Read tools (require connection) ------------------------------

    @mcp.tool(
        name="cluster_health",
        description=(
            "Return high-level health of the indexer cluster: peer counts (up/searchable), "
            "active bundle ID, fixup tasks (streaming/RF/SF/generation), SHC captain name, "
            "license manager URL. Read-only. Requires cluster_connect first."
        ),
    )
    async def cluster_health() -> dict:
        try:
            return await cluster_tools.cluster_health(gw)
        except NotConnectedError as e:
            return _err(e)

    @mcp.tool(
        name="list_peers",
        description=(
            "List all indexer cluster peers with name, status, bucket count, searchability, "
            "site, and Splunk version. Pass detailed=true for replication_count, primary_count, "
            "last_heartbeat, pending_job_count, and indexing disk space. Requires cluster_connect."
        ),
    )
    async def list_peers(detailed: bool = False) -> list[dict] | dict:
        try:
            return await cluster_tools.list_peers(gw, detailed=detailed)
        except NotConnectedError as e:
            return _err(e)

    @mcp.tool(
        name="license_status",
        description=(
            "Return license stacks (Enterprise/Developer/Forwarder/Free), pools with usage "
            "in bytes, and up to 10 recent violation messages. Auto-routes to the license "
            "manager URL discovered from the cluster manager config. Requires cluster_connect."
        ),
    )
    async def license_status() -> dict:
        try:
            return await license_tools.license_status(gw)
        except NotConnectedError as e:
            return _err(e)

    @mcp.tool(
        name="search",
        description=(
            "Execute an SPL search across the cluster. Routes to the Search Head Cluster "
            "captain (dynamic) so the query fans out to all indexer peers. Falls back to "
            "any SHC member if no captain is elected. The `search ` prefix is added "
            "automatically if missing. Returns event count, scan count, run duration, and "
            "result rows. Use earliest/latest like '-1h' or absolute epoch. Requires "
            "cluster_connect first."
        ),
    )
    async def search(
        spl: str,
        earliest: Optional[str] = None,
        latest: Optional[str] = None,
        max_results: int = 100,
        timeout_s: int = 60,
    ) -> dict:
        try:
            return await search_tools.search(
                gw, spl, earliest=earliest, latest=latest,
                max_results=max_results, timeout_s=timeout_s,
            )
        except NotConnectedError as e:
            return _err(e)

    @mcp.tool(
        name="shc_status",
        description=(
            "Return Search Head Cluster (SHC) composition: members with status / last_heartbeat / "
            "mgmt_uri, the elected captain with its details, and 'this node' info. Useful to "
            "diagnose captain election, sync issues, and offline SHC members. Queries the captain "
            "node directly if known, else any SHC member. Requires cluster_connect."
        ),
    )
    async def shc_status() -> dict:
        try:
            return await shc_tools.shc_status(gw)
        except NotConnectedError as e:
            return _err(e)

    @mcp.tool(
        name="list_indexes",
        description=(
            "List indexes in the indexer cluster with per-index health: bucket counts, "
            "is_searchable, search_factor_met, replication_count. Routes to the cluster "
            "manager for cluster-wide view. By default skips internal indexes "
            "(_internal/_audit/_introspection/etc); set include_internal=true to include them. "
            "Sorted by buckets_with_data desc. Requires cluster_connect."
        ),
    )
    async def list_indexes(include_internal: bool = False) -> dict:
        try:
            return await indexes_tools.list_indexes(gw, include_internal=include_internal)
        except NotConnectedError as e:
            return _err(e)

    @mcp.tool(
        name="index_detail",
        description=(
            "Deep info on a single index from the cluster manager perspective: bucket counts, "
            "searchability, search_factor_met, full raw content. Use list_indexes first to "
            "find names. Requires cluster_connect."
        ),
    )
    async def index_detail(name: str) -> dict:
        try:
            return await indexes_tools.index_detail(gw, name)
        except NotConnectedError as e:
            return _err(e)

    @mcp.tool(
        name="node_disk",
        description=(
            "Per-node disk partition usage across the cluster. Returns mount point, capacity, "
            "free space, and used percent for every partition on every Splunk node (CM, indexers, "
            "SHs, license manager). Filter with `node` (exact label, e.g. 'sf-student01-idx2') or "
            "`role` (cluster_manager / indexer / search_head / license_manager). Surfaces the "
            "hottest partition across the cluster. Requires cluster_connect."
        ),
    )
    async def node_disk(node: Optional[str] = None, role: Optional[str] = None) -> dict:
        try:
            return await disk_tools.node_disk(gw, node=node, role=role)
        except NotConnectedError as e:
            return _err(e)

    @mcp.tool(
        name="tail_log",
        description=(
            "Tail a Splunk log on a node via SSH. The node argument accepts the label "
            "('sf-student01-idx2') or the SSH alias ('idx2'). log_name is one of: splunkd, "
            "splunkd_access, metrics, license_usage, license_usage_summary, audit, health, "
            "scheduler, web_service, web_access — or pass an absolute path. Optional grep "
            "(case-insensitive regex) filters lines. lines defaults to 100 (max 1000). "
            "Requires SSH access from this host to the Splunk node (key auth). Requires "
            "cluster_connect."
        ),
    )
    async def tail_log(
        node: str,
        log_name: str = "splunkd",
        lines: int = 100,
        grep: Optional[str] = None,
    ) -> dict:
        try:
            return await logs_tools.tail_log(gw, node=node, log_name=log_name, lines=lines, grep=grep)
        except NotConnectedError as e:
            return _err(e)

    # ---- Scenario triggers (WRITE) ------------------------------------

    @mcp.tool(
        name="scenario_license_violation",
        description=(
            "WRITE. Trigger a license violation scenario by lowering the daily quota of a "
            "license pool. Saves the original quota to disk so it can be restored. Use "
            "target_quota_mb to set how low (default 5 MB — easy to exceed). Pool name "
            "defaults to 'auto_generated_pool_enterprise'. After triggering, ingest just "
            "a few MB of data to provoke violations. Call scenario_license_violation_recover "
            "to undo. Requires cluster_connect."
        ),
    )
    async def scenario_license_violation(
        target_quota_mb: int = 5,
        pool_name: str = "auto_generated_pool_enterprise",
    ) -> dict:
        try:
            return await scenario_tools.scenario_license_violation(
                gw, target_quota_mb=target_quota_mb, pool_name=pool_name,
            )
        except NotConnectedError as e:
            return _err(e)

    @mcp.tool(
        name="scenario_license_violation_recover",
        description=(
            "WRITE. Restore the original license pool quota that scenario_license_violation "
            "modified. Reads pre-state from disk. Idempotent — safe to call multiple times. "
            "Requires cluster_connect."
        ),
    )
    async def scenario_license_violation_recover() -> dict:
        try:
            return await scenario_tools.scenario_license_violation_recover(gw)
        except NotConnectedError as e:
            return _err(e)

    @mcp.tool(
        name="scenario_recover_baseline",
        description=(
            "WRITE. DESTRUCTIVE. Roll back cluster VMs to a Proxmox snapshot (default "
            "'pre-socready-baseline'). target='all' rolls back every cluster VM; or pass a "
            "node label like 'sf-student01-idx2' or a comma-separated list. Takes ~30-60s "
            "per VM. Splunk restarts fresh, all in-memory state lost. Use this as the "
            "ultimate reset after a destructive scenario. Requires SSH alias 'proxmox' "
            "configured. Requires cluster_connect."
        ),
    )
    async def scenario_recover_baseline(
        target: str = "all",
        snapshot_name: str = "pre-socready-baseline",
        proxmox_host: str = "proxmox",
    ) -> dict:
        try:
            return await scenario_tools.scenario_recover_baseline(
                gw, target=target, snapshot_name=snapshot_name, proxmox_host=proxmox_host,
            )
        except NotConnectedError as e:
            return _err(e)

    return mcp


def main() -> None:
    app = build_app()
    app.run()  # stdio transport by default


if __name__ == "__main__":
    main()

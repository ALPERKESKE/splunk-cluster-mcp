"""Smoke tests — exercise tool logic against a live Splunk cluster.

These are NOT pure unit tests; they require:
  - Cluster running and reachable
  - SPLUNK_BOOTSTRAP_URL and SPLUNK_SHC_BOOTSTRAP_URL in env or .env
  - Valid admin credentials

Run: `uv run pytest -x tests/test_smoke.py`
Skip when no env: `pytest -m smoke --no-header`
"""
from __future__ import annotations

import os
import pytest

# These tests require a live cluster; skip otherwise
pytestmark = pytest.mark.skipif(
    not os.environ.get("SPLUNK_BOOTSTRAP_URL"),
    reason="SPLUNK_BOOTSTRAP_URL not set; skipping live-cluster tests",
)


@pytest.fixture
def gw():
    from splunk_cluster_mcp.gateway import Gateway
    return Gateway()


@pytest.mark.asyncio
async def test_topology_discovers_cm_and_indexers(gw):
    topo = await gw.topology()
    assert topo.cluster_manager.name
    assert len(topo.indexers) >= 1
    assert all(p.mgmt_url.startswith("https://") for p in topo.indexers)


@pytest.mark.asyncio
async def test_cluster_health_reports_peers(gw):
    from splunk_cluster_mcp.tools import cluster as cluster_tools
    h = await cluster_tools.cluster_health(gw)
    assert h["peer_count"] >= 1
    assert h["service_ready"] is True


@pytest.mark.asyncio
async def test_list_peers_returns_rows(gw):
    from splunk_cluster_mcp.tools import cluster as cluster_tools
    peers = await cluster_tools.list_peers(gw)
    assert isinstance(peers, list)
    assert len(peers) >= 1
    for p in peers:
        assert "name" in p
        assert "status" in p


@pytest.mark.asyncio
async def test_license_status_returns_stacks(gw):
    from splunk_cluster_mcp.tools import license as lic
    s = await lic.license_status(gw)
    assert "stacks" in s
    assert len(s["stacks"]) >= 1


@pytest.mark.asyncio
async def test_shc_status_returns_members(gw):
    from splunk_cluster_mcp.tools import shc as shc_tools
    s = await shc_tools.shc_status(gw)
    if "error" in s:
        pytest.skip(f"SHC not configured: {s['error']}")
    assert s["member_count"] >= 1


@pytest.mark.asyncio
async def test_list_indexes_returns_indexes(gw):
    from splunk_cluster_mcp.tools import indexes as idx_tools
    r = await idx_tools.list_indexes(gw)
    assert "indexes" in r
    assert r["index_count"] >= 0


@pytest.mark.asyncio
async def test_node_disk_fans_out(gw):
    from splunk_cluster_mcp.tools import disk as disk_tools
    r = await disk_tools.node_disk(gw)
    assert r["queried_count"] >= 1
    assert r["successful"] >= 1


@pytest.mark.asyncio
async def test_search_runs(gw):
    from splunk_cluster_mcp.tools import search as search_tools
    r = await search_tools.search(
        gw, "| rest /services/server/info | head 1", earliest="-1m", latest="now"
    )
    assert not r.get("is_failed"), r
    assert r["result_count"] >= 1

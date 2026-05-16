# splunk-cluster-mcp

> A **cluster-aware** Model Context Protocol gateway for distributed Splunk deployments. Lets Claude (or any MCP client) talk to a multi-node Splunk cluster through one endpoint, routing each tool call to the right node (cluster manager, search head cluster captain, license manager, indexers, …).

**Built because**: Splunk's official MCP server (1.1.2, May 2026) is single-instance. In a real cluster, search runs on a search head, cluster admin runs on the cluster manager, license operations live on the license manager. No existing MCP knew about that.

---

## Install — Claude Code Plugin Marketplace (recommended)

The fastest way. Inside any Claude Code session:

```
/plugin marketplace add ALPERKESKE/splunk-cluster-mcp
/plugin install splunk-cluster-mcp@splunk-cluster-mcp
```

Claude Code clones the repo into `~/.claude/plugins/cache` and registers the MCP automatically. Restart Claude Code; the gateway is ready in every session.

First-run: ask Claude to connect to your cluster.

```
Connect to my Splunk cluster — CM is https://cm.example.com:8089,
SHC bootstrap is https://sh1.example.com:8089, admin/<password>.
```

Claude calls `cluster_connect(...)` and the rest of the tools become live.

## Install — manual (without Claude Code plugin system)

If you prefer to wire the MCP up yourself:

```bash
git clone https://github.com/ALPERKESKE/splunk-cluster-mcp.git
cd splunk-cluster-mcp
uv sync
claude mcp add splunk-cluster --scope user -- \
  uv --directory "$(pwd)" run python -m splunk_cluster_mcp.server
```

Or add to `.mcp.json` manually:

```json
{
  "mcpServers": {
    "splunk-cluster": {
      "type": "stdio",
      "command": "uv",
      "args": ["--directory", "/path/to/splunk-cluster-mcp", "run", "python", "-m", "splunk_cluster_mcp.server"]
    }
  }
}
```

---

## Tool catalog

| Tool | Routes to | Notes |
|---|---|---|
| `cluster_connect` | — | runtime credential setup (in-memory only) |
| `cluster_connection_status` | — | diagnostic — are we connected, where to |
| `cluster_health` | Cluster Manager | peers up/searchable, bundle, fixups |
| `list_peers` | Cluster Manager | indexer cluster peer detail |
| `shc_status` | SHC Captain (or any member) | members, captain, sync state |
| `list_indexes` | Cluster Manager | cluster-wide index list with bucket health |
| `index_detail` | Cluster Manager | deep info on a single index |
| `node_disk` | All nodes (fan-out) | partition usage per node, hottest partition |
| `tail_log` | Any node (SSH) | tail splunkd, license_usage, metrics, … |
| `license_status` | License Manager | stacks, pools, violations |
| `list_users` | Cluster Manager | users + roles (with capabilities if detailed=true) |
| `search` | SHC Captain (dynamic) | SPL via async job, cluster-wide |

---

## Credentials — three options

**1. Environment variables** (good for CI / headless):

```bash
export SPLUNK_BOOTSTRAP_URL=https://cm.example.com:8089
export SPLUNK_SHC_BOOTSTRAP_URL=https://sh1.example.com:8089
export SPLUNK_USERNAME=admin
export SPLUNK_PASSWORD=changeme
export SPLUNK_VERIFY_SSL=false   # lab self-signed certs
```

**2. `.env` file** in the project root (good for dev clones):

```bash
cp .env.example .env
# edit .env
```

**3. `cluster_connect` tool at runtime** (recommended for plugin install):

```
cluster_connect(
  bootstrap_url="https://cm.example.com:8089",
  username="admin",
  password="…",
  shc_url="https://sh1.example.com:8089",   # optional
  verify_ssl=false                          # optional
)
```

Credentials live **in memory only** for the MCP session — never written to disk.

---

## What you can ask Claude

Once connected, try:

- _“What's the cluster health?”_ → `cluster_health`
- _“List indexer peers with replication detail.”_ → `list_peers(detailed=true)`
- _“Show me the SHC captain and members.”_ → `shc_status`
- _“What indexes have the most data?”_ → `list_indexes`
- _“Tail the last 50 ERROR lines from splunkd.log on idx2.”_ → `tail_log(node="idx2", grep="ERROR")`
- _“Per-node disk usage. Which partition is hottest?”_ → `node_disk`
- _“Run SPL: index=_internal sourcetype=splunkd ERROR \| stats count by component.”_ → `search`
- _“Show me the last 10 license violation messages.”_ → `license_status`
- _“List users and their roles. Include capabilities and allowed indexes.”_ → `list_users(detailed=true)`

---

## Architecture

```
                     ┌──────────────────┐
                     │     Claude       │
                     └─────────┬────────┘
                               │ stdio (MCP JSON-RPC)
                     ┌─────────▼────────────┐
                     │  splunk-cluster-mcp  │  ← this repo
                     │  (Python · FastMCP)  │
                     └──────┬───────────────┘
                            │ HTTPS :8089 + SSH
   ┌──────────┬─────────────┼─────────────┬──────────┬──────────┐
   ▼          ▼             ▼             ▼          ▼          ▼
 cm1:8089   idx1:8089   sh1/2/3:8089   lm1:8089   ds1:8089   hf1:8089
 (cluster   (indexer    (search       (license   (deploy.   (heavy
  manager)   peer)       head)         manager)   server)    fwd)
```

**Routing logic** (built into each tool):

| Intent | Target |
|---|---|
| Cluster admin / peers / bundle / fixups | Cluster Manager |
| SHC members, captain, sync state | SHC Captain (dynamic) or any member |
| SPL search | SHC Captain (auto-fallback to any SH) |
| License pool / quota / violations | License Manager (auto-discovered from CM) |
| Per-node disk / partition usage | Each node directly (parallel fan-out) |
| Log tail | Named node via SSH |

**Topology discovery** at `cluster_connect` time:
1. CM → indexer peers, license manager URL
2. Any SHC member → SHC members + dynamic captain
3. Cached 60 s, refreshed on demand

---

## Why not just use the official Splunk MCP server?

The [official MCP](https://splunkbase.splunk.com/app/7931) ([Cisco DevNet repo](https://github.com/CiscoDevNet/Splunk-MCP-Server-official)) is excellent for **single-instance** data exploration: SPL, saved searches, knowledge objects, AI helpers. But:

- It runs **inside one Splunk instance** as an app — installed on a single host
- No tools for cluster manager admin (peers, bundles, fixups)
- No SHC captain awareness
- No license manager routing
- No multi-node fan-out (disks, logs, indexes across the cluster)

This project is **complementary**: it focuses on cluster orchestration. A future version may **compose** the official MCP (`splunk_run_query` delegated to it) so both layers coexist under one Claude-facing namespace.

---

## Tested against

- Splunk 9.4.1 (build e3bdab203ac8)
- 12-node Proxmox cluster (cm1, 3× idx, 3× sh + SHC, dep1, lm1, ds1, hf1, mc1)
- Claude Code

## Limitations

- Phase 1 uses HTTP basic auth (shared admin). Per-role tokens are Phase 2.
- No persistent topology cache between MCP sessions — re-discovers on each start.
- `verify_ssl=false` is default for lab convenience. Set `true` in prod.

## Roadmap

- **Phase 2**: federation with Splunk's official MCP server (compose `splunk_run_query` and other official tools under a single namespace)
- **Phase 2**: OAuth 2.1 per-role tokens (RBAC)
- **Phase 2**: optional OS-keyring credential storage
- **Phase 2**: write tools (push bundle, restart node, set retention) once auth is RBAC-safe

## License

MIT.

## Acknowledgements

Splunk icons in diagram references from [GimAndTonic/Splunk-Icon-Library](https://github.com/GimAndTonic/Splunk-Icon-Library) (community).

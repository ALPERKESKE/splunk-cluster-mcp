# splunk-cluster-mcp

> A small MCP that makes Claude a bit easier to use against a distributed Splunk cluster. Point it at the cluster manager and it discovers the rest — indexer peers, search head cluster captain, license manager — then routes each tool to the right node.

**Built because**: Splunk's official MCP is single-instance. In a multi-node cluster, some things live on the cluster manager, some on a search head, some on the license manager. I wanted Claude to figure that out instead of me telling it for every call.

---

## What this is — and isn't

A small quality-of-life layer for cluster setups. It:

- **Doesn't** add any new Splunk capability — everything here is already in Splunk's REST API
- **Doesn't** replace Splunk's official MCP — the two are complementary
- **Doesn't** ship enterprise auth — Phase 1 uses HTTP basic auth with a shared admin
- **Does** save you from juggling 8 SSH sessions to read cluster-wide state
- **Does** keep working when your SHC captain changes (re-discovers on every refresh)
- **Does** fan out to all peers in parallel for cluster-wide queries (disks, indexes)

If you run a single Splunk instance, just use Splunk's official MCP — it has more depth (knowledge objects, AI helpers). This project earns its keep only when you have a distributed cluster.

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

**Auth modes:** Bearer token is preferred. Create a token in Splunk via
Settings → Tokens → New Token (or `POST /services/authorization/tokens`).
HTTP basic auth (username + password) is the fallback. **Use a least-privilege
role for the token / user — not full admin.** See [`SECURITY.md`](./SECURITY.md).

> **Note on tokens in a distributed cluster.** A Splunk token is signed with
> the issuing node's `splunk.secret` *and* recorded in that node's local
> KVStore. Tokens are only portable across nodes that share **both**: the same
> `splunk.secret` and the same KVStore. In practice that's a Search Head
> Cluster (members share KVStore) — not the indexer cluster or the license
> manager. We confirmed this in our lab: even after syncing `splunk.secret` to
> all 12 nodes, a token issued on the CM was rejected on other nodes with
> `Token signature was valid, but could not find token in App KVStore`. So for
> a cluster-wide single credential today, **HTTP basic auth with a shared
> least-privilege role is the simplest path**. Tokens are still the right
> choice for single-instance or SHC-only deployments. Per-node token
> management is on the Phase 2 roadmap.

**1. Environment variables** (good for CI / headless):

```bash
export SPLUNK_BOOTSTRAP_URL=https://cm.example.com:8089
export SPLUNK_SHC_BOOTSTRAP_URL=https://sh1.example.com:8089
# Cluster-wide: HTTP basic auth with a shared least-privilege role
export SPLUNK_USERNAME=mcp-readonly
export SPLUNK_PASSWORD=...
# Single-instance / SHC-only alternative: bearer token
# export SPLUNK_TOKEN=<token>
export SPLUNK_VERIFY_SSL=true            # default; set false for lab self-signed certs
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
  username="mcp-readonly", password="...",  # cluster-wide
  # or token="<your-token>",                # single-instance / SHC only
  shc_url="https://sh1.example.com:8089",   # optional
  verify_ssl=true                           # default
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

## How this relates to Splunk's official MCP

The [official Splunk MCP](https://splunkbase.splunk.com/app/7931) ([Cisco DevNet repo](https://github.com/CiscoDevNet/Splunk-MCP-Server-official)) is the right choice for single-instance data exploration — SPL, saved searches, knowledge objects, AI helpers. It runs **inside one Splunk instance** as an app, so it sees that one instance's view of the world.

This project picks up where the official MCP stops:

- Discovers an indexer cluster from the cluster manager
- Re-discovers the SHC captain dynamically (captain changes on election)
- Auto-routes to the license manager via the CM's config
- Fans out across nodes in parallel for cluster-wide reads (disks, logs, indexes)

The two are complementary, not competing. A future version may **compose** the official MCP — delegating `splunk_run_query` and the AI helpers to it — so both layers coexist under one Claude-facing namespace.

---

## Tested against

- Splunk 9.4.1 (build e3bdab203ac8)
- 12-node Proxmox cluster (cm1, 3× idx, 3× sh + SHC, dep1, lm1, ds1, hf1, mc1)
- Claude Code

## Security

See [`SECURITY.md`](./SECURITY.md) for the full security model. Quick highlights:

- **Use a token, not a password.** Splunk Settings → Tokens → New Token.
- **Use a least-privilege role.** `admin` lets Claude run `| delete` if steered to.
- **Keep `verify_ssl=true`** (the default). `false` is for trusted lab networks only.
- **Credentials are in-memory only** — `cluster_connect` never writes them to disk.
- **Tool outputs flow to Anthropic's API** via Claude — review what your indexes contain.

## Limitations

- Phase 1 uses a single auth context across the cluster. Per-role split is Phase 2.
- A single bearer token does not work cluster-wide (tokens are per-node in
  KVStore — see [Credentials](#credentials--three-options)). For cluster-wide
  use, prefer basic auth with a shared least-privilege role.
- No persistent topology cache between MCP sessions — re-discovers on each start.

## Roadmap

- **Phase 2**: federation with Splunk's official MCP server (compose `splunk_run_query` and other official tools under a single namespace)
- **Phase 2**: OAuth 2.1 per-role tokens (RBAC)
- **Phase 2**: optional OS-keyring credential storage
- **Phase 2**: write tools (push bundle, restart node, set retention) once auth is RBAC-safe

## License

MIT.

## Trademark notice

Splunk® is a registered trademark of Splunk LLC. This project is an independent, community-built integration that talks to Splunk's public REST API. It is **not affiliated with, endorsed by, or sponsored by Splunk LLC**. The Splunk name and any references to Splunk products are used here in their nominative sense to describe interoperability.

## Acknowledgements

- Splunk's [public REST API](https://docs.splunk.com/Documentation/Splunk/latest/RESTREF/RESTprolog) — the entire surface this gateway exposes.
- [FastMCP](https://github.com/jlowin/fastmcp) — the Python framework for MCP server development.
- The [Model Context Protocol](https://modelcontextprotocol.io) spec by Anthropic and contributors.

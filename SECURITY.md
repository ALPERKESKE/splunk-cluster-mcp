# Security Notes

This is an external Python client that talks to Splunk's REST API on behalf of an
LLM (Claude). Below is what it gets right, what to be aware of, and how to harden
the deployment.

## Threat model in one paragraph

The MCP holds Splunk credentials (token or admin password) **in memory** and uses
them to call Splunk's management port (8089). It does not modify any Splunk-side
configuration. It does not install anything inside Splunk. Tool outputs flow back
through the MCP to Claude (and therefore to Anthropic's API).

## What we get right

- **Credentials in memory only** — the `cluster_connect` path never writes
  tokens or passwords to disk.
- **Token-first auth** — Bearer token auth is preferred over basic auth.
  Tokens can be scoped per-role and revoked individually.
- **TLS verification on by default** — `SPLUNK_VERIFY_SSL=true` is the
  default. Set to `false` only on trusted lab networks with self-signed certs.
- **No credentials in logs** — admin password and bearer tokens are not
  echoed to log output.
- **Shell-injection prevention** — SSH-backed tools use `shlex.quote` for any
  user-provided string interpolated into a remote command.
- **Read-only by default** — Phase 1 ships only read tools. The single
  "write" path, `cluster_connect`, only updates in-memory gateway state.

## What you should know

### Use a least-privilege Splunk role

The MCP authenticates as whatever user/token you configure. If you pass
`admin`-level credentials, Claude can (in principle) be steered to run
destructive SPL like `| delete` against any index. **Create a dedicated
read-only role and use those credentials.**

Suggested role (`mcp-readonly`):

- Inherits `user`
- `srchIndexesAllowed` = `*` (or only the indexes you want exposed)
- No `can_delete`, no `edit_*` capabilities
- Recommend a token scoped to this role rather than a password.

### TLS

The default is `verify_ssl=true`. On managed Splunk, valid certificates are
typical and you should keep verification on.

For lab/dev with self-signed certificates, you can pass `verify_ssl=false`
to `cluster_connect` or set `SPLUNK_VERIFY_SSL=false`. Be aware this allows
MITM on the management traffic if you're on an untrusted network.

### Tool outputs travel to Anthropic

Whatever Splunk returns (search results, peer status, log lines from
`tail_log`, etc.) is sent to Claude, which means it is processed by
Anthropic's API. Review Anthropic's data-retention policy before exposing
indexes that contain PII, secrets, or regulated data.

### SSH and TOFU

`tail_log` uses SSH with `StrictHostKeyChecking=accept-new`. The first
connection to a node silently accepts its host key (TOFU). Subsequent
connections fail loudly on key mismatch. If you don't trust the first-contact
network, pre-populate `~/.ssh/known_hosts` for your Splunk nodes.

### Prompt injection of cluster_connect

`cluster_connect` is a tool — a sufficiently determined prompt can convince
Claude to call it with attacker-controlled URLs and your credentials. Review
the URL `cluster_connect` is being called with, especially in untrusted
contexts.

### Plugin marketplace trust

When this MCP is installed via Claude Code's plugin marketplace, Claude Code
clones the repository and runs its code. You are trusting:
- The integrity of the GitHub repository
- Future commits pushed by the maintainer
- The dependencies declared in `pyproject.toml`

Pinning a specific commit SHA (the marketplace.json `source` accepts a
`sha` field) protects against later commits.

### Shared admin in Phase 1

Phase 1 uses a single set of credentials across all cluster nodes. There is
no per-role separation between, say, "cluster admin" and "search". Phase 2
roadmap includes per-role tokens.

### Token portability across the cluster

Splunk tokens are signed with the issuing node's `splunk.secret` **and**
recorded in that node's local KVStore. They are only portable across nodes
that share both — i.e. a Search Head Cluster, where members replicate
KVStore. They are **not** portable between the cluster manager, indexer
peers, and the license manager, even when `splunk.secret` is identical
across them. We verified this in our lab: with `splunk.secret` synced to
all 12 nodes, a CM-issued token still failed on other nodes with
`Token signature was valid, but could not find token in App KVStore`.

Practical implication for this MCP:

- **Cluster-wide single credential:** use HTTP basic auth with a shared
  least-privilege role. This is what Phase 1 supports cleanly.
- **Single-instance or SHC-only:** a bearer token works and is the
  preferred mode.
- Per-node token issuance (one token per role, stored in memory) is on the
  Phase 2 roadmap.

## Reporting an issue

Open a GitHub issue at https://github.com/ALPERKESKE/splunk-cluster-mcp/issues
or contact the maintainer directly. There is no bug-bounty program — this is
a hobby/community project.

"""Log tail tool — SSH into a node and tail a Splunk log file."""
from __future__ import annotations

import logging
import shlex
from typing import Any

from ..gateway import Gateway
from ..ssh_util import SSHError, ssh_run

log = logging.getLogger(__name__)

# Splunk logs that ship by default. Tools accept short names → expanded to full path.
KNOWN_LOGS = {
    "splunkd": "/opt/splunk/var/log/splunk/splunkd.log",
    "splunkd_access": "/opt/splunk/var/log/splunk/splunkd_access.log",
    "splunkd_ui_access": "/opt/splunk/var/log/splunk/splunkd_ui_access.log",
    "metrics": "/opt/splunk/var/log/splunk/metrics.log",
    "license_usage": "/opt/splunk/var/log/splunk/license_usage.log",
    "license_usage_summary": "/opt/splunk/var/log/splunk/license_usage_summary.log",
    "audit": "/opt/splunk/var/log/splunk/audit.log",
    "health": "/opt/splunk/var/log/splunk/health.log",
    "scheduler": "/opt/splunk/var/log/splunk/scheduler.log",
    "web_service": "/opt/splunk/var/log/splunk/web_service.log",
    "web_access": "/opt/splunk/var/log/splunk/web_access.log",
    "introspection": "/opt/splunk/var/log/splunk/splunkd_stdout.log",
}

# Map our role labels → likely ssh-config alias users provide
def _ssh_alias_for(name: str) -> str:
    """Splunk node label 'sf-student01-idx2' → ssh alias 'idx2'."""
    if name.startswith("sf-student01-"):
        return name[len("sf-student01-"):]
    return name


async def tail_log(
    gw: Gateway,
    node: str,
    log_name: str = "splunkd",
    lines: int = 100,
    grep: str | None = None,
) -> dict[str, Any]:
    """Tail a Splunk log on a given node via SSH.

    Args:
        node: Node label (e.g., 'sf-student01-idx2') or ssh alias (e.g., 'idx2').
        log_name: Short name from KNOWN_LOGS, or a full path (e.g., '/opt/splunk/var/log/.../foo.log').
        lines: Number of trailing lines (default 100, max 1000).
        grep: Optional case-insensitive pattern to filter lines (egrep -i).
    """
    lines = max(1, min(lines, 1000))

    # Resolve log path
    if log_name.startswith("/"):
        log_path = log_name
    else:
        log_path = KNOWN_LOGS.get(log_name)
        if log_path is None:
            return {
                "error": f"Unknown log shortname '{log_name}'",
                "known_logs": sorted(KNOWN_LOGS.keys()),
                "hint": "Or pass an absolute path starting with /",
            }

    # Resolve ssh host (try alias mapping)
    ssh_host = _ssh_alias_for(node)

    # Build remote command
    if grep:
        # Use grep -i; quote safely
        safe_pattern = shlex.quote(grep)
        remote_cmd = f"tail -n 5000 {shlex.quote(log_path)} | grep -i -E -- {safe_pattern} | tail -n {lines}"
    else:
        remote_cmd = f"tail -n {lines} {shlex.quote(log_path)}"

    try:
        output = await ssh_run(ssh_host, remote_cmd, timeout_s=20)
    except SSHError as e:
        return {
            "error": f"SSH to '{ssh_host}' failed: {e}",
            "hint": f"Make sure ~/.ssh/config has alias '{ssh_host}' or pass the IP/hostname directly.",
        }
    except Exception as e:
        return {"error": f"tail failed: {e}"}

    log_lines = output.rstrip("\n").splitlines()
    return {
        "node": node,
        "ssh_target": ssh_host,
        "log_path": log_path,
        "requested_lines": lines,
        "grep_filter": grep,
        "returned_line_count": len(log_lines),
        "lines": log_lines,
    }

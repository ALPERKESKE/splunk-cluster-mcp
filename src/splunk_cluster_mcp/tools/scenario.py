"""Scenario trigger tools — controlled lab incidents for SOCReady content.

Every scenario should be RECOVERABLE without manual intervention. We save
pre-state in state.json so the recovery tool can reverse the action.

Phase 1 scenarios:
  - scenario_license_violation: lower license pool quota → violations accumulate
  - scenario_recover_baseline: qm rollback to pre-socready-baseline snapshot on Proxmox
"""
from __future__ import annotations

import logging
import shlex
import time
from typing import Any
from urllib.parse import urlparse

from .. import state as state_mod
from ..gateway import Gateway
from ..ssh_util import SSHError, ssh_run

log = logging.getLogger(__name__)

DEFAULT_VIOLATION_QUOTA_MB = 5


def _strip_label_prefix(label: str | None) -> str | None:
    """Map Splunk node label ('sf-student01-lm1') to ssh alias ('lm1')."""
    if not label:
        return None
    if label.startswith("sf-student01-"):
        return label[len("sf-student01-"):]
    return label


# --------- license violation ---------------------------------------------------

async def scenario_license_violation(
    gw: Gateway,
    target_quota_mb: int = DEFAULT_VIOLATION_QUOTA_MB,
    pool_name: str = "auto_generated_pool_enterprise",
    license_manager_ssh: str | None = None,
) -> dict[str, Any]:
    """Lower a license pool quota via SSH+CLI to provoke usage violations.

    Splunk REST does not expose pool quota edit for auto-generated pools, so we
    use the CLI: `splunk edit licenser-pools -name <X> -quota <N>mb`.

    Args:
        target_quota_mb: New quota in megabytes (default 5).
        pool_name: Pool to edit. Default is auto_generated_pool_enterprise.
        license_manager_ssh: SSH alias of the LM (default: derived from topology, e.g. 'lm1').
    """
    topo = await gw.topology()
    if not topo.license_manager:
        return {"error": "license_manager not discoverable from CM. Cannot run scenario."}

    cfg = gw._ensure_connected()

    # Read original quota first (for recovery)
    async with gw.client_for(topo.license_manager.mgmt_url) as lm:
        try:
            cur_resp = await lm.get(f"/services/licenser/pools/{pool_name}")
        except Exception as e:
            return {"error": f"Could not read pool '{pool_name}': {e}",
                    "hint": "Use license_status() to see existing pool names."}
        cur_content = cur_resp.get("entry", [{}])[0].get("content", {})
        original_quota = cur_content.get("quota")
        original_effective = cur_content.get("effective_quota")

    # Determine SSH alias for the LM
    if not license_manager_ssh:
        # Try to derive from license_manager node label (Phase 1: 'sf-student01-lm1' -> 'lm1')
        # but the topology stores 'license_manager' as fixed name. We map via URL host instead.
        parsed = urlparse(topo.license_manager.mgmt_url)
        # Lab-specific: pick alias 'lm1' for the cluster license manager
        # Future: maintain a node-label → ssh-alias map in topology
        license_manager_ssh = "lm1"

    # Save pre-state
    pre_state = {
        "type": "license_violation",
        "pool": pool_name,
        "original_quota": original_quota,
        "original_effective_quota_bytes": original_effective,
        "target_quota_bytes": target_quota_mb * 1024 * 1024,
        "target_quota_mb": target_quota_mb,
        "license_manager_url": topo.license_manager.mgmt_url,
        "license_manager_ssh": license_manager_ssh,
        "started_at": time.time(),
    }
    state_mod.upsert("license_violation", pre_state)

    # Run CLI to lower quota
    cli_cmd = (
        f"/opt/splunk/bin/splunk edit licenser-pools "
        f"-name {shlex.quote(pool_name)} "
        f"-quota {target_quota_mb}mb "
        f"-auth admin:{shlex.quote(cfg.password)}"
    )
    try:
        out = await ssh_run(license_manager_ssh, cli_cmd, timeout_s=30)
    except SSHError as e:
        return {
            "error": f"SSH command to {license_manager_ssh} failed: {e}",
            "pre_state_saved": pre_state,
            "hint": f"Ensure ~/.ssh/config has alias '{license_manager_ssh}' with key auth to the license manager.",
        }
    except Exception as e:
        return {"error": f"Unexpected error: {e}", "pre_state_saved": pre_state}

    # Verify via REST
    async with gw.client_for(topo.license_manager.mgmt_url) as lm:
        try:
            verify_resp = await lm.get(f"/services/licenser/pools/{pool_name}")
            new_content = verify_resp.get("entry", [{}])[0].get("content", {})
        except Exception:
            new_content = {}

    return {
        "status": "triggered",
        "scenario": "license_violation",
        "pool": pool_name,
        "cli_output": out.strip()[-300:] if out else "",
        "before": {
            "quota": original_quota,
            "effective_quota_bytes": original_effective,
        },
        "after": {
            "quota": new_content.get("quota"),
            "effective_quota_bytes": new_content.get("effective_quota"),
            "used_bytes": new_content.get("used_bytes"),
        },
        "expected_effect": (
            f"Once indexed daily volume exceeds {target_quota_mb} MB, license_usage.log will "
            "show violations and `license_status` will list licenser-messages entries. "
            "Push some data through hf1 to provoke the violation."
        ),
        "recovery": (
            "Call scenario_license_violation_recover() to restore the original quota. "
            "If state is lost, manually: ssh lm1 '/opt/splunk/bin/splunk edit licenser-pools "
            f"-name {pool_name} -quota MAX -auth admin:<pw>'"
        ),
    }


async def scenario_license_violation_recover(gw: Gateway) -> dict[str, Any]:
    """Restore the license pool quota changed by scenario_license_violation."""
    pre_state = state_mod.load_state().get("license_violation")
    if not pre_state:
        return {
            "error": "No license_violation scenario state found. Nothing to recover.",
            "hint": "If you know the original quota, run: ssh lm1 '/opt/splunk/bin/splunk edit licenser-pools -name <pool> -quota MAX -auth admin:<pw>'",
        }

    cfg = gw._ensure_connected()
    pool_name = pre_state["pool"]
    ssh_alias = pre_state.get("license_manager_ssh", "lm1")
    original_quota = pre_state.get("original_quota")  # 'MAX' or bytes-as-string
    original_eff_bytes = pre_state.get("original_effective_quota_bytes")

    # Determine quota arg for CLI
    if str(original_quota).strip().upper() == "MAX":
        quota_arg = "MAX"
    elif original_eff_bytes:
        # Convert bytes back to MB suffix (Splunk CLI accepts kb/mb/gb/tb)
        # Use mb with rounding; if it's enormous, gb
        mb = original_eff_bytes / (1024 * 1024)
        if mb >= 1024:
            quota_arg = f"{round(mb / 1024)}gb"
        else:
            quota_arg = f"{round(mb)}mb"
    else:
        quota_arg = "MAX"

    cli_cmd = (
        f"/opt/splunk/bin/splunk edit licenser-pools "
        f"-name {shlex.quote(pool_name)} "
        f"-quota {quota_arg} "
        f"-auth admin:{shlex.quote(cfg.password)}"
    )
    try:
        out = await ssh_run(ssh_alias, cli_cmd, timeout_s=30)
    except SSHError as e:
        return {"error": f"SSH recovery failed: {e}", "pre_state": pre_state}

    state_mod.pop("license_violation")
    return {
        "status": "recovered",
        "scenario": "license_violation",
        "pool": pool_name,
        "restored_quota_arg": quota_arg,
        "cli_output": out.strip()[-300:] if out else "",
        "elapsed_seconds": round(time.time() - pre_state.get("started_at", time.time()), 1),
    }


# --------- snapshot rollback (heavy hammer) -----------------------------------

NODE_TO_VMID = {
    "sf-student01-cm1": 9030,
    "sf-student01-idx1": 9031,
    "sf-student01-idx2": 9032,
    "sf-student01-idx3": 9033,
    "sf-student01-sh1": 9034,
    "sf-student01-sh2": 9035,
    "sf-student01-sh3": 9036,
    "sf-student01-dep1": 9037,
    "sf-student01-lm1": 9038,
    "sf-student01-ds1": 9039,
    "sf-student01-hf1": 9040,
    "sf-student01-mc1": 9041,
}

ALL_VMIDS = list(NODE_TO_VMID.values())

DEFAULT_PROXMOX_HOST = "proxmox"
DEFAULT_SNAPSHOT_NAME = "pre-socready-baseline"


async def scenario_recover_baseline(
    gw: Gateway,
    target: str = "all",
    snapshot_name: str = DEFAULT_SNAPSHOT_NAME,
    proxmox_host: str = DEFAULT_PROXMOX_HOST,
) -> dict[str, Any]:
    """Roll cluster VMs back to a Proxmox snapshot."""
    if target == "all":
        vmids = ALL_VMIDS
    else:
        labels = [t.strip() for t in target.split(",")]
        vmids = []
        unknown = []
        for lbl in labels:
            v = NODE_TO_VMID.get(lbl)
            if v is None:
                unknown.append(lbl)
            else:
                vmids.append(v)
        if unknown:
            return {"error": f"Unknown node label(s): {unknown}", "known": list(NODE_TO_VMID.keys())}

    results = []
    for vmid in vmids:
        node = next((k for k, v in NODE_TO_VMID.items() if v == vmid), str(vmid))
        log.info("Rolling back VM %s (%s) to snapshot %s", vmid, node, snapshot_name)
        cmd = f"qm rollback {vmid} {shlex.quote(snapshot_name)} --start 1"
        try:
            out = await ssh_run(proxmox_host, cmd, timeout_s=120)
            results.append({"vmid": vmid, "node": node, "ok": True, "out": out.strip()[-200:]})
        except SSHError as e:
            results.append({"vmid": vmid, "node": node, "ok": False, "error": str(e)})
        except Exception as e:
            results.append({"vmid": vmid, "node": node, "ok": False, "error": str(e)})

    success = sum(1 for r in results if r.get("ok"))
    return {
        "status": "complete" if success == len(results) else "partial",
        "scenario": "recover_baseline",
        "snapshot": snapshot_name,
        "target": target,
        "total_vms": len(results),
        "successful": success,
        "failed": len(results) - success,
        "details": results,
        "note": (
            "Cluster needs ~1-2 minutes to fully start splunkd after rollback. "
            "Topology cache will refresh automatically; you may need to recall cluster_health."
        ),
    }

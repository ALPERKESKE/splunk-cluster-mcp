"""SSH subprocess helpers — used by tools that need shell on a node (e.g. tail_log)."""
from __future__ import annotations

import asyncio
import logging
import shlex
from typing import Optional

log = logging.getLogger(__name__)


class SSHError(RuntimeError):
    def __init__(self, returncode: int, stderr: str, cmd: str):
        super().__init__(f"SSH command failed (rc={returncode}): {stderr.strip()[:200]} -- cmd={cmd}")
        self.returncode = returncode
        self.stderr = stderr
        self.cmd = cmd


async def ssh_run(
    host: str,
    remote_cmd: str,
    *,
    timeout_s: int = 20,
    user: Optional[str] = None,
    extra_ssh_args: tuple[str, ...] = (),
) -> str:
    """Run a shell command on `host` via ssh and return stdout (str).

    Relies on ~/.ssh/config aliases (e.g., 'cm1', 'idx1', 'proxmox') and key auth.
    Does NOT add credentials — host must be in ssh config or key-authenticated.

    Args:
        host: hostname or ssh-config alias.
        remote_cmd: command to run remotely (will be shell-quoted by ssh).
        timeout_s: kill after this many seconds.
        user: optional user override (e.g., 'root'). If None, uses ssh config.
        extra_ssh_args: extra options like ('-o', 'BatchMode=yes').

    Raises:
        SSHError on non-zero exit.
        asyncio.TimeoutError if exceeds timeout_s.
    """
    target = f"{user}@{host}" if user else host
    args = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=5",
        "-o", "StrictHostKeyChecking=accept-new",
        *extra_ssh_args,
        target,
        remote_cmd,
    ]
    log.debug("ssh_run: %s", " ".join(shlex.quote(a) for a in args))

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise

    if proc.returncode != 0:
        raise SSHError(
            proc.returncode or -1,
            stderr.decode("utf-8", errors="replace"),
            " ".join(shlex.quote(a) for a in args),
        )
    return stdout.decode("utf-8", errors="replace")

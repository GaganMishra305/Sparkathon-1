"""
Reusable async subprocess runner.

Every scanner adapter calls ``run_command()`` instead of duplicating
process creation, timeout handling, decoding, and cleanup logic.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from typing import Optional

from domain.models import CommandResult

logger = logging.getLogger("command_runner")

# Default limits
DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_MAX_OUTPUT_BYTES = 2_000_000  # 2 MB


async def run_command(
    command: list[str],
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    cwd: Optional[str] = None,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> CommandResult:
    """Execute *command* as a subprocess and return a ``CommandResult``.

    Guarantees:
    - The process is started in a new session so the entire process tree
      can be killed on timeout.
    - Stdout/stderr are capped at *max_output_bytes* to prevent OOM.
    - On timeout the process group is killed and properly reaped.
    - Duration is always recorded.
    """
    started = time.monotonic()

    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        start_new_session=True,
    )

    timed_out = False
    try:
        stdout_raw, stderr_raw = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        timed_out = True
        logger.warning(
            "Command timed out after %ds: %s", timeout_seconds, command
        )
        # Kill the entire process group, then reap
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            # Process already exited or we lack permissions
            process.kill()
        # Always reap to avoid zombies
        stdout_raw, stderr_raw = await process.communicate()

    duration = time.monotonic() - started

    # Cap output size to prevent memory issues
    stdout_text = stdout_raw[:max_output_bytes].decode("utf-8", errors="replace")
    stderr_text = stderr_raw[:max_output_bytes].decode("utf-8", errors="replace")

    return CommandResult(
        command=command,
        return_code=process.returncode,
        stdout=stdout_text,
        stderr=stderr_text,
        duration_seconds=round(duration, 3),
        timed_out=timed_out,
    )

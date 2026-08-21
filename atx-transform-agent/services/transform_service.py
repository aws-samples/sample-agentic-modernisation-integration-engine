"""Transform service — runs ATX CLI via subprocess.Popen with streaming output.

``output.log`` is the durable record the stream endpoint tails, so stdout
de-noising is applied **at write time**: the file holds only lines a human would
want to read, and replay and live views are therefore identical by construction.
Filtering at read time would instead require rebuilding the de-noiser's
progress-repaint state on every poll pass of every reconnecting client, and would
have to strip and re-attach the ``[timestamp] `` prefix to see the raw line.
"""

import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from config import settings
from services.plan_context_defaults import (
    SOURCE_AGENT_DEFAULT,
    default_applied_notice,
    resolve_configuration,
)
from services.stdout_filter import StdoutFilter

logger = logging.getLogger(__name__)

# Track running processes for live streaming
running_processes: dict[str, subprocess.Popen] = {}

# Track transformations this process has work in flight for, from the moment
# `POST /transform` accepts one until its background task returns. Wider than
# `running_processes`, which is empty during the pre-launch clone window.
active_transformations: set[str] = set()


def mark_active(repo_id: str) -> None:
    """Record that this process has work in flight for ``repo_id``."""
    active_transformations.add(repo_id)


def clear_active(repo_id: str) -> None:
    """Record that this process no longer has work in flight for ``repo_id``."""
    active_transformations.discard(repo_id)


def is_tracked(repo_id: str) -> bool:
    """True if this process is doing (or about to do) work for ``repo_id``.

    In-memory by nature, and that is the point: a persisted ``running`` status with
    nothing tracked can only be the remains of a run killed by an agent restart. It is
    liveness only — never a cache of status, which the stream must always read from
    disk.
    """
    return repo_id in active_transformations or is_running(repo_id)


def get_log_path(repo_id: str) -> Path:
    """Get the log file path for a transformation."""
    log_dir = Path(settings.storage_path) / repo_id / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "output.log"


def _write_log_line(log_file, denoise: StdoutFilter, raw: str) -> None:
    """Write one line to ``output.log`` through the de-noising write path.

    The single place a log line is produced, so agent-authored notices and captured CLI
    stdout are shaped identically — same de-noiser, same ``[timestamp] `` prefix — and
    the stream's replay and live views stay identical by construction.
    """
    payload = denoise(raw)
    if payload is None:
        return
    timestamp = datetime.now(timezone.utc).isoformat()
    log_file.write(f"[{timestamp}] {payload}\n")
    log_file.flush()


def build_atx_command(
    transformation_type: str,
    repo_path: Path,
    configuration: str | None = None,
) -> list[str]:
    """Build the ATX CLI command for a transformation.

    ``-g`` is resolved through :func:`services.plan_context_defaults.resolve_configuration`,
    so a definition that cannot run without an ``additionalPlanContext`` gets the agent's
    default here — at the one place the flag is ever assembled — rather than depending on
    every caller to remember. A supplied ``configuration`` is passed through untouched.

    Args:
        transformation_type: The ATX transformation definition name.
        repo_path: Path to the repository.
        configuration: Optional configuration for ATX CLI -g flag.

    Returns:
        Command as a list of strings.
    """
    configuration = resolve_configuration(transformation_type, configuration).value
    cmd = [
        settings.atx_cli_path,
        "custom",
        "def",
        "exec",
        "-n",
        transformation_type,
        "-p",
        str(repo_path),
        "-x",
        "-t",
    ]
    if configuration:
        cmd.extend(["-g", configuration])
    return cmd


def run_transformation(
    repo_id: str,
    transformation_type: str,
    repo_path: Path,
    configuration: str | None = None,
) -> int:
    """Run ATX CLI transformation with streaming output via subprocess.Popen.

    Writes de-noised stdout/stderr line-by-line to the log file with ISO
    timestamps (``[2026-08-03T11:38:55.899505+00:00] line content``).
    Tracks the running process in `running_processes` dict.

    Args:
        repo_id: Unique identifier for this transformation.
        transformation_type: ATX transformation definition name.
        repo_path: Path to the cloned repository.
        configuration: Optional configuration for ATX CLI -g flag.

    Returns:
        The ATX CLI process exit code.
    """
    log_path = get_log_path(repo_id)
    resolved = resolve_configuration(transformation_type, configuration)
    cmd = build_atx_command(transformation_type, repo_path, resolved.value)

    logger.info(f"Starting transformation {repo_id}: {' '.join(cmd)}")
    if resolved.source == SOURCE_AGENT_DEFAULT:
        logger.info(f"Transformation {repo_id} using default configuration: {resolved.value}")

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(repo_path),
        )
        running_processes[repo_id] = process

        # Same CLI, same noise as the analysis agent — see services/stdout_filter.py.
        denoise = StdoutFilter()
        with open(log_path, "w") as log_file:
            # An applied default is announced in the transformation's own log, before
            # any CLI output, so the target version the run actually used is visible in
            # the console the user is already watching rather than being silent.
            if resolved.source == SOURCE_AGENT_DEFAULT and resolved.value:
                _write_log_line(log_file, denoise, default_applied_notice(transformation_type, resolved.value))

            for line in iter(process.stdout.readline, ""):
                _write_log_line(log_file, denoise, line.rstrip("\n"))

        process.wait()
        exit_code = process.returncode

        # Write final status
        timestamp = datetime.now(timezone.utc).isoformat()
        with open(log_path, "a") as log_file:
            if exit_code == 0:
                log_file.write(f"[{timestamp}] Transformation completed successfully (exit code 0)\n")
            else:
                log_file.write(f"[{timestamp}] Transformation failed (exit code {exit_code})\n")

        return exit_code

    except Exception as e:
        timestamp = datetime.now(timezone.utc).isoformat()
        with open(log_path, "a") as log_file:
            log_file.write(f"[{timestamp}] ERROR: {e}\n")
        raise
    finally:
        running_processes.pop(repo_id, None)


def is_running(repo_id: str) -> bool:
    """Check if a transformation is still running."""
    proc = running_processes.get(repo_id)
    if proc is None:
        return False
    return proc.poll() is None

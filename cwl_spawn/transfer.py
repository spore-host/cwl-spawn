"""S3 workdir bridge: stage a CWL step's command + working directory up to S3
and pull results back down.

cwltool is local-filesystem based: it lays out a job directory (outdir + a
staged input tree via its PathMapper) and expects the command to run against
those local paths, then collects outputs from the local outdir. This module
builds the (pure) S3 keys and ``aws s3`` argv that move those bytes between the
local job dir, S3, and the ephemeral instance. The thin subprocess calls live in
job.py; everything here is pure and unit-tested without AWS.
"""

from __future__ import annotations

import shlex
from typing import Mapping


def step_s3_prefix(base: str, run_id: str, try_counter: int) -> str:
    """S3 prefix for one step attempt. ``try-N`` isolates retries so a stale
    attempt's .exitcode is never read as the next attempt's."""
    return f"{base.rstrip('/')}/{run_id}/try-{try_counter}"


def build_command_file_contents(command: str, env: Mapping[str, str]) -> str:
    """Contents of the ``command`` file: env exports first, then the command body."""
    lines = [f"export {k}={shlex.quote(str(v))}\n" for k, v in (env or {}).items()]
    body = command if command.endswith("\n") else command + "\n"
    return "".join(lines) + body


def build_upload_command_argv(local_command_file: str, s3_prefix: str, region: str) -> list[str]:
    """``aws s3 cp <local command> <prefix>/command``. Pure."""
    return [
        "aws", "s3", "cp", local_command_file, f"{s3_prefix}/command",
        "--region", region or "us-east-1", "--quiet",
    ]


def build_upload_work_argv(local_work_dir: str, s3_prefix: str, region: str) -> list[str]:
    """``aws s3 sync <local work dir> <prefix>/work`` (recursive, incl. staged
    inputs). Pure."""
    return [
        "aws", "s3", "sync", local_work_dir, f"{s3_prefix}/work",
        "--region", region or "us-east-1", "--quiet",
    ]


def build_download_results_argv(
    s3_prefix: str,
    local_work_dir: str,
    local_stdout_txt: str,
    local_stderr_txt: str,
    region: str,
) -> list[list[str]]:
    """Argv list to pull results back into the (try-aware) local job paths: sync
    work/ down, then cp stdout.txt / stderr.txt. No ``--delete`` (don't risk the
    local tree). Pure."""
    r = region or "us-east-1"
    return [
        ["aws", "s3", "sync", f"{s3_prefix}/work", local_work_dir, "--region", r, "--quiet"],
        ["aws", "s3", "cp", f"{s3_prefix}/stdout.txt", local_stdout_txt, "--region", r, "--quiet"],
        ["aws", "s3", "cp", f"{s3_prefix}/stderr.txt", local_stderr_txt, "--region", r, "--quiet"],
    ]

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


def step_s3_prefix(base: str, run_id: str, try_counter: int) -> str:
    """S3 prefix for one step attempt. ``try-N`` isolates retries so a stale
    attempt's results are never read as the next attempt's."""
    return f"{base.rstrip('/')}/{run_id}/try-{try_counter}"


def build_upload_work_argv(local_work_dir: str, s3_prefix: str, region: str) -> list[str]:
    """``aws s3 sync <local work dir> <prefix>/work`` (recursive, incl. staged
    inputs). Pure. The up-half of the bridge between cwltool's local tree and the
    S3 work prefix ``spawn task run`` stages from."""
    return [
        "aws", "s3", "sync", local_work_dir, f"{s3_prefix}/work",
        "--region", region or "us-east-1", "--quiet",
    ]


def build_download_work_argv(work_s3: str, local_work_dir: str, region: str) -> list[str]:
    """``aws s3 sync <prefix>/work <local outdir>`` — the down-half: pull the
    step's result tree (spawn synced it up, including CWL stdout/stderr redirect
    files) back into the local outdir for cwltool to collect. No ``--delete``.
    Pure."""
    return [
        "aws", "s3", "sync", work_s3, local_work_dir,
        "--region", region or "us-east-1", "--quiet",
    ]

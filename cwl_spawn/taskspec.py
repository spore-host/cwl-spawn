"""Build a spawn TaskSpec for one CWL step and parse its CompletionRecord.

cwl-spawn no longer orchestrates launch/staging/completion itself — it shells out
to ``spawn task run``, which owns S3 staging, the container run, sizing (truffle),
the scoped IAM profile, and the durable completion record. This module is the
translation layer: it maps a cwltool ``CommandLineJob``'s fields to the TaskSpec
JSON shape spawn expects, and reads the CompletionRecord back. Pure (no I/O, no
AWS), unit-tested without a cluster.

TaskSpec contract (spore-host/spawn pkg/taskproto): {task_id, command []string,
container?, resources{cpu,memory_gib,gpus,architecture,families,...},
inputs[]{source,destination}, outputs[]{source,destination},
lifecycle{ttl,on_complete}, env{}}. Manifests copy s3://<->local; a trailing
slash on the source means recursive.
"""

from __future__ import annotations

import re
import shlex
from typing import Mapping, Optional

# A valid shell identifier for an env var name. spawn re-validates env keys and
# hard-fails the spec on an invalid one, so we drop non-identifier keys here.
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Family prefix of an instance type, e.g. "c7i" from "c7i.4xlarge".
_FAMILY_RE = re.compile(r"^([a-z][a-z0-9]*?[0-9]+[a-z]*)\.")


def build_command_string(
    command_line: list,
    stdin: Optional[str] = None,
    stdout: Optional[str] = None,
    stderr: Optional[str] = None,
) -> str:
    """Turn cwltool's ``job.command_line`` argv + stdin/stdout/stderr redirects
    into a single shell command string. Pure.

    Each token is shell-quoted; redirections are appended as ``< / > / 2>`` so the
    step's declared streams land in files inside the work dir, exactly as cwltool
    expects to collect them. (Migrated from the old staging.py — the redirect
    semantics are unchanged; it now feeds the ``bash -lc`` inner string.)
    """
    line = " ".join(shlex.quote(str(a)) for a in command_line)
    if stdin:
        line += f" < {shlex.quote(stdin)}"
    if stdout:
        line += f" > {shlex.quote(stdout)}"
    if stderr:
        line += f" 2> {shlex.quote(stderr)}"
    return line


def mib_to_gib(ram_mib: object) -> Optional[float]:
    """Coerce cwltool's ``builder.resources['ram']`` (MiB) to GiB, or None if
    missing/unparseable/non-positive. (Migrated from the old sizing.py.)"""
    if ram_mib is None:
        return None
    try:
        val = float(ram_mib)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if val <= 0:
        return None
    return val / 1024.0


def instance_type_family(instance_type: Optional[str]) -> Optional[str]:
    """Extract the family prefix from an instance type ("c7i" from
    "c7i.4xlarge"), or None if it doesn't look like one. Used to map the CWL
    ``spawn:instanceType`` hint onto TaskSpec ``resources.families`` — spawn has
    no exact instance-type pin, so the hint steers the family, and spawn's sizer
    picks the cheapest fit within it. Lossy: it does NOT pin the exact size."""
    if not instance_type:
        return None
    m = _FAMILY_RE.match(instance_type.strip())
    return m.group(1) if m else None


def clean_env(environment: Optional[Mapping[str, str]]) -> dict:
    """Keep only env entries whose key is a valid shell identifier (spawn rejects
    the spec otherwise). Values are unrestricted."""
    if not environment:
        return {}
    return {k: str(v) for k, v in environment.items() if _ENV_KEY_RE.match(k)}


def _lifecycle(ttl: str, on_complete: str, cost_limit: Optional[float]) -> dict:
    """The lifecycle block, with cost_limit included only when set (#12).

    TTL bounds a step in TIME, not money, and spored enforces the two
    INDEPENDENTLY — first limit to fire wins — so a cost cap is a genuine second
    belt. Without it the only ceiling is the TTL, defaulting to 4h, so a workflow
    running N steps has a worst case of N x 4h x the instance rate.

    The failure it catches is a step that HANGS rather than fails: it produces no
    error for cwltool to retry or abort on, so it bills until the TTL expires.
    Omitted when unset so spawn's own default still applies.
    """
    lifecycle: dict = {"ttl": ttl, "on_complete": on_complete}
    if cost_limit is not None and float(cost_limit) > 0:
        lifecycle["cost_limit"] = float(cost_limit)
    return lifecycle


def build_task_spec(
    *,
    task_id: str,
    command_line: list,
    stdin: Optional[str],
    stdout: Optional[str],
    stderr: Optional[str],
    environment: Optional[Mapping[str, str]],
    work_s3_uri: str,
    job_dir: str,
    docker_image: str = "",
    cores: Optional[int] = None,
    ram_mib: object = None,
    architecture: Optional[str] = None,
    instance_hint: Optional[str] = None,
    ttl: str = "4h",
    on_complete: str = "terminate",
    cost_limit: Optional[float] = None,
) -> dict:
    """Build the TaskSpec dict for one CWL step. Pure.

    ``work_s3_uri`` is the per-step S3 work prefix (cwl-spawn uploads the staged
    outdir there before launch, and pulls results from there after); it is
    identity-mounted into the instance at ``job_dir`` so absolute paths cwltool
    baked into the command resolve. The command is wrapped in ``bash -lc 'cd
    <job_dir> && <inner>'`` because TaskSpec.command is argv with no shell (spawn
    runs it verbatim, host or in-container), and there is no working-directory
    field — the ``cd`` supplies cwltool's expected cwd.
    """
    work_src = work_s3_uri if work_s3_uri.endswith("/") else work_s3_uri + "/"
    job_work = job_dir.rstrip("/")

    inner = build_command_string(command_line, stdin, stdout, stderr)
    command = ["/bin/bash", "-lc", f"cd {shlex.quote(job_work)} && {inner}"]

    resources: dict = {}
    if cores and int(cores) > 0:
        resources["cpu"] = int(cores)
    mem_gib = mib_to_gib(ram_mib)
    if mem_gib is not None:
        resources["memory_gib"] = mem_gib
    if architecture:
        resources["architecture"] = architecture
    fam = instance_type_family(instance_hint)
    if fam:
        resources["families"] = [fam]

    spec: dict = {
        "task_id": task_id,
        "command": command,
        "resources": resources,
        "inputs": [{"source": work_src, "destination": job_work}],
        "outputs": [{"source": job_work + "/", "destination": work_src}],
        "lifecycle": _lifecycle(ttl, on_complete, cost_limit),
    }
    if docker_image.strip():
        spec["container"] = docker_image.strip()
    env = clean_env(environment)
    if env:
        spec["env"] = env
    return spec


# ---- completion, from `spawn task status --check-complete` / -o json ----------

def check_complete_to_status(returncode: int) -> Optional[str]:
    """Map ``spawn task status --check-complete`` exit code to a status.

    spawn's contract: 0=completed, 1=failed, 2=running, 3=error. Returns
    "completed"/"failed" on 0/1, None on 2 (not done — poll again), and RAISES on
    3 (spawn couldn't determine status — an error, not a task outcome) or any
    unrecognized code (a contract change we want to hear about loudly)."""
    if returncode == 0:
        return "completed"
    if returncode == 1:
        return "failed"
    if returncode == 2:
        return None
    raise RuntimeError(f"`spawn task status --check-complete` returned error/unknown code {returncode}")


def parse_completion_record(stdout: str) -> dict:
    """Parse the CompletionRecord JSON emitted by ``spawn task run --wait -o json``
    (or ``spawn task status <id> -o json``). Returns the dict; raises on invalid
    JSON. Callers read ``exit_code`` (int) and ``state`` ("completed"/"failed")."""
    import json

    rec = json.loads(stdout)
    if not isinstance(rec, dict):
        raise RuntimeError("completion record is not a JSON object")
    return rec

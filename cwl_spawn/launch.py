"""Build the ``spawn launch`` / ``spawn terminate`` argv for a CWL step.

Ported verbatim from miniwdl-spawn (which ported it from nf-spawn): the spawn CLI
contract is engine-agnostic. ``--on-complete terminate`` (instance self-destructs
when the step signals done), ``--user-data-file`` (NOT ``--user-data``), and the
non-blocking ``--wait-for-*=false`` + ``-y`` flags so launch returns immediately
and completion is polled out-of-band via the durable ``.exitcode`` in S3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class LaunchSpec:
    """Inputs for one step's ``spawn launch`` invocation."""

    name: str
    instance_type: str
    region: str
    user_data_file: str
    ttl: str = "4h"
    spot: bool = False
    ami: str = ""
    volume_size: int = 0
    az: str = ""
    fsx_id: str = ""
    fsx_mount_point: str = ""
    # EBS snapshot mounts, each "snap-...:/mount[:ro|rw]" (already formatted).
    attach_volumes: list[str] = field(default_factory=list)
    # Service-level IAM policies for the instance role (spawn --iam-policy). The
    # default spored role grants only spawn-internal buckets, so the step instance
    # needs S3 access to read/write the SPAWN_WORKDIR_S3 bridge bucket.
    iam_policy: list[str] = field(default_factory=lambda: ["s3:FullAccess"])


def build_launch_argv(spec: LaunchSpec) -> list[str]:
    """Build the ``spawn launch`` argv. Pure."""
    argv = [
        "spawn",
        "launch",
        spec.name,
        "--instance-type",
        spec.instance_type,
        "--region",
        spec.region,
        "--ttl",
        spec.ttl,
        "--on-complete",
        "terminate",
        "--user-data-file",
        spec.user_data_file,
        "--wait-for-running=false",
        "--wait-for-ssh=false",
        "-y",
    ]
    if spec.ami:
        argv += ["--ami", spec.ami]
    if spec.volume_size and spec.volume_size > 0:
        argv += ["--volume-size", str(spec.volume_size)]
    for v in spec.attach_volumes or []:
        argv += ["--attach-volume", v]
    for p in spec.iam_policy or []:
        argv += ["--iam-policy", p]
    if spec.az:
        argv += ["--az", spec.az]
    if spec.fsx_id:
        argv += ["--fsx-id", spec.fsx_id]
        if spec.fsx_mount_point:
            argv += ["--fsx-mount-point", spec.fsx_mount_point]
    if spec.spot:
        argv += ["--spot"]
    return argv


def build_cancel_argv(name: str, region: Optional[str]) -> list[str]:
    """Build ``spawn terminate <name> --region <region> --yes``.

    ``spawn terminate`` is the single-instance teardown (by name or id); ``spawn
    cancel`` is for parameter *sweeps*, not one instance. Always include --region
    (a region-less teardown can silently leak a billable instance) and --yes.
    """
    argv = ["spawn", "terminate", name]
    if region:
        argv += ["--region", region]
    argv += ["--yes"]
    return argv

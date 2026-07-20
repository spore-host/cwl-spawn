"""SpawnJob: a cwltool CommandLineJob that runs one step on a spawn instance.

Overrides ``CommandLineJob.run`` — the cwltool "run one step" seam — to dispatch
one step to an ephemeral EC2 instance via ``spawn task run`` instead of running
it locally. spawn owns sizing (truffle), the scoped IAM profile, S3 staging, the
container run, and the durable completion record; cwl-spawn's job is only to
(1) upload cwltool's staged local outdir to an S3 work prefix, (2) build a
TaskSpec (see ``taskspec.build_task_spec``), (3) ``spawn task run --spec … --wait
-o json`` and read the CompletionRecord, (4) pull results back into the outdir so
cwltool collects outputs. The pure building blocks (``taskspec``, ``transfer``)
are unit-tested without AWS.

Config comes from env: ``SPAWN_WORKDIR_S3`` (required — the S3 bridge prefix),
``SPAWN_REGION`` (default us-east-1), ``SPAWN_TTL`` (default 4h),
``SPAWN_POLL_INTERVAL`` (seconds, default 15 — the ``spawn task run`` poll
cadence).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
from typing import TYPE_CHECKING, Optional

from cwltool.job import CommandLineJob

from . import taskspec, transfer

if TYPE_CHECKING:
    from cwltool.context import RuntimeContext

logger = logging.getLogger("cwl-spawn")

_NAME_SANITIZE = re.compile(r"[^a-z0-9-]+")

# The on-instance work dir the staged tree is mounted at. spawn bind-mounts the
# parent of each manifest path into the container, and identity-mounts it, so the
# command's cwd (set via `cd` in the wrapped bash -lc) resolves the same on the
# host and inside a DockerRequirement image.
#
# Must live under a dir the *unprivileged* command user can create: spawn runs the
# task command as the instance's login user (`su - <user>`), not root, so the
# wrapper's `mkdir -p` runs unprivileged. `/var/tmp` is world-writable (1777) and
# disk-backed on the EBS root volume — unlike `/tmp` (can be tmpfs/RAM) and unlike
# root-owned dirs such as `/mnt` (0755 root, mkdir → Permission denied).
_JOB_DIR = "/var/tmp/cwl_spawn_job/work"


def _cfg(name: str, default: str) -> str:
    val = os.environ.get(name)
    return val if val else default


class SpawnJob(CommandLineJob):
    """Run one CWL CommandLineTool step on an ephemeral EC2 instance via spawn."""

    def run(
        self,
        runtimeContext: "RuntimeContext",
        tmpdir_lock: Optional[threading.Lock] = None,
    ) -> None:
        # Reproduce cwltool's run() preamble: ensure tmpdir + stage inputs/initial
        # workdir locally, so self.outdir holds the staged input tree we upload.
        if tmpdir_lock:
            with tmpdir_lock:
                if not os.path.exists(self.tmpdir):
                    os.makedirs(self.tmpdir)
        elif not os.path.exists(self.tmpdir):
            os.makedirs(self.tmpdir)
        self._setup(runtimeContext)
        self._stage_local(runtimeContext)

        workdir_s3 = _cfg("SPAWN_WORKDIR_S3", "")
        if not workdir_s3:
            raise RuntimeError(
                "cwl-spawn: no S3 workdir configured. Set SPAWN_WORKDIR_S3 to an "
                "s3:// prefix the run can read/write."
            )
        if shutil.which("aws") is None:
            raise RuntimeError("cwl-spawn: the `aws` CLI is required on PATH for S3 staging.")

        if shutil.which("spawn") is None:
            raise RuntimeError("cwl-spawn: the `spawn` CLI is required on PATH.")

        region = _cfg("SPAWN_REGION", "us-east-1")
        ttl = _cfg("SPAWN_TTL", "4h")
        poll = float(_cfg("SPAWN_POLL_INTERVAL", "15"))
        run_id = self._run_id()
        task_id = ("cwl-" + run_id).replace("_", "-")[:60]
        s3_prefix = transfer.step_s3_prefix(workdir_s3, run_id, 1)
        work_s3 = f"{s3_prefix}/work"

        # 1. Upload the staged outdir (inputs + initial work dir) to the S3 work
        #    prefix. spawn's task runner stages it back down onto the instance,
        #    runs the step, and syncs results here — we pull them below.
        self._run_argv(transfer.build_upload_work_argv(self.outdir, s3_prefix, region), check=True)

        # 2. Build the TaskSpec and hand it to `spawn task run`, which owns
        #    sizing (truffle), the scoped IAM profile, staging, the container run
        #    (docker install + docker run for a DockerRequirement image), and the
        #    durable completion record. cwl-spawn no longer builds a staging
        #    script or calls `spawn launch` directly.
        spec = taskspec.build_task_spec(
            task_id=task_id,
            command_line=[str(a) for a in self.command_line],
            stdin=self.stdin,
            stdout=self.stdout,
            stderr=self.stderr,
            environment=dict(self.environment or {}),
            work_s3_uri=work_s3,
            job_dir=_JOB_DIR,
            docker_image=self._docker_image(),
            cores=self._resource("cores"),
            ram_mib=self._resource("ram"),
            instance_hint=self._instance_hint(),
            ttl=ttl,
            on_complete="terminate",
        )
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", prefix=f"cwl-spawn-{task_id}-", delete=False
        ) as fh:
            json.dump(spec, fh)
            spec_file = fh.name

        processStatus = "permanentFail"
        rcode = 1
        try:
            logger.info("cwl-spawn: dispatching %s via `spawn task run`", task_id)
            # One-shot: launch, wait, and emit the CompletionRecord as JSON
            # (spawn#386 — `task run --wait -o json`). --poll-interval matches the
            # engine's configured cadence. `spawn` exits non-zero when the task
            # itself failed; we still parse the record for the real exit code.
            out = self._run_argv(
                [
                    "spawn", "task", "run",
                    "--spec", spec_file,
                    "--region", region,
                    "--wait",
                    "--poll-interval", f"{int(poll)}s",
                    "-o", "json",
                ],
                check=False,
            )
            rec = self._parse_completion(out.stdout, task_id)
            if rec is None:
                raise RuntimeError(
                    f"cwl-spawn: could not parse completion record for {task_id}; "
                    f"spawn exit {out.returncode}, stderr: {(out.stderr or '').strip()[:500]}"
                )
            rcode = int(rec.get("exit_code", 1))

            # 3. Pull results from the S3 work prefix back into the local outdir so
            #    cwltool collects outputs (CWL redirects wrote into the work tree).
            self._pull_results(work_s3, region)

            processStatus = "success" if rcode in (list(self.successCodes) + [0]) else "permanentFail"
        finally:
            try:
                os.unlink(spec_file)
            except OSError:
                pass

        # 4. Collect outputs from the pulled-back outdir and fire cwltool's callback.
        outputs: dict = {}
        try:
            outputs = dict(self.collect_outputs(self.outdir, rcode))  # type: ignore[misc]
        except Exception:
            logger.exception("cwl-spawn: output collection failed for %s", task_id)
            processStatus = "permanentFail"
        if self.output_callback:
            self.output_callback(outputs, processStatus)

    # ---- helpers ---------------------------------------------------------

    def _stage_local(self, runtimeContext: "RuntimeContext") -> None:
        """Stage the step's input files + initial work dir into the local job dir,
        exactly as cwltool's CommandLineJob.run does, so the uploaded outdir holds
        every input the command references."""
        from cwltool.job import relink_initialworkdir, stage_files

        stage_files(
            self.pathmapper,
            ignore_writable=True,
            symlink=True,
            secret_store=runtimeContext.secret_store,
        )
        if self.generatemapper is not None:
            stage_files(
                self.generatemapper,
                ignore_writable=self.inplace_update,
                symlink=True,
                secret_store=runtimeContext.secret_store,
            )
            relink_initialworkdir(
                self.generatemapper,
                self.outdir,
                self.builder.outdir,
                inplace_update=self.inplace_update,
            )

    def _run_id(self) -> str:
        """A filesystem/S3/EC2-name-safe id for this step attempt."""
        base = (self.name or "step").lower()
        base = _NAME_SANITIZE.sub("-", base).strip("-") or "step"
        return base

    def _docker_image(self) -> str:
        req, _ = self.get_requirement("DockerRequirement")
        if req:
            return str(req.get("dockerPull", "") or "")
        return ""

    def _resource(self, key: str):  # type: ignore[no-untyped-def]
        """Read a value from cwltool's evaluated builder.resources (cores/ram)."""
        res = getattr(self.builder, "resources", {}) or {}
        return res.get(key)

    def _instance_hint(self) -> Optional[str]:
        """The optional spore.host InstanceType hint on the step, if any. spawn
        has no exact instance-type pin, so build_task_spec maps this to a family
        allow-list (see taskspec.instance_type_family)."""
        hint, _ = self.get_requirement("http://spore.host/cwl#InstanceType")
        return str(hint.get("instanceType", "")) if hint else None

    def _parse_completion(self, stdout: Optional[str], task_id: str) -> Optional[dict]:
        """Parse the CompletionRecord JSON from `spawn task run --wait -o json`.
        Returns None (caller raises) if stdout isn't parseable."""
        if not stdout or not stdout.strip():
            return None
        try:
            return taskspec.parse_completion_record(stdout)
        except Exception:
            logger.exception("cwl-spawn: could not parse completion record for %s", task_id)
            return None

    def _pull_results(self, work_s3: str, region: str) -> None:
        """Sync the step's result tree from the S3 work prefix back into the local
        outdir, so cwltool collects outputs. CWL stdout/stderr redirects wrote
        into the work tree (spawn synced it up), so a single work-dir sync
        suffices — no separate stdout.txt/stderr.txt objects."""
        self._run_argv(
            transfer.build_download_work_argv(work_s3, self.outdir, region), check=False
        )

    def _run_argv(self, argv, check):  # type: ignore[no-untyped-def]
        return subprocess.run(argv, check=check, capture_output=True, text=True)

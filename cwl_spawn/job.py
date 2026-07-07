"""SpawnJob: a cwltool CommandLineJob that runs one step on a spawn instance.

Overrides ``CommandLineJob.run`` — the cwltool "run one step" seam — to dispatch
to an ephemeral EC2 instance through the ``spawn`` CLI instead of running locally,
then poll a durable ``.exitcode`` object in S3 for completion. Mirrors
miniwdl-spawn's ``SpawnContainer._run``; the pure building blocks (launch,
transfer, completion, sizing, staging) are shared in spirit and unit-tested
without AWS.

Config comes from env (matching miniwdl-spawn): ``SPAWN_WORKDIR_S3`` (required —
the S3 bridge prefix), ``SPAWN_REGION`` (default us-east-1), ``SPAWN_TTL``
(default 4h), ``SPAWN_POLL_INTERVAL`` (seconds, default 15).
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from typing import TYPE_CHECKING, Optional

from cwltool.job import CommandLineJob

from . import completion, launch, sizing, staging, transfer

if TYPE_CHECKING:
    from cwltool.context import RuntimeContext

logger = logging.getLogger("cwl-spawn")

_NAME_SANITIZE = re.compile(r"[^a-z0-9-]+")


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

        region = _cfg("SPAWN_REGION", "us-east-1")
        ttl = _cfg("SPAWN_TTL", "4h")
        poll = float(_cfg("SPAWN_POLL_INTERVAL", "15"))
        run_id = self._run_id()
        name = ("cwl-" + run_id).replace("_", "-")[:60]
        s3_prefix = transfer.step_s3_prefix(workdir_s3, run_id, 1)

        # 1. Write the command file (the step's argv + redirects) into outdir, then
        #    upload command + the whole outdir (staged inputs included) to S3.
        command_str = staging.build_command_string(
            [str(a) for a in self.command_line],
            stdin=self.stdin,
            stdout=self.stdout,
            stderr=self.stderr,
        )
        command_file = os.path.join(self.outdir, ".cwl_spawn_command")
        with open(command_file, "w") as f:
            f.write(transfer.build_command_file_contents(command_str, dict(self.environment or {})))

        self._run_argv(
            transfer.build_upload_command_argv(command_file, s3_prefix, region), check=True
        )
        self._run_argv(transfer.build_upload_work_argv(self.outdir, s3_prefix, region), check=True)

        # 2. Build the per-step staging script + launch spec.
        docker_image = self._docker_image()
        script = staging.build_staging_script(
            workdir_s3=s3_prefix,
            region=region,
            docker_image=docker_image,
            setup=staging.build_setup_script(docker_image),
        )
        spec = launch.LaunchSpec(
            name=name,
            instance_type=self._resolve_instance_type(),
            region=region,
            user_data_file="",  # set below
            ttl=ttl,
        )
        with tempfile.NamedTemporaryFile(
            "w", suffix=".sh", prefix=f"cwl-spawn-{name}-", delete=False
        ) as fh:
            fh.write(script)
            spec.user_data_file = fh.name

        processStatus = "permanentFail"
        rcode = 1
        try:
            logger.info("cwl-spawn: launching %s (%s)", name, spec.instance_type)
            self._run_argv(launch.build_launch_argv(spec), check=True)

            # 3. Poll the durable .exitcode object; pull results on completion.
            probe = completion.build_exitcode_probe_argv(s3_prefix, region)
            while True:
                out = self._run_argv(probe, check=False)
                if out.returncode == 0:
                    code = completion.parse_exit_code(out.stdout)
                    if code is not None:
                        self._pull_results(s3_prefix, region)
                        rcode = code
                        break
                time.sleep(poll)

            processStatus = "success" if rcode in (list(self.successCodes) + [0]) else "permanentFail"
        finally:
            try:
                os.unlink(spec.user_data_file)
            except OSError:
                pass

        # 4. Collect outputs from the pulled-back outdir and fire cwltool's callback.
        outputs: dict = {}
        try:
            outputs = dict(self.collect_outputs(self.outdir, rcode))  # type: ignore[misc]
        except Exception:
            logger.exception("cwl-spawn: output collection failed for %s", name)
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

    def _resolve_instance_type(self) -> str:
        res = getattr(self.builder, "resources", {}) or {}
        hint, _ = self.get_requirement("http://spore.host/cwl#InstanceType")
        override = str(hint.get("instanceType", "")) if hint else ""
        return sizing.pick_instance_type(
            override=override or None,
            cores=res.get("cores"),
            ram_mib=res.get("ram"),
        )

    def _pull_results(self, s3_prefix: str, region: str) -> None:
        stdout_txt = os.path.join(self.outdir, "stdout.txt")
        stderr_txt = os.path.join(self.outdir, "stderr.txt")
        for argv in transfer.build_download_results_argv(
            s3_prefix, self.outdir, stdout_txt, stderr_txt, region
        ):
            self._run_argv(argv, check=False)

    def _run_argv(self, argv, check):  # type: ignore[no-untyped-def]
        return subprocess.run(argv, check=check, capture_output=True, text=True)

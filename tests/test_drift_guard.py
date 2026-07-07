"""Drift guard: cwl-spawn subclasses cwltool's INTERNAL job-runner classes, which
cwltool versions by date with no semver contract. These tests fail loudly if a
cwltool bump moves the seam — bump the pin deliberately, re-verify, update here.
See CLAUDE.md.
"""

import inspect

from cwltool.command_line_tool import CommandLineTool
from cwltool.job import CommandLineJob, JobBase


def test_commandlinejob_run_is_the_seam():
    # We override CommandLineJob.run(runtimeContext, tmpdir_lock=None).
    assert issubclass(CommandLineJob, JobBase)
    sig = inspect.signature(CommandLineJob.run)
    params = list(sig.parameters)
    assert params[0] == "self"
    assert "runtimeContext" in params
    # tmpdir_lock is the optional second arg we forward.
    assert "tmpdir_lock" in params


def test_commandlinetool_make_job_runner_is_overridable():
    # We override make_job_runner to return SpawnJob.
    assert hasattr(CommandLineTool, "make_job_runner")
    sig = inspect.signature(CommandLineTool.make_job_runner)
    assert "runtimeContext" in sig.parameters


def test_jobbase_carries_the_fields_we_read():
    # Fields SpawnJob.run reads off the job instance. If cwltool renames any of
    # these, dispatch breaks — catch it here, not in a live run.
    src = inspect.getsource(JobBase.__init__)
    for field in (
        "command_line",
        "pathmapper",
        "generatemapper",
        "outdir",
        "stdin",
        "stdout",
        "stderr",
        "environment",
        "successCodes",
        "collect_outputs",
        "output_callback",
    ):
        assert f"self.{field}" in src, f"JobBase no longer sets self.{field}"


def test_resources_keys_for_sizing():
    # SpawnJob._resolve_instance_type reads builder.resources['cores'/'ram'].
    # cwltool populates these in process.py; assert the names still exist there.
    from cwltool import process

    src = inspect.getsource(process)
    assert '"cores"' in src and '"ram"' in src

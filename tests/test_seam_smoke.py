"""Offline seam smoke test: run examples/hello.cwl through cwltool with the
cwl-spawn factory, faking the spawn/aws subprocesses, and assert that
(a) a SpawnJob was constructed for the CommandLineTool step, and (b) the step was
dispatched via `spawn task run` (not `spawn launch`, not run locally). No AWS, no
real instance.
"""

import os

import pytest

from cwl_spawn import job as job_mod
from cwl_spawn.tool import SpawnCommandLineTool, make_spawn_tool


def test_make_spawn_tool_returns_spawn_tool_for_commandlinetool():
    # A minimal CommandLineTool dict routes to SpawnCommandLineTool; other classes
    # fall through to cwltool's default.
    from cwltool.context import LoadingContext
    from ruamel.yaml.comments import CommentedMap

    lc = LoadingContext()
    clt = CommentedMap({"class": "CommandLineTool", "cwlVersion": "v1.2",
                        "inputs": [], "outputs": [], "baseCommand": "true"})
    tool = make_spawn_tool(clt, lc)
    assert isinstance(tool, SpawnCommandLineTool)
    assert tool.make_job_runner(object()).__name__ == "SpawnJob"  # type: ignore[arg-type]


@pytest.mark.skipif(
    os.environ.get("CWL_SPAWN_SKIP_SEAM") == "1", reason="explicitly skipped"
)
def test_hello_cwl_dispatches_via_spawn(monkeypatch, tmp_path):
    """End-to-end through cwltool, but every external command is faked. Proves the
    step reaches SpawnJob.run and dispatches via `spawn task run` (one-shot
    --wait -o json), and that a faked CompletionRecord (exit_code 0) drives it to
    a success callback with the declared output collected."""
    calls: list[list[str]] = []
    dispatched = {"task_run": False}

    class FakeCompleted:
        def __init__(self, rc=0, out=""):
            self.returncode = rc
            self.stdout = out
            self.stderr = ""

    def fake_run(argv, check=False, capture_output=False, text=False, **kw):
        calls.append(list(argv))
        prog = argv[0]
        # `spawn task run --spec … --wait -o json` → emit a CompletionRecord.
        if prog == "spawn" and argv[1:3] == ["task", "run"]:
            dispatched["task_run"] = True
            return FakeCompleted(
                0,
                '{"task_id":"cwl-hello","exit_code":0,"state":"completed",'
                '"started_at":"2026-07-19T00:00:00Z","ended_at":"2026-07-19T00:00:05Z"}',
            )
        # It must NOT use the old low-level `spawn launch`.
        if prog == "spawn" and argv[1:2] == ["launch"]:
            raise AssertionError("cwl-spawn must dispatch via `spawn task run`, not `spawn launch`")
        if prog == "aws" and argv[1:3] == ["s3", "sync"]:
            # Results pull: dest (argv[4]) is the job outdir; seed the declared
            # stdout output so cwltool's collection (glob greeting.txt) succeeds.
            dest = argv[4]
            if os.path.isdir(dest):
                with open(os.path.join(dest, "greeting.txt"), "w") as gf:
                    gf.write("hello, spore\n")
            return FakeCompleted(0, "")
        # every other aws/spawn call: succeed quietly
        return FakeCompleted(0, "")

    # Patch the subprocess the job uses + the PATH checks (aws + spawn present).
    monkeypatch.setattr(job_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(job_mod.shutil, "which", lambda _p: f"/usr/bin/{_p}")
    monkeypatch.setenv("SPAWN_WORKDIR_S3", "s3://throwaway/cwl-runs")
    monkeypatch.setenv("SPAWN_REGION", "us-east-1")
    monkeypatch.setenv("SPAWN_POLL_INTERVAL", "0")

    here = os.path.dirname(os.path.dirname(__file__))
    hello = os.path.join(here, "examples", "hello.cwl")

    from cwltool import main as cwlmain
    from cwltool.context import LoadingContext, RuntimeContext

    lc = LoadingContext()
    lc.construct_tool_object = make_spawn_tool
    rc = RuntimeContext()
    rc.outdir = str(tmp_path / "out")
    rc.basedir = here
    rc.use_container = False  # bare command; no docker in the test env

    exit_code = cwlmain.main(
        argsl=[hello, "--name", "spore"],
        loadingContext=lc,
        runtimeContext=rc,
    )

    assert dispatched["task_run"], f"`spawn task run` was never invoked; calls={calls[:5]}"
    assert exit_code == 0

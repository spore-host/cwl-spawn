from cwl_spawn.transfer import (
    build_command_file_contents,
    build_download_results_argv,
    build_upload_command_argv,
    build_upload_work_argv,
    step_s3_prefix,
)


def test_step_s3_prefix_is_try_isolated():
    assert step_s3_prefix("s3://b/runs", "abc", 1) == "s3://b/runs/abc/try-1"
    assert step_s3_prefix("s3://b/runs/", "abc", 2) == "s3://b/runs/abc/try-2"


def test_command_file_contents_exports_env_then_body():
    out = build_command_file_contents("echo hi", {"FOO": "bar baz"})
    assert out == "export FOO='bar baz'\necho hi\n"
    # Already-newline-terminated body isn't doubled.
    assert build_command_file_contents("echo hi\n", {}) == "echo hi\n"


def test_upload_command_argv():
    assert build_upload_command_argv("/tmp/cmd", "s3://b/p", "us-west-2") == [
        "aws", "s3", "cp", "/tmp/cmd", "s3://b/p/command", "--region", "us-west-2", "--quiet"
    ]


def test_upload_work_argv_default_region():
    argv = build_upload_work_argv("/tmp/work", "s3://b/p", "")
    assert argv[:2] == ["aws", "s3"] and argv[2] == "sync"
    assert argv[argv.index("--region") + 1] == "us-east-1"


def test_download_results_argv():
    cmds = build_download_results_argv(
        "s3://b/p", "/tmp/work", "/tmp/stdout", "/tmp/stderr", "eu-west-1"
    )
    assert cmds[0][:3] == ["aws", "s3", "sync"]
    assert cmds[0][3:5] == ["s3://b/p/work", "/tmp/work"]
    assert cmds[1][3:5] == ["s3://b/p/stdout.txt", "/tmp/stdout"]
    assert cmds[2][3:5] == ["s3://b/p/stderr.txt", "/tmp/stderr"]
    for c in cmds:
        assert c[c.index("--region") + 1] == "eu-west-1"
        # Never --delete: don't risk clobbering the local tree.
        assert "--delete" not in c

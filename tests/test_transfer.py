from cwl_spawn.transfer import (
    build_download_work_argv,
    build_upload_work_argv,
    step_s3_prefix,
)


def test_step_s3_prefix_is_try_isolated():
    assert step_s3_prefix("s3://b/runs", "abc", 1) == "s3://b/runs/abc/try-1"
    assert step_s3_prefix("s3://b/runs/", "abc", 2) == "s3://b/runs/abc/try-2"


def test_upload_work_argv_default_region():
    argv = build_upload_work_argv("/tmp/work", "s3://b/p", "")
    assert argv[:3] == ["aws", "s3", "sync"]
    assert argv[3:5] == ["/tmp/work", "s3://b/p/work"]
    assert argv[argv.index("--region") + 1] == "us-east-1"


def test_download_work_argv():
    argv = build_download_work_argv("s3://b/p/work", "/tmp/out", "eu-west-1")
    assert argv[:3] == ["aws", "s3", "sync"]
    assert argv[3:5] == ["s3://b/p/work", "/tmp/out"]
    assert argv[argv.index("--region") + 1] == "eu-west-1"
    # Never --delete: don't risk clobbering the local tree.
    assert "--delete" not in argv

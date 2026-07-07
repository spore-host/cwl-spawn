from cwl_spawn.staging import (
    JOB_DIR,
    build_command_string,
    build_run_line,
    build_setup_script,
    build_staging_script,
)


def test_command_string_quotes_and_redirects():
    cmd = build_command_string(
        ["echo", "hello, world"], stdout="greeting.txt"
    )
    assert cmd == "echo 'hello, world' > greeting.txt\n"


def test_command_string_all_redirects():
    cmd = build_command_string(["cat"], stdin="in.txt", stdout="out.txt", stderr="err.txt")
    assert cmd == "cat < in.txt > out.txt 2> err.txt\n"


def test_command_string_no_redirects():
    assert build_command_string(["true"]) == "true\n"


def test_run_line_bare_vs_docker():
    bare = build_run_line("")
    assert "cd" in bare and "/bin/bash ../command" in bare and "docker" not in bare
    dock = build_run_line("ubuntu:24.04")
    assert dock.startswith("docker run --rm")
    assert "ubuntu:24.04" in dock
    # Bind-mounts the job dir at the same path so ../command resolves in-container.
    assert '-v "${JD}":"${JD}"' in dock
    assert '-w "${JD}/work"' in dock


def test_setup_script_only_when_docker():
    assert build_setup_script("") == ""
    s = build_setup_script("ubuntu:24.04")
    assert "command -v docker" in s and "dnf install -y docker" in s


def test_staging_script_structure_and_exitcode_last():
    script = build_staging_script(
        workdir_s3="s3://b/runs/step/try-1", region="us-east-1", docker_image=""
    )
    assert script.startswith("#!/bin/bash\n")
    assert JOB_DIR in script
    # command is fetched, then the work tree is synced down.
    i_cmd_fetch = script.index('aws s3 cp "${WORKDIR_S3}/command"')
    i_work_fetch = script.index('aws s3 sync "${WORKDIR_S3}/work" "${JD}/work"')
    assert i_cmd_fetch < i_work_fetch
    # results upload: work + std streams FIRST, .exitcode LAST (durable signal).
    i_work_up = script.rindex('aws s3 sync "${JD}/work" "${WORKDIR_S3}/work"')
    i_exit_up = script.rindex('cp "${JD}/.exitcode"')
    assert i_work_up < i_exit_up
    # completion signal present.
    assert "spored complete" in script or "SPAWN_COMPLETE" in script

"""Pure unit tests for the TaskSpec builder + completion parsing. No AWS, no
cwltool — just the translation from cwltool job fields to spawn's TaskSpec shape.
"""

import json

import pytest

from cwl_spawn.taskspec import (
    build_command_string,
    build_task_spec,
    check_complete_to_status,
    clean_env,
    instance_type_family,
    mib_to_gib,
    parse_completion_record,
)


# ---- command string (migrated from the old staging tests) --------------------

def test_command_string_quotes_and_redirects():
    # shlex.quote only quotes tokens that need it: "echo" stays bare, "a b" and
    # "$x" get single-quoted; the redirect target is quoted too.
    s = build_command_string(["echo", "a b", "$x"], stdout="out.txt")
    assert s == "echo 'a b' '$x' > out.txt"


def test_command_string_all_redirects():
    s = build_command_string(["cmd"], stdin="in", stdout="out", stderr="err")
    assert s == "cmd < in > out 2> err"


def test_command_string_metachars_are_quoted():
    # Dangerous tokens must be single-quoted so they can't re-parse in bash -lc.
    s = build_command_string(["sh", "-c", "a; rm -rf /"])
    assert "'a; rm -rf /'" in s


# ---- mib_to_gib (migrated from the old sizing tests) -------------------------

@pytest.mark.parametrize(
    "mib,want",
    [(1024, 1.0), (2048, 2.0), (0, None), (-5, None), (None, None), ("junk", None)],
)
def test_mib_to_gib(mib, want):
    assert mib_to_gib(mib) == want


# ---- instance-hint → family (lossy mapping) ----------------------------------

def test_instance_type_family():
    assert instance_type_family("c7i.4xlarge") == "c7i"
    assert instance_type_family("m9g.24xlarge") == "m9g"
    assert instance_type_family("t3.micro") == "t3"
    assert instance_type_family("garbage") is None
    assert instance_type_family(None) is None


def test_clean_env_drops_invalid_keys():
    assert clean_env({"OK_1": "v", "bad-key": "x", "1num": "y", "A": 3}) == {"OK_1": "v", "A": "3"}
    assert clean_env(None) == {}


# ---- build_task_spec ---------------------------------------------------------

def _spec(**over):
    base = dict(
        task_id="cwl-echo",
        command_line=["echo", "hi"],
        stdin=None,
        stdout="greeting.txt",
        stderr=None,
        environment={"FOO": "bar"},
        work_s3_uri="s3://wd/echo/try-1/work",
        job_dir="/mnt/cwl_spawn_job/work",
        ttl="4h",
    )
    base.update(over)
    return build_task_spec(**base)


def test_command_wrapped_in_bash_lc_with_cd():
    spec = _spec()
    cmd = spec["command"]
    assert cmd[0] == "/bin/bash" and cmd[1] == "-lc"
    # cd into the job dir (shlex.quote leaves a plain path bare), then the
    # redirect-bearing inner command.
    assert cmd[2] == "cd /mnt/cwl_spawn_job/work && echo hi > greeting.txt"


def test_host_run_has_no_container_key():
    spec = _spec(docker_image="")
    assert "container" not in spec


def test_container_key_set_when_image_given():
    spec = _spec(docker_image="quay.io/biocontainers/bwa:0.7.18")
    assert spec["container"] == "quay.io/biocontainers/bwa:0.7.18"


def test_manifests_identity_mount_with_recursive_slashes():
    spec = _spec()
    assert spec["inputs"] == [
        {"source": "s3://wd/echo/try-1/work/", "destination": "/mnt/cwl_spawn_job/work"}
    ]
    # output source is the job dir with a trailing slash → spawn syncs the tree back.
    assert spec["outputs"] == [
        {"source": "/mnt/cwl_spawn_job/work/", "destination": "s3://wd/echo/try-1/work/"}
    ]


def test_resources_from_cores_and_ram():
    spec = _spec(cores=8, ram_mib=16384)
    assert spec["resources"]["cpu"] == 8
    assert spec["resources"]["memory_gib"] == 16.0


def test_resources_omitted_when_unsized():
    spec = _spec(cores=None, ram_mib=None)
    assert spec["resources"] == {}


def test_instance_hint_maps_to_family_not_exact_pin():
    spec = _spec(instance_hint="c7i.4xlarge")
    # Lossy by design: the hint steers the family; spawn's sizer picks within it.
    assert spec["resources"]["families"] == ["c7i"]
    # There is intentionally no exact instance_type field.
    assert "instance_type" not in spec["resources"]


def test_env_passthrough_and_omitted_when_empty():
    assert _spec(environment={"A": "1"})["env"] == {"A": "1"}
    assert "env" not in _spec(environment={})


def test_spec_has_required_fields_and_json_round_trips():
    spec = _spec()
    # The fields spawn's TaskSpec.Validate requires.
    assert spec["task_id"] and spec["command"] and spec["lifecycle"]["ttl"] == "4h"
    assert spec["lifecycle"]["on_complete"] == "terminate"
    assert json.loads(json.dumps(spec)) == spec  # serializable


# ---- completion parsing ------------------------------------------------------

def test_check_complete_to_status():
    assert check_complete_to_status(0) == "completed"
    assert check_complete_to_status(1) == "failed"
    assert check_complete_to_status(2) is None  # running → keep polling
    with pytest.raises(RuntimeError):
        check_complete_to_status(3)  # error
    with pytest.raises(RuntimeError):
        check_complete_to_status(42)  # unknown contract


def test_parse_completion_record():
    rec = parse_completion_record('{"task_id":"t","exit_code":0,"state":"completed"}')
    assert rec["exit_code"] == 0 and rec["state"] == "completed"
    with pytest.raises(json.JSONDecodeError):
        parse_completion_record("not json")
    with pytest.raises(RuntimeError):
        parse_completion_record('"a string, not an object"')

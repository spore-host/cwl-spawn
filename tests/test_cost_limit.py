"""lifecycle.cost_limit (cwl-spawn#12)."""

from __future__ import annotations

from cwl_spawn import job, taskspec


def _spec(**kw) -> dict:
    base = dict(
        task_id="step-1",
        command_line=["echo", "hi"],
        stdin=None,
        stdout=None,
        stderr=None,
        environment=None,
        work_s3_uri="s3://b/runs/x",
        job_dir="/tmp/job",
    )
    base.update(kw)
    return taskspec.build_task_spec(**base)


def test_cost_limit_is_emitted():
    """Second belt: spored enforces TTL and cost independently, first to fire
    wins. Without a cap the only ceiling is the TTL (4h default)."""
    assert _spec(cost_limit=0.05)["lifecycle"]["cost_limit"] == 0.05


def test_cost_limit_omitted_when_unset():
    assert "cost_limit" not in _spec()["lifecycle"]
    assert "cost_limit" not in _spec(cost_limit=None)["lifecycle"]


def test_zero_or_negative_is_unset():
    """A zero cap would mean "terminate immediately"."""
    assert "cost_limit" not in _spec(cost_limit=0)["lifecycle"]
    assert "cost_limit" not in _spec(cost_limit=-1)["lifecycle"]


def test_ttl_and_on_complete_unaffected():
    assert _spec(ttl="30m", cost_limit=2.0)["lifecycle"] == {
        "ttl": "30m",
        "on_complete": "terminate",
        "cost_limit": 2.0,
    }


def test_env_parsing(monkeypatch):
    monkeypatch.setenv("SPAWN_COST_LIMIT", "0.25")
    assert job._cost_limit_cfg() == 0.25

    # Unset / empty -> no cap, which is the previous behaviour.
    monkeypatch.delenv("SPAWN_COST_LIMIT", raising=False)
    assert job._cost_limit_cfg() is None
    monkeypatch.setenv("SPAWN_COST_LIMIT", "")
    assert job._cost_limit_cfg() is None


def test_bad_env_degrades_rather_than_raising(monkeypatch):
    """It arrives as an environment string; a typo must not take down a workflow
    that is otherwise fine."""
    monkeypatch.setenv("SPAWN_COST_LIMIT", "half-a-dollar")
    assert job._cost_limit_cfg() is None
    monkeypatch.setenv("SPAWN_COST_LIMIT", "0")
    assert job._cost_limit_cfg() is None

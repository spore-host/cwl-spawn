# CLAUDE.md — cwl-spawn

`cwl-spawn` runs each **CWL** `CommandLineTool` step on an ephemeral EC2 instance
via [spore-host/spawn](https://github.com/spore-host/spawn). The CWL analog of
`nf-spawn` (Nextflow) and `miniwdl-spawn` (WDL). Part of the spore.host suite
(spore-host#396).

## Versioning & changelog (required)

Follows **[Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html)** and keeps a
**[Keep a Changelog](https://keepachangelog.com/en/1.1.0/)**-format `CHANGELOG.md`
(spore.host-wide policy).

**Every user-facing change updates `CHANGELOG.md`** in the same PR under
`## [Unreleased]` (Added/Changed/Deprecated/Removed/Fixed/Security).

**On release:**
1. Rename `## [Unreleased]` → `## [X.Y.Z] - YYYY-MM-DD`; open a fresh Unreleased; update links.
2. SemVer: MAJOR breaking / MINOR feature / PATCH fix (pre-1.0 breaking → MINOR).
3. **Bump `version` in `pyproject.toml` to match** — the release workflow fails if the tag
   and `pyproject.toml` version drift.
4. Tag `vX.Y.Z` → the Release workflow builds + publishes.

## Build & test

Python package (3.10+). Needs the `spawn` and `truffle` CLIs on PATH at runtime,
plus AWS credentials, for real runs.

- `pip install -e ".[dev]"` — install with dev deps
- `pytest` — pure-function unit tests (no AWS; the bulk of coverage)
- `ruff check .` && `mypy cwl_spawn` — lint + type-check

## Architecture

- `tool.py` — `make_spawn_tool` (a cwltool `construct_tool_object` hook) +
  `SpawnCommandLineTool(CommandLineTool)` overriding `make_job_runner`.
- `job.py` — `SpawnJob(CommandLineJob)` overriding `run()`: the cwltool-facing
  adapter that dispatches one step to a spawn instance.
- `cli.py` — the `cwl-spawn` console script; drives cwltool as a library with the
  custom `construct_tool_object`.
- `launch.py` / `transfer.py` / `completion.py` / `sizing.py` / `staging.py` —
  **pure** helpers (no I/O), unit-tested. Keep new logic here, not in job.py.

## cwltool coupling — pin + drift-guard

cwltool has **no plugin entry-point** (unlike miniwdl's `container_backend`). We
subclass cwltool's internal job-runner classes (`CommandLineJob.run`,
`CommandLineTool.make_job_runner`) and inject via the public
`LoadingContext.construct_tool_object` factory. Those internals are **calendar-
versioned with no semver contract**, so:

- **Pin an exact cwltool build** in `pyproject.toml` (currently
  `3.2.20260413085819`).
- A **drift-guard test** (`tests/test_drift_guard.py`) asserts the seam still
  exists (`CommandLineJob.run` overridable; `builder.resources` carries
  `cores`/`ram`). It must fail loudly when a cwltool bump moves the seam — bump
  deliberately, re-verify, then update the pin.

## Cost safety

Real runs launch billable EC2 instances. Any real-AWS test MUST set a TTL,
terminate explicitly, and leak-check afterward (no orphaned instances). The
adapter always launches with `--on-complete terminate` and a TTL.

## Reuse / lineage

Mirrors `miniwdl-spawn`'s proven design (same `spawn` CLI contract, same
`.exitcode`-in-S3 completion, same truffle auto-sizing). `launch.py`,
`transfer.py`, `completion.py` are ported ~verbatim; `sizing.py` reads CWL's
`ram` (MiB) instead of WDL's memory string. When in doubt, check how
`miniwdl-spawn` solved it.

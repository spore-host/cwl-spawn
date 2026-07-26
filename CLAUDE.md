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
  adapter that dispatches one step via `spawn task run`. Thin subprocess calls
  only; the logic lives in the pure modules below.
- `cli.py` — the `cwl-spawn` console script; drives cwltool as a library with the
  custom `construct_tool_object`.
- `taskspec.py` — **pure**: builds the spawn TaskSpec dict from a step's cwltool
  fields (command+redirects → `bash -lc`, DockerRequirement → container,
  cores/ram → resources, S3↔local manifests) and parses the CompletionRecord.
  Keep new logic here, not in job.py.
- `transfer.py` — **pure**: the `aws s3 sync` argv that bridge cwltool's local
  outdir ↔ the S3 work prefix `spawn task run` stages from.
- spawn owns launch/sizing/staging/container/completion/IAM (via `spawn task
  run`), so cwl-spawn no longer has launch/staging/completion/sizing modules.

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

cwl-spawn targets the **spawn task-execution protocol** (`spawn task run` +
TaskSpec/CompletionRecord, spawn#386) — spawn owns sizing/staging/container/
completion/IAM. This is the reference port; the other adapters (nf-spawn,
miniwdl-spawn, snakemake, airflow-spawn) migrate to the same `spawn task run`
contract. The only cwl-specific logic left here is the cwltool seam (job.py) and
the CWL→TaskSpec mapping (taskspec.py).

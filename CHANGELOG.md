# Changelog

All notable changes to **cwl-spawn** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security
- **Added Dependabot, so the SHA-pinned actions actually get bumped**
  ([#6](https://github.com/spore-host/cwl-spawn/issues/6)). All 5 `uses:` refs were already pinned to commit SHAs — which
  is exactly the situation that needs this: a SHA never moves, including past a
  security fix, and unlike `@v5` nothing updates it. Pinning and Dependabot are one
  control, not two; shipping only the pin trades a mutable-tag hole for a slow one.
  - The new `.github/dependabot.yml` covers `github-actions` and `pip`, weekly with
    a 7-day cooldown — a freshly published tag is exactly when a compromised or
    broken one is still unnoticed. Group pattern is `*`, not `actions/*`, because
    `softprops/action-gh-release` (which creates the GitHub Release under
    `contents: write`) would otherwise fall outside the group and stop being
    bumped. `ruff >=0.16` is ignored so a bump can't undo the deliberate cap.
  - `tests/test_ci_hygiene.py` makes both halves regressions rather than
    conventions: reverting a pin or dropping the Dependabot entry now fails
    `pytest`, which CI already runs. `pyyaml` joins the `[dev]` extra for it and is
    imported unguarded — a `try`/`except` import degrades to a skip, and a skipped
    wiring test reports green while asserting nothing.
  No behaviour change — CI wiring and tests only.

### Fixed
- **CI was red on `main` and `ruff` is now capped `<0.16`.** ruff 0.16 moved a
  large set of opinionated rules (`BLE`, `PLW`, `TRY`, `C408`, `EXE`, `B017`,
  `UP035`, …) into its **default** rule set, and the dev extra asked only for
  `ruff>=0.5` — so `ruff check .` adopted 31 new violations the moment ruff
  published, in code that hadn't been touched. Same cap as `airflow-spawn`,
  `miniwdl-spawn` and `snakemake-executor-plugin-spawn`. Adopting those rules
  should be a deliberate change via an explicit `[tool.ruff.lint] select`, not
  something a ruff release does to us. (`mypy` and the 31 tests were already
  passing — the lint step aborts the job before them, so nothing else was hidden.)

## [0.2.0] - 2026-07-19

### Changed
- **cwl-spawn now dispatches each step through `spawn task run`** instead of
  orchestrating the launch itself (spawn#386 adapter migration). It builds a
  spawn **TaskSpec** and runs `spawn task run --spec … --wait -o json`, then reads
  the **CompletionRecord** back. spawn now owns instance sizing (truffle), the S3
  staging, the container run (Docker install + `docker run` for a
  `DockerRequirement` image), the durable completion record, and the instance IAM
  profile — so cwl-spawn no longer reimplements any of it. The step's
  stdin/stdout/stderr redirects and cwd are preserved by wrapping the command in
  `bash -lc 'cd <workdir> && …'`.
- **Instance IAM is now least-privilege.** Previously the step instance got
  `--iam-policy s3:FullAccess`; spawn now attaches a scoped profile granting
  exactly the input/output/results buckets the task touches.

### Removed
- Bundled launch/staging/completion/sizing machinery (`launch.py`,
  `staging.py`, `completion.py`, `sizing.py`) — spawn owns these now.
- The `spawn:instanceType` hint no longer pins an **exact** instance type; it now
  maps to a truffle **family allow-list** (e.g. `c7i.4xlarge` → the `c7i` family)
  and spawn's sizer picks the cheapest fit within it. (Exact-pin support is
  tracked as a spawn TaskSpec follow-up.)
- `truffle` is no longer required on `PATH` (spawn sizes the instance itself);
  `spawn` and `aws` are still required.

### Fixed
- **Steps no longer fail with a "Permission denied" job-dir error.** The on-instance
  work dir moved from `/mnt/cwl_spawn_job/work` to `/var/tmp/cwl_spawn_job/work`.
  spawn runs the task command as the instance's unprivileged login user, which
  cannot `mkdir` under the root-owned `/mnt`; `/var/tmp` is world-writable and
  disk-backed (not tmpfs). Without this, no output file was ever written and
  cwltool reported the step's output missing.

## [0.1.0] - 2026-07-07

### Added
- Initial release: run each CWL `CommandLineTool` step on an ephemeral EC2
  instance via spore-host/spawn, with truffle auto-sizing from the step's
  `ResourceRequirement`, S3-staged inputs/outputs, a durable `.exitcode`-in-S3
  completion signal, and `--on-complete terminate` so instances self-destruct.
  The CWL analog of nf-spawn (Nextflow) and miniwdl-spawn (WDL). Closes
  spore-host#396.
- `cwl-spawn <workflow.cwl> <inputs>` console script: drives cwltool as a library
  with a `construct_tool_object` hook (`make_spawn_tool`) that returns a
  `SpawnCommandLineTool` whose steps run via `SpawnJob` (a `CommandLineJob`
  subclass). cwltool keeps ownership of parsing, scheduling, scatter/gather, and
  output collection.
- Drift-guard test suite pinning the cwltool internal seam (`CommandLineJob.run`,
  `make_job_runner`, `builder.resources` cores/ram) — fails loudly if a cwltool
  bump moves it.
- Verified end-to-end on real AWS: `examples/hello.cwl` ran on a spawned EC2
  instance (auto-sized, S3-bridged), cwltool collected the output and reported
  success, and the instance self-terminated (leak-checked clean).

[Unreleased]: https://github.com/spore-host/cwl-spawn/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/spore-host/cwl-spawn/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/spore-host/cwl-spawn/releases/tag/v0.1.0

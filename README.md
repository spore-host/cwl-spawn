# cwl-spawn

Run each **CWL** `CommandLineTool` step on a purpose-sized, ephemeral EC2
instance via [spore-host/spawn](https://github.com/spore-host/spawn) — the CWL
analog of [nf-spawn](https://github.com/spore-host/nf-spawn) (Nextflow) and
[miniwdl-spawn](https://github.com/spore-host/miniwdl-spawn) (WDL). Part of the
spore.host suite.

Each step is auto-sized from its CWL `ResourceRequirement` (via `truffle`),
launched with a TTL and `--on-complete terminate`, its inputs/outputs bridged
through S3, and torn down when it finishes — so a workflow costs only the compute
each step actually uses, with no forgotten instances.

## Install

```bash
pip install cwl-spawn
```

Requires the `spawn` and `truffle` CLIs on `PATH` and AWS credentials for real
runs.

## Use

```bash
export SPAWN_WORKDIR_S3=s3://my-bucket/cwl-runs   # the S3 bridge (required)
export SPAWN_REGION=us-east-1                      # optional (default us-east-1)
export SPAWN_TTL=4h                                # optional TTL backstop

cwl-spawn workflow.cwl inputs.yml
```

`cwl-spawn` drives `cwltool` as a library, replacing the local job runner with
one that dispatches each `CommandLineTool` to a spawn instance. Standard cwltool
behavior (parsing, scheduling, output collection) is unchanged.

### Sizing

A step's EC2 instance type is chosen by, in order:
1. an explicit `spawn:instanceType` hint on the step,
2. the cheapest instance that fits the step's `ResourceRequirement`
   (`coresMin`/`ramMin`) via `truffle search --pick-first`,
3. a default (`t3.medium`).

## How it works

Mirrors the proven miniwdl-spawn / nf-spawn design: the step's command + working
directory are staged to `SPAWN_WORKDIR_S3`, the instance runs the command, writes
results and a final `.exitcode` object back to S3 (the durable completion signal
that outlives the self-terminating instance), and cwltool collects the outputs
from the pulled-back working directory.

## License

Apache-2.0

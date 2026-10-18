# cwl-spawn

Run each **CWL** `CommandLineTool` step on a purpose-sized, ephemeral EC2
instance via [spore-host/spawn](https://github.com/spore-host/spawn) — the CWL
analog of [nf-spawn](https://github.com/spore-host/nf-spawn) (Nextflow) and
[miniwdl-spawn](https://github.com/spore-host/miniwdl-spawn) (WDL). Part of the
spore.host suite.

Each step is auto-sized from its CWL `ResourceRequirement` (spawn picks the
cheapest instance that fits), launched with a TTL and `--on-complete terminate`, its inputs/outputs bridged
through S3, and torn down when it finishes — so a workflow costs only the compute
each step actually uses, with no forgotten instances.

## Install

```bash
pip install cwl-spawn
```

Requires the `spawn` and `aws` CLIs on `PATH` and AWS credentials for real runs.
spawn sizes instances with its built-in truffle, so no separate `truffle` CLI is
needed.

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

spawn sizes each step: it picks the cheapest instance that fits the step's
`ResourceRequirement` (`coresMin`/`ramMin`). A `spawn:instanceType` hint on the
step steers the instance *family* (`c7i.4xlarge` → the `c7i` family), and spawn
picks the cheapest fit within it. It does not pin the exact size.

## How it works

cwl-spawn uploads the step's working directory to `SPAWN_WORKDIR_S3`, builds a
spawn TaskSpec, and runs `spawn task run --spec … --wait -o json`. spawn launches
the instance, stages the inputs, runs the command, copies the working directory
back to S3, and writes a durable completion record that outlives the
self-terminating instance. cwl-spawn reads the exit code from that record, pulls
the working directory back, and cwltool collects the outputs from it.

## License

Apache-2.0

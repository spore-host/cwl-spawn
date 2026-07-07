# Changelog

All notable changes to **cwl-spawn** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Initial release: run each CWL `CommandLineTool` step on an ephemeral EC2
  instance via spore-host/spawn, with truffle auto-sizing from the step's
  `ResourceRequirement`, S3-staged inputs/outputs, a durable `.exitcode`-in-S3
  completion signal, and `--on-complete terminate` so instances self-destruct.
  The CWL analog of nf-spawn (Nextflow) and miniwdl-spawn (WDL). Closes
  spore-host#396.

[Unreleased]: https://github.com/spore-host/cwl-spawn/commits/main

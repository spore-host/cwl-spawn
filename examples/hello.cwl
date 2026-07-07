#!/usr/bin/env cwl-runner
cwlVersion: v1.2
class: CommandLineTool
label: hello — echo a greeting (the cwl-spawn smoke workflow)

baseCommand: [echo]

inputs:
  name:
    type: string
    default: spore
    inputBinding:
      position: 1
      prefix: "hello,"

stdout: greeting.txt

outputs:
  greeting:
    type: stdout

# Sizing: no ResourceRequirement -> cwl-spawn falls back to the default instance
# type. Add a ResourceRequirement (coresMin/ramMin) to auto-size via truffle, or
# a spore.host InstanceType hint to pin one.
requirements:
  - class: InlineJavascriptRequirement

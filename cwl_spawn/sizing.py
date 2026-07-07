"""Map a CWL step's ResourceRequirement to an EC2 instance type.

CWL's ``ResourceRequirement`` (coresMin/ramMin) is declarative, so — like WDL —
we can auto-pick the cheapest instance that fits via truffle. cwltool evaluates
requirements into ``builder.resources`` as ``cores`` (int) and ``ram`` (MiB); this
module turns those into a ``truffle search`` query. An explicit per-step hint
(``spawn:instanceType``) always wins; if truffle is unavailable we fall back to a
configurable default. Pure except for the one subprocess call to truffle
(injected for tests).
"""

from __future__ import annotations

import math
import shutil
import subprocess
from typing import Callable, Optional

# Default when neither an explicit type nor a successful truffle lookup is available.
DEFAULT_INSTANCE_TYPE = "t3.medium"


def mib_to_gib(ram_mib: object) -> Optional[float]:
    """Coerce cwltool's ``builder.resources['ram']`` (MiB, per CWL spec) to GiB.

    cwltool stores ram as a number of mebibytes (``math.ceil(ramMin)``). Returns
    None if it's missing or unparseable (caller then omits the --min-memory filter).
    """
    if ram_mib is None:
        return None
    try:
        val = float(ram_mib)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if val <= 0:
        return None
    return val / 1024.0  # MiB -> GiB


def build_truffle_argv(
    min_vcpu: Optional[int], min_memory_gib: Optional[float], architecture: Optional[str]
) -> list[str]:
    """Build the ``truffle search`` argv that returns the cheapest fitting type.

    ``--pick-first`` makes truffle emit only the top result's instance type;
    ``--show-price`` makes the default sort cheapest-first. Pure.
    """
    argv = ["truffle", "search", "--pick-first", "--show-price"]
    if min_vcpu and min_vcpu > 0:
        argv += ["--min-vcpu", str(int(min_vcpu))]
    if min_memory_gib and min_memory_gib > 0:
        # Round up so we never under-provision a fractional GiB request.
        argv += ["--min-memory", str(int(math.ceil(min_memory_gib)))]
    if architecture:
        argv += ["--architecture", architecture]
    return argv


def pick_instance_type(
    *,
    override: Optional[str] = None,
    cores: Optional[int] = None,
    ram_mib: object = None,
    architecture: Optional[str] = None,
    default: str = DEFAULT_INSTANCE_TYPE,
    runner: Optional[Callable[[list[str]], str]] = None,
) -> str:
    """Resolve the instance type for a step.

    Precedence: ``override`` (a ``spawn:instanceType`` hint) > truffle cheapest-fit
    (from cores/ram) > ``default``. ``runner`` runs the truffle argv and returns
    its stdout (injected in tests); when None, a real subprocess is used iff
    truffle is on PATH.
    """
    if override:
        return override.strip()

    min_mem_gib = mib_to_gib(ram_mib)
    if (cores is None or cores <= 0) and min_mem_gib is None:
        return default  # nothing to size on

    argv = build_truffle_argv(cores, min_mem_gib, architecture)

    if runner is None:
        if shutil.which("truffle") is None:
            return default

        def runner(a: list[str]) -> str:
            return subprocess.run(
                a, capture_output=True, text=True, timeout=120, check=True
            ).stdout

    try:
        out = runner(argv)
    except Exception:
        return default
    for line in (out or "").strip().splitlines():
        line = line.strip()
        if line:
            return line
    return default

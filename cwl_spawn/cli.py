"""The ``cwl-spawn`` console script: run a CWL workflow with each step dispatched
to an ephemeral EC2 instance via spawn.

Drives cwltool as a library, passing a ``LoadingContext`` whose
``construct_tool_object`` is our ``make_spawn_tool`` hook — so every
CommandLineTool step uses ``SpawnJob`` while cwltool keeps ownership of parsing,
scheduling, scatter/gather, and output collection. All cwltool CLI args are
forwarded unchanged (``cwl-spawn --help`` shows cwltool's own options).
"""

from __future__ import annotations

import sys
from typing import Optional


def main(argv: Optional[list[str]] = None) -> int:
    from cwltool import main as cwlmain
    from cwltool.context import LoadingContext

    from .tool import make_spawn_tool

    args = list(sys.argv[1:] if argv is None else argv)
    loading_context = LoadingContext()
    loading_context.construct_tool_object = make_spawn_tool
    return cwlmain.main(argsl=args, loadingContext=loading_context)


if __name__ == "__main__":
    raise SystemExit(main())

"""cwltool integration seam: a ``construct_tool_object`` factory that swaps the
local job runner for one that dispatches each CommandLineTool step to spawn.

cwltool has no plugin entry-point (unlike miniwdl's ``container_backend``), so we
inject via the public ``LoadingContext.construct_tool_object`` hook. For a
``class: CommandLineTool`` we return a ``SpawnCommandLineTool`` whose
``make_job_runner`` returns our ``SpawnJob``; everything else (Workflow,
ExpressionTool, sub-workflows) falls through to cwltool's ``default_make_tool``,
so scheduling, scatter/gather, and output collection are unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cwltool.command_line_tool import CommandLineTool
from cwltool.context import LoadingContext, RuntimeContext
from cwltool.workflow import default_make_tool

if TYPE_CHECKING:
    from cwltool.job import JobBase
    from cwltool.process import Process
    from ruamel.yaml.comments import CommentedMap


class SpawnCommandLineTool(CommandLineTool):
    """A CommandLineTool whose steps run on ephemeral spawn instances."""

    def make_job_runner(self, runtimeContext: "RuntimeContext") -> "type[JobBase]":
        # Import here so tool.py stays importable even while job.py is evolving,
        # and to avoid any import cycle.
        from .job import SpawnJob

        return SpawnJob


def make_spawn_tool(
    toolpath_object: "CommentedMap", loadingContext: "LoadingContext"
) -> "Process":
    """A ``construct_tool_object`` factory: SpawnCommandLineTool for a
    CommandLineTool, else cwltool's default. Pass this as
    ``LoadingContext(construct_tool_object=make_spawn_tool)``.
    """
    if toolpath_object.get("class") == "CommandLineTool":
        return SpawnCommandLineTool(toolpath_object, loadingContext)
    return default_make_tool(toolpath_object, loadingContext)

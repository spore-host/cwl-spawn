"""Build the per-step staging script that runs on the ephemeral instance.

The script is passed to ``spawn launch --user-data-file``. It reconstructs the
step's job directory at a fixed absolute path (so any absolute paths cwltool
baked into the command resolve), pulls the command + working tree from S3, runs
the command (bare, or inside the step's DockerRequirement image bind-mounting the
job dir at the same path), captures the real exit code, syncs results back, and
uploads ``.exitcode`` *last* as the durable completion signal.

All functions are pure string builders (no I/O), unit-tested without AWS. Mirrors
miniwdl-spawn's staging.py; the difference is CWL supplies an explicit command
string (from ``job.command_line`` + redirects) rather than a container-path
command file, so JOB_DIR is our own fixed prefix rather than miniwdl's.
"""

from __future__ import annotations

import shlex

# Fixed job dir on the instance's EBS root (NOT /tmp, which is tmpfs/RAM on
# AL2023). The staged working tree lands here; the command runs with cwd = the
# work subdir, matching how cwltool's local runner uses the job outdir.
JOB_DIR = "/mnt/cwl_spawn_job"


def _q(s: str) -> str:
    return shlex.quote(s)


def build_command_string(
    command_line: list[str],
    stdin: str | None = None,
    stdout: str | None = None,
    stderr: str | None = None,
) -> str:
    """Turn cwltool's ``job.command_line`` argv + stdin/stdout/stderr redirects
    into a single shell command string (the ``command`` file body). Pure.

    Redirections are appended as ``< stdin`` / ``> stdout`` / ``2> stderr`` so the
    step's declared streams land in files inside the work dir, exactly as cwltool
    expects to collect them. Each token is shell-quoted.
    """
    parts = [shlex.quote(str(a)) for a in command_line]
    line = " ".join(parts)
    if stdin:
        line += f" < {shlex.quote(stdin)}"
    if stdout:
        line += f" > {shlex.quote(stdout)}"
    if stderr:
        line += f" 2> {shlex.quote(stderr)}"
    return line + "\n"


def build_run_line(docker_image: str, run_options: str = "") -> str:
    """Run the step command: cwd = work, ``/bin/bash ../command`` with
    append-redirection to ../stdout.txt / ../stderr.txt. Bare when no image, else
    inside Docker bind-mounting JOB_DIR at the same path (so ``../command``
    resolves identically inside the container)."""
    redir = "/bin/bash ../command >> ../stdout.txt 2>> ../stderr.txt"
    if not docker_image.strip():
        return f'( cd "${{JD}}/work" && {redir} )\n'
    opts = (run_options.strip() + " ") if run_options.strip() else ""
    return (
        'docker run --rm -v "${JD}":"${JD}" -w "${JD}/work" '
        + opts
        + f"{_q(docker_image.strip())} /bin/bash -c {_q(redir)}\n"
    )


def build_setup_script(docker_image: str) -> str:
    """Per-step bootstrap: ensure Docker when the step has a container image
    (stock AL2023 has none). Idempotent (``command -v docker`` guard). aws CLI +
    spored ship on AL2023 already."""
    if not docker_image.strip():
        return ""
    return (
        "# cwl-spawn: ensure Docker (stock AL2023 has none); idempotent.\n"
        "if ! command -v docker >/dev/null 2>&1; then\n"
        '  echo "cwl-spawn: installing Docker..." >&2\n'
        '  sudo dnf install -y docker || { echo "cwl-spawn: docker install failed" >&2; exit 1; }\n'
        "fi\n"
        "sudo systemctl enable --now docker 2>/dev/null || sudo systemctl start docker "
        '|| { echo "cwl-spawn: could not start docker" >&2; exit 1; }\n'
    )


def build_staging_script(
    *,
    workdir_s3: str,
    region: str,
    docker_image: str = "",
    run_options: str = "",
    setup: str = "",
) -> str:
    """Assemble the full user-data staging script. Pure.

    ``workdir_s3`` is the per-attempt prefix (…/<run_id>/try-N); the script reads
    ``<prefix>/command`` + ``<prefix>/work`` and writes results back there.
    """
    sb: list[str] = ["#!/bin/bash\n", "set -uo pipefail\n\n"]
    sb.append(f"WORKDIR_S3={_q(workdir_s3)}\n")
    sb.append(f"AWS_REGION={_q(region)}\n")
    sb.append(f"JD={JOB_DIR}\n\n")

    # Recreate the job tree on the EBS root, writable by a non-root container user.
    sb.append('sudo mkdir -p "${JD}/work"\n')
    sb.append('sudo chown -R "$(id -u):$(id -g)" "${JD}"\n')
    sb.append('chmod -R 0777 "${JD}"\n\n')

    if setup.strip():
        sb.append(setup.rstrip() + "\n\n")

    # 1. Pull command + the whole work/ tree (staged inputs already inside it).
    sb.append(
        'aws s3 cp "${WORKDIR_S3}/command" "${JD}/command" --region "${AWS_REGION}" --quiet '
        '|| { echo "cwl-spawn: failed to fetch command" >&2; exit 1; }\n'
    )
    sb.append('aws s3 sync "${WORKDIR_S3}/work" "${JD}/work" --region "${AWS_REGION}" --quiet\n\n')

    # 2. Pre-create stdout/stderr so the >> redirection + back-cp always have targets.
    sb.append(': > "${JD}/stdout.txt"\n')
    sb.append(': > "${JD}/stderr.txt"\n\n')

    # 3. Run; capture the real exit code.
    sb.append(build_run_line(docker_image, run_options))
    sb.append("TASK_RC=$?\n")
    sb.append('echo "${TASK_RC}" > "${JD}/.exitcode"\n\n')

    # 4. Push results back: work/ + stdout + stderr FIRST, .exitcode LAST.
    sb.append('aws s3 sync "${JD}/work" "${WORKDIR_S3}/work" --region "${AWS_REGION}" --quiet\n')
    sb.append(
        'aws s3 cp "${JD}/stdout.txt" "${WORKDIR_S3}/stdout.txt" --region "${AWS_REGION}" --quiet\n'
    )
    sb.append(
        'aws s3 cp "${JD}/stderr.txt" "${WORKDIR_S3}/stderr.txt" --region "${AWS_REGION}" --quiet\n'
    )
    sb.append(
        'aws s3 cp "${JD}/.exitcode" "${WORKDIR_S3}/.exitcode" --region "${AWS_REGION}" --quiet\n\n'
    )

    # 5. Signal completion so spored terminates the instance.
    sb.append('if [ "${TASK_RC}" -eq 0 ]; then S=success; else S=failed; fi\n')
    sb.append('spored complete --status "${S}" 2>/dev/null || touch /tmp/SPAWN_COMPLETE\n')
    return "".join(sb)

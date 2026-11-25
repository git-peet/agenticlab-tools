#!/usr/bin/env python3
"""Report whether workflow files reference local scripts and directories that exist.

Sources inspected (paths are relative to the repository root):

    workflows   .github/workflows/*.yml, .github/workflows/*.yaml

No YAML parser is involved: the files are read textually, which is enough to
locate ``run:`` scalars (inline, ``|``, and ``>`` blocks) and the
``working-directory:`` sibling of the step they belong to. Steps are delimited
by their ``- `` sequence dash, and block scalars of any key are skipped so their
bodies cannot be mistaken for step keys.

Rules:

    script reference   a token that follows bash/sh/dash/zsh/source/python/
                       python3, or that ends in .sh/.bash/.py. It is resolved
                       against the step's working-directory (else the repo
                       root) and must exist.
    working-directory  resolved against the repo root and must be a directory.
    unclassifiable     a token in one of those two positions that contains a
                       shell variable or ``${{ }}`` expression, is absolute, or
                       contains a glob. It cannot be verified, so it is reported
                       as a warning instead of an error.
    skipped            a script-position token that follows a ``docker`` word on
                       the same line (``docker compose run``, ``docker exec``,
                       ``docker run``, ...) executes inside a container, where
                       repository paths are not visible from here.

Severity and exit codes:

    fatal:    exit 2   an input could not be used (missing root or workflow dir,
                       unreadable workflow file, no workflow files)
    error:    exit 1   a script or working-directory does not exist
    warning:  exit 0   a reference could not be classified

The last line written to stdout is a machine-readable summary:

    summary: check=workflow-paths status=<ok|error|unusable> workflows=<n> \
runs=<n> scripts=<n> workdirs=<n> errors=<n> warnings=<n>

``runs`` counts checked ``run:`` blocks, ``scripts`` the script references that
were verified against the filesystem, and ``workdirs`` the ``working-directory``
values that were verified.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

CHECK_NAME = "workflow-paths"

WORKFLOW_DIR = ".github/workflows"
WORKFLOW_GLOBS = ("*.yml", "*.yaml")

FATAL = "fatal"
ERROR = "error"
WARNING = "warning"

STATUS_OK = "ok"
STATUS_ERROR = "error"
STATUS_UNUSABLE = "unusable"

EXIT_CODES = {STATUS_OK: 0, STATUS_ERROR: 1, STATUS_UNUSABLE: 2}

BLOCK_INDICATORS = ("|", ">")
INTERPRETERS = frozenset({"bash", "sh", "dash", "zsh", "source", "python", "python3"})
SCRIPT_SUFFIXES = (".sh", ".bash", ".py")
TOKEN_TRIM = "\"'`()<>|&;,[]"

UNCLASSIFIABLE_VARIABLE = "shell variable or expression"
UNCLASSIFIABLE_ABSOLUTE = "absolute path outside the repository"
UNCLASSIFIABLE_GLOB = "path contains a glob"


# --------------------------------------------------------------------------- #
# Textual workflow parsing
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Finding:
    severity: str
    path: str
    message: str

    def render(self) -> str:
        return f"{self.severity}: {self.path}: {self.message}"


@dataclass
class Step:
    """One workflow step: its ``run`` body (with line numbers) and workdir."""

    path: str
    lineno: int
    run: list[tuple[int, str]] = field(default_factory=list)
    workdir: str | None = None
    workdir_lineno: int = 0


@dataclass(frozen=True)
class WorkflowScan:
    path: str
    steps: tuple[Step, ...]
    fatal: str | None = None

    @property
    def runs(self) -> int:
        return sum(1 for step in self.steps if step.run)


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip())


def _strip_comment(text: str) -> str:
    """Drop a trailing ``#`` comment; ``#`` inside quotes or a word is kept."""
    for index, char in enumerate(text):
        if char != "#":
            continue
        if index == 0 or text[index - 1].isspace():
            return text[:index]
    return text


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _collect_block(
    lines: list[str], start: int, key_indent: int
) -> tuple[list[tuple[int, str]], int]:
    """Collect a block scalar body: ``(lineno, text)`` pairs, dedented.

    Returns the body and the index of the first line after it. Lines indented
    more than the key belong to the block; a blank line is kept only when more
    content follows.
    """
    body: list[tuple[int, str]] = []
    index = start
    while index < len(lines):
        raw = lines[index]
        if not raw.strip():
            body.append((index + 1, ""))
            index += 1
            continue
        if _indent_of(raw) <= key_indent:
            break
        body.append((index + 1, raw))
        index += 1
    while body and not body[-1][1].strip():
        body.pop()
    indents = [_indent_of(text) for _, text in body if text.strip()]
    cut = min(indents) if indents else 0
    return [(lineno, text[cut:] if len(text) >= cut else text) for lineno, text in body], index


def parse_workflow(text: str, display: str) -> WorkflowScan:
    """Extract steps, their ``run`` bodies, and their ``working-directory``."""
    steps: list[Step] = []
    current: Step | None = None
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        raw = lines[index]
        lineno = index + 1
        index += 1
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = _indent_of(raw)
        content = stripped
        if content.startswith("- "):
            current = Step(display, lineno)
            steps.append(current)
            content = content[2:].lstrip()
            indent += 2
        key, separator, value = content.partition(":")
        if not separator:
            continue
        key = key.strip()
        value = _strip_comment(value).strip()

        if value[:1] in BLOCK_INDICATORS and value:
            body, index = _collect_block(lines, index, indent)
            if key != "run" or current is None:
                continue
            if value.startswith(">"):
                folded = " ".join(text.strip() for _, text in body if text.strip())
                current.run = [(body[0][0], folded)] if body else []
            else:
                current.run = body
            continue

        if key == "run" and current is not None:
            current.run = [(lineno, value)] if value else []
        elif key == "working-directory" and current is not None:
            current.workdir = _unquote(value)
            current.workdir_lineno = lineno
    return WorkflowScan(display, tuple(steps))


# --------------------------------------------------------------------------- #
# Reference checks
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Reference:
    """One path-like token found in a ``run`` body."""

    token: str
    lineno: int
    in_container: bool


def _trim_token(token: str) -> str:
    return token.strip(TOKEN_TRIM)


def _is_path_shaped(token: str) -> bool:
    return token.endswith(SCRIPT_SUFFIXES)


def collect_references(step: Step) -> list[Reference]:
    """Return script-position tokens from a step's ``run`` body."""
    references: list[Reference] = []
    for lineno, text in step.run:
        line = _strip_comment(text)
        if not line.strip():
            continue
        container = False
        previous = ""
        for raw_token in line.split():
            token = _trim_token(raw_token)
            if not token:
                previous = ""
                continue
            if token == "docker":
                container = True
            if token.startswith("-"):
                # A flag is never a path; `python -m x` / `bash -x y` stay unwatched.
                previous = token
                continue
            if previous in INTERPRETERS or _is_path_shaped(token):
                references.append(Reference(token, lineno, container))
            previous = token
    return references


def _unclassifiable_reason(token: str) -> str | None:
    if "$" in token:
        return UNCLASSIFIABLE_VARIABLE
    if token.startswith(("/", "~")):
        return UNCLASSIFIABLE_ABSOLUTE
    if "*" in token or "?" in token:
        return UNCLASSIFIABLE_GLOB
    return None


def check_run(step: Step, base: Path, display: str) -> tuple[int, list[Finding]]:
    """Check one step's script references; return the checked count and findings."""
    findings: list[Finding] = []
    checked = 0
    for reference in collect_references(step):
        if reference.in_container:
            continue
        reason = _unclassifiable_reason(reference.token)
        if reason is not None:
            findings.append(
                Finding(
                    WARNING,
                    f"{display}:{reference.lineno}",
                    f"cannot classify {reference.token!r} ({reason})",
                )
            )
            continue
        if "://" in reference.token:
            continue
        relative = reference.token[2:] if reference.token.startswith("./") else reference.token
        if not relative:
            continue
        checked += 1
        if not (base / relative).is_file():
            resolved = f"{(base / relative)}"
            findings.append(
                Finding(
                    ERROR,
                    f"{display}:{reference.lineno}",
                    f"script {reference.token!r} not found (resolved to {resolved!r})",
                )
            )
    return checked, findings


def check_workdir(step: Step, root: Path, display: str) -> tuple[bool, Path | None, Finding | None]:
    """Validate ``working-directory``; return (checked, base, finding)."""
    if step.workdir is None:
        return False, None, None
    value = step.workdir
    reason = _unclassifiable_reason(value)
    if reason is not None:
        return False, None, Finding(
            WARNING,
            f"{display}:{step.workdir_lineno}",
            f"cannot classify working-directory {value!r} ({reason})",
        )
    if ".." in Path(value).parts:
        return False, None, Finding(
            WARNING,
            f"{display}:{step.workdir_lineno}",
            f"cannot classify working-directory {value!r} (contains a parent-directory segment)",
        )
    candidate = root / value
    if not candidate.is_dir():
        return True, None, Finding(
            ERROR,
            f"{display}:{step.workdir_lineno}",
            f"working-directory {value!r} does not exist",
        )
    return True, candidate, None


# --------------------------------------------------------------------------- #
# Evaluation and output
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class WorkflowReport:
    status: str
    workflows: int
    runs: int
    scripts: int
    workdirs: int
    findings: tuple[Finding, ...]

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.status]

    def count(self, severity: str) -> int:
        return sum(1 for finding in self.findings if finding.severity == severity)

    def summary_line(self) -> str:
        return (
            f"summary: check={CHECK_NAME} status={self.status} "
            f"workflows={self.workflows} runs={self.runs} scripts={self.scripts} "
            f"workdirs={self.workdirs} "
            f"errors={self.count(ERROR)} warnings={self.count(WARNING)}"
        )


def unusable_report(
    findings: list[Finding], workflows: int = 0, runs: int = 0
) -> WorkflowReport:
    return WorkflowReport(STATUS_UNUSABLE, workflows, runs, 0, 0, tuple(findings))


def collect_workflows(root: Path) -> list[Path] | None:
    """Return workflow files (sorted), or None when the directory is absent."""
    workflow_dir = root / WORKFLOW_DIR
    if not workflow_dir.is_dir():
        return None
    paths = {path for pattern in WORKFLOW_GLOBS for path in workflow_dir.glob(pattern)}
    return sorted(path for path in paths if path.is_file())


def evaluate(root: Path) -> WorkflowReport:
    paths = collect_workflows(root)
    if paths is None:
        return unusable_report(
            [Finding(FATAL, WORKFLOW_DIR, "workflow directory not found")]
        )
    if not paths:
        return unusable_report([Finding(FATAL, WORKFLOW_DIR, "no workflow files found")])

    findings: list[Finding] = []
    scans: list[WorkflowScan] = []
    for path in paths:
        display = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            findings.append(Finding(FATAL, display, "cannot read file"))
            continue
        scans.append(parse_workflow(text, display))
    if any(finding.severity == FATAL for finding in findings):
        return unusable_report(findings, workflows=len(paths))

    runs = sum(scan.runs for scan in scans)
    scripts = 0
    workdirs = 0
    for scan in scans:
        for step in scan.steps:
            checked_workdir, base, workdir_finding = check_workdir(step, root, scan.path)
            if checked_workdir:
                workdirs += 1
            if workdir_finding is not None:
                findings.append(workdir_finding)
            if not step.run:
                continue
            checked_scripts, run_findings = check_run(
                step, base if base is not None else root, scan.path
            )
            scripts += checked_scripts
            findings.extend(run_findings)

    status = STATUS_ERROR if any(f.severity == ERROR for f in findings) else STATUS_OK
    return WorkflowReport(
        status, len(scans), runs, scripts, workdirs, tuple(findings)
    )


def emit(report: WorkflowReport, stream=None) -> None:
    out = sys.stdout if stream is None else stream
    for finding in report.findings:
        print(finding.render(), file=out)
    print(report.summary_line(), file=out)


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="check_workflow_paths.py",
        description=(
            "Check that workflow files reference existing local scripts and "
            "working directories. Workflow YAML is scanned textually."
        ),
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="repository root to inspect (default: the parent of this script)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root: Path = default_root() if args.root is None else args.root
    if not root.is_dir():
        emit(
            unusable_report([Finding(FATAL, str(root), "root directory not found")])
        )
        return EXIT_CODES[STATUS_UNUSABLE]
    report = evaluate(root)
    emit(report)
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())

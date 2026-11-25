#!/usr/bin/env python3
"""Report whether the Alembic revision files form one linear, valid chain.

Source inspected (paths are relative to the repository root):

    revisions   backend/app/alembic/versions/**/*.py   (revision / down_revision)

Revision files are read with :mod:`ast` only: they are never imported, never
executed, and no database, Alembic environment, or network is touched. Only
module-level ``revision`` and ``down_revision`` assignments are evaluated, and
only when they are string (or ``None``/tuple-of-string) literals.

Severity and exit codes:

    fatal:    exit 2   an input could not be used (missing root or revision dir,
                       unreadable/unparsable file, non-literal declaration)
    error:    exit 1   the history is invalid (duplicate revision id, dangling
                       down_revision, cycle, more than one head, no revisions)
    warning:  exit 0   a revision file declares no down_revision at all

The last line written to stdout is a machine-readable summary:

    summary: check=alembic-chain status=<ok|error|unusable> heads=<id[,id...]|none> \
revisions=<n> errors=<n> warnings=<n>

Heads are the revisions that no other revision names as a parent, sorted by id;
a merge revision (tuple ``down_revision``) collapses branches into one head.
Duplicate ids are reported once per additional declaration, and the first file
in sorted path order supplies the parent edge used for traversal.
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path

CHECK_NAME = "alembic-chain"

VERSIONS_DIR = "backend/app/alembic/versions"

FATAL = "fatal"
ERROR = "error"
WARNING = "warning"

STATUS_OK = "ok"
STATUS_ERROR = "error"
STATUS_UNUSABLE = "unusable"

EXIT_CODES = {STATUS_OK: 0, STATUS_ERROR: 1, STATUS_UNUSABLE: 2}

REVISION_ATTR = "revision"
DOWN_REVISION_ATTR = "down_revision"


# --------------------------------------------------------------------------- #
# Parsing: module-level literals via ast, no import, no execution
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Finding:
    severity: str
    path: str
    message: str

    def render(self) -> str:
        return f"{self.severity}: {self.path}: {self.message}"


@dataclass(frozen=True)
class Revision:
    """One parsed revision file."""

    revision: str
    parents: tuple[str, ...]
    path: str
    lineno: int


@dataclass
class ParsedFile:
    """Either a revision, or the fatal reason the file could not be used."""

    path: str
    lineno: int
    revision: Revision | None = None
    notes: list[Finding] = field(default_factory=list)
    fatal: str | None = None


def _module_level_values(tree: ast.Module) -> dict[str, tuple[ast.expr, int]]:
    """Map a module-level name to its assigned value node and line number.

    Handles ``name = value``, ``name = other = value``, and annotated
    ``name: T = value``. Statements nested in ``if``/``try``/functions are
    deliberately ignored: Alembic reads the module-level names only.
    """
    values: dict[str, tuple[ast.expr, int]] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    values[target.id] = (node.value, node.lineno)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            if isinstance(node.target, ast.Name):
                values[node.target.id] = (node.value, node.lineno)
    return values


def _string_literal(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _string_sequence(node: ast.expr) -> tuple[str, ...] | None:
    """Parse a tuple/list of string literals, as used by merge revisions."""
    if not isinstance(node, (ast.Tuple, ast.List)):
        return None
    parts: list[str] = []
    for element in node.elts:
        text = _string_literal(element)
        if text is None:
            return None
        parts.append(text)
    return tuple(parts)


def _describe_unsupported(node: ast.expr) -> str:
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - defensive, unparse is total in practice
        return type(node).__name__


def parse_revision_file(path: Path, display: str) -> ParsedFile:
    """Read one revision file and extract its ``revision`` / ``down_revision``."""
    parsed = ParsedFile(display, 0)
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        parsed.fatal = "cannot read file"
        return parsed
    try:
        tree = ast.parse(source, filename=display)
    except SyntaxError as exc:
        parsed.fatal = f"cannot parse Python: {exc.msg} (line {exc.lineno})"
        return parsed

    values = _module_level_values(tree)
    if REVISION_ATTR not in values:
        parsed.fatal = f"no module-level {REVISION_ATTR} assignment"
        return parsed

    revision_node, revision_lineno = values[REVISION_ATTR]
    parsed.lineno = revision_lineno
    revision = _string_literal(revision_node)
    if revision is None:
        parsed.fatal = (
            f"{REVISION_ATTR} is not a string literal: {_describe_unsupported(revision_node)}"
        )
        return parsed
    if not revision:
        parsed.fatal = f"{REVISION_ATTR} is an empty string"
        return parsed

    declares_down = DOWN_REVISION_ATTR in values
    parents: tuple[str, ...] = ()
    if declares_down:
        down_node, down_lineno = values[DOWN_REVISION_ATTR]
        if isinstance(down_node, ast.Constant) and down_node.value is None:
            parents = ()
        else:
            literal = _string_literal(down_node)
            if literal is not None:
                if literal:
                    parents = (literal,)
                else:
                    parsed.notes.append(
                        Finding(
                            WARNING,
                            f"{display}:{down_lineno}",
                            f"empty {DOWN_REVISION_ATTR} treated as a root revision",
                        )
                    )
            else:
                sequence = _string_sequence(down_node)
                if sequence is None:
                    parsed.fatal = (
                        f"{DOWN_REVISION_ATTR} is not a string, None, or a tuple of "
                        f"strings: {_describe_unsupported(down_node)}"
                    )
                    return parsed
                parents = sequence
    else:
        parsed.notes.append(
            Finding(
                WARNING,
                f"{display}:{revision_lineno}",
                f"no module-level {DOWN_REVISION_ATTR} assignment; treated as a root revision",
            )
        )

    parsed.revision = Revision(revision, parents, display, revision_lineno)
    return parsed


def collect_files(root: Path) -> list[Path] | None:
    """Return the revision files in sorted order, or None when the dir is absent."""
    versions_dir = root / VERSIONS_DIR
    if not versions_dir.is_dir():
        return None
    return sorted(p for p in versions_dir.rglob("*.py") if p.is_file())


# --------------------------------------------------------------------------- #
# Graph checks
# --------------------------------------------------------------------------- #


def _display(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:  # pragma: no cover - only when --root is not an ancestor
        return path.as_posix()


def find_cycles(edges: dict[str, tuple[str, ...]]) -> list[tuple[str, ...]]:
    """Return each distinct down_revision cycle as a closed path, sorted."""
    white, grey, black = 0, 1, 2
    color = {node: white for node in edges}
    cycles: list[tuple[str, ...]] = []
    seen: set[tuple[str, ...]] = set()

    for start in sorted(edges):
        if color[start] != white:
            continue
        color[start] = grey
        path = [start]
        stack = [(start, list(edges[start]), 0)]
        while stack:
            node, children, index = stack[-1]
            if index >= len(children):
                stack.pop()
                path.pop()
                color[node] = black
                continue
            stack[-1] = (node, children, index + 1)
            child = children[index]
            if child not in color:
                continue  # dangling parent, reported separately
            if color[child] == grey:
                cycle = tuple(path[path.index(child) :]) + (child,)
                if cycle not in seen:
                    seen.add(cycle)
                    cycles.append(cycle)
            elif color[child] == white:
                color[child] = grey
                path.append(child)
                stack.append((child, list(edges[child]), 0))
    return sorted(cycles)


@dataclass(frozen=True)
class ChainReport:
    status: str
    heads: tuple[str, ...]
    revisions: int
    findings: tuple[Finding, ...]

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.status]

    def count(self, severity: str) -> int:
        return sum(1 for finding in self.findings if finding.severity == severity)

    def format_heads(self) -> str:
        return ",".join(self.heads) if self.heads else "none"

    def summary_line(self) -> str:
        return (
            f"summary: check={CHECK_NAME} status={self.status} "
            f"heads={self.format_heads()} revisions={self.revisions} "
            f"errors={self.count(ERROR)} warnings={self.count(WARNING)}"
        )


def unusable_report(findings: list[Finding], revisions: int = 0) -> ChainReport:
    return ChainReport(STATUS_UNUSABLE, (), revisions, tuple(findings))


def evaluate(root: Path) -> ChainReport:
    paths = collect_files(root)
    if paths is None:
        return unusable_report(
            [Finding(FATAL, VERSIONS_DIR, "revision directory not found")]
        )

    parsed_files = [parse_revision_file(path, _display(root, path)) for path in paths]
    revisions = [parsed.revision for parsed in parsed_files if parsed.revision is not None]
    count = len(revisions)

    findings: list[Finding] = []
    fatals: list[Finding] = []
    for parsed in parsed_files:
        findings.extend(parsed.notes)
        if parsed.fatal is not None:
            fatals.append(Finding(FATAL, parsed.path, parsed.fatal))
    if fatals:
        return unusable_report(fatals, count)

    if not revisions:
        findings.append(Finding(ERROR, VERSIONS_DIR, "no revision files found"))
        return ChainReport(STATUS_ERROR, (), 0, tuple(findings))

    # Duplicate ids: report each re-declaration; first sorted path wins the edge.
    by_id: dict[str, Revision] = {}
    for revision in revisions:
        first = by_id.get(revision.revision)
        if first is None:
            by_id[revision.revision] = revision
            continue
        findings.append(
            Finding(
                ERROR,
                f"{revision.path}:{revision.lineno}",
                f"duplicate revision id {revision.revision!r}, "
                f"already declared in {first.path}:{first.lineno}",
            )
        )

    edges = {revision_id: revision.parents for revision_id, revision in by_id.items()}

    for revision_id in sorted(edges):
        revision = by_id[revision_id]
        for parent in revision.parents:
            if parent in edges:
                continue
            findings.append(
                Finding(
                    ERROR,
                    f"{revision.path}:{revision.lineno}",
                    f"down_revision {parent!r} has no matching revision file",
                )
            )

    for cycle in find_cycles(edges):
        entry = by_id.get(cycle[0])
        path = entry.path if entry is not None else VERSIONS_DIR
        findings.append(
            Finding(ERROR, path, f"cycle in down_revision chain: {' -> '.join(cycle)}")
        )

    referenced = {parent for parents in edges.values() for parent in parents}
    heads = tuple(sorted(set(edges) - referenced))
    if len(heads) > 1:
        detail = ", ".join(f"{head} ({by_id[head].path})" for head in heads)
        findings.append(
            Finding(ERROR, VERSIONS_DIR, f"multiple heads resolved: {detail}")
        )

    status = STATUS_ERROR if any(f.severity == ERROR for f in findings) else STATUS_OK
    return ChainReport(status, heads, count, tuple(findings))


def emit(report: ChainReport, stream=None) -> None:
    out = sys.stdout if stream is None else stream
    for finding in report.findings:
        print(finding.render(), file=out)
    print(report.summary_line(), file=out)


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="check_alembic_chain.py",
        description=(
            "Check that the Alembic revision files form one valid, single-headed chain. "
            "Files are parsed with ast; nothing is imported and no database is used."
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
            unusable_report(
                [Finding(FATAL, str(root), "root directory not found")], revisions=0
            )
        )
        return EXIT_CODES[STATUS_UNUSABLE]
    report = evaluate(root)
    emit(report)
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())

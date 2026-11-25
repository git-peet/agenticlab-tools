#!/usr/bin/env python3
"""Report whether compose interpolations and ``.env.example`` agree.

Sources inspected (paths are relative to the repository root):

    compose files   compose*.yml, compose*.yaml
    documentation   .env.example

Compose files are read as UTF-8 text; no YAML parser is involved, which is
enough to find variable interpolations in any scalar, label, command, or
environment value. ``.env`` and every other ``.env.*`` file are never read:
``.env.example`` is the template that a fresh clone starts from.

Interpolations understood (Docker Compose forms):

    ${VAR:?message}   required   the value must be set, or Compose fails
    ${VAR?message}    required   the same, for unset-but-may-be-empty values
    ${VAR:-default}   optional   falls back to a default
    ${VAR-default}    optional   the same, only when unset
    ${VAR:+alternate} optional   replaced when set
    ${VAR+alternate}  optional   the same, only when set
    ${VAR}            optional   may be supplied from the environment
    $VAR              optional   the short form of ``${VAR}``
    $$                literal    an escaped dollar sign, not an interpolation

Nested forms such as ``${A:-${B}}`` contribute both variables. A ``#`` that
starts a comment (at the beginning of a line or after whitespace, outside
quotes) is ignored so prose cannot produce findings.

Documentation is read as one ``KEY=value`` per line in ``.env.example``;
blank lines, ``#`` comments, an optional ``export`` prefix, and quoted or
empty values are understood. Only the key matters, and a key is documented
even when its value is empty.

Severity and exit codes:

    fatal:    exit 2   an input could not be used (missing root, no compose
                       files, missing or unreadable ``.env.example``,
                       unreadable or non-UTF-8 compose file)
    error:    exit 1   a required interpolation (``:?`` or ``?``) is not
                       documented in ``.env.example``
    warning:  exit 0   an optional interpolation is not documented
    info:     exit 0   a variable is documented in ``.env.example`` but is
                       interpolated in no compose file

The last line written to stdout is a machine-readable summary:

    summary: check=compose-env status=<ok|error|unusable> files=<n> \
documented=<n> interpolations=<n> errors=<n> warnings=<n> info=<n>

``files`` counts the compose files read, ``documented`` the keys in
``.env.example``, and ``interpolations`` every occurrence examined, including
repeated uses of one variable.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

CHECK_NAME = "compose-env"

COMPOSE_GLOBS = ("compose*.yml", "compose*.yaml")
ENV_EXAMPLE_PATH = ".env.example"

FATAL = "fatal"
ERROR = "error"
WARNING = "warning"
INFO = "info"

STATUS_OK = "ok"
STATUS_ERROR = "error"
STATUS_UNUSABLE = "unusable"

EXIT_CODES = {STATUS_OK: 0, STATUS_ERROR: 1, STATUS_UNUSABLE: 2}

_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_REQUIRED_OPERATORS = (":?", "?")
_EXPORT_PREFIX = "export "


# --------------------------------------------------------------------------- #
# Textual interpolation parsing
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Finding:
    severity: str
    path: str
    message: str

    def render(self) -> str:
        return f"{self.severity}: {self.path}: {self.message}"


@dataclass(frozen=True)
class Interpolation:
    """One ``$``-interpolation found in a compose file."""

    name: str
    required: bool
    raw: str
    lineno: int


def strip_comment(line: str) -> str:
    """Drop a YAML comment: a ``#`` at line start or after whitespace, unquoted."""
    quote: str | None = None
    for index, char in enumerate(line):
        if quote is not None:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "#" and (index == 0 or line[index - 1].isspace()):
            return line[:index]
    return line


def _matching_brace(text: str, start: int) -> int | None:
    """Return the index of the ``}`` closing a ``${`` whose body starts at start."""
    depth = 1
    index = start
    while index < len(text):
        if text.startswith("${", index):
            depth += 1
            index += 2
            continue
        if text[index] == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _parse_braced(body: str, raw: str, lineno: int) -> tuple[Interpolation, str] | None:
    """Parse the body of ``${...}``; return the interpolation and its argument."""
    match = _NAME_RE.match(body)
    if match is None:
        return None
    name = match.group()
    rest = body[match.end() :]
    required = any(rest.startswith(operator) for operator in _REQUIRED_OPERATORS)
    return Interpolation(name, required, raw, lineno), rest


def extract_interpolations(text: str) -> list[Interpolation]:
    """Return every interpolation in a compose file, in source order."""
    found: list[Interpolation] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        _scan_line(strip_comment(line), lineno, found)
    return found


def _scan_line(text: str, lineno: int, found: list[Interpolation]) -> None:
    index = 0
    while index < len(text):
        char = text[index]
        if char != "$":
            index += 1
            continue
        if text.startswith("$$", index):
            index += 2
            continue
        if text.startswith("${", index):
            end = _matching_brace(text, index + 2)
            if end is None:
                index += 2
                continue
            raw = text[index : end + 1]
            parsed = _parse_braced(text[index + 2 : end], raw, lineno)
            if parsed is not None:
                occurrence, argument = parsed
                found.append(occurrence)
                _scan_line(argument, lineno, found)
            index = end + 1
            continue
        match = _NAME_RE.match(text, index + 1)
        if match is not None:
            found.append(
                Interpolation(match.group(), False, f"${match.group()}", lineno)
            )
            index = match.end()
            continue
        index += 1


# --------------------------------------------------------------------------- #
# .env.example parsing
# --------------------------------------------------------------------------- #


def parse_env_example(text: str) -> list[tuple[str, int]]:
    """Return ``(key, lineno)`` for each documented key, first declaration wins."""
    documented: dict[str, int] = {}
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith(_EXPORT_PREFIX):
            stripped = stripped[len(_EXPORT_PREFIX) :].lstrip()
        key, separator, _value = stripped.partition("=")
        key = key.strip()
        if not separator or _NAME_RE.fullmatch(key) is None:
            continue
        documented.setdefault(key, lineno)
    return list(documented.items())


# --------------------------------------------------------------------------- #
# Evaluation and output
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ComposeReport:
    status: str
    files: int
    documented: int
    interpolations: int
    findings: tuple[Finding, ...]

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.status]

    def count(self, severity: str) -> int:
        return sum(1 for finding in self.findings if finding.severity == severity)

    def summary_line(self) -> str:
        return (
            f"summary: check={CHECK_NAME} status={self.status} "
            f"files={self.files} documented={self.documented} "
            f"interpolations={self.interpolations} "
            f"errors={self.count(ERROR)} warnings={self.count(WARNING)} "
            f"info={self.count(INFO)}"
        )


def unusable_report(
    findings: list[Finding],
    files: int = 0,
    documented: int = 0,
    interpolations: int = 0,
) -> ComposeReport:
    return ComposeReport(STATUS_UNUSABLE, files, documented, interpolations, tuple(findings))


def iter_compose_files(root: Path) -> list[Path]:
    """Return the compose files in sorted order."""
    paths = {
        path
        for pattern in COMPOSE_GLOBS
        for path in root.glob(pattern)
        if path.is_file()
    }
    return sorted(paths)


def _display(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:  # pragma: no cover - only when --root is not an ancestor
        return path.as_posix()


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _render_interpolation(occurrence: Interpolation) -> str:
    if occurrence.required:
        return f"required interpolation {occurrence.raw!r} is not documented"
    return f"optional interpolation {occurrence.raw!r} is not documented"


def evaluate(root: Path) -> ComposeReport:
    compose_paths = iter_compose_files(root)
    if not compose_paths:
        return unusable_report(
            [Finding(FATAL, COMPOSE_GLOBS[0], "no compose files found")]
        )

    env_path = root / ENV_EXAMPLE_PATH
    if not env_path.is_file():
        return unusable_report(
            [Finding(FATAL, ENV_EXAMPLE_PATH, "environment example not found")]
        )
    env_text = _read_text(env_path)
    if env_text is None:
        return unusable_report([Finding(FATAL, ENV_EXAMPLE_PATH, "cannot read file")])

    findings: list[Finding] = []
    documented = parse_env_example(env_text)
    documented_keys = {key for key, _lineno in documented}

    scans: list[tuple[str, list[Interpolation]]] = []
    for path in compose_paths:
        display = _display(root, path)
        text = _read_text(path)
        if text is None:
            findings.append(Finding(FATAL, display, "cannot read file"))
            continue
        scans.append((display, extract_interpolations(text)))
    if any(finding.severity == FATAL for finding in findings):
        return unusable_report(findings, files=len(compose_paths), documented=len(documented))

    interpolations = 0
    used: set[str] = set()
    for display, occurrences in scans:
        for occurrence in occurrences:
            interpolations += 1
            used.add(occurrence.name)
            if occurrence.name in documented_keys:
                continue
            findings.append(
                Finding(
                    ERROR if occurrence.required else WARNING,
                    f"{display}:{occurrence.lineno}",
                    _render_interpolation(occurrence),
                )
            )

    for key, lineno in documented:
        if key in used:
            continue
        findings.append(
            Finding(
                INFO,
                f"{ENV_EXAMPLE_PATH}:{lineno}",
                f"variable {key} is documented but not interpolated in any compose file",
            )
        )

    status = STATUS_ERROR if any(f.severity == ERROR for f in findings) else STATUS_OK
    return ComposeReport(
        status,
        len(compose_paths),
        len(documented),
        interpolations,
        tuple(findings),
    )


def emit(report: ComposeReport, stream=None) -> None:
    out = sys.stdout if stream is None else stream
    for finding in report.findings:
        print(finding.render(), file=out)
    print(report.summary_line(), file=out)


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="check_compose_env.py",
        description=(
            "Check that compose interpolations and .env.example agree. "
            "Compose files and .env.example are read as text; .env is never read."
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
        emit(unusable_report([Finding(FATAL, str(root), "root directory not found")]))
        return EXIT_CODES[STATUS_UNUSABLE]
    report = evaluate(root)
    emit(report)
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())

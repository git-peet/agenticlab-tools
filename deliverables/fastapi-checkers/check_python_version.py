#!/usr/bin/env python3
"""Report whether every declared Python runtime version in this repository agrees.

Sources inspected (paths are relative to the repository root):

    python-version-file   .python-version
    requires-python       backend/pyproject.toml   (project.requires-python)
    dockerfile            backend/Dockerfile       (FROM python:<tag>)
    ci                    .github/workflows/*.yml  (python-version-file / python-version)

``.python-version`` is the required source; the other three are optional. A
missing optional source is a warning, an absent required source is an error.

Severity and exit codes:

    fatal:    exit 2   an input could not be used (missing root, unparsable source)
    error:    exit 1   the sources disagree, or a required source is missing
    warning:  exit 0   an optional source is missing, or a declaration is unclassifiable

The last line written to stdout is a machine-readable summary:

    summary: check=python-version status=<ok|error|unusable> resolved=<X.Y|none> \
sources=<found>/<expected> errors=<n> warnings=<n>

Versions are compared by ``(major, minor)``. ``requires-python`` additionally
must be satisfied by the resolved version, and its lower bound must match it.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CHECK_NAME = "python-version"

PY_VERSION_PATH = ".python-version"
PYPROJECT_PATH = "backend/pyproject.toml"
DOCKERFILE_PATH = "backend/Dockerfile"
CI_DIR = ".github/workflows"
CI_GLOBS = ("*.yml", "*.yaml")

SOURCE_ORDER = ("python-version-file", "requires-python", "dockerfile", "ci")
EXPECTED_SOURCES = len(SOURCE_ORDER)

FATAL = "fatal"
ERROR = "error"
WARNING = "warning"

STATUS_OK = "ok"
STATUS_ERROR = "error"
STATUS_UNUSABLE = "unusable"

EXIT_CODES = {STATUS_OK: 0, STATUS_ERROR: 1, STATUS_UNUSABLE: 2}

Version = tuple[int, int]

_NUMBER_RE = re.compile(r"^\d+$")
_VERSION_RE = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)(?:\.\d+)?$")
_IMAGE_TAG_RE = re.compile(r"^(?P<version>\d+(?:\.\d+){0,2})(?:[-@].*)?$")
_FROM_RE = re.compile(r"^\s*FROM\s+(?:--platform=\S+\s+)?(?P<image>[^\s#]+)", re.IGNORECASE)
_SPECIFIER_RE = re.compile(r"^(?P<op>~=|==|!=|<=|>=|<|>)(?P<version>\d+(?:\.\d+){0,2})$")
_CI_FILE_RE = re.compile(r"^\s*-?\s*python-version-file\s*:\s*(?P<value>\S.*)$")
_CI_LITERAL_RE = re.compile(r"^\s*-?\s*python-version\s*:\s*(?P<value>\S.*)$")
_CI_DELEGATED_TARGETS = frozenset({".python-version", "./.python-version"})


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #


def parse_version(text: str) -> Version | None:
    """Parse ``3.14`` / ``3.14.8`` into ``(3, 14)``; return None if unusable."""
    match = _VERSION_RE.match(text.strip())
    if match is None:
        return None
    return (int(match["major"]), int(match["minor"]))


def format_version(version: Version | None) -> str:
    return "none" if version is None else f"{version[0]}.{version[1]}"


@dataclass(frozen=True)
class Specifier:
    op: str
    version: tuple[int, ...]

    def _padded(self, width: int = 3) -> tuple[int, ...]:
        return self.version + (0,) * (width - len(self.version))

    def satisfied_by(self, target: tuple[int, int, int]) -> bool:
        bound = self._padded(len(target))
        if self.op == ">=":
            return target >= bound
        if self.op == ">":
            return target > bound
        if self.op == "<=":
            return target <= bound
        if self.op == "<":
            return target < bound
        if self.op == "==":
            return target == bound
        if self.op == "!=":
            return target != bound
        if self.op == "~=":
            if len(self.version) < 2:
                return target == bound
            upper = (self.version[0], self.version[1] + 1) + (0,) * (len(target) - 2)
            return target >= bound and target < upper
        return False


@dataclass(frozen=True)
class RequiresPython:
    text: str
    specifiers: tuple[Specifier, ...]

    @property
    def floor(self) -> Version | None:
        """Lowest declared lower bound, compared as ``(major, minor)``."""
        bounds = [
            spec.version
            for spec in self.specifiers
            if spec.op in (">=", ">", "~=", "==") and len(spec.version) >= 2
        ]
        if not bounds:
            return None
        lowest = min(bounds)
        return (lowest[0], lowest[1])

    def contains(self, version: Version) -> bool:
        target = (version[0], version[1], 0)
        return all(spec.satisfied_by(target) for spec in self.specifiers)


def parse_requires_python(text: str) -> RequiresPython | None:
    """Parse the comma-separated specifier set; None when any part is unusable."""
    specifiers: list[Specifier] = []
    for part in text.split(","):
        stripped = part.strip()
        if not stripped:
            return None
        match = _SPECIFIER_RE.match(stripped)
        if match is None:
            return None
        version = tuple(int(piece) for piece in match["version"].split("."))
        specifiers.append(Specifier(match["op"], version))
    if not specifiers:
        return None
    return RequiresPython(text.strip(), tuple(specifiers))


# --------------------------------------------------------------------------- #
# Source readers
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Finding:
    severity: str
    path: str
    message: str

    def render(self) -> str:
        return f"{self.severity}: {self.path}: {self.message}"


@dataclass
class SourceReport:
    """What one source contributed: its value, its warnings, or why it failed."""

    source_id: str
    display: str
    present: bool = False
    version: Version | None = None
    requires: RequiresPython | None = None
    declarations: list[tuple[str, int, Version]] = field(default_factory=list)
    notes: list[Finding] = field(default_factory=list)
    fatal: str | None = None


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def read_python_version_file(root: Path) -> SourceReport:
    report = SourceReport("python-version-file", PY_VERSION_PATH)
    path = root / PY_VERSION_PATH
    if not path.is_file():
        return report
    report.present = True
    text = _read_text(path)
    if text is None:
        report.fatal = "cannot read file"
        return report
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        version = parse_version(stripped)
        if version is None:
            report.fatal = f"cannot parse a version from {stripped!r}"
            return report
        report.version = version
        return report
    report.fatal = "file declares no version"
    return report


def read_requires_python(root: Path) -> SourceReport:
    report = SourceReport("requires-python", PYPROJECT_PATH)
    path = root / PYPROJECT_PATH
    if not path.is_file():
        return report
    report.present = True
    text = _read_text(path)
    if text is None:
        report.fatal = "cannot read file"
        return report
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        report.fatal = f"cannot parse TOML: {exc}"
        return report
    project = data.get("project") if isinstance(data, dict) else None
    requires = project.get("requires-python") if isinstance(project, dict) else None
    if not isinstance(requires, str) or not requires.strip():
        report.notes.append(Finding(WARNING, PYPROJECT_PATH, "no requires-python declared"))
        return report
    parsed = parse_requires_python(requires)
    if parsed is None:
        report.fatal = f"cannot parse requires-python {requires!r}"
        return report
    report.requires = parsed
    return report


def read_dockerfile(root: Path) -> SourceReport:
    report = SourceReport("dockerfile", DOCKERFILE_PATH)
    path = root / DOCKERFILE_PATH
    if not path.is_file():
        return report
    report.present = True
    text = _read_text(path)
    if text is None:
        report.fatal = "cannot read file"
        return report
    tag: str | None = None
    lineno = 0
    for index, line in enumerate(text.splitlines(), start=1):
        match = _FROM_RE.match(line)
        if match is None:
            continue
        image = match["image"]
        if image.lower().startswith("python:"):
            lineno, tag = index, image.split(":", 1)[1].split("@", 1)[0]
            break
    if tag is None:
        report.fatal = "no python base image (FROM python:<tag>) found"
        return report
    match = _IMAGE_TAG_RE.match(tag)
    if match is None:
        report.notes.append(
            Finding(WARNING, f"{DOCKERFILE_PATH}:{lineno}", f"unclassifiable image tag {tag!r}")
        )
        return report
    declared = match["version"]
    if "." not in declared:
        report.notes.append(
            Finding(WARNING, f"{DOCKERFILE_PATH}:{lineno}", f"coarse image tag {tag!r}")
        )
        return report
    version = parse_version(declared)
    if version is None:
        report.notes.append(
            Finding(WARNING, f"{DOCKERFILE_PATH}:{lineno}", f"unclassifiable image tag {tag!r}")
        )
        return report
    report.version = version
    return report


def _strip_quotes(value: str) -> str:
    return value.strip().strip("'\"")


def read_ci(root: Path) -> SourceReport:
    report = SourceReport("ci", CI_DIR)
    ci_dir = root / CI_DIR
    if not ci_dir.is_dir():
        return report
    paths = sorted({path for pattern in CI_GLOBS for path in ci_dir.glob(pattern)})
    for path in paths:
        display = f"{CI_DIR}/{path.name}"
        text = _read_text(path)
        if text is None:
            report.fatal = f"cannot read file {display}"
            return report
        for lineno, line in enumerate(text.splitlines(), start=1):
            code = line.split("#", 1)[0]
            file_match = _CI_FILE_RE.match(code)
            if file_match is not None:
                report.present = True
                target = _strip_quotes(file_match["value"])
                if target.startswith("${{"):
                    report.notes.append(
                        Finding(WARNING, f"{display}:{lineno}", "templated python-version-file")
                    )
                elif target not in _CI_DELEGATED_TARGETS:
                    report.notes.append(
                        Finding(
                            WARNING,
                            f"{display}:{lineno}",
                            f"python-version-file points to {target!r}, which is not inspected",
                        )
                    )
                continue
            literal_match = _CI_LITERAL_RE.match(code)
            if literal_match is None:
                continue
            report.present = True
            literal = _strip_quotes(literal_match["value"])
            if literal.startswith("${{"):
                report.notes.append(
                    Finding(WARNING, f"{display}:{lineno}", "templated python-version")
                )
                continue
            version = parse_version(literal)
            if version is None:
                detail = "coarse" if _NUMBER_RE.match(literal) else "unclassifiable"
                report.notes.append(
                    Finding(WARNING, f"{display}:{lineno}", f"{detail} python-version {literal!r}")
                )
                continue
            report.declarations.append((display, lineno, version))
    return report


def collect_reports(root: Path) -> list[SourceReport]:
    return [
        read_python_version_file(root),
        read_requires_python(root),
        read_dockerfile(root),
        read_ci(root),
    ]


# --------------------------------------------------------------------------- #
# Evaluation and output
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Report:
    status: str
    resolved: Version | None
    present: int
    findings: tuple[Finding, ...]

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.status]

    def count(self, severity: str) -> int:
        return sum(1 for finding in self.findings if finding.severity == severity)

    def summary_line(self) -> str:
        return (
            f"summary: check={CHECK_NAME} status={self.status} "
            f"resolved={format_version(self.resolved)} "
            f"sources={self.present}/{EXPECTED_SOURCES} "
            f"errors={self.count(ERROR)} warnings={self.count(WARNING)}"
        )


def unusable_report(findings: list[Finding], present: int = 0) -> Report:
    return Report(STATUS_UNUSABLE, None, present, tuple(findings))


def evaluate(reports: list[SourceReport], present: int | None = None) -> Report:
    by_id = {report.source_id: report for report in reports}
    present = sum(1 for report in reports if report.present) if present is None else present

    fatals = [
        Finding(FATAL, report.display, report.fatal)
        for report in reports
        if report.fatal is not None
    ]
    if fatals:
        return unusable_report(fatals, present)

    primary = by_id["python-version-file"]
    resolved = primary.version
    findings: list[Finding] = []
    if resolved is None:
        findings.append(Finding(ERROR, PY_VERSION_PATH, "required source missing"))

    for source_id in SOURCE_ORDER[1:]:
        report = by_id[source_id]
        if not report.present:
            findings.append(Finding(WARNING, report.display, "source missing"))
            continue
        findings.extend(report.notes)
        if resolved is None:
            continue
        if source_id == "requires-python":
            requires = report.requires
            if requires is None:
                continue
            if not requires.contains(resolved):
                findings.append(
                    Finding(
                        ERROR,
                        report.display,
                        f"resolved {format_version(resolved)} (from {PY_VERSION_PATH}) does not "
                        f"satisfy requires-python {requires.text!r}",
                    )
                )
            elif requires.floor is not None and requires.floor != resolved:
                findings.append(
                    Finding(
                        ERROR,
                        report.display,
                        f"requires-python floor {format_version(requires.floor)} does not "
                        f"match resolved {format_version(resolved)} (from {PY_VERSION_PATH})",
                    )
                )
        elif source_id == "dockerfile":
            if report.version is not None and report.version != resolved:
                findings.append(
                    Finding(
                        ERROR,
                        report.display,
                        f"base image pins {format_version(report.version)}, "
                        f"{PY_VERSION_PATH} resolves {format_version(resolved)}",
                    )
                )
        elif source_id == "ci":
            for display, lineno, version in report.declarations:
                if version != resolved:
                    findings.append(
                        Finding(
                            ERROR,
                            f"{display}:{lineno}",
                            f"python-version {format_version(version)} does not match "
                            f"resolved {format_version(resolved)} (from {PY_VERSION_PATH})",
                        )
                    )

    status = STATUS_ERROR if any(f.severity == ERROR for f in findings) else STATUS_OK
    return Report(status, resolved, present, tuple(findings))


def emit(report: Report, stream=None) -> None:
    out = sys.stdout if stream is None else stream
    for finding in report.findings:
        print(finding.render(), file=out)
    print(report.summary_line(), file=out)


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="check_python_version.py",
        description="Check that the declared Python runtime versions agree.",
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
                [Finding(FATAL, str(root), "root directory not found")], present=0
            )
        )
        return EXIT_CODES[STATUS_UNUSABLE]
    report = evaluate(collect_reports(root))
    emit(report)
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Report whether Markdown link targets in this repository resolve to real paths.

Pages inspected (paths are relative to the repository root):

    root pages   *.md
    docs         docs/**/*.md
    packages     backend/README.md, frontend/README.md, packages/*/README.md

``AgenticLab/``, ``.pi/``, ``.agents/``, and ``.claude/`` are workspace
integration layers, not documentation of this project, and are outside the
scan set. Every page is read as UTF-8 text; nothing is fetched.

A link target is the text between ``](`` and the matching ``)``, so both halves
of a nested badge link (``[![alt](image)](href)``) are checked. Fenced code
blocks and inline code spans are ignored so examples cannot produce findings,
and these target forms are understood:

    <path with spaces>      angle brackets are stripped before resolving
    path "title"            a quoted parenthetical title is stripped
    file.md#section         a fragment (and ?query) is stripped
    my%20file.md            percent-encoding is decoded

Resolved targets are checked relative to the page that contains them:

    error:    exit 1   the target does not exist (missing file or directory)
    warning:  exit 0   the target escapes the repository root, or is
                       site-root relative (``/path``) and so cannot be verified
                       locally

Scheme-bearing targets (``https:``, ``mailto:``, ``tel:``, ...), pure
``#anchor`` targets, and empty targets are ignored and not counted.
Reference-style definitions (``[label]: target``) and HTML ``href`` attributes
are not Markdown link syntax and are not inspected; a target must have its
``](target)`` on one line.

Severity and exit codes:

    fatal:    exit 2   an input could not be used (missing root, no Markdown
                       pages, unreadable or non-UTF-8 page)
    error:    exit 1   a relative target does not exist
    warning:  exit 0   a target could not be resolved inside the repository

The last line written to stdout is a machine-readable summary:

    summary: check=doc-links status=<ok|error|unusable> files=<n> links=<n> \
errors=<n> warnings=<n>

``links`` counts the relative targets examined, including escaping ones.
"""

from __future__ import annotations

import argparse
import posixpath
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

CHECK_NAME = "doc-links"

ROOT_GLOB = "*.md"
DOCS_GLOB = "docs/**/*.md"
EXPLICIT_PAGES = ("backend/README.md", "frontend/README.md")
PACKAGES_GLOB = "packages/*/README.md"

FATAL = "fatal"
ERROR = "error"
WARNING = "warning"

STATUS_OK = "ok"
STATUS_ERROR = "error"
STATUS_UNUSABLE = "unusable"

EXIT_CODES = {STATUS_OK: 0, STATUS_ERROR: 1, STATUS_UNUSABLE: 2}

FENCE_MARKERS = ("```", "~~~")
_INLINE_CODE_RE = re.compile(r"(`+)(.+?)\1")
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")
_TITLE_STARTS = "\"'("


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Finding:
    severity: str
    path: str
    message: str

    def render(self) -> str:
        return f"{self.severity}: {self.path}: {self.message}"


def mask_code(line: str) -> str:
    """Blank out inline code spans so their ``](target)`` text is not read."""
    return _INLINE_CODE_RE.sub(lambda match: " " * len(match.group(0)), line)


def extract_targets(line: str) -> list[str]:
    """Return raw targets of every ``](...)`` on one line, parens balanced."""
    targets: list[str] = []
    index = 0
    while True:
        start = line.find("](", index)
        if start == -1:
            return targets
        cursor = start + 2
        depth = 1
        buffer: list[str] = []
        while cursor < len(line):
            char = line[cursor]
            if char == "\\" and cursor + 1 < len(line):
                buffer.append(line[cursor + 1])
                cursor += 2
                continue
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    break
            buffer.append(char)
            cursor += 1
        if depth != 0:
            return targets
        targets.append("".join(buffer))
        index = cursor + 1


def normalize_target(raw: str) -> str:
    """Strip angle brackets and any trailing link title from a target."""
    value = raw.strip()
    if value.startswith("<"):
        end = value.find(">")
        if end != -1:
            return value[1:end].strip()
    for index in range(len(value) - 1, -1, -1):
        if value[index].isspace():
            remainder = value[index + 1 :]
            if remainder[:1] in _TITLE_STARTS:
                return value[:index].strip()
    return value


def scan_page(root: Path, text: str, display: str) -> tuple[list[Finding], int]:
    """Check one page; return its findings and the number of targets examined."""
    base = posixpath.dirname(display)
    findings: list[Finding] = []
    links = 0
    fence: str | None = None
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip()
        if fence is not None:
            if stripped.startswith(fence):
                fence = None
            continue
        opening = next(
            (marker for marker in FENCE_MARKERS if stripped.startswith(marker)), None
        )
        if opening is not None:
            fence = opening
            continue

        for raw in extract_targets(mask_code(line)):
            target = normalize_target(raw)
            if not target or _SCHEME_RE.match(target) or target.startswith("#"):
                continue
            path_part = target.split("#", 1)[0].split("?", 1)[0]
            if not path_part:
                continue
            links += 1
            location = f"{display}:{lineno}"
            if path_part.startswith("/"):
                findings.append(
                    Finding(
                        WARNING,
                        location,
                        f"link target {target!r} is site-root relative and "
                        f"cannot be verified locally",
                    )
                )
                continue
            resolved = posixpath.normpath(posixpath.join(base, unquote(path_part)))
            if resolved == ".." or resolved.startswith("../"):
                findings.append(
                    Finding(
                        WARNING,
                        location,
                        f"link target {target!r} escapes the repository root",
                    )
                )
                continue
            try:
                exists = (root / resolved).exists()
            except (OSError, ValueError):
                exists = False
            if not exists:
                findings.append(
                    Finding(
                        ERROR,
                        location,
                        f"link target {target!r} does not exist "
                        f"(resolved to {resolved!r})",
                    )
                )
    return findings, links


# --------------------------------------------------------------------------- #
# Evaluation and output
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DocLinkReport:
    status: str
    files: int
    links: int
    findings: tuple[Finding, ...]

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.status]

    def count(self, severity: str) -> int:
        return sum(1 for finding in self.findings if finding.severity == severity)

    def summary_line(self) -> str:
        return (
            f"summary: check={CHECK_NAME} status={self.status} "
            f"files={self.files} links={self.links} "
            f"errors={self.count(ERROR)} warnings={self.count(WARNING)}"
        )


def unusable_report(findings: list[Finding], files: int = 0) -> DocLinkReport:
    return DocLinkReport(STATUS_UNUSABLE, files, 0, tuple(findings))


def iter_pages(root: Path) -> list[Path]:
    """Return the in-scope Markdown pages in sorted, deduplicated order."""
    pages = set(root.glob(ROOT_GLOB))
    pages.update(root.glob(DOCS_GLOB))
    pages.update(root.glob(PACKAGES_GLOB))
    for relative in EXPLICIT_PAGES:
        candidate = root / relative
        if candidate.is_file():
            pages.add(candidate)
    return sorted(path for path in pages if path.is_file())


def _display(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:  # pragma: no cover - only when --root is not an ancestor
        return path.as_posix()


def evaluate(root: Path) -> DocLinkReport:
    pages = iter_pages(root)
    if not pages:
        return unusable_report([Finding(FATAL, ".", "no markdown pages found")])

    findings: list[Finding] = []
    links = 0
    readable = 0
    for path in pages:
        display = _display(root, path)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            findings.append(Finding(FATAL, display, "cannot read file"))
            continue
        readable += 1
        page_findings, page_links = scan_page(root, text, display)
        findings.extend(page_findings)
        links += page_links

    if any(finding.severity == FATAL for finding in findings):
        return unusable_report(findings, readable)

    status = STATUS_ERROR if any(f.severity == ERROR for f in findings) else STATUS_OK
    return DocLinkReport(status, readable, links, tuple(findings))


def emit(report: DocLinkReport, stream=None) -> None:
    out = sys.stdout if stream is None else stream
    for finding in report.findings:
        print(finding.render(), file=out)
    print(report.summary_line(), file=out)


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="check_doc_links.py",
        description=(
            "Check that relative Markdown link targets resolve inside the "
            "repository. Pages are read as text; nothing is fetched."
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

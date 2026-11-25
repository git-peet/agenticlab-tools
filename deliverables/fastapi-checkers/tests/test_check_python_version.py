"""Tests for ``scripts/check_python_version.py``.

Fixtures are minimal four-file trees in a temporary directory; assertions about
exit codes and the summary line drive the script as a subprocess, so the real
CLI contract is exercised end to end.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SCRIPTS_DIR.parent
SCRIPT = SCRIPTS_DIR / "check_python_version.py"


def load_checker():
    spec = importlib.util.spec_from_file_location("check_python_version", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves annotations through sys.modules, so register first.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CHECKER = load_checker()

BASE_FILES = {
    ".python-version": "3.14\n",
    "backend/pyproject.toml": '[project]\nname = "app"\nrequires-python = ">=3.14,<4.0"\n',
    "backend/Dockerfile": "FROM oven/bun:1 AS frontend-build\n\nFROM python:3.14\n",
    ".github/workflows/test-backend.yml": (
        "jobs:\n"
        "  test-backend:\n"
        "    steps:\n"
        "      - name: Set up Python\n"
        "        uses: actions/setup-python@v7\n"
        "        with:\n"
        "          python-version-file: .python-version\n"
    ),
}


class CheckerTestCase(unittest.TestCase):
    def make_repo(self, overrides=None, remove=()):
        files = dict(BASE_FILES)
        files.update(overrides or {})
        for name in remove:
            files.pop(name, None)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for name, content in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return root

    def run_checker(self, root):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(root)],
            capture_output=True,
            text=True,
            check=False,
        )


class ParsingTests(CheckerTestCase):
    def test_parse_version_normalises_major_minor(self):
        self.assertEqual(CHECKER.parse_version("3.14"), (3, 14))
        self.assertEqual(CHECKER.parse_version(" 3.14.8 "), (3, 14))
        self.assertIsNone(CHECKER.parse_version("banana"))
        self.assertIsNone(CHECKER.parse_version("3"))

    def test_requires_python_range_is_satisfied_by_resolved_version(self):
        # AC5: ">=3.14,<4.0" accepts 3.14 and rejects 3.13 / 4.0.
        requires = CHECKER.parse_requires_python(">=3.14,<4.0")
        self.assertIsNotNone(requires)
        self.assertTrue(requires.contains((3, 14)))
        self.assertFalse(requires.contains((3, 13)))
        self.assertFalse(requires.contains((4, 0)))
        self.assertEqual(requires.floor, (3, 14))


class CleanRepositoryTests(CheckerTestCase):
    def test_current_repository_is_clean(self):
        # AC1: exit 0, one summary line, resolved version reported.
        result = self.run_checker(REPO_ROOT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        summary = [
            line for line in result.stdout.splitlines() if line.startswith("summary: ")
        ]
        self.assertEqual(len(summary), 1)
        self.assertEqual(
            summary[0],
            "summary: check=python-version status=ok resolved=3.14 "
            "sources=4/4 errors=0 warnings=0",
        )

    def test_summary_line_is_last_stdout_line_and_parseable(self):
        result = self.run_checker(REPO_ROOT)
        lines = result.stdout.splitlines()
        self.assertTrue(lines)
        self.assertTrue(lines[-1].startswith("summary: "))
        self.assertEqual(
            len([line for line in lines if line.startswith("summary: ")]), 1
        )
        fields = dict(item.split("=", 1) for item in lines[-1][len("summary: ") :].split())
        self.assertEqual(fields["check"], "python-version")
        self.assertEqual(fields["status"], "ok")
        self.assertEqual(fields["errors"], "0")
        self.assertEqual(fields["warnings"], "0")

    def test_clean_fixture_reports_all_four_sources(self):
        result = self.run_checker(self.make_repo())
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("error:", result.stdout)
        self.assertIn("sources=4/4", result.stdout)


class DisagreementTests(CheckerTestCase):
    def test_python_version_file_mismatch_is_an_error(self):
        # AC2: a single edited source must be detected in a temp copy.
        root = self.make_repo({".python-version": "3.13\n"})
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertTrue(
            any(
                line.startswith("error: ")
                and ".python-version" in line
                and "resolved 3.13" in line
                for line in result.stdout.splitlines()
            ),
            result.stdout,
        )
        self.assertIn("status=error", result.stdout)

    def test_dockerfile_mismatch_is_an_error(self):
        root = self.make_repo(
            {"backend/Dockerfile": "FROM oven/bun:1 AS build\n\nFROM python:3.13\n"}
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertTrue(
            any(
                line.startswith("error: backend/Dockerfile:")
                for line in result.stdout.splitlines()
            ),
            result.stdout,
        )

    def test_ci_literal_mismatch_is_an_error(self):
        root = self.make_repo(
            {
                ".github/workflows/test-backend.yml": (
                    "jobs:\n"
                    "  test:\n"
                    "    steps:\n"
                    "      - uses: actions/setup-python@v7\n"
                    "        with:\n"
                    '          python-version: "3.12"\n'
                )
            }
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertTrue(
            any(
                line.startswith("error: .github/workflows/test-backend.yml:")
                for line in result.stdout.splitlines()
            ),
            result.stdout,
        )

    def test_ci_literal_match_is_accepted(self):
        root = self.make_repo(
            {
                ".github/workflows/test-backend.yml": (
                    "jobs:\n"
                    "  test:\n"
                    "    steps:\n"
                    '      - run: echo "3.14"\n'
                    "        with:\n"
                    '          python-version: "3.14"\n'
                )
            }
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("error:", result.stdout)

    def test_requires_python_floor_drift_is_an_error(self):
        root = self.make_repo(
            {
                "backend/pyproject.toml": (
                    '[project]\nname = "app"\nrequires-python = ">=3.13,<4.0"\n'
                )
            }
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertTrue(
            any(
                line.startswith("error: backend/pyproject.toml:")
                and "floor" in line
                for line in result.stdout.splitlines()
            ),
            result.stdout,
        )


class MissingAndUnusableTests(CheckerTestCase):
    def test_missing_optional_source_is_a_warning(self):
        # AC3: absent optional source -> warning, exit 0.
        result = self.run_checker(self.make_repo(remove=("backend/Dockerfile",)))
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertTrue(
            any(
                line.startswith("warning: backend/Dockerfile: source missing")
                for line in result.stdout.splitlines()
            ),
            result.stdout,
        )
        self.assertIn("sources=3/4", result.stdout)
        self.assertIn("warnings=1", result.stdout)

    def test_unparsable_python_version_file_is_unusable(self):
        # AC4: exit 2 with status=unusable.
        result = self.run_checker(self.make_repo({".python-version": "banana\n"}))
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertTrue(
            any(line.startswith("fatal: .python-version:") for line in result.stdout.splitlines()),
            result.stdout,
        )
        self.assertIn("status=unusable", result.stdout)
        self.assertIn("resolved=none", result.stdout)

    def test_nonexistent_root_is_unusable(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does-not-exist"
            result = self.run_checker(missing)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("root directory not found", result.stdout)
        self.assertIn("status=unusable", result.stdout)


if __name__ == "__main__":
    unittest.main()

"""Tests for ``scripts/check_compose_env.py``.

Fixtures are minimal ``compose*.yml`` plus ``.env.example`` trees in a
temporary directory; assertions about exit codes and the summary line drive
the script as a subprocess, so the real CLI contract is exercised end to end.
The current repository is covered too, which pins the clean baseline (3
compose files, 19 interpolations, 3 optional-interpolation warnings, 4
documented-but-unused infos, exit 0).
"""

from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SCRIPTS_DIR.parent
SCRIPT = SCRIPTS_DIR / "check_compose_env.py"


def load_checker():
    spec = importlib.util.spec_from_file_location("check_compose_env", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CHECKER = load_checker()

BASE_COMPOSE = """services:
  db:
    image: postgres:18
    environment:
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD:?Variable not set}
  backend:
    environment:
      PROJECT_NAME: ${PROJECT_NAME:?Variable not set}
      SMTP_USER: ${SMTP_USER:-}
"""

BASE_ENV = """PROJECT_NAME=FastAPI
POSTGRES_PASSWORD=replace_me
SMTP_USER=
"""


class ComposeTestCase(unittest.TestCase):
    def make_repo(self, files):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for name, content in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                path.write_bytes(content)
            else:
                path.write_text(content, encoding="utf-8")
        return root

    def clean_repo(self, overrides=None, remove=()):
        files = {"compose.yml": BASE_COMPOSE, ".env.example": BASE_ENV}
        files.update(overrides or {})
        for name in remove:
            files.pop(name, None)
        return self.make_repo(files)

    def run_checker(self, root):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(root)],
            capture_output=True,
            text=True,
            check=False,
        )

    def findings(self, result, severity):
        return [
            line
            for line in result.stdout.splitlines()
            if line.startswith(f"{severity}: ")
        ]

    def summary_fields(self, result):
        lines = result.stdout.splitlines()
        self.assertTrue(lines, "no output")
        self.assertTrue(lines[-1].startswith("summary: "), lines[-1])
        self.assertEqual(
            len([line for line in lines if line.startswith("summary: ")]), 1
        )
        return dict(item.split("=", 1) for item in lines[-1][len("summary: ") :].split())


class CurrentRepositoryTests(ComposeTestCase):
    def test_current_repository_exits_zero_with_clean_baseline(self):
        # AC1: exit 0, no undocumented required variable.
        result = self.run_checker(REPO_ROOT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.findings(result, "error"), [], result.stdout)
        self.assertEqual(
            result.stdout.splitlines()[-1],
            "summary: check=compose-env status=ok files=3 documented=13 "
            "interpolations=19 errors=0 warnings=3 info=4",
        )

    def test_current_repository_optional_warnings_are_the_environment_only_vars(self):
        result = self.run_checker(REPO_ROOT)
        warnings = self.findings(result, "warning")
        self.assertEqual(len(warnings), 3, result.stdout)
        for name in ("CI", "SMTP_USER", "SMTP_PASSWORD"):
            self.assertTrue(
                any(name in line and "optional interpolation" in line for line in warnings),
                result.stdout,
            )

    def test_current_repository_reports_documented_but_unused_variables(self):
        result = self.run_checker(REPO_ROOT)
        info = self.findings(result, "info")
        self.assertEqual(len(info), 4, result.stdout)
        for name in ("DATABASE_URL", "SMTP_PORT", "SMTP_TLS", "SMTP_SSL"):
            self.assertTrue(any(name in line for line in info), result.stdout)

    def test_current_repository_summary_is_parseable(self):
        fields = self.summary_fields(self.run_checker(REPO_ROOT))
        self.assertEqual(fields["check"], "compose-env")
        self.assertEqual(fields["status"], "ok")
        self.assertEqual(fields["files"], "3")
        self.assertEqual(fields["errors"], "0")
        self.assertEqual(fields["warnings"], "3")
        self.assertEqual(fields["info"], "4")


class RequiredInterpolationTests(ComposeTestCase):
    def test_undocumented_required_variable_is_an_error(self):
        # AC2: an undocumented `${VAR:?}` must fail.
        root = self.clean_repo(
            {
                "compose.yml": "services:\n  db:\n    environment:\n"
                "      NEW_SECRET: ${NEW_SECRET:?Variable not set}\n",
                ".env.example": "POSTGRES_PASSWORD=x\n",
            }
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        errors = self.findings(result, "error")
        self.assertEqual(len(errors), 1, result.stdout)
        self.assertTrue(errors[0].startswith("error: compose.yml:4: "), errors[0])
        self.assertIn("NEW_SECRET", errors[0])
        self.assertIn("required interpolation", errors[0])
        fields = self.summary_fields(result)
        self.assertEqual(fields["status"], "error")
        self.assertEqual(fields["errors"], "1")

    def test_undocumented_bare_required_form_is_an_error(self):
        root = self.clean_repo(
            {
                "compose.yml": "services:\n  x:\n    image: ${NEW_SECRET?missing}\n",
                ".env.example": "",
            }
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertTrue(
            any("required interpolation" in line for line in self.findings(result, "error")),
            result.stdout,
        )

    def test_documented_required_variable_passes(self):
        root = self.clean_repo()
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.findings(result, "error"), [], result.stdout)
        self.assertEqual(self.summary_fields(result)["interpolations"], "3")

    def test_one_documented_one_undocumented_reports_only_the_missing(self):
        root = self.clean_repo(
            {
                "compose.yml": (
                    "services:\n  x:\n    environment:\n"
                    "      A: ${A:?set}\n"
                    "      B: ${B:?set}\n"
                ),
                ".env.example": "A=1\n",
            }
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        errors = self.findings(result, "error")
        self.assertEqual(len(errors), 1, result.stdout)
        self.assertIn("B", errors[0])
        self.assertNotIn("'${A:?set}'", errors[0])


class OptionalInterpolationTests(ComposeTestCase):
    def test_undocumented_default_value_is_a_warning_and_exits_zero(self):
        root = self.clean_repo(
            {
                "compose.yml": "services:\n  x:\n    image: app:${TAG:-latest}\n",
                ".env.example": "",
            }
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.findings(result, "error"), [], result.stdout)
        warnings = self.findings(result, "warning")
        self.assertEqual(len(warnings), 1, result.stdout)
        self.assertIn("TAG", warnings[0])
        self.assertEqual(self.summary_fields(result)["status"], "ok")

    def test_undocumented_bare_interpolation_is_a_warning(self):
        root = self.clean_repo(
            {
                "compose.yml": "services:\n  x:\n    image: app:${TAG}\n",
                ".env.example": "",
            }
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 0, result.stdout)
        warnings = self.findings(result, "warning")
        self.assertEqual(len(warnings), 1, result.stdout)
        self.assertIn("optional interpolation", warnings[0])

    def test_undocumented_short_form_is_a_warning(self):
        root = self.clean_repo(
            {
                "compose.yml": "services:\n  x:\n    image: app:$TAG\n",
                ".env.example": "",
            }
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 0, result.stdout)
        warnings = self.findings(result, "warning")
        self.assertEqual(len(warnings), 1, result.stdout)
        self.assertIn("TAG", warnings[0])

    def test_undocumented_alternate_form_is_a_warning(self):
        root = self.clean_repo(
            {
                "compose.yml": "services:\n  x:\n    environment:\n      A: ${A:+on}\n",
                ".env.example": "",
            }
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(len(self.findings(result, "warning")), 1, result.stdout)

    def test_repeated_uses_are_reported_per_occurrence(self):
        root = self.clean_repo(
            {
                "compose.yml": (
                    "services:\n  x:\n    image: ${A:-one}\n"
                    "    command: ${A:-one}\n"
                ),
                ".env.example": "",
            }
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(len(self.findings(result, "warning")), 2, result.stdout)
        self.assertEqual(self.summary_fields(result)["interpolations"], "2")


class UnusedDocumentationTests(ComposeTestCase):
    def test_documented_but_unused_variable_is_info_and_exits_zero(self):
        root = self.clean_repo(
            {
                "compose.yml": "services:\n  x:\n    environment:\n      U: ${USED:-}\n",
                ".env.example": "USED=1\nUNUSED=2\n",
            }
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.findings(result, "error"), [], result.stdout)
        self.assertEqual(self.findings(result, "warning"), [], result.stdout)
        info = self.findings(result, "info")
        self.assertEqual(len(info), 1, result.stdout)
        self.assertTrue(info[0].startswith("info: .env.example:2: "), info[0])
        self.assertIn("UNUSED", info[0])
        fields = self.summary_fields(result)
        self.assertEqual(fields["info"], "1")
        self.assertEqual(fields["status"], "ok")

    def test_cross_file_use_counts_as_used(self):
        root = self.clean_repo(
            {
                "compose.yml": "services:\n  x:\n    image: busybox\n",
                "compose.deploy.yml": "services:\n  x:\n    environment:\n"
                "      A: ${A:-}\n",
                ".env.example": "A=1\n",
            }
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.findings(result, "info"), [], result.stdout)
        self.assertEqual(self.summary_fields(result)["files"], "2")


class EnvExampleParsingTests(ComposeTestCase):
    def test_comments_exports_empty_and_quoted_values_document_keys(self):
        root = self.clean_repo(
            {
                "compose.yml": (
                    "services:\n  x:\n    environment:\n"
                    "      A: ${A:?set}\n"
                    "      B: ${B:?set}\n"
                    "      C: ${C:?set}\n"
                    "      D: ${D:?set}\n"
                ),
                ".env.example": (
                    "# comment\n"
                    "\n"
                    "A=\n"
                    "export B=1\n"
                    "  C  =  \"quoted value\"  \n"
                    "D=plain\n"
                ),
            }
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.findings(result, "error"), [], result.stdout)
        self.assertEqual(self.summary_fields(result)["documented"], "4")

    def test_keys_are_case_sensitive(self):
        root = self.clean_repo(
            {
                "compose.yml": "services:\n  x:\n    image: ${lower:?set}\n",
                ".env.example": "LOWER=1\n",
            }
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(len(self.findings(result, "error")), 1, result.stdout)

    def test_non_assignment_lines_do_not_document_keys(self):
        root = self.clean_repo(
            {
                "compose.yml": "services:\n  x:\n    image: ${A:?set}\n",
                ".env.example": "A\nB C=1\n",
            }
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(self.summary_fields(result)["documented"], "0")

    def test_dot_env_is_not_consulted_as_documentation(self):
        # Only .env.example is a template; a real .env may not be read.
        root = self.clean_repo(
            {
                "compose.yml": "services:\n  x:\n    image: ${NEW_SECRET:?set}\n",
                ".env.example": "",
                ".env": "NEW_SECRET=secret\n",
            }
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertNotIn("secret", result.stdout)
        self.assertEqual(len(self.findings(result, "error")), 1, result.stdout)


class ParsingTests(ComposeTestCase):
    def names(self, text):
        return [(item.name, item.required) for item in CHECKER.extract_interpolations(text)]

    def test_required_and_optional_forms_are_classified(self):
        self.assertEqual(
            self.names(
                "${A:?m} ${B?m} ${C:-d} ${D-d} ${E:+x} ${F+x} ${G} $H"
            ),
            [
                ("A", True),
                ("B", True),
                ("C", False),
                ("D", False),
                ("E", False),
                ("F", False),
                ("G", False),
                ("H", False),
            ],
        )

    def test_nested_interpolation_contributes_both_variables(self):
        self.assertEqual(
            self.names("${OUTER:-${INNER}}"),
            [("OUTER", False), ("INNER", False)],
        )

    def test_escaped_dollar_is_not_an_interpolation(self):
        self.assertEqual(self.names("$${LITERAL} $${ALSO}"), [])
        self.assertEqual(self.names("$$${REAL}"), [("REAL", False)])

    def test_positional_and_lone_dollar_are_ignored(self):
        self.assertEqual(self.names("$1 $ ${} ${9BAD} price$"), [])

    def test_inline_comment_is_ignored(self):
        self.assertEqual(self.names("image: app # uses ${COMMENTED}"), [])

    def test_hash_inside_quotes_is_not_a_comment(self):
        self.assertEqual(self.names("command: echo 'a # ${KEPT}'"), [("KEPT", False)])

    def test_unterminated_brace_is_ignored(self):
        self.assertEqual(self.names("${BROKEN"), [])

    def test_line_numbers_point_at_the_interpolation(self):
        occurrences = CHECKER.extract_interpolations("a\nb ${X}\n")
        self.assertEqual([(item.name, item.lineno) for item in occurrences], [("X", 2)])

    def test_env_example_parser_reports_first_declaration_line(self):
        self.assertEqual(
            CHECKER.parse_env_example("# c\nA=1\nA=2\n\nexport B=\n"),
            [("A", 2), ("B", 5)],
        )


class MultipleComposeFileTests(ComposeTestCase):
    def test_yml_and_yaml_compose_files_are_both_scanned(self):
        root = self.clean_repo(
            {
                "compose.yml": "services:\n  x:\n    image: ${A:?set}\n",
                "compose.extra.yaml": "services:\n  y:\n    image: ${B:?set}\n",
                ".env.example": "A=1\nB=2\n",
            }
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 0, result.stdout)
        fields = self.summary_fields(result)
        self.assertEqual(fields["files"], "2")
        self.assertEqual(fields["interpolations"], "2")

    def test_unrelated_yml_files_are_not_scanned(self):
        root = self.clean_repo(
            {"other.yml": "value: ${MISSING:?set}\n"}
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.summary_fields(result)["files"], "1")


class UnusableInputTests(ComposeTestCase):
    def test_missing_root_is_fatal(self):
        result = self.run_checker(Path("/nonexistent-check-compose-env"))
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("status=unusable", result.stdout)

    def test_no_compose_files_is_fatal(self):
        root = self.make_repo({".env.example": "A=1\n"})
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("no compose files found", result.stdout)
        self.assertEqual(self.summary_fields(result)["files"], "0")

    def test_missing_env_example_is_fatal(self):
        root = self.clean_repo(remove=(".env.example",))
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("environment example not found", result.stdout)
        self.assertEqual(self.summary_fields(result)["status"], "unusable")

    def test_non_utf8_compose_file_is_fatal(self):
        root = self.clean_repo({"compose.yml": b"\xff\xfe broken\n"})
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("cannot read file", result.stdout)

    def test_non_utf8_env_example_is_fatal(self):
        root = self.clean_repo({".env.example": b"\xff\xfe broken\n"})
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("cannot read file", result.stdout)


class SafetyTests(ComposeTestCase):
    def test_script_uses_only_standard_library_imports(self):
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
        self.assertTrue(imported)
        self.assertLessEqual(imported, set(sys.stdlib_module_names), imported)

    def test_script_never_opens_a_real_dot_env_file(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('ENV_EXAMPLE_PATH = ".env.example"', source)
        self.assertNotIn('".env"', source)
        self.assertNotIn("'.env'", source)


if __name__ == "__main__":
    unittest.main()

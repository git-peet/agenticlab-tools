"""Tests for ``scripts/check_workflow_paths.py``.

Fixtures are minimal workflow trees in a temporary directory; assertions about
exit codes and the summary line drive the script as a subprocess, so the real
CLI contract is exercised end to end. The current repository is covered too,
which pins the clean baseline.
"""

from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SCRIPTS_DIR.parent
SCRIPT = SCRIPTS_DIR / "check_workflow_paths.py"
WORKFLOW_DIR = ".github/workflows"


def load_checker():
    spec = importlib.util.spec_from_file_location("check_workflow_paths", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CHECKER = load_checker()

STEP_HEADER = (
    "name: fixture\n"
    "on: push\n"
    "jobs:\n"
    "  test:\n"
    "    runs-on: ubuntu-latest\n"
    "    steps:\n"
)


def workflow_yaml(steps):
    """Render a one-job workflow; ``steps`` is dedented relative to column 0."""
    body = textwrap.indent(textwrap.dedent(steps).strip("\n"), "      ")
    return STEP_HEADER + body + "\n"


class WorkflowTestCase(unittest.TestCase):
    BASE_FILES = (
        "scripts/prestart.sh",
        "scripts/generate-client.sh",
        "scripts/prepare_release.py",
        "backend/scripts/prestart.sh",
        "backend/scripts/tests-start.sh",
        "backend/pyproject.toml",
        "frontend/package.json",
        "compose.yml",
    )

    def make_repo(self, workflows, files=()):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for name in (*self.BASE_FILES, *files):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("#!/bin/sh\n", encoding="utf-8")
        workflow_dir = root / WORKFLOW_DIR
        workflow_dir.mkdir(parents=True, exist_ok=True)
        for name, content in workflows.items():
            (workflow_dir / name).write_text(content, encoding="utf-8")
        return root

    def run_checker(self, root):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(root)],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(root) if root.is_dir() else None,
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


class CurrentRepositoryTests(WorkflowTestCase):
    def test_current_repository_is_clean(self):
        result = self.run_checker(REPO_ROOT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.findings(result, "error"), [], result.stdout)
        self.assertEqual(self.findings(result, "warning"), [], result.stdout)
        lines = result.stdout.splitlines()
        self.assertEqual(
            lines[-1],
            "summary: check=workflow-paths status=ok workflows=15 runs=51 "
            "scripts=8 workdirs=7 errors=0 warnings=0",
        )

    def test_current_repository_summary_is_parseable(self):
        fields = self.summary_fields(self.run_checker(REPO_ROOT))
        self.assertEqual(fields["check"], "workflow-paths")
        self.assertEqual(fields["status"], "ok")
        self.assertEqual(fields["errors"], "0")
        self.assertEqual(fields["warnings"], "0")
        for key in ("workflows", "runs", "scripts", "workdirs"):
            self.assertGreater(int(fields[key]), 0, key)

    def test_current_repository_resolves_backend_working_directory(self):
        # test-backend.yml runs `bash scripts/prestart.sh` from backend/.
        report = CHECKER.evaluate(REPO_ROOT)
        self.assertEqual(report.status, "ok")
        self.assertGreaterEqual(report.scripts, 8)
        self.assertGreaterEqual(report.workdirs, 7)


class CleanFixtureTests(WorkflowTestCase):
    def test_existing_scripts_and_directories_pass(self):
        root = self.make_repo(
            {
                "ok.yml": workflow_yaml(
                    """
                    - run: bash scripts/prestart.sh
                    - run: python scripts/prepare_release.py current-version
                    - run: uv run bash scripts/tests-start.sh "Coverage"
                      working-directory: backend
                    - run: bun ci
                      working-directory: frontend
                    """
                )
            }
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 0, result.stdout)
        fields = self.summary_fields(result)
        self.assertEqual(fields["workflows"], "1")
        self.assertEqual(fields["runs"], "4")
        self.assertEqual(fields["scripts"], "3")
        self.assertEqual(fields["workdirs"], "2")
        self.assertEqual(fields["status"], "ok")

    def test_commented_reference_is_ignored(self):
        root = self.make_repo(
            {"ok.yml": workflow_yaml("# run: bash scripts/missing.sh\n")}
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.findings(result, "error"), [])

    def test_interpreter_flags_are_not_treated_as_paths(self):
        root = self.make_repo(
            {
                "flags.yml": workflow_yaml(
                    """
                    - run: python -m pytest backend/app
                    - run: bash -x scripts/prestart.sh
                    - run: python -c "print('ok')"
                    """
                )
            }
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.findings(result, "error"), [], result.stdout)
        self.assertEqual(self.findings(result, "warning"), [], result.stdout)

    def test_non_path_arguments_are_not_references(self):
        root = self.make_repo(
            {
                "ok.yml": workflow_yaml(
                    """
                    - run: bunx playwright merge-reports --reporter html ./all-blob-reports
                    - run: uv run smokeshow upload backend/htmlcov
                    - run: docker compose -f compose.yml build
                    - run: echo "$GITHUB_CONTEXT"
                    - run: uv run prek run --from-ref origin/${GITHUB_BASE_REF} --to-ref HEAD
                    """
                )
            }
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.findings(result, "error"), [], result.stdout)
        self.assertEqual(self.findings(result, "warning"), [], result.stdout)


class MissingScriptTests(WorkflowTestCase):
    def test_missing_script_is_an_error(self):
        root = self.make_repo({"bad.yml": workflow_yaml("- run: bash scripts/gone.sh\n")})
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        errors = self.findings(result, "error")
        self.assertEqual(len(errors), 1, result.stdout)
        self.assertIn(f"{WORKFLOW_DIR}/bad.yml:", errors[0])
        self.assertIn("scripts/gone.sh", errors[0])
        self.assertEqual(self.summary_fields(result)["status"], "error")

    def test_missing_python_script_is_an_error(self):
        root = self.make_repo(
            {"bad.yml": workflow_yaml("- run: uv run python scripts/nope.py prepare\n")}
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertTrue(
            any("scripts/nope.py" in line for line in self.findings(result, "error"))
        )

    def test_script_resolves_against_working_directory(self):
        # `scripts/tests-start.sh` exists only under backend/.
        root = self.make_repo(
            {
                "with-workdir.yml": workflow_yaml(
                    """
                    - run: bash scripts/tests-start.sh
                      working-directory: backend
                    """
                )
            }
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_script_missing_without_working_directory_is_an_error(self):
        root = self.make_repo(
            {"no-workdir.yml": workflow_yaml("- run: bash scripts/tests-start.sh\n")}
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("scripts/tests-start.sh", result.stdout)
        self.assertIn("resolved to", result.stdout)

    def test_multiline_run_block_is_scanned(self):
        root = self.make_repo(
            {
                "block.yml": workflow_yaml(
                    """
                    - name: Prepare release
                      run: |
                        set -euo pipefail
                        version="$(python scripts/prepare_release.py current-version)"
                        bash scripts/does-not-exist.sh "$version"
                    """
                )
            }
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        errors = self.findings(result, "error")
        self.assertTrue(any("scripts/does-not-exist.sh" in line for line in errors))
        self.assertTrue(
            any("block.yml:11:" in line and "does-not-exist" in line for line in errors),
            errors,
        )

    def test_inline_list_step_form_is_parsed(self):
        root = self.make_repo(
            {
                "inline.yml": workflow_yaml(
                    """
                    - run: uv sync
                      working-directory: backend
                    - run: bash scripts/missing-inline.sh
                    """
                )
            }
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertTrue(
            any(
                "scripts/missing-inline.sh" in line
                for line in self.findings(result, "error")
            )
        )


class WorkingDirectoryTests(WorkflowTestCase):
    def test_missing_working_directory_is_an_error(self):
        root = self.make_repo(
            {
                "bad.yml": workflow_yaml(
                    """
                    - run: uv sync
                      working-directory: backend-nope
                    """
                )
            }
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        errors = self.findings(result, "error")
        self.assertEqual(len(errors), 1, result.stdout)
        self.assertIn("working-directory 'backend-nope' does not exist", errors[0])
        self.assertEqual(self.summary_fields(result)["workdirs"], "1")

    def test_working_directory_pointing_at_a_file_is_an_error(self):
        root = self.make_repo(
            {
                "bad.yml": workflow_yaml(
                    """
                    - run: uv sync
                      working-directory: compose.yml
                    """
                )
            }
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("working-directory 'compose.yml' does not exist", result.stdout)


class UnclassifiableReferenceTests(WorkflowTestCase):
    def test_shell_variable_script_path_is_a_warning_not_an_error(self):
        # Required case: the path cannot be resolved, so it must not fail the run.
        root = self.make_repo(
            {
                "vars.yml": workflow_yaml(
                    """
                    - run: bash "$SCRIPTS_DIR/prestart.sh"
                    - run: bash scripts/$NAME.sh
                    """
                )
            }
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.findings(result, "error"), [], result.stdout)
        warnings = self.findings(result, "warning")
        self.assertEqual(len(warnings), 2, result.stdout)
        self.assertTrue(any("shell variable" in line for line in warnings))
        fields = self.summary_fields(result)
        self.assertEqual(fields["status"], "ok")
        self.assertEqual(fields["warnings"], "2")

    def test_expression_script_path_is_a_warning(self):
        root = self.make_repo(
            {
                "expr.yml": workflow_yaml(
                    """
                    - run: bash ${{ matrix.script }}
                    """
                )
            }
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(len(self.findings(result, "warning")), 1, result.stdout)
        self.assertEqual(self.findings(result, "error"), [])

    def test_absolute_script_path_is_a_warning(self):
        root = self.make_repo(
            {"abs.yml": workflow_yaml("- run: bash /usr/local/bin/setup.sh\n")}
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 0, result.stdout)
        warnings = self.findings(result, "warning")
        self.assertEqual(len(warnings), 1, result.stdout)
        self.assertIn("absolute path", warnings[0])

    def test_glob_script_path_is_a_warning(self):
        root = self.make_repo(
            {"glob.yml": workflow_yaml("- run: bash scripts/*.sh\n")}
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("glob", result.stdout)
        self.assertEqual(self.findings(result, "error"), [])

    def test_expression_working_directory_is_a_warning(self):
        root = self.make_repo(
            {
                "expr.yml": workflow_yaml(
                    """
                    - run: uv sync
                      working-directory: ${{ matrix.directory }}
                    """
                )
            }
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(len(self.findings(result, "warning")), 1, result.stdout)
        self.assertEqual(self.findings(result, "error"), [])

    def test_parent_directory_working_directory_is_a_warning(self):
        root = self.make_repo(
            {
                "escape.yml": workflow_yaml(
                    """
                    - run: uv sync
                      working-directory: ../outside
                    """
                )
            }
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(len(self.findings(result, "warning")), 1, result.stdout)


class ContainerCommandTests(WorkflowTestCase):
    def test_container_scripts_are_not_resolved_in_the_repository(self):
        # `scripts/in-container.sh` only exists inside the image, not in the repo.
        root = self.make_repo(
            {
                "compose.yml": workflow_yaml(
                    """
                    - run: docker compose run --rm backend bash scripts/in-container.sh
                    - run: docker compose exec -T backend bash scripts/in-container.sh
                    """
                )
            }
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.findings(result, "error"), [], result.stdout)
        self.assertEqual(self.findings(result, "warning"), [], result.stdout)
        self.assertEqual(self.summary_fields(result)["scripts"], "0")

    def test_local_script_before_a_container_command_is_still_checked(self):
        root = self.make_repo(
            {
                "mixed.yml": workflow_yaml(
                    "- run: bash scripts/missing.sh && docker compose up -d\n"
                )
            }
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertTrue(
            any("scripts/missing.sh" in line for line in self.findings(result, "error"))
        )


class ParsingEdgeCaseTests(WorkflowTestCase):
    def test_other_block_scalars_are_skipped(self):
        # A composite-action body full of shell text must not be read as steps.
        root = self.make_repo(
            {
                "guard.yml": workflow_yaml(
                    """
                    - name: Guard
                      uses: actions/github-script@v9
                      with:
                        script: |
                          const text = "run: bash scripts/nope.sh";
                          - not a step
                          run: bash scripts/also-nope.sh
                    - run: bash scripts/prestart.sh
                    """
                )
            }
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.findings(result, "error"), [], result.stdout)
        self.assertEqual(self.summary_fields(result)["runs"], "1")

    def test_matrix_lists_do_not_break_step_pairing(self):
        root = self.make_repo(
            {
                "matrix.yml": (
                    "name: matrix\n"
                    "on: push\n"
                    "jobs:\n"
                    "  test:\n"
                    "    runs-on: ubuntu-latest\n"
                    "    strategy:\n"
                    "      matrix:\n"
                    "        shard: [1, 2]\n"
                    "    steps:\n"
                    "      - run: bash scripts/prestart.sh\n"
                    "        working-directory: backend\n"
                )
            }
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.summary_fields(result)["workdirs"], "1")

    def test_folded_block_is_scanned(self):
        root = self.make_repo(
            {"folded.yml": workflow_yaml("- run: >\n    bash scripts/missing-folded.sh\n")}
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("scripts/missing-folded.sh", result.stdout)

    def test_yaml_extension_is_scanned(self):
        root = self.make_repo({"alt.yaml": workflow_yaml("- run: bash scripts/gone.sh\n")})
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("alt.yaml", result.stdout)

    def test_collect_references_marks_container_context(self):
        scan = CHECKER.parse_workflow(
            workflow_yaml(
                """
                - run: docker compose run --rm backend bash scripts/a.sh
                """
            ),
            "wf.yml",
        )
        step = scan.steps[0]
        references = CHECKER.collect_references(step)
        self.assertEqual(
            [(ref.token, ref.in_container) for ref in references],
            [("scripts/a.sh", True)],
        )


class UnusableInputTests(WorkflowTestCase):
    def test_missing_workflow_directory_is_fatal(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        result = self.run_checker(Path(tmp.name))
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertTrue(
            any(line.startswith("fatal: ") for line in result.stdout.splitlines()),
            result.stdout,
        )
        self.assertEqual(self.summary_fields(result)["status"], "unusable")

    def test_empty_workflow_directory_is_fatal(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / WORKFLOW_DIR).mkdir(parents=True)
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("no workflow files found", result.stdout)

    def test_missing_root_is_fatal(self):
        result = self.run_checker(Path("/nonexistent-check-workflow-paths"))
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("status=unusable", result.stdout)


class SafetyTests(WorkflowTestCase):
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

    def test_loading_the_checker_imports_no_yaml_library(self):
        self.assertNotIn("yaml", sys.modules)
        self.assertFalse(hasattr(CHECKER, "yaml"))


if __name__ == "__main__":
    unittest.main()

"""Tests for ``scripts/check_alembic_chain.py``.

Fixtures are minimal revision trees in a temporary directory; assertions about
exit codes and the summary line drive the script as a subprocess, so the real
CLI contract is exercised end to end. The current repository is covered too,
which pins the one-head/six-revision baseline.
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
SCRIPT = SCRIPTS_DIR / "check_alembic_chain.py"
VERSIONS_DIR = "backend/app/alembic/versions"


def load_checker():
    spec = importlib.util.spec_from_file_location("check_alembic_chain", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CHECKER = load_checker()


def revision_file(revision, down_revision, extra=""):
    """Render a revision module with a literal revision / down_revision."""
    down = "None" if down_revision is None else repr(down_revision)
    return f'revision = {revision!r}\ndown_revision = {down}\n{extra}'


def chain(*ids):
    """Build a linear revision set: ids[0] is the root."""
    return {
        f"{revision}_rev.py": revision_file(revision, parent)
        for revision, parent in zip(ids, (None, *ids[:-1]))
    }


class ChainTestCase(unittest.TestCase):
    def make_repo(self, files):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        versions = root / VERSIONS_DIR
        versions.mkdir(parents=True)
        for name, content in files.items():
            (versions / name).write_text(content, encoding="utf-8")
        return root

    def run_checker(self, root):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(root)],
            capture_output=True,
            text=True,
            check=False,
            # Run inside the fixture so a stray import-time write would land there.
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


class CurrentRepositoryTests(ChainTestCase):
    def test_current_repository_is_clean(self):
        # Acceptance: exit 0 with one head and six revisions.
        result = self.run_checker(REPO_ROOT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            self.findings(result, "error"), [], result.stdout
        )
        lines = result.stdout.splitlines()
        self.assertEqual(
            lines[-1],
            "summary: check=alembic-chain status=ok heads=a1b2c3d4e5f6 "
            "revisions=6 errors=0 warnings=0",
        )

    def test_current_repository_summary_is_parseable(self):
        fields = self.summary_fields(self.run_checker(REPO_ROOT))
        self.assertEqual(fields["check"], "alembic-chain")
        self.assertEqual(fields["status"], "ok")
        self.assertEqual(fields["revisions"], "6")
        self.assertEqual(fields["errors"], "0")
        self.assertNotEqual(fields["heads"], "none")

    def test_current_repository_head_matches_last_created_at_revision(self):
        # The priority revision is built on the created_at revision.
        report = CHECKER.evaluate(REPO_ROOT)
        self.assertEqual(report.heads, ("a1b2c3d4e5f6",))
        self.assertEqual(report.revisions, 6)


class CleanFixtureTests(ChainTestCase):
    def test_linear_chain_has_one_head(self):
        root = self.make_repo(chain("aaa", "bbb", "ccc"))
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 0, result.stdout)
        fields = self.summary_fields(result)
        self.assertEqual(fields["heads"], "ccc")
        self.assertEqual(fields["revisions"], "3")
        self.assertEqual(fields["status"], "ok")

    def test_merge_revision_collapses_two_roots_into_one_head(self):
        files = {
            "m1.py": revision_file("m1", None),
            "m2.py": revision_file("m2", None),
            "merge.py": revision_file("merge", ("m1", "m2")),
        }
        root = self.make_repo(files)
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 0, result.stdout)
        fields = self.summary_fields(result)
        self.assertEqual(fields["heads"], "merge")
        self.assertEqual(fields["revisions"], "3")

    def test_missing_down_revision_assignment_is_a_warning(self):
        root = self.make_repo({"a.py": 'revision = "aaa"\n'})
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(len(self.findings(result, "warning")), 1, result.stdout)
        self.assertIn("no module-level down_revision", result.stdout)
        self.assertIn("warnings=1", result.stdout)

    def test_nested_revision_directories_are_inspected(self):
        root = self.make_repo(chain("aaa", "bbb"))
        nested = root / VERSIONS_DIR / "2024"
        nested.mkdir()
        (nested / "ccc.py").write_text(revision_file("ccc", "bbb"), encoding="utf-8")
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.summary_fields(result)["heads"], "ccc")


class MultipleHeadsTests(ChainTestCase):
    def test_injected_second_head_is_an_error(self):
        files = {
            **chain("aaa", "bbb"),
            "stranger.py": revision_file("ccc", "aaa"),
        }
        root = self.make_repo(files)
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        errors = self.findings(result, "error")
        self.assertTrue(
            any("multiple heads resolved" in line for line in errors), result.stdout
        )
        self.assertIn("bbb", result.stdout)
        self.assertIn("ccc", result.stdout)
        self.assertEqual(self.summary_fields(result)["status"], "error")

    def test_two_roots_are_two_heads(self):
        root = self.make_repo(
            {"a.py": revision_file("aaa", None), "b.py": revision_file("bbb", None)}
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        fields = self.summary_fields(result)
        self.assertEqual(fields["heads"], "aaa,bbb")
        self.assertEqual(fields["errors"], "1")


class DanglingParentTests(ChainTestCase):
    def test_dangling_down_revision_is_an_error(self):
        root = self.make_repo({"a.py": revision_file("aaa", "missing")})
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        errors = self.findings(result, "error")
        self.assertTrue(
            any(
                "down_revision 'missing' has no matching revision file" in line
                for line in errors
            ),
            result.stdout,
        )
        fields = self.summary_fields(result)
        self.assertEqual(fields["status"], "error")
        self.assertEqual(fields["revisions"], "1")

    def test_dangling_parent_in_tuple_is_an_error(self):
        files = {
            "m1.py": revision_file("m1", None),
            "merge.py": revision_file("merge", ("m1", "gone")),
        }
        root = self.make_repo(files)
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("'gone' has no matching revision file", result.stdout)


class CycleTests(ChainTestCase):
    def test_two_node_cycle_is_an_error(self):
        files = {
            "a.py": revision_file("aaa", "bbb"),
            "b.py": revision_file("bbb", "aaa"),
        }
        root = self.make_repo(files)
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        errors = self.findings(result, "error")
        self.assertTrue(
            any("cycle in down_revision chain" in line for line in errors),
            result.stdout,
        )
        self.assertIn("aaa -> bbb -> aaa", result.stdout)
        fields = self.summary_fields(result)
        self.assertEqual(fields["status"], "error")
        self.assertEqual(fields["heads"], "none")

    def test_self_referencing_revision_is_an_error(self):
        root = self.make_repo({"a.py": revision_file("aaa", "aaa")})
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("cycle in down_revision chain: aaa -> aaa", result.stdout)

    def test_cycle_in_longer_chain_is_an_error(self):
        files = {
            "root.py": revision_file("root", None),
            "a.py": revision_file("aaa", "bbb"),
            "b.py": revision_file("bbb", "aaa"),
            "tip.py": revision_file("tip", "root"),
        }
        root_dir = self.make_repo(files)
        result = self.run_checker(root_dir)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("cycle in down_revision chain", result.stdout)

    def test_find_cycles_reports_closed_path(self):
        cycles = CHECKER.find_cycles({"aaa": ("bbb",), "bbb": ("aaa",)})
        self.assertEqual(cycles, [("aaa", "bbb", "aaa")])


class DuplicateRevisionTests(ChainTestCase):
    def test_duplicate_revision_id_is_an_error(self):
        files = {
            "a.py": revision_file("dup", None),
            "b.py": revision_file("dup", None),
        }
        root = self.make_repo(files)
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        errors = self.findings(result, "error")
        self.assertTrue(
            any("duplicate revision id 'dup'" in line for line in errors), result.stdout
        )
        self.assertIn("revisions=2", result.stdout)
        self.assertEqual(self.summary_fields(result)["status"], "error")

    def test_duplicate_id_with_chain_still_errors(self):
        files = {
            **chain("aaa", "bbb"),
            "copy.py": revision_file("bbb", "aaa"),
        }
        root = self.make_repo(files)
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("duplicate revision id 'bbb'", result.stdout)


class SafetyTests(ChainTestCase):
    def test_revision_files_are_parsed_not_imported(self):
        # A revision module with import-time side effects must never run.
        content = (
            "from pathlib import Path\n"
            "\n"
            'Path("imported-marker").write_text("imported", encoding="utf-8")\n'
            "\n"
            'revision = "aaa"\n'
            "down_revision = None\n"
            "\n"
            'raise RuntimeError("revision files must never be imported")\n'
        )
        root = self.make_repo({"a.py": content})
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.findings(result, "error"), [])
        self.assertFalse((root / "imported-marker").exists())
        self.assertFalse((REPO_ROOT / "imported-marker").exists())

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

    def test_loading_the_checker_imports_no_migration_libraries(self):
        self.assertNotIn("alembic", sys.modules)
        self.assertNotIn("sqlalchemy", sys.modules)
        self.assertNotIn("psycopg", sys.modules)


class UnusableInputTests(ChainTestCase):
    def test_missing_revision_directory_is_fatal(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        result = self.run_checker(Path(tmp.name))
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertTrue(
            any(line.startswith("fatal: ") for line in result.stdout.splitlines()),
            result.stdout,
        )
        self.assertEqual(self.summary_fields(result)["status"], "unusable")

    def test_missing_root_is_fatal(self):
        result = self.run_checker(Path("/nonexistent-check-alembic-chain"))
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("status=unusable", result.stdout)

    def test_syntax_error_file_is_fatal(self):
        root = self.make_repo({"broken.py": 'revision = "aaa"\ndown_revision = None\n('})
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("cannot parse Python", result.stdout)

    def test_non_literal_revision_is_fatal(self):
        root = self.make_repo({"a.py": "REVISION = 'aaa'\nrevision = REVISION\n"})
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("revision is not a string literal", result.stdout)

    def test_uninterpretable_down_revision_is_fatal(self):
        root = self.make_repo({"a.py": "revision = 'aaa'\ndown_revision = get_parent()\n"})
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("down_revision is not a string, None, or a tuple", result.stdout)

    def test_empty_revision_directory_is_an_error(self):
        root = self.make_repo({})
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("no revision files found", result.stdout)
        fields = self.summary_fields(result)
        self.assertEqual(fields["heads"], "none")
        self.assertEqual(fields["revisions"], "0")


class ParserTests(ChainTestCase):
    def test_annotated_assignment_is_read(self):
        root = self.make_repo(
            {
                "a.py": (
                    "from typing import Union\n"
                    "\n"
                    'revision: str = "aaa"\n'
                    'down_revision: Union[str, None] = "zzz"\n'
                )
            }
        )
        report = CHECKER.evaluate(root)
        parsed = CHECKER.parse_revision_file(
            root / VERSIONS_DIR / "a.py", f"{VERSIONS_DIR}/a.py"
        )
        self.assertIsNotNone(parsed.revision)
        self.assertEqual(parsed.revision.revision, "aaa")
        self.assertEqual(parsed.revision.parents, ("zzz",))
        self.assertEqual(report.revisions, 1)

    def test_docstring_quoted_revision_is_not_confused(self):
        root = self.make_repo(
            {
                "a.py": (
                    '"""revision = "wrong" """\n'
                    "\n"
                    'revision = "aaa"\n'
                    "down_revision = None\n"
                )
            }
        )
        parsed = CHECKER.parse_revision_file(
            root / VERSIONS_DIR / "a.py", f"{VERSIONS_DIR}/a.py"
        )
        self.assertIsNotNone(parsed.revision)
        self.assertEqual(parsed.revision.revision, "aaa")

    def test_nested_assignment_is_ignored(self):
        root = self.make_repo(
            {
                "a.py": (
                    'revision = "aaa"\n'
                    "down_revision = None\n"
                    "if False:\n"
                    '    revision = "shadow"\n'
                    "    down_revision = 'x'\n"
                )
            }
        )
        parsed = CHECKER.parse_revision_file(
            root / VERSIONS_DIR / "a.py", f"{VERSIONS_DIR}/a.py"
        )
        self.assertIsNotNone(parsed.revision)
        self.assertEqual(parsed.revision.revision, "aaa")
        self.assertEqual(parsed.revision.parents, ())


if __name__ == "__main__":
    unittest.main()

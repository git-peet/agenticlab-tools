"""Tests for ``scripts/check_doc_links.py``.

Fixtures are minimal Markdown trees in a temporary directory; assertions about
exit codes and the summary line drive the script as a subprocess, so the real
CLI contract is exercised end to end. The current repository is covered too,
which pins the clean baseline (11 pages, 22 relative targets, 4 badge-link
warnings).
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
SCRIPT = SCRIPTS_DIR / "check_doc_links.py"


def load_checker():
    spec = importlib.util.spec_from_file_location("check_doc_links", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CHECKER = load_checker()

CLEAN_PAGE = (
    "# Page\n"
    "\n"
    "[Home](../README.md)\n"
)

CLEAN_README = (
    "# Fixture\n"
    "\n"
    "[page](docs/page.md)\n"
    "[directory](docs)\n"
    "![shot](img/shot.png)\n"
    "[anchor](#section)\n"
    "[external](https://example.com/x)\n"
    "[mail](mailto:someone@example.com)\n"
)


class DocLinkTestCase(unittest.TestCase):
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

    def clean_repo(self, overrides=None):
        files = {
            "README.md": CLEAN_README,
            "docs/page.md": CLEAN_PAGE,
            "img/shot.png": "",
        }
        files.update(overrides or {})
        return self.make_repo(files)

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


class CurrentRepositoryTests(DocLinkTestCase):
    def test_current_repository_is_clean(self):
        result = self.run_checker(REPO_ROOT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.findings(result, "error"), [], result.stdout)
        lines = result.stdout.splitlines()
        self.assertEqual(
            lines[-1],
            "summary: check=doc-links status=ok files=11 links=22 "
            "errors=0 warnings=4",
        )

    def test_current_repository_warnings_are_the_badge_links(self):
        result = self.run_checker(REPO_ROOT)
        warnings = self.findings(result, "warning")
        self.assertEqual(len(warnings), 4, result.stdout)
        for line in warnings:
            self.assertIn("README.md:", line)
            self.assertIn("escapes the repository root", line)
        self.assertTrue(any("badge.svg" in line for line in warnings))

    def test_current_repository_summary_is_parseable(self):
        fields = self.summary_fields(self.run_checker(REPO_ROOT))
        self.assertEqual(fields["check"], "doc-links")
        self.assertEqual(fields["status"], "ok")
        self.assertGreaterEqual(int(fields["files"]), 10)
        self.assertGreater(int(fields["links"]), 0)


class CleanFixtureTests(DocLinkTestCase):
    def test_clean_fixture_passes(self):
        result = self.run_checker(self.clean_repo())
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.findings(result, "error"), [], result.stdout)
        fields = self.summary_fields(result)
        self.assertEqual(fields["files"], "2")
        self.assertEqual(fields["links"], "4")
        self.assertEqual(fields["errors"], "0")
        self.assertEqual(fields["warnings"], "0")

    def test_anchor_only_and_empty_targets_are_ignored(self):
        root = self.make_repo(
            {"README.md": "[a](#top)\n[b]()\n[c](#section-2)\n"}
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.summary_fields(result)["links"], "0")

    def test_external_schemes_are_ignored_and_not_counted(self):
        root = self.make_repo(
            {
                "README.md": (
                    "[a](https://example.com)\n"
                    "[b](http://example.com)\n"
                    "[c](mailto:x@example.com)\n"
                    "[d](tel:+15550100)\n"
                )
            }
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.summary_fields(result)["links"], "0")

    def test_angle_target_and_title_suffix_resolve(self):
        root = self.make_repo(
            {
                "README.md": (
                    '[a](<docs/my page.md>)\n'
                    '[b](docs/my page.md "Install guide")\n'
                    "[c](docs/page.md#install)\n"
                ),
                "docs/my page.md": "# Spaced\n",
                "docs/page.md": "# Page\n",
            }
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.findings(result, "error"), [], result.stdout)
        self.assertEqual(self.summary_fields(result)["links"], "3")


class BrokenLinkTests(DocLinkTestCase):
    def test_missing_file_is_an_error(self):
        root = self.make_repo({"README.md": "# T\n\n[b](docs/gone.md)\n"})
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        errors = self.findings(result, "error")
        self.assertEqual(len(errors), 1, result.stdout)
        self.assertIn("README.md:3:", errors[0])
        self.assertIn("docs/gone.md", errors[0])
        self.assertEqual(self.summary_fields(result)["status"], "error")

    def test_missing_relative_from_docs_page_is_an_error(self):
        root = self.make_repo(
            {"README.md": "# T\n", "docs/page.md": "[Broken](../missing.md)\n"}
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertTrue(
            any(
                line.startswith("error: docs/page.md:1:")
                and "missing.md" in line
                for line in self.findings(result, "error")
            ),
            result.stdout,
        )

    def test_missing_directory_target_is_an_error(self):
        root = self.make_repo({"README.md": "[d](no-such-dir)\n"})
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertTrue(
            any("no-such-dir" in line for line in self.findings(result, "error"))
        )

    def test_existing_directory_target_passes(self):
        root = self.make_repo(
            {"README.md": "[d](docs)\n[e](docs/)\n", "docs/.keep": ""}
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 0, result.stdout)
        fields = self.summary_fields(result)
        self.assertEqual(fields["links"], "2")
        self.assertEqual(fields["errors"], "0")

    def test_missing_spaced_target_is_an_error(self):
        # A space is part of the path, not the start of a title.
        root = self.make_repo({"README.md": "[d](docs/my page.md)\n"})
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertTrue(
            any("docs/my page.md" in line for line in self.findings(result, "error")),
            result.stdout,
        )

    def test_percent_encoded_target_resolves(self):
        root = self.make_repo(
            {"README.md": "[d](docs/my%20page.md)\n", "docs/my page.md": "# P\n"}
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.summary_fields(result)["links"], "1")

    def test_broken_link_in_package_readme_is_an_error(self):
        root = self.make_repo({"packages/foo/README.md": "[d](gone.md)\n"})
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("packages/foo/README.md", result.stdout)

    def test_broken_link_in_nested_docs_page_is_an_error(self):
        root = self.make_repo({"docs/guide/deep.md": "[d](gone.md)\n"})
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("docs/guide/deep.md", result.stdout)

    def test_broken_explicit_package_readme_is_an_error(self):
        root = self.make_repo({"backend/README.md": "[d](nope.md)\n"})
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("backend/README.md", result.stdout)


class EscapingLinkTests(DocLinkTestCase):
    def test_root_escaping_link_is_a_warning_not_an_error(self):
        root = self.make_repo(
            {"README.md": "[ci](../../actions/workflows/test.yml)\n"}
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.findings(result, "error"), [], result.stdout)
        warnings = self.findings(result, "warning")
        self.assertEqual(len(warnings), 1, result.stdout)
        self.assertIn("escapes the repository root", warnings[0])
        fields = self.summary_fields(result)
        self.assertEqual(fields["status"], "ok")
        self.assertEqual(fields["links"], "1")

    def test_escaping_target_from_docs_page_is_a_warning(self):
        root = self.make_repo({"docs/page.md": "[out](../../outside.md)\n"})
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(len(self.findings(result, "warning")), 1, result.stdout)

    def test_site_root_relative_link_is_a_warning(self):
        root = self.make_repo({"README.md": "[d](/docs/page.md)\n"})
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 0, result.stdout)
        warnings = self.findings(result, "warning")
        self.assertEqual(len(warnings), 1, result.stdout)
        self.assertIn("site-root relative", warnings[0])


class CodeMaskingTests(DocLinkTestCase):
    def test_fenced_code_block_is_ignored(self):
        root = self.make_repo(
            {
                "README.md": (
                    "# T\n"
                    "\n"
                    "```text\n"
                    "[example](missing.md)\n"
                    "```\n"
                )
            }
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.findings(result, "error"), [], result.stdout)
        self.assertEqual(self.summary_fields(result)["links"], "0")

    def test_inline_code_is_ignored(self):
        root = self.make_repo(
            {"README.md": "Use `[example](missing.md)` in prose.\n"}
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.findings(result, "error"), [], result.stdout)

    def test_link_after_closing_fence_is_checked(self):
        root = self.make_repo(
            {
                "README.md": (
                    "```sh\n"
                    "echo hi\n"
                    "```\n"
                    "[d](missing.md)\n"
                )
            }
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertTrue(
            any("README.md:4:" in line for line in self.findings(result, "error")),
            result.stdout,
        )


class BadgeTests(DocLinkTestCase):
    def test_nested_badge_checks_both_targets(self):
        root = self.make_repo(
            {
                "README.md": (
                    "[![Build](../../actions/workflows/test.yml/badge.svg)]"
                    "(../../actions/workflows/test.yml)\n"
                )
            }
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 0, result.stdout)
        warnings = self.findings(result, "warning")
        self.assertEqual(len(warnings), 2, result.stdout)
        self.assertTrue(any("badge.svg" in line for line in warnings))
        self.assertEqual(self.summary_fields(result)["links"], "2")

    def test_nested_badge_inner_missing_target_is_an_error(self):
        root = self.make_repo(
            {
                "README.md": "[![Build](img/missing.svg)](docs/page.md)\n",
                "docs/page.md": "# P\n",
            }
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertTrue(
            any("img/missing.svg" in line for line in self.findings(result, "error")),
            result.stdout,
        )


class ScanBoundaryTests(DocLinkTestCase):
    def test_integration_layer_pages_are_not_scanned(self):
        root = self.make_repo(
            {"README.md": "# T\n", "AgenticLab/broken.md": "[d](missing.md)\n"}
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.summary_fields(result)["files"], "1")

    def test_non_readme_markdown_outside_docs_is_not_scanned(self):
        root = self.make_repo(
            {"README.md": "# T\n", "backend/notes.md": "[d](missing.md)\n"}
        )
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.summary_fields(result)["files"], "1")


class UnusableInputTests(DocLinkTestCase):
    def test_missing_root_is_fatal(self):
        result = self.run_checker(Path("/nonexistent-check-doc-links"))
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("status=unusable", result.stdout)

    def test_no_markdown_pages_is_fatal(self):
        root = self.make_repo({"src/app.py": "print('hi')\n"})
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("no markdown pages found", result.stdout)
        fields = self.summary_fields(result)
        self.assertEqual(fields["status"], "unusable")
        self.assertEqual(fields["files"], "0")

    def test_non_utf8_page_is_fatal(self):
        root = self.make_repo({"README.md": b"\xff\xfe broken bytes\n"})
        result = self.run_checker(root)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("cannot read file", result.stdout)


class ParserTests(DocLinkTestCase):
    def test_extract_targets_handles_balanced_parentheses(self):
        targets = CHECKER.extract_targets("[d](docs/a(1).md)")
        self.assertEqual(targets, ["docs/a(1).md"])

    def test_extract_targets_finds_both_badge_targets(self):
        targets = CHECKER.extract_targets(
            "[![B](img/badge.svg)](../../actions/workflows/x.yml)"
        )
        self.assertEqual(
            targets,
            ["img/badge.svg", "../../actions/workflows/x.yml"],
        )

    def test_extract_targets_ignores_unbalanced_target(self):
        self.assertEqual(CHECKER.extract_targets("[d](docs/a.md"), [])

    def test_normalize_target_strips_angle_brackets_and_title(self):
        self.assertEqual(CHECKER.normalize_target("<docs/my page.md>"), "docs/my page.md")
        self.assertEqual(
            CHECKER.normalize_target('docs/page.md "Title"'), "docs/page.md"
        )
        self.assertEqual(CHECKER.normalize_target("docs/page.md"), "docs/page.md")

    def test_mask_code_blanks_inline_spans(self):
        masked = CHECKER.mask_code("see `[x](missing.md)` here")
        self.assertNotIn("missing.md", masked)
        self.assertIn("here", masked)


class SafetyTests(DocLinkTestCase):
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

    def test_loading_the_checker_needs_no_network_libraries(self):
        self.assertNotIn("requests", sys.modules)
        self.assertNotIn("urllib.request", sys.modules)


if __name__ == "__main__":
    unittest.main()

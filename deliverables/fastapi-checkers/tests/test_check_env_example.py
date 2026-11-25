"""Tests for scripts/check_env_example.py using hermetic temporary fixtures."""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_env_example as subject  # noqa: E402  (needs the sys.path entry above)

CONFIG_TEMPLATE = '''\
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_ignore_empty": True}

    REQUIRED_KEY: str
    DEFAULTED_KEY: str = "value"
    OPTIONAL_NONE: str | None = None

    @computed_field
    @property
    def computed(self) -> bool:  # type: ignore[prop-decorator]
        return True

    _private: int = 1

    def helper(self) -> None:
        self.computed


settings = Settings()
'''

ALIGNED_EXAMPLE = """\
# Example environment for the drift check.
REQUIRED_KEY=required-value
DEFAULTED_KEY=default-value
OPTIONAL_NONE=
"""


class DriftCheckTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.config_path = Path(tmp.name) / "config.py"
        self.example_path = Path(tmp.name) / ".env.example"

    def write(self, example: str = ALIGNED_EXAMPLE, config: str = CONFIG_TEMPLATE) -> None:
        self.config_path.write_text(config, encoding="utf-8")
        self.example_path.write_text(example, encoding="utf-8")

    def drift(self, *, allowlist: frozenset[str] = frozenset()) -> subject.Drift:
        return subject.compare(
            self.config_path, self.example_path, allowlist=allowlist
        )

    def run_main(self, *, allowlist: frozenset[str] = frozenset()) -> tuple[int, str]:
        out = io.StringIO()
        with redirect_stdout(out):
            code = subject.main(
                [str(self.config_path), str(self.example_path)], allowlist=allowlist
            )
        return code, out.getvalue()

    def test_aligned_project_reports_nothing(self) -> None:
        self.write()
        found = self.drift()
        self.assertEqual(found, subject.Drift([], [], [], []))
        code, output = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn(
            "summary: errors=0 warnings=0 allowlisted=0 result=ok", output
        )

    def test_required_setting_missing_from_example_is_an_error(self) -> None:
        self.write(example="DEFAULTED_KEY=default-value\nOPTIONAL_NONE=\n")
        self.assertEqual(self.drift().errors, ["REQUIRED_KEY"])
        code, output = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("ERRORS (1)", output)
        self.assertIn("result=fail", output)

    def test_defaulted_setting_missing_from_example_is_only_a_warning(self) -> None:
        self.write(example="REQUIRED_KEY=required-value\nDEFAULTED_KEY=default-value\n")
        self.assertEqual(self.drift().undocumented, ["OPTIONAL_NONE"])
        code, output = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("WARNINGS (1)", output)
        self.assertIn("result=ok", output)

    def test_example_key_without_setting_is_a_warning(self) -> None:
        self.write(example=ALIGNED_EXAMPLE + "COMPOSE_ONLY=compose-value\n")
        self.assertEqual(self.drift().unknown, ["COMPOSE_ONLY"])
        code, output = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("COMPOSE_ONLY (not a setting)", output)
        self.assertIn("summary: errors=0 warnings=1 allowlisted=0 result=ok", output)

    def test_allowlisted_key_is_counted_not_reported_as_unknown(self) -> None:
        self.write(example=ALIGNED_EXAMPLE + "COMPOSE_ONLY=compose-value\n")
        found = self.drift(allowlist=frozenset({"COMPOSE_ONLY"}))
        self.assertEqual(found.unknown, [])
        self.assertEqual(found.allowlisted, ["COMPOSE_ONLY"])
        code, output = self.run_main(allowlist=frozenset({"COMPOSE_ONLY"}))
        self.assertEqual(code, 0)
        self.assertIn("ALLOWLISTED (1)", output)
        self.assertIn("summary: errors=0 warnings=0 allowlisted=1 result=ok", output)

    def test_only_annotated_class_fields_count_as_settings(self) -> None:
        self.write()
        fields = subject.declared_settings(self.config_path)
        self.assertEqual(
            fields,
            {"REQUIRED_KEY": False, "DEFAULTED_KEY": True, "OPTIONAL_NONE": True},
        )
        self.assertNotIn("model_config", fields)
        self.assertNotIn("computed", fields)
        self.assertNotIn("_private", fields)
        self.assertNotIn("settings", fields)

    def test_example_parsing_skips_comments_and_keeps_empty_values(self) -> None:
        self.write(
            example=(
                "# a comment\n"
                "\n"
                "   \n"
                "EMPTY_VALUE=\n"
                "WITH_EQUALS=a=b\n"
                "  SPACED_KEY = spaced\n"
                "NOT_A_KEY\n"
            )
        )
        self.assertEqual(
            subject.documented_keys(self.example_path),
            {"EMPTY_VALUE", "WITH_EQUALS", "SPACED_KEY"},
        )

    def test_missing_config_file_exits_two(self) -> None:
        self.write()
        self.config_path.unlink()
        code, _ = self.run_main()
        self.assertEqual(code, 2)

    def test_allowlist_entry_shadowing_a_setting_exits_two(self) -> None:
        self.write()
        code, _ = self.run_main(allowlist=frozenset({"REQUIRED_KEY"}))
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()

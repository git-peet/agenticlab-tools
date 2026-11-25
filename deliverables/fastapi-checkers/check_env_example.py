"""Check for drift between Settings declarations and the example environment.

Parses ``backend/app/core/config.py`` statically with :mod:`ast`, so the app is
never imported and no ``.env`` file is read, then compares the declared
``Settings`` fields against the keys documented in ``.env.example``.

Findings:
- error: a setting declared without a default that is absent from .env.example
- warning: a setting with a default that is undocumented, or an .env.example
  key that is neither a setting nor allowlisted
- allowlisted: an .env.example key that belongs to docker compose, not Settings

Exit codes: 0 = no errors, 1 = errors found, 2 = unusable input.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "backend/app/core/config.py"
EXAMPLE_PATH = REPO_ROOT / ".env.example"
SETTINGS_CLASS = "Settings"

# Documented in .env.example for docker compose interpolation (compose.yml);
# the backend Settings model does not declare them, so they are not orphans.
COMPOSE_ONLY_KEYS = frozenset({"POSTGRES_PASSWORD", "DOMAIN"})


class Drift(NamedTuple):
    """Keys that disagree between Settings and the example environment."""

    errors: list[str]
    undocumented: list[str]
    unknown: list[str]
    allowlisted: list[str]


def declared_settings(config_path: Path) -> dict[str, bool]:
    """Map Settings field names to whether each one declares a default."""
    tree = ast.parse(config_path.read_text(encoding="utf-8"), filename=str(config_path))
    fields: dict[str, bool] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != SETTINGS_CLASS:
            continue
        for item in node.body:
            if not isinstance(item, ast.AnnAssign):
                continue
            if not isinstance(item.target, ast.Name) or item.target.id.startswith("_"):
                continue
            fields[item.target.id] = item.value is not None
    if not fields:
        raise ValueError(f"no annotated fields found in {SETTINGS_CLASS} in {config_path}")
    return fields


def documented_keys(example_path: Path) -> set[str]:
    """Keys documented in the example file; empty values still count."""
    keys: set[str] = set()
    for line in example_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, _ = stripped.partition("=")
        if separator and key.strip():
            keys.add(key.strip())
    return keys


def compare(
    config_path: Path = CONFIG_PATH,
    example_path: Path = EXAMPLE_PATH,
    *,
    allowlist: frozenset[str] = COMPOSE_ONLY_KEYS,
) -> Drift:
    """Compare declared settings with documented keys, split by severity."""
    fields = declared_settings(config_path)
    documented = documented_keys(example_path)

    shadowed = sorted(set(allowlist) & set(fields))
    if shadowed:
        raise ValueError(f"allowlist entries are also declared settings: {', '.join(shadowed)}")

    errors = sorted(
        name for name, has_default in fields.items() if not has_default and name not in documented
    )
    undocumented = sorted(
        name for name, has_default in fields.items() if has_default and name not in documented
    )
    unknown = sorted(documented - set(fields) - set(allowlist))
    allowlisted = sorted(documented & set(allowlist))
    return Drift(errors, undocumented, unknown, allowlisted)


def _report(title: str, entries: list[str], description: str) -> None:
    if not entries:
        return
    print(f"{title} ({len(entries)}) - {description}:")
    for entry in entries:
        print(f"  - {entry}")


def main(
    argv: Sequence[str] | None = None,
    *,
    allowlist: frozenset[str] = COMPOSE_ONLY_KEYS,
) -> int:
    """Report drift; optional CONFIG and EXAMPLE paths override the defaults."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) > 2:
        print("usage: check_env_example.py [config.py [.env.example]]", file=sys.stderr)
        return 2

    config_path = Path(args[0]) if args else CONFIG_PATH
    example_path = Path(args[1]) if len(args) > 1 else EXAMPLE_PATH
    try:
        drift = compare(config_path, example_path, allowlist=allowlist)
    except (OSError, SyntaxError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    warnings = [*drift.undocumented, *(f"{key} (not a setting)" for key in drift.unknown)]
    _report("ERRORS", drift.errors, "required settings with no default missing")
    _report("WARNINGS", warnings, "undocumented defaulted settings or example-only keys")
    _report("ALLOWLISTED", drift.allowlisted, "compose-only keys, not Settings fields")

    failed = bool(drift.errors)
    print(
        f"summary: errors={len(drift.errors)} warnings={len(warnings)} "
        f"allowlisted={len(drift.allowlisted)} result={'fail' if failed else 'ok'}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

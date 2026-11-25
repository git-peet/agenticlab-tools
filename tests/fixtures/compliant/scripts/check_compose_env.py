#!/usr/bin/env python3
"""Minimal compliant checker used to test the evaluation interface itself."""
import argparse
import re
import sys
from pathlib import Path

VAR = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)(:?[-?+]?)([^}]*)\}")

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    root = Path(args.root)
    example = root / ".env.example"
    if not root.is_dir() or not example.is_file():
        print(f"fatal: {example}: no such environment template")
        print("summary: check=compose-env status=unusable errors=0 warnings=0")
        return 2
    documented = {line.split("=", 1)[0].strip() for line in example.read_text().splitlines()
                  if "=" in line and not line.strip().startswith("#")}
    errors = warnings = 0
    for compose in sorted(root.glob("compose*.yml")):
        for number, line in enumerate(compose.read_text().splitlines(), 1):
            for name, operator, _ in VAR.findall(line):
                if name in documented:
                    continue
                if operator.startswith(":?"):
                    print(f"error: {compose.name}:{number}: required interpolation '{name}' is not documented")
                    errors += 1
                else:
                    print(f"warning: {compose.name}:{number}: optional interpolation '{name}' is not documented")
                    warnings += 1
    status = "error" if errors else "ok"
    print(f"summary: check=compose-env status={status} errors={errors} warnings={warnings}")
    return 1 if errors else 0

if __name__ == "__main__":
    sys.exit(main())

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
        print(f"ERROR: {example} does not exist")
        return 1
    documented = {line.split("=", 1)[0].strip() for line in example.read_text().splitlines()
                  if "=" in line and not line.strip().startswith("#")}
    errors = warnings = 0
    for compose in sorted(root.glob("compose*.yml")):
        for number, line in enumerate(compose.read_text().splitlines(), 1):
            for name, operator, _ in VAR.findall(line):
                if name in documented:
                    continue
                if operator.startswith(":?"):
                    print(f"ERROR: required variable {name} is not documented in .env.example ({compose.name}:{number})")
                    errors += 1
                else:
                    print(f"WARNING: {name} optional, undocumented")
                    warnings += 1
    status = "error" if errors else "ok"
    print(f"Result: {errors} error(s), {warnings} warning(s)")
    return 1 if errors else 0

if __name__ == "__main__":
    sys.exit(main())

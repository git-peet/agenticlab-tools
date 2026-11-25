# FastAPI template maintenance checkers

Stdlib-only checkers produced during AgenticLab delivery runs against the sanitized
Full Stack FastAPI Template. They are kept here because their origin workspaces were
disposable; nothing here is part of the AgenticLab payload.

| Script | Checks | Origin |
|---|---|---|
| `check_python_version.py` | `.python-version`, `requires-python`, Dockerfile base image, CI delegation agree | `ALPHA-DELIVERY-001` PBI 1 |
| `check_alembic_chain.py` | single migration head, no cycles, no dangling `down_revision`, unique ids | `ALPHA-DELIVERY-001` PBI 2 |
| `check_workflow_paths.py` | scripts and `working-directory` referenced by CI exist | `ALPHA-DELIVERY-001` PBI 3 |
| `check_doc_links.py` | relative Markdown links resolve; root-escaping links warned | `ALPHA-DELIVERY-001` PBI 4 |
| `check_compose_env.py` | compose interpolations documented in `.env.example` | `ALPHA-DELIVERY-001` PBI 5 |
| `check_env_example.py` | `Settings` fields documented in `.env.example` | exp8 delivery task |

Shared convention: findings on stdout as `severity: <repo-relative path>: <message>`,
`fatal:`/`error:`/`warning:`/`info:` lowercase, final line
`summary: check=<slug> status=<ok|error|unusable> … errors=<n> warnings=<n>`, exit codes
`2` unusable / `1` errors / `0` clean, `--root PATH` for isolation.

Install into a target checkout as `scripts/check_<concern>.py` plus
`scripts/tests/test_<concern>.py`, then run from the repository root:

```bash
python3 scripts/check_python_version.py
python3 -m unittest discover -s scripts/tests
```

They assume the repository layout (`.python-version`, `backend/`, `compose*.yml`,
`.github/workflows/`), so they fail outside a checkout.

Known limitation: `test_check_doc_links.py` asserts the repository's exact file and
link counts, so it must be re-baselined whenever Markdown files are added or removed.
In the original workspace it counted 11 files because that checkout also held a
`PBIs.md`; a clean template counts 10. Every other test is structure-based.

Note: `check_compose_env.py` in this directory is the version from `exp10`, which scored
5/7 against the convention above. The 7/7 variants from the knowledge experiments were
produced under a record that supplied a slightly different summary shape; they are
preserved in the experiment archive, not here.

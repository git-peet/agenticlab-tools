# Payload regression suites

External tests for the AgenticLab clean payload. They live here, not in the payload,
because the distributed payload ships no test folder.

## Pointing at a payload

`tests/payload/_payload.mjs` resolves the payload under test:

- default: the sibling checkout `Projects/C03-clean-payload`
- override: `PAYLOAD_ROOT=/path/to/payload`

```bash
# sibling layout (normal local use)
node --test tests/payload/*.test.mjs

# any other checkout
PAYLOAD_ROOT=/elsewhere/clean-payload node --test tests/payload/*.test.mjs
```

The suites import from `_payload.mjs` rather than from the payload directly, so the
payload location is a single decision in one file. A wrong `PAYLOAD_ROOT` fails loudly
with `ERR_MODULE_NOT_FOUND` rather than silently skipping.

| Suite | Covers |
|---|---|
| `action-scope.test.mjs` | scope policy: exact allow, near-miss, out-of-scope, protected paths, symlink escape, validation commands, gate failures |
| `action-scope-gate.test.mjs` | adapter wiring: arming on `/implement`, relay rejection, `!` interception, fail-closed on unreadable scope |
| `action-scope-fail-closed.test.mjs` | a removed checkpoint must not release enforcement |
| `action-scope-shell-actions.test.mjs` | `sed -i` and `find` actions are scope-checked |
| `knowledge-friction-fixes.test.mjs` | queued `.gitignore` text, prefixed paths, write-capable read verbs, checkpoint removal segments |
| `delivery-run-fixes.test.mjs` | query-only verbs, grep patterns, plan release on log write |
| `payload-refresh.test.mjs` | managed paths, manifests, conflict detection, record without payload, CLI behaviour |
| `system-subject-guard.test.mjs` | rejection of candidates describing the installation itself |

These are external tests: they exercise the payload as installed code, not a copy.

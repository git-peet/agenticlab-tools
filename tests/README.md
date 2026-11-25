# Payload regression suites

External tests for the AgenticLab clean payload. They live here, not in the payload,
because the distributed payload ships no test folder.

They import `../../../C03-clean-payload/adapters/pi/*` directly, so they must stay in this
repository position and the clean payload checkout must exist as a sibling:

```text
Projects/
├── C03-clean-payload/      # payload under test
└── AgenticLab-tools/
    └── tests/payload/      # these suites
```

Run:

```bash
node --test tests/payload/*.test.mjs
```

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

These are external and expect the payload at a fixed sibling path. If the payload moves,
rewrite the import prefix rather than copying the payload here.

# AgenticLab tools

Small, disposable measurement and inspection tools. Nothing here is part of the
distributed payload; nothing here changes a workspace.

## `session-metrics.mjs`

Extracts delivery-run metrics from Pi session JSONL files into the CSV shapes
established in `AgenticLab-v5/evaluation/metrics/`, plus a gate-block table that
the earlier manual aggregation did not capture.

### Why it exists

`AgenticLab-v5/evaluation/metrics/README.md` defines the measures, their windows,
and their limits. It was produced by hand. This script makes that reproducible, and
adds one thing the manual pass could not report per run: **which governance gate
refused what, and how often**.

### What it reads

Only metadata and usage fields from Pi session files:

- message roles and timestamps
- tool call names, ids, and timestamps; for `bash`, the command string, used only to
  count test-like and Playwright invocations and to pair a failure with its command
- token usage and Pi's per-message cost
- compaction entries

No prompt text, tool output, or file content is written to the output. The single
exception is a gate refusal's first line, truncated, used to classify the block.

### Usage

```bash
# quick single-session run
node session-metrics.mjs --run ALPHA-DELIVERY-001 \
  --session ~/.pi/agent/sessions/<dir>/<file>.jsonl \
  --role delivery_pi --label exp10 --out ./out

# declared windows, multiple roles (preferred)
node session-metrics.mjs --config ./runs/alpha-delivery-001.json
```

Config shape:

```json
{
  "runId": "ALPHA-DELIVERY-001",
  "outDir": "./out",
  "sessions": [
    {
      "path": "/abs/path/session.jsonl",
      "role": "delivery_pi",
      "label": "exp10",
      "start": "2026-11-24T09:00:00Z",
      "end": "2026-11-24T12:00:00Z",
      "attributionStatus": "run_window",
      "acceptancePassed": 4,
      "acceptanceTotal": 4,
      "notes": "free text"
    }
  ]
}
```

Outputs, per run:

- `<run>-session-metrics.csv` — same columns as the established evaluation CSV
- `<run>-tool-roundtrips.csv` — call-to-result deltas per tool
- `<run>-gate-blocks.csv` — category, tool, count, first occurrence, example reason

### Reading the numbers honestly

Carried over from the evaluation README, because these are the easiest things to
misread:

| Measure | It is not |
|---|---|
| `tool_result_isError_flags` | a test-failure rate |
| `reported_cost_usd` | an invoice |
| `window_span_seconds` | active or hands-on time |
| `playwright_commands_in_session` | test cases run |
| `gate-blocks` count | good or bad by itself — a wrong block is friction, a missing block is a hole |

Diagnostics (`acceptance_tests_passed/total`) are supplied manually from the
interpreted run result; they are never inferred from session counts.

### Not captured, by design

- Approval prompts, decisions, and latency. The confirm dialog leaves no trace in
  the session file. Count these in the run result, or add instrumentation only if a
  named question needs it.
- Whether retrieved knowledge changed an answer. That is a judgment call for the
  run result, not a measurement.
- Content of any kind.

## Tests

```bash
node --test tests/                 # extractor unit tests
node --test tests/payload/*.test.mjs   # payload regression suites
```

The payload suites resolve their target from `PAYLOAD_ROOT`, defaulting to the sibling
`Projects/C03-clean-payload` checkout. See `tests/README.md`.

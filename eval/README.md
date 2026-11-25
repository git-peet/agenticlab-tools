# Evaluation interface

Measures AgenticLab from several angles and reports it. It does not decide anything.

## Boundary

- **Here:** machinery that produces numbers and per-criterion evidence.
- **`AgenticLab-v5/evaluation/`:** the record — preregistrations, results, interpretations,
  decisions. Those stay with the design repository because they document *why*.

A tool that scored "pass" would launder conclusions, so it reports `met`/`not met` with the
deciding line, and `compare` reports deltas only.

## Commands

```bash
# score an artifact directory against a rubric
node eval/eval.mjs score --rubric eval/rubrics/check-script-convention.json \
  --dir /path/to/checkout --label exp15

# compare scored conditions (deltas only)
node eval/eval.mjs compare \
  --score eval/out/check-script-convention--exp11.json:control \
  --score eval/out/check-script-convention--exp15.json:verbose+example
```

`score` writes a JSON record with every criterion, its met state, and the exact evidence
line, so a human can audit the grade instead of trusting it.

## Rubrics

A rubric is data, not code: each criterion is `file-exists`, `source-contains`, or
`run-expect`. `run-expect` may materialise a fixture (currently `required-var`), pass
`--root`, and assert an exit code and/or an output pattern.

## Session metrics

`../session-metrics.mjs` extracts cost, tokens, spans, tool use, gate blocks, and
retrieval activation from Pi session files. It is the other half of the picture: rubric
scoring answers "did it conform", session metrics answer "what did it cost and which
guards fired".

## Known limits

- Approval prompts and confirmations leave no trace in session data; they are not measured.
- Rubrics are task-specific by design. A new experiment needs its own rubric rather than a
  parameterised general one.
- Scores are mechanical; whether a difference is meaningful is not decided here.

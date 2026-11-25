#!/usr/bin/env node
import { promises as fs } from "node:fs";
import path from "node:path";

import { compareScores, loadRubric, renderComparison, scoreArtifact } from "./lib/rubric.mjs";

/**
 * Evaluation interface: score an artifact against a rubric, compare scored conditions.
 *
 * It reports measurements and deltas. It never declares a run passed, and it never
 * interprets a result: that belongs in the evaluation record beside the
 * preregistration.
 *
 * Usage:
 *   node eval/eval.mjs score --rubric eval/rubrics/<id>.json --dir <checkout> [--label NAME] [--json OUT]
 *   node eval/eval.mjs compare --score a.json[:label] --score b.json[:label] [...]
 */

function parseArgs(argv) {
  const options = { command: argv[0], rubric: null, dir: null, label: null, json: null, scores: [] };
  for (let index = 1; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--rubric") options.rubric = argv[++index];
    else if (arg === "--dir") options.dir = argv[++index];
    else if (arg === "--label") options.label = argv[++index];
    else if (arg === "--json") options.json = argv[++index];
    else if (arg === "--score") options.scores.push(argv[++index]);
  }
  return options;
}

function fail(message) {
  process.stderr.write(`error: ${message}\n`);
  process.exit(2);
}

function defaultOutPath(rubricId, label) {
  const slug = String(label ?? "artifact").replaceAll(/[^a-zA-Z0-9._-]+/gu, "-");
  return path.join("eval", "out", `${rubricId}--${slug}.json`);
}

const options = parseArgs(process.argv.slice(2));

if (options.command === "score") {
  if (!options.rubric) fail("--rubric is required");
  if (!options.dir) fail("--dir is required");
  const rubric = await loadRubric(options.rubric);
  const score = await scoreArtifact(rubric, options.dir);
  const record = { label: options.label ?? path.basename(score.artifact), scoredAt: new Date().toISOString(), score };

  const outPath = options.json ?? defaultOutPath(score.rubric, record.label);
  await fs.mkdir(path.dirname(outPath), { recursive: true });
  await fs.writeFile(outPath, `${JSON.stringify(record, null, 2)}\n`, "utf8");

  for (const criterion of score.criteria) {
    process.stdout.write(`${criterion.met ? "met  " : "MISS "} ${criterion.id}: ${criterion.evidence}\n`);
  }
  process.stdout.write(`score: ${score.scored}/${score.total} (${score.rubric})\nwritten: ${outPath}\n`);
  process.exit(0);
}

if (options.command === "compare") {
  if (options.scores.length < 2) fail("compare needs at least two --score files");
  const entries = [];
  for (const spec of options.scores) {
    // "path[:label]" — split on the last colon that is not part of the path itself
    const separator = spec.lastIndexOf(":");
    const hasLabel = separator > spec.lastIndexOf("/");
    const file = hasLabel ? spec.slice(0, separator) : spec;
    const label = hasLabel ? spec.slice(separator + 1) : null;
    const record = JSON.parse(await fs.readFile(path.resolve(file), "utf8"));
    entries.push({ label: label ?? record.label ?? path.basename(file), score: record.score ?? record });
  }
  const comparison = compareScores(entries);
  process.stdout.write(`${renderComparison(comparison)}\n`);
  process.exit(0);
}

fail(`unknown command '${options.command ?? ""}'; use score or compare`);

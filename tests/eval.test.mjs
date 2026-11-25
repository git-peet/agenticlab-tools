import assert from "node:assert/strict";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

import {
  compareScores,
  loadRubric,
  renderComparison,
  scoreArtifact,
} from "../eval/lib/rubric.mjs";

const RUBRIC = path.resolve(import.meta.dirname, "../eval/rubrics/check-script-convention.json");
const FIXTURES = path.resolve(import.meta.dirname, "fixtures");

async function tempDir(t, prefix) {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), `eval-${prefix}-`));
  t.after(async () => fs.rm(dir, { recursive: true, force: true }));
  return dir;
}

test("the rubric loads and every criterion is a supported type", async () => {
  const rubric = await loadRubric(RUBRIC);
  assert.equal(rubric.criteria.length, 7);
  assert.deepEqual(
    [...new Set(rubric.criteria.map((criterion) => criterion.type))].sort(),
    ["file-exists", "run-expect", "source-contains"],
  );
});

test("a malformed rubric is rejected rather than silently scored", async (t) => {
  const dir = await tempDir(t, "badrubric");
  const file = path.join(dir, "rubric.json");
  await fs.writeFile(file, JSON.stringify({ id: "x", criteria: [{ id: "a", type: "psychic" }] }), "utf8");
  await assert.rejects(loadRubric(file), /unsupported criterion type/u);

  await fs.writeFile(file, JSON.stringify({ criteria: [] }), "utf8");
  await assert.rejects(loadRubric(file), /must have an id/u);
});

test("a compliant artifact scores full marks", async (t) => {
  const rubric = await loadRubric(RUBRIC);
  const score = await scoreArtifact(rubric, path.join(FIXTURES, "compliant"));
  assert.equal(score.total, 7);
  assert.equal(score.scored, 7, JSON.stringify(score.criteria.filter((c) => !c.met), null, 2));
});

test("a non-conforming artifact loses exactly the convention criteria", async (t) => {
  const rubric = await loadRubric(RUBRIC);
  const score = await scoreArtifact(rubric, path.join(FIXTURES, "sloppy"));
  const missed = score.criteria.filter((criterion) => !criterion.met).map((criterion) => criterion.id);
  assert.deepEqual(missed.sort(), ["severity-prefixes", "summary-line", "unusable-input"].sort());
  assert.equal(score.scored, 4);
});

test("every criterion carries an evidence line", async () => {
  const rubric = await loadRubric(RUBRIC);
  const score = await scoreArtifact(rubric, path.join(FIXTURES, "compliant"));
  for (const criterion of score.criteria) {
    assert.equal(typeof criterion.evidence, "string");
    assert.ok(criterion.evidence.length > 0, `${criterion.id} has no evidence`);
  }
});

test("a missing artifact fails without throwing", async (t) => {
  const rubric = await loadRubric(RUBRIC);
  const empty = await tempDir(t, "empty");
  const score = await scoreArtifact(rubric, empty);
  assert.equal(score.scored, 0);
  assert.equal(score.total, 7);
});

test("comparison refuses mismatched rubrics", async (t) => {
  const rubric = await loadRubric(RUBRIC);
  const score = await scoreArtifact(rubric, path.join(FIXTURES, "compliant"));
  const other = { ...score, rubric: "something-else" };
  assert.throws(() => compareScores([{ label: "a", score }, { label: "b", score: other }]), /different rubrics/u);
});

test("comparison reports per-criterion deltas and a rendered table", async () => {
  const rubric = await loadRubric(RUBRIC);
  const good = await scoreArtifact(rubric, path.join(FIXTURES, "compliant"));
  const poor = await scoreArtifact(rubric, path.join(FIXTURES, "sloppy"));
  const comparison = compareScores([
    { label: "good", score: good },
    { label: "poor", score: poor },
  ]);

  assert.deepEqual(comparison.conditions.map((condition) => condition.scored), [7, 4]);
  const summary = comparison.criteria.find((criterion) => criterion.id === "summary-line");
  assert.deepEqual(summary.byCondition.map((cell) => cell.met), [true, false]);

  const rendered = renderComparison(comparison);
  assert.match(rendered, /criterion \| good \| poor/u);
  assert.match(rendered, /\*\*total\*\* \| 7\/7 \| 4\/7/u);
});

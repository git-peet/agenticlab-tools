import assert from "node:assert/strict";
import { test } from "node:test";

import { describesSystemBehavior } from "./_payload.mjs";

const systemClaims = [
  {
    title: "Implementation logs and AgenticLab state are gitignored in this repo",
    claim: "Implementation logs and AgenticLab state are gitignored, so they never appear in git status.",
  },
  { title: "Checkpoint scope", claim: "plan-checkpoint.json carries the approved inScope list." },
  { title: "Pending queue", claim: "Candidates wait in knowledge-pending until promoted." },
  { title: "Extension wiring", claim: ".pi/settings.json registers the write gate." },
  { title: "Record collection", claim: "Active records live under knowledge/records." },
];

const projectClaims = [
  {
    title: "Items API enforces ownership",
    claim: "GET /api/v1/items/{id} returns 403 for another user's item.",
  },
  { title: "Docs are tracked", claim: "docs/ is not an ignored path in this repository." },
  { title: "Date format decision", claim: "The controller parses and returns YYYY-MM-DD." },
  { title: "Coverage gap", claim: "No test covers password reset with an expired token." },
];

test("a candidate whose subject is this installation is rejected", () => {
  for (const candidate of systemClaims) {
    assert.equal(describesSystemBehavior(candidate), true, candidate.title);
  }
});

test("ordinary project claims are allowed", () => {
  for (const candidate of projectClaims) {
    assert.equal(describesSystemBehavior(candidate), false, candidate.title);
  }
});

test("evidence is not part of the subject check", () => {
  // A project fact may quote a file that also mentions installation paths.
  const candidate = {
    title: "Docs are tracked",
    claim: "docs/ is not an ignored path in this repository.",
    evidence: "Root .gitignore also lists /AgenticLab/, /.pi/, and /knowledge-pending/.",
  };
  assert.equal(describesSystemBehavior(candidate), false);
});

test("malformed input is ignored rather than thrown on", () => {
  assert.equal(describesSystemBehavior(undefined), false);
  assert.equal(describesSystemBehavior({}), false);
  assert.equal(describesSystemBehavior({ title: "", claim: "" }), false);
});

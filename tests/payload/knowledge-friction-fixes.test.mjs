import assert from "node:assert/strict";
import path from "node:path";
import { test } from "node:test";

import { referencesPrivateQueue } from "./_payload.mjs";
import { shellReferencesKnowledge } from "./_payload.mjs";

const CWD = path.resolve(import.meta.dirname, "..");
const CHECKPOINT = "AgenticLab/knowledge/checkpoints/plan-checkpoint.json";

test("quoted .gitignore text mentioning the queue is allowed", () => {
  assert.equal(
    referencesPrivateQueue(
      "Root .gitignore ignores /AgenticLab/, /.pi/, /implementation-logs/, and /knowledge-pending/; docs/ is not ignored.",
    ),
    false,
  );
  assert.equal(referencesPrivateQueue("The private queue is AgenticLab/knowledge-pending"), false);
  assert.equal(referencesPrivateQueue("candidate text with no queue mention"), false);
});

test("a path descending into the queue is still rejected", () => {
  assert.equal(referencesPrivateQueue("See AgenticLab/knowledge-pending/abc.json"), true);
  assert.equal(referencesPrivateQueue("file at knowledge-pending/queue.lock"), true);
  assert.equal(referencesPrivateQueue("AgenticLab/knowledge-pending//x.md"), true);
});

test("the private queue guard ignores non-string-safe input", () => {
  assert.equal(referencesPrivateQueue(""), false);
  assert.equal(referencesPrivateQueue(undefined), false);
  assert.equal(referencesPrivateQueue(null), false);
});

test("plain checkpoint removal is allowed", async () => {
  for (const command of [
    `rm ${CHECKPOINT}`,
    `rm -f ${CHECKPOINT}`,
    `rm ./${CHECKPOINT}`,
    `cd ${CWD} && rm ${CHECKPOINT}`,
    `cd ${CWD} && rm -f ./${CHECKPOINT}`,
  ]) {
    assert.equal(await shellReferencesKnowledge(CWD, command), false, command);
  }
});

test("checkpoint removal with extra mutations is still blocked", async () => {
  for (const command of [
    `rm ${CHECKPOINT} && echo x > AgenticLab/knowledge/records/a.md`,
    `rm AgenticLab/knowledge/records/other.md`,
    `cd AgenticLab/knowledge && rm ${CHECKPOINT} && rm -rf AgenticLab/knowledge/records`,
    `rm ${CHECKPOINT} AgenticLab/knowledge/records/a.md`,
    `rm -rf ${CHECKPOINT}`,
  ]) {
    assert.equal(await shellReferencesKnowledge(CWD, command), true, command);
  }
});

test("unrelated knowledge mutations remain blocked", async () => {
  assert.equal(await shellReferencesKnowledge(CWD, "rm -rf AgenticLab/knowledge"), true);
  assert.equal(
    await shellReferencesKnowledge(CWD, "echo x > AgenticLab/knowledge/records/a.md"),
    true,
  );
});

test("read-only knowledge access is allowed with or without a cd prefix", async () => {
  for (const command of [
    "ls -la AgenticLab/knowledge/checkpoints/",
    `cd ${CWD} && ls -la AgenticLab/knowledge/checkpoints/`,
    `cd ${CWD} && cat AgenticLab/knowledge/INDEX.md`,
    `cd ${CWD} && pwd && grep -c approved AgenticLab/knowledge/INDEX.md`,
  ]) {
    assert.equal(await shellReferencesKnowledge(CWD, command), false, command);
  }
});

test("a read segment cannot smuggle a later mutation", async () => {
  assert.equal(
    await shellReferencesKnowledge(
      CWD,
      `cd ${CWD} && ls AgenticLab/knowledge/records && rm AgenticLab/knowledge/records/a.md`,
    ),
    true,
  );
});

test("tools that can write are not knowledge read paths", async () => {
  for (const command of [
    "sed -i s/a/b/ AgenticLab/knowledge/records/x.md",
    "sed -n 'w /tmp/out' AgenticLab/knowledge/INDEX.md",
    "awk 'BEGIN{print > \"/tmp/x\"}' AgenticLab/knowledge/INDEX.md",
    "find AgenticLab/knowledge -delete",
    "find AgenticLab/knowledge -exec rm {} ;",
    "find AgenticLab/knowledge -fprint /tmp/list",
  ]) {
    assert.equal(await shellReferencesKnowledge(CWD, command), true, command);
  }
});

test("output redirection disqualifies a knowledge read", async () => {
  for (const command of [
    "cat AgenticLab/knowledge/INDEX.md > AgenticLab/knowledge/records/a.md",
    "cat AgenticLab/knowledge/INDEX.md >> /tmp/leak",
    `cd ${CWD} && grep approved AgenticLab/knowledge/INDEX.md > /tmp/x`,
  ]) {
    assert.equal(await shellReferencesKnowledge(CWD, command), true, command);
  }
});

test("plain readers and name-only find stay allowed", async () => {
  for (const command of [
    "cat AgenticLab/knowledge/INDEX.md",
    "wc -l AgenticLab/knowledge/INDEX.md",
    "grep -c approved AgenticLab/knowledge/INDEX.md",
    'find AgenticLab/knowledge -name "*.md"',
    `cd ${CWD} && head -20 AgenticLab/knowledge/INDEX.md`,
  ]) {
    assert.equal(await shellReferencesKnowledge(CWD, command), false, command);
  }
});

test("harmless trailing segments do not invalidate the checkpoint removal", async () => {
  for (const command of [
    `rm ${CHECKPOINT} && echo DELETED`,
    `rm ${CHECKPOINT} && ls AgenticLab/knowledge/checkpoints`,
    `cd ${CWD} && rm -f ${CHECKPOINT} && echo done`,
    `rm ${CHECKPOINT}; printf ok`,
  ]) {
    assert.equal(await shellReferencesKnowledge(CWD, command), false, command);
  }
});

test("a write-capable trailing segment still invalidates the removal", async () => {
  for (const command of [
    `rm ${CHECKPOINT} && echo x > AgenticLab/knowledge/records/a.md`,
    `rm ${CHECKPOINT} && echo leaked > /tmp/x`,
    `rm ${CHECKPOINT} && rm -rf AgenticLab/knowledge/records`,
  ]) {
    assert.equal(await shellReferencesKnowledge(CWD, command), true, command);
  }
});

import assert from "node:assert/strict";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

import { shellReferencesKnowledge } from "./_payload.mjs";
import { classifySensitivePathAccess } from "./_payload.mjs";
import { registerActionScopeGate } from "./_payload.mjs";
import { CHECKPOINT_RELATIVE_PATH } from "./_payload.mjs";

const CWD = path.resolve(import.meta.dirname, "..");
const KNOWLEDGE = "AgenticLab/knowledge";

test("query-only verbs no longer block a knowledge read", async () => {
  for (const command of [
    `date +%Y-%m-%d && cat ${KNOWLEDGE}/INDEX.md`,
    `date -u +%H:%M && find ${KNOWLEDGE} -name "*.md"`,
    `whoami && cat ${KNOWLEDGE}/INDEX.md`,
    `uname -a && ls ${KNOWLEDGE}`,
  ]) {
    assert.equal(await shellReferencesKnowledge(CWD, command), false, command);
  }
});

test("verbs that can set state with a flag stay blocked", async () => {
  for (const command of [
    `date -s now && cat ${KNOWLEDGE}/INDEX.md`,
    `date --set=2020-01-01 && ls ${KNOWLEDGE}`,
  ]) {
    assert.equal(await shellReferencesKnowledge(CWD, command), true, command);
  }
});

test("a grep pattern is not treated as a path", async () => {
  for (const command of ["grep -c .", "git status --porcelain | grep -c .", "rg TODO backend/app"]) {
    assert.equal(await classifySensitivePathAccess("bash", { command }, CWD), null, command);
  }
});

test("searching the workspace or a protected file is still blocked", async () => {
  for (const command of ["grep -r SECRET .", "rg -n KEY .", "cat .env"]) {
    assert.notEqual(await classifySensitivePathAccess("bash", { command }, CWD), null, command);
  }
});

function fakePi() {
  const handlers = new Map();
  return {
    on(name, handler) {
      if (!handlers.has(name)) handlers.set(name, []);
      handlers.get(name).push(handler);
    },
    async emit(name, event, ctx) {
      let result;
      for (const handler of handlers.get(name) ?? []) {
        const next = await handler(event, ctx);
        if (next !== undefined) result = next;
      }
      return result;
    },
  };
}

async function armedWithLog(t, { writeLog }) {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "gate-release-"));
  t.after(async () => fs.rm(dir, { recursive: true, force: true }));
  const logRel = "AgenticLab/implementation-logs/ws/task/implementation-log.md";
  const checkpoint = path.join(dir, CHECKPOINT_RELATIVE_PATH);
  await fs.mkdir(path.dirname(checkpoint), { recursive: true });
  await fs.writeFile(checkpoint, JSON.stringify({
    status: "approved", inScope: ["docs/a.md", logRel], outOfScope: [], validation: [],
  }), "utf8");
  if (writeLog) {
    await fs.mkdir(path.dirname(path.join(dir, logRel)), { recursive: true });
    await fs.writeFile(path.join(dir, logRel), "# log\n", "utf8");
  }
  const pi = fakePi();
  registerActionScopeGate(pi);
  const ctx = { cwd: dir };
  await pi.emit("input", { text: "/implement task", source: "interactive" }, ctx);
  return { dir, pi, ctx };
}

test("a written implementation log releases enforcement at settle", async (t) => {
  const { pi, ctx } = await armedWithLog(t, { writeLog: true });

  const before = await pi.emit("tool_call", { toolName: "write", input: { path: "outside.txt" } }, ctx);
  assert.equal(before.block, true, "still enforced before the agent settles");

  await pi.emit("agent_settled", {}, ctx);

  assert.equal(
    await pi.emit("tool_call", { toolName: "write", input: { path: "outside.txt" } }, ctx),
    undefined,
    "enforcement released once the plan's log exists",
  );
});

test("without the log the gate stays armed", async (t) => {
  const { pi, ctx } = await armedWithLog(t, { writeLog: false });

  await pi.emit("agent_settled", {}, ctx);

  const decision = await pi.emit("tool_call", { toolName: "write", input: { path: "outside.txt" } }, ctx);
  assert.equal(decision.block, true);
});

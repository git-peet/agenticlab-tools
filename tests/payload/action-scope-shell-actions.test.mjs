import assert from "node:assert/strict";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

import {
  classifyActionScope,
  readApprovedScope,
} from "./_payload.mjs";

const CHECKPOINT = "AgenticLab/knowledge/checkpoints/plan-checkpoint.json";

async function scopeWith(t, inScope) {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "action-scope-shell-"));
  t.after(async () => fs.rm(dir, { recursive: true, force: true }));
  const target = path.join(dir, CHECKPOINT);
  await fs.mkdir(path.dirname(target), { recursive: true });
  await fs.writeFile(target, JSON.stringify({ status: "approved", inScope, outOfScope: [], validation: [] }), "utf8");
  return { dir, scope: await readApprovedScope(dir) };
}

const cases = [
  ['find backend -name "*.py"', true],
  ["find backend -delete", true],
  ["find outside -delete", false],
  ["find outside -exec rm {} ;", false],
  ["find /etc -delete", false],
  ["sed -n 1,2p backend/a.txt", true],
  ["sed s/a/b/ outside/a.txt", true],
  ["sed -i s/a/b/ backend/a.txt", true],
  ["sed -i s/a/b/ outside/a.txt", false],
  ["sed -i.bak s/a/b/ outside/a.txt", false],
  ["sed -ni s/a/b/ outside/a.txt", false],
  ["sed --in-place s/a/b/ outside/a.txt", false],
];

test("in-place sed and find actions are scope-checked", async (t) => {
  const { dir, scope } = await scopeWith(t, ["backend/"]);
  for (const [command, allowed] of cases) {
    const decision = await classifyActionScope("bash", { command }, dir, scope);
    assert.equal(decision === null, allowed, `${command} -> ${decision?.reason}`);
  }
});

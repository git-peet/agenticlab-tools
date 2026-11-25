import assert from "node:assert/strict";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

import { registerActionScopeGate } from "../../../C03-clean-payload/adapters/pi/action-scope-gate.mjs";
import { CHECKPOINT_RELATIVE_PATH } from "../../../C03-clean-payload/adapters/pi/action-scope-policy.mjs";

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

test("removing the checkpoint does not release enforcement", async (t) => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "action-scope-failclosed-"));
  t.after(async () => fs.rm(dir, { recursive: true, force: true }));
  const checkpoint = path.join(dir, CHECKPOINT_RELATIVE_PATH);
  await fs.mkdir(path.dirname(checkpoint), { recursive: true });
  await fs.writeFile(checkpoint, JSON.stringify({
    status: "approved", inScope: ["docs/a.md"], outOfScope: [], validation: [],
  }), "utf8");

  const pi = fakePi();
  registerActionScopeGate(pi);
  const ctx = { cwd: dir };
  await pi.emit("input", { text: "/implement task", source: "interactive" }, ctx);

  const before = await pi.emit("tool_call", { toolName: "write", input: { path: "outside.txt" } }, ctx);
  assert.equal(before.block, true);

  // The authorized removal itself is allowed ...
  assert.equal(
    await pi.emit("tool_call", {
      toolName: "bash",
      input: { command: `rm ${CHECKPOINT_RELATIVE_PATH}` },
    }, ctx),
    undefined,
  );
  await fs.rm(checkpoint, { force: true });

  // ... and enforcement that cannot be verified still refuses later writes.
  const after = await pi.emit("tool_call", { toolName: "write", input: { path: "outside.txt" } }, ctx);
  assert.equal(after.block, true, "missing checkpoint must fail closed, not release");
  assert.match(after.reason, /could not be verified/u);
});

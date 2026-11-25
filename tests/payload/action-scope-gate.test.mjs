import assert from "node:assert/strict";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

import { registerActionScopeGate } from "./_payload.mjs";
import { CHECKPOINT_RELATIVE_PATH } from "./_payload.mjs";

function fakePi() {
  const handlers = new Map();
  return {
    handlers,
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

async function workspaceWithCheckpoint(t, checkpoint) {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "c04-action-gate-"));
  t.after(async () => fs.rm(dir, { recursive: true, force: true }));
  if (checkpoint !== undefined) {
    const target = path.join(dir, CHECKPOINT_RELATIVE_PATH);
    await fs.mkdir(path.dirname(target), { recursive: true });
    await fs.writeFile(target, JSON.stringify(checkpoint), "utf8");
  }
  return dir;
}

const approved = (overrides = {}) => ({
  status: "approved",
  inScope: ["backend/"],
  outOfScope: [],
  validation: [],
  ...overrides,
});

test("adapter enforces scope only while /implement is active", async (t) => {
  const dir = await workspaceWithCheckpoint(t, approved());
  const pi = fakePi();
  registerActionScopeGate(pi);
  const ctx = { cwd: dir };

  await pi.emit("session_start", { reason: "startup" }, ctx);

  // Fresh session: no deterministic claim before /implement.
  assert.equal(
    await pi.emit("tool_call", { toolName: "write", input: { path: "outside.txt" } }, ctx),
    undefined,
  );

  await pi.emit("input", { text: "/implement bounded task", source: "interactive" }, ctx);
  const blocked = await pi.emit(
    "tool_call", { toolName: "write", input: { path: "outside.txt" } }, ctx,
  );
  assert.equal(blocked.block, true);
  assert.match(blocked.reason, /Action-scope fidelity/u);

  assert.equal(
    await pi.emit("tool_call", { toolName: "write", input: { path: "backend/a.py" } }, ctx),
    undefined,
  );

  await pi.emit("input", { text: "/plan another task", source: "interactive" }, ctx);
  assert.equal(
    await pi.emit("tool_call", { toolName: "write", input: { path: "outside.txt" } }, ctx),
    undefined,
  );
});

test("adapter fails closed when the checkpoint is unavailable or unreadable", async (t) => {
  const missing = await workspaceWithCheckpoint(t);
  const missingPi = fakePi();
  registerActionScopeGate(missingPi);
  await missingPi.emit("input", { text: "/implement", source: "interactive" }, { cwd: missing });

  const missingDecision = await missingPi.emit(
    "tool_call", { toolName: "write", input: { path: "backend/a.py" } }, { cwd: missing },
  );
  assert.equal(missingDecision.block, true);
  assert.match(missingDecision.reason, /could not be verified.*checkpoint is unavailable/u);

  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "c04-action-gate-error-"));
  t.after(async () => fs.rm(dir, { recursive: true, force: true }));
  const checkpointPath = path.join(dir, CHECKPOINT_RELATIVE_PATH);
  await fs.mkdir(path.dirname(checkpointPath), { recursive: true });
  await fs.mkdir(checkpointPath);

  const errorPi = fakePi();
  registerActionScopeGate(errorPi);
  const ctx = { cwd: dir };
  await errorPi.emit("input", { text: "/implement", source: "interactive" }, ctx);
  const errorDecision = await errorPi.emit(
    "tool_call", { toolName: "write", input: { path: "backend/a.py" } }, ctx,
  );
  assert.equal(errorDecision.block, true);
  assert.match(errorDecision.reason, /could not be verified/u);
});

test("user-initiated shell commands are intercepted when armed", async (t) => {
  const dir = await workspaceWithCheckpoint(t, approved());
  const pi = fakePi();
  registerActionScopeGate(pi);
  const ctx = { cwd: dir };

  await pi.emit("input", { text: "/implement", source: "interactive" }, ctx);
  const result = await pi.emit(
    "user_bash", { command: "cp tests/a.txt outside/b.txt", cwd: dir }, ctx,
  );
  assert.equal(result.result.exitCode, 126);
  assert.match(result.result.output, /Action-scope fidelity/u);
});

test("a relayed /implement does not arm enforcement", async (t) => {
  const dir = await workspaceWithCheckpoint(t, approved());
  const pi = fakePi();
  registerActionScopeGate(pi);
  const ctx = { cwd: dir };

  await pi.emit("input", { text: "/implement", source: "extension" }, ctx);
  assert.equal(
    await pi.emit("tool_call", { toolName: "write", input: { path: "outside.txt" } }, ctx),
    undefined,
  );
});

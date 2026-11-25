import assert from "node:assert/strict";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

import {
  CHECKPOINT_RELATIVE_PATH,
  classifyActionScope,
  classifyImplementationInput,
  extractValidationCommands,
  readApprovedScope,
} from "../../../C03-clean-payload/adapters/pi/action-scope-policy.mjs";

async function isolatedWorkspace(t, checkpoint) {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "c04-action-scope-"));
  t.after(async () => fs.rm(dir, { recursive: true, force: true }));

  if (checkpoint !== undefined) {
    const target = path.join(dir, CHECKPOINT_RELATIVE_PATH);
    await fs.mkdir(path.dirname(target), { recursive: true });
    await fs.writeFile(
      target,
      typeof checkpoint === "string" ? checkpoint : JSON.stringify(checkpoint),
      "utf8",
    );
  }
  return dir;
}

const approvedCheckpoint = (overrides = {}) => ({
  phase: "plan",
  status: "approved",
  task: "synthetic action-scope fixture",
  scope: "project:fastapi-c04-action-scope",
  inScope: [],
  outOfScope: [],
  validation: [],
  ...overrides,
});

test("only a direct /implement arms enforcement", () => {
  assert.equal(classifyImplementationInput("/implement task", "interactive"), "arm");
  assert.equal(classifyImplementationInput("/implement", "interactive"), "arm");
  assert.equal(classifyImplementationInput("/implement", "extension"), null);
  assert.equal(classifyImplementationInput("/plan task", "interactive"), "disarm");
  assert.equal(classifyImplementationInput("/premortem", "interactive"), "disarm");
  assert.equal(classifyImplementationInput("please edit the file", "interactive"), null);
});

test("validation commands are extracted from checkpoint entries", () => {
  const commands = extractValidationCommands([
    "isolated tests; command: node --test tests/action-scope.test.mjs; cwd: .; "
      + "environment: none; prerequisites: none; side effects: none; status: planned",
    "blocked check; command: unavailable; cwd: .; status: blocked",
  ]);
  assert.deepEqual(commands, ["node --test tests/action-scope.test.mjs", "unavailable"]);
});

test("exact in-scope write is allowed; near-miss and broader writes are blocked", async (t) => {
  const dir = await isolatedWorkspace(t, approvedCheckpoint({
    inScope: ["backend/app/api/routes/items.py"],
  }));
  const scope = await readApprovedScope(dir);

  assert.equal(
    await classifyActionScope("write", { path: "backend/app/api/routes/items.py" }, dir, scope),
    null,
  );

  const nearMiss = await classifyActionScope(
    "write", { path: "backend/app/api/routes/users.py" }, dir, scope,
  );
  assert.match(nearMiss.reason, /not an exact approved path/u);

  const broader = await classifyActionScope("write", { path: "backend/" }, dir, scope);
  assert.match(broader.reason, /not an exact approved path/u);
});

test("outOfScope overrides an in-scope parent", async (t) => {
  const dir = await isolatedWorkspace(t, approvedCheckpoint({
    inScope: ["backend/"],
    outOfScope: ["backend/app/core/config.py"],
  }));
  const scope = await readApprovedScope(dir);

  const decision = await classifyActionScope(
    "edit", { path: "backend/app/core/config.py" }, dir, scope,
  );
  assert.match(decision.reason, /not an exact approved path/u);
  assert.equal(
    await classifyActionScope("edit", { path: "backend/app/api/routes/items.py" }, dir, scope),
    null,
  );
});

test("protected env path is blocked even under a broad scope", async (t) => {
  const dir = await isolatedWorkspace(t, approvedCheckpoint({ inScope: ["."] }));
  const scope = await readApprovedScope(dir);

  const blocked = await classifyActionScope("write", { path: ".env" }, dir, scope);
  assert.match(blocked.reason, /protected environment file/u);
  assert.equal(await classifyActionScope("write", { path: ".env.example" }, dir, scope), null);
});

test("symlink escape from an in-scope directory is blocked", async (t) => {
  const dir = await isolatedWorkspace(t, approvedCheckpoint({ inScope: ["backend/"] }));
  await fs.mkdir(path.join(dir, "backend"), { recursive: true });
  await fs.writeFile(path.join(dir, "outside.txt"), "synthetic", "utf8");
  await fs.symlink(path.join(dir, "outside.txt"), path.join(dir, "backend", "escape.txt"));
  const scope = await readApprovedScope(dir);

  const decision = await classifyActionScope(
    "write", { path: "backend/escape.txt" }, dir, scope,
  );
  assert.match(decision.reason, /not an exact approved path/u);
});

test("exact approved validation command is allowed", async (t) => {
  const dir = await isolatedWorkspace(t, approvedCheckpoint({
    inScope: ["backend/"],
    validation: [
      "tests; command: node --test tests/action-scope.test.mjs; cwd: .; "
        + "environment: none; prerequisites: none; side effects: none; status: planned",
      "cleanup; command: rm -rf backend/tmp-artifacts; cwd: .; "
        + "environment: none; prerequisites: none; side effects: removes approved artifacts; status: planned",
    ],
  }));
  const scope = await readApprovedScope(dir);

  assert.equal(
    await classifyActionScope(
      "bash", { command: "node --test tests/action-scope.test.mjs" }, dir, scope,
    ),
    null,
  );
  assert.equal(
    await classifyActionScope(
      "bash", { command: "rm -rf backend/tmp-artifacts" }, dir, scope,
    ),
    null,
  );
});

test("extra shell segment cannot be appended to an approved command", async (t) => {
  const dir = await isolatedWorkspace(t, approvedCheckpoint({
    inScope: ["backend/"],
    validation: ["tests; command: node --test tests/action-scope.test.mjs; cwd: .; status: planned"],
  }));
  const scope = await readApprovedScope(dir);

  const decision = await classifyActionScope(
    "bash",
    { command: "node --test tests/action-scope.test.mjs && rm -rf backend" },
    dir,
    scope,
  );
  assert.match(decision.reason, /recursive deletion/u);
});

test("mutating shell targets are checked against exact scope", async (t) => {
  const dir = await isolatedWorkspace(t, approvedCheckpoint({
    inScope: ["backend/", "tests/"],
  }));
  const scope = await readApprovedScope(dir);

  assert.equal(
    await classifyActionScope("bash", { command: "cp tests/a.txt backend/b.txt" }, dir, scope),
    null,
  );

  const outside = await classifyActionScope(
    "bash", { command: "cp tests/a.txt outside/b.txt" }, dir, scope,
  );
  assert.match(outside.reason, /not an exact approved path/u);
});

test("exact checkpoint deletion is allowed as a workflow transition", async (t) => {
  const dir = await isolatedWorkspace(t, approvedCheckpoint({ inScope: ["backend/"] }));
  const scope = await readApprovedScope(dir);

  assert.equal(
    await classifyActionScope(
      "bash",
      { command: "rm AgenticLab/knowledge/checkpoints/plan-checkpoint.json" },
      dir,
      scope,
    ),
    null,
  );

  const other = await classifyActionScope(
    "bash",
    { command: "rm AgenticLab/knowledge/checkpoints/other.json" },
    dir,
    scope,
  );
  assert.match(other.reason, /not an exact approved path/u);
});

test("git and external network mutations are always outside scope", async (t) => {
  const dir = await isolatedWorkspace(t, approvedCheckpoint({ inScope: ["."] }));
  const scope = await readApprovedScope(dir);

  assert.equal(await classifyActionScope("bash", { command: "git status --short" }, dir, scope), null);

  const push = await classifyActionScope("bash", { command: "git push" }, dir, scope);
  assert.match(push.reason, /git mutations/u);

  const curl = await classifyActionScope("bash", { command: "curl https://example.invalid" }, dir, scope);
  assert.match(curl.reason, /external network operations/u);
});

test("missing, malformed, or unapproved checkpoints fail closed", async (t) => {
  const missing = await isolatedWorkspace(t);
  await assert.rejects(readApprovedScope(missing), /unavailable/u);

  const malformed = await isolatedWorkspace(t, "{not json");
  await assert.rejects(readApprovedScope(malformed), /malformed/u);

  const unapproved = await isolatedWorkspace(t, approvedCheckpoint({ status: "draft" }));
  await assert.rejects(readApprovedScope(unapproved), /not approved/u);

  const emptyScope = await isolatedWorkspace(t, approvedCheckpoint({ inScope: [] }));
  await assert.rejects(readApprovedScope(emptyScope), /no inScope/u);

  await assert.rejects(
    classifyActionScope("write", { path: "backend/a.txt" }, missing, {}),
    /scope is invalid/u,
  );
});

import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

import {
  classifyRefresh,
  collectManifest,
  isManagedPath,
  readRevision,
  summarizeRefresh,
  writeRevision,
} from "../../../C03-clean-payload/adapters/pi/payload-refresh-policy.mjs";

async function workspace(t, files) {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "payload-refresh-"));
  t.after(async () => fs.rm(dir, { recursive: true, force: true }));
  for (const [rel, content] of Object.entries(files)) {
    const target = path.join(dir, rel);
    await fs.mkdir(path.dirname(target), { recursive: true });
    await fs.writeFile(target, content, "utf8");
  }
  return dir;
}

test("managed paths cover payload files and exclude workspace-owned state", () => {
  for (const rel of ["AGENTS.md", "README.md", "brain/x.md", "adapters/pi/a.mjs", "prompts/delivery/plan.md", "config/knowledge-capture-policy.template.json"]) {
    assert.equal(isManagedPath(rel), true, rel);
  }
  for (const rel of [
    ".pi/settings.json",
    "PROJECT-SCOPE.md",
    "config/knowledge-capture-policy.json",
    "knowledge/INDEX.md",
    "knowledge/records/a.md",
    "knowledge/checkpoints/plan-checkpoint.json",
    "knowledge-pending/bundle.json",
    "implementation-logs/w/t/log.md",
    "adapters/pi/tests/a.test.mjs",
    "SOURCE-REVISION.json",
    "helpers/.gitkeep",
  ]) {
    assert.equal(isManagedPath(rel), false, rel);
  }
});

test("manifest ignores unmanaged files", async (t) => {
  const dir = await workspace(t, {
    "AGENTS.md": "a",
    "adapters/pi/gate.mjs": "b",
    "knowledge/INDEX.md": "c",
    ".pi/settings.json": "{}",
  });
  const manifest = await collectManifest(dir);
  assert.deepEqual(Object.keys(manifest).sort(), ["AGENTS.md", "adapters/pi/gate.mjs"]);
});

test("noop when installed tree matches payload and revision", async (t) => {
  const payload = await workspace(t, { "AGENTS.md": "a", "brain/x.md": "b" });
  const installed = await workspace(t, { "AgenticLab/AGENTS.md": "a", "AgenticLab/brain/x.md": "b" });
  const manifest = await collectManifest(path.join(installed, "AgenticLab"));
  await writeRevision(installed, { revision: "abc123", manifest });

  const incoming = await collectManifest(payload);
  const now = await collectManifest(path.join(installed, "AgenticLab"));
  const recorded = (await readRevision(installed)).files;
  const result = classifyRefresh({ installed: now, incoming, recorded });
  const summary = summarizeRefresh({ installedRevision: "abc123", incomingRevision: "abc123", result });

  assert.equal(summary.status, "noop");
  assert.deepEqual(result.added, []);
  assert.deepEqual(result.conflicts, []);
});

test("an unknown incoming revision label does not create phantom changes", async (t) => {
  const payload = await workspace(t, { "AGENTS.md": "a" });
  const installed = await workspace(t, { "AgenticLab/AGENTS.md": "a" });
  const manifest = await collectManifest(path.join(installed, "AgenticLab"));
  await writeRevision(installed, { revision: "abc123", manifest });

  const incoming = await collectManifest(payload);
  const now = await collectManifest(path.join(installed, "AgenticLab"));
  const recorded = (await readRevision(installed)).files;
  const result = classifyRefresh({ installed: now, incoming, recorded });
  const summary = summarizeRefresh({
    installedRevision: "abc123",
    incomingRevision: "payload-under-comparison",
    result,
  });

  assert.equal(summary.status, "noop");
  assert.equal(summary.incomingRevisionKnown, false);
});

test("adds and replaces files when the payload moved on", async (t) => {
  const installed = await workspace(t, { "AgenticLab/AGENTS.md": "old", "AgenticLab/brain/x.md": "same" });
  const manifest = await collectManifest(path.join(installed, "AgenticLab"));
  await writeRevision(installed, { revision: "old1", manifest });

  const payload = await workspace(t, { "AGENTS.md": "new", "brain/x.md": "same", "prompts/p.md": "fresh" });
  const incoming = await collectManifest(payload);
  const now = await collectManifest(path.join(installed, "AgenticLab"));
  const recorded = (await readRevision(installed)).files;
  const result = classifyRefresh({ installed: now, incoming, recorded });

  assert.deepEqual(result.added, ["prompts/p.md"]);
  assert.deepEqual(result.replaced, ["AGENTS.md"]);
  assert.deepEqual(result.unchanged, ["brain/x.md"]);
  assert.deepEqual(result.conflicts, []);
});

test("a locally edited managed file is a conflict, never a replacement", async (t) => {
  const installed = await workspace(t, { "AgenticLab/AGENTS.md": "pristine" });
  const manifest = await collectManifest(path.join(installed, "AgenticLab"));
  await writeRevision(installed, { revision: "old1", manifest });

  await fs.writeFile(path.join(installed, "AgenticLab/AGENTS.md"), "locally edited", "utf8");

  const payload = await workspace(t, { "AGENTS.md": "upstream newer" });
  const incoming = await collectManifest(payload);
  const now = await collectManifest(path.join(installed, "AgenticLab"));
  const recorded = (await readRevision(installed)).files;
  const result = classifyRefresh({ installed: now, incoming, recorded });

  assert.deepEqual(result.conflicts, ["AGENTS.md"]);
  assert.deepEqual(result.replaced, []);
});

test("files dropped from the payload are reported, not scheduled for deletion", async (t) => {
  const installed = await workspace(t, { "AgenticLab/AGENTS.md": "a", "AgenticLab/brain/old.md": "b" });
  const manifest = await collectManifest(path.join(installed, "AgenticLab"));
  await writeRevision(installed, { revision: "v1", manifest });

  const payload = await workspace(t, { "AGENTS.md": "a" });
  const incoming = await collectManifest(payload);
  const now = await collectManifest(path.join(installed, "AgenticLab"));
  const recorded = (await readRevision(installed)).files;
  const result = classifyRefresh({ installed: now, incoming, recorded });

  assert.deepEqual(result.obsolete, ["brain/old.md"]);
});

test("a missing or malformed revision is reported, not guessed", async (t) => {
  const dir = await workspace(t, { "AgenticLab/AGENTS.md": "a" });
  await assert.rejects(readRevision(dir), /predates refresh support/u);

  await fs.writeFile(path.join(dir, "AgenticLab/SOURCE-REVISION.json"), "{not json", "utf8");
  await assert.rejects(readRevision(dir), /malformed/u);

  await fs.writeFile(path.join(dir, "AgenticLab/SOURCE-REVISION.json"), JSON.stringify({ files: {} }), "utf8");
  await assert.rejects(readRevision(dir), /missing its commit id/u);
});

test("the CLI can record a baseline without a payload directory", async (t) => {
  const dir = await workspace(t, {
    "AgenticLab/AGENTS.md": "a",
    "AgenticLab/brain/x.md": "b",
  });
  const cli = new URL("../../../C03-clean-payload/adapters/pi/payload-refresh-cli.mjs", import.meta.url);

  const output = await new Promise((resolve, reject) => {
    execFile(
      process.execPath,
      [cli.pathname, "--record", "--revision", "abc123", "--cwd", dir],
      (error, stdout) => (error ? reject(error) : resolve(stdout)),
    );
  });

  const result = JSON.parse(output);
  assert.equal(result.status, "recorded");
  assert.equal(result.revision, "abc123");
  assert.equal(result.files, 2);

  const recorded = await readRevision(dir);
  assert.equal(recorded.revision, "abc123");
});

test("the CLI still requires --payload for a preview", async (t) => {
  const dir = await workspace(t, { "AgenticLab/AGENTS.md": "a" });
  const cli = new URL("../../../C03-clean-payload/adapters/pi/payload-refresh-cli.mjs", import.meta.url);

  const output = await new Promise((resolve) => {
    execFile(
      process.execPath,
      [cli.pathname, "--cwd", dir],
      (error, stdout) => resolve(stdout),
    );
  });

  const result = JSON.parse(output);
  assert.equal(result.status, "error");
  assert.match(result.reason, /--payload/u);
});

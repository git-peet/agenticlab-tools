import { execFile } from "node:child_process";
import assert from "node:assert/strict";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

import { aggregate, classifyGate, percentile, readEntries } from "../session-metrics.mjs";

function entry(type, message) {
  return JSON.stringify({ type, timestamp: message?.timestamp, message });
}

const assistant = (timestamp, blocks, usage) => ({
  role: "assistant",
  timestamp,
  provider: "p",
  model: "m",
  thinkingLevel: "high",
  usage,
  content: blocks,
});

const call = (id, name, args = {}) => ({ type: "toolCall", id, name, arguments: args });

test("gate refusals are classified by their stable opening text", () => {
  assert.equal(classifyGate("Action-scope fidelity: target 'x' is not an exact approved path; blocked."), "action_scope");
  assert.equal(classifyGate("Sensitive environment files are protected. Content reads..."), "sensitive_path");
  assert.equal(classifyGate("Shell access to AgenticLab knowledge, the private pending queue..."), "knowledge_shell");
  assert.equal(classifyGate("Project knowledge change was not approved."), "knowledge_write");
  assert.equal(classifyGate("All good"), null);
});

test("percentiles tolerate small and empty samples", () => {
  assert.equal(percentile([], 50), "");
  assert.equal(percentile([5], 50), "5.000");
  assert.equal(percentile([1, 2, 3, 4], 50), "2.000");
  assert.equal(percentile([1, 2, 3, 4], 90), "4.000");
});

test("aggregate counts messages, tools, tokens, and gate refusals", async (t) => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "session-metrics-"));
  t.after(async () => fs.rm(dir, { recursive: true, force: true }));
  const file = path.join(dir, "session.jsonl");
  const usage = { input: 10, output: 5, cacheRead: 3, cacheWrite: 0, totalTokens: 18, cost: { total: 0.5 } };

  await fs.writeFile(file, [
    entry("session", undefined),
    entry("message", { role: "user", timestamp: 1000 }),
    entry("message", assistant(2000, [call("c1", "bash", { command: "pytest -q" })], usage)),
    entry("message", { role: "toolResult", toolCallId: "c1", toolName: "bash", isError: false, timestamp: 3500 }),
    entry("message", assistant(9000, [call("c2", "bash", { command: "cat .env" })], usage)),
    entry("message", {
      role: "toolResult", toolCallId: "c2", toolName: "bash", isError: true, timestamp: 9500,
      content: [{ type: "text", text: "Sensitive environment files are protected." }],
    }),
  ].join("\n"), "utf8");

  const entries = await readEntries(file);
  const { row, gateRows, roundtripRows } = aggregate({
    runId: "T1",
    session: { role: "delivery_pi", label: "unit", entries },
  });
  const byName = Object.fromEntries(row.map((value, index) => [index, value]));

  assert.equal(byName[0], "run");
  assert.equal(byName[8], 1);   // user_messages
  assert.equal(byName[9], 2);   // assistant_messages
  assert.equal(byName[11], 2);  // tool_calls
  assert.equal(byName[13], 1);  // isError flags
  assert.equal(byName[17], 2);  // bash calls
  assert.equal(byName[21], 20); // input tokens
  assert.equal(byName[25], 36); // total tokens
  assert.equal(byName[26], "1.000000000");
  assert.equal(byName[30], 0);  // compactions
  assert.equal(byName[31], 0);  // playwright commands

  assert.equal(gateRows.length, 1);
  assert.equal(gateRows[0][3], "sensitive_path");
  assert.equal(roundtripRows.length, 1);
  assert.equal(roundtripRows[0][5], "2.00"); // 1.5s + 0.5s
});

test("a declared window excludes messages outside it", async (t) => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "session-metrics-window-"));
  t.after(async () => fs.rm(dir, { recursive: true, force: true }));
  const file = path.join(dir, "session.jsonl");
  await fs.writeFile(file, [
    entry("message", { role: "user", timestamp: 1000 }),
    entry("message", { role: "user", timestamp: 5000 }),
    entry("message", { role: "user", timestamp: 9000 }),
  ].join("\n"), "utf8");

  const entries = await readEntries(file);
  const { row } = aggregate({
    runId: "T2",
    session: { role: "delivery_pi", label: "unit", entries, windowStart: 2000, windowEnd: 6000 },
  });

  assert.equal(row[8], 1);      // only the middle user message
  assert.equal(row[6], 4);      // 4 second declared span
  assert.equal(row[7], "declared window");
});

test("retrieval activation and outcomes are derived from the adapter's own status", async (t) => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "session-metrics-retrieval-"));
  t.after(async () => fs.rm(dir, { recursive: true, force: true }));
  const file = path.join(dir, "session.jsonl");
  const usage = { input: 1, output: 1, cacheRead: 0, cacheWrite: 0, totalTokens: 2, cost: { total: 0 } };

  await fs.writeFile(file, [
    entry("message", assistant(1000, [call("r1", "readKnowledgeRecords", { query: "a" })], usage)),
    entry("message", {
      role: "toolResult", toolCallId: "r1", toolName: "readKnowledgeRecords", isError: false, timestamp: 1200,
      content: [{ type: "text", text: JSON.stringify({ status: "ok", count: 2, complete: true }) }],
    }),
    entry("message", assistant(2000, [call("r2", "readKnowledgeRecords", { query: "b" })], usage)),
    entry("message", {
      role: "toolResult", toolCallId: "r2", toolName: "readKnowledgeRecords", isError: false, timestamp: 2200,
      content: [{ type: "text", text: JSON.stringify({ status: "no_match", count: 0, complete: true }) }],
    }),
  ].join("\n"), "utf8");

  const entries = await readEntries(file);
  const { row } = aggregate({ runId: "T3", session: { role: "delivery_pi", label: "unit", entries } });
  const retrievalStart = 35; // index of retrieval_calls in the session row

  assert.equal(row[retrievalStart], 2, "two retrieval calls");
  assert.equal(row[retrievalStart + 1], 1, "one applicable result");
  assert.equal(row[retrievalStart + 2], 1, "one no_match result");
  assert.equal(row[retrievalStart + 3], 0, "no filtered results");
  assert.equal(row[retrievalStart + 4], 0, "no unavailable results");
});

test("the CLI honours declared --start and --end windows", async (t) => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "session-metrics-cli-window-"));
  t.after(async () => fs.rm(dir, { recursive: true, force: true }));
  const session = path.join(dir, "session.jsonl");
  const usage = { input: 1, output: 1, cacheRead: 0, cacheWrite: 0, totalTokens: 2, cost: { total: 0 } };
  await fs.writeFile(session, [
    entry("message", { role: "user", timestamp: 1000 }),
    entry("message", assistant(2000, [], usage)),
    entry("message", { role: "user", timestamp: 5000 }),
    entry("message", assistant(6000, [], usage)),
  ].join("\n"), "utf8");

  const cli = path.resolve(import.meta.dirname, "../session-metrics.mjs");
  const out = path.join(dir, "out");
  await new Promise((resolve, reject) => {
    execFile(process.execPath, [cli, "--run", "W", "--session", session, "--role", "delivery_pi", "--label", "w", "--start", "1970-01-01T00:00:02Z", "--end", "1970-01-01T00:00:03Z", "--out", out], (error) => (error ? reject(error) : resolve()));
  });

  const row = (await fs.readFile(path.join(out, "W-session-metrics.csv"), "utf8")).split("\n")[1];
  const fields = row.split(",");
  assert.equal(fields[8], "0"); // the user message at 1 s is outside the window
  assert.equal(fields[9], "1"); // the assistant message at 6 s is inside the window
  assert.equal(fields[24], "1"); // input tokens, one assistant in window
});

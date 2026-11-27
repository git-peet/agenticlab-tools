import { execFile } from "node:child_process";
import { promises as fs } from "node:fs";
import path from "node:path";

import { runPayloadStaticChecks, EXPECTED_PAYLOAD_REVISION } from "./checks/payload-static.mjs";
import { runDocsSyncChecks } from "./checks/docs-sync.mjs";

const TOOLS_ROOT = path.resolve(import.meta.dirname, "..");
const CANONICAL_ROOT = process.env.CANONICAL_ROOT
  ? path.resolve(process.env.CANONICAL_ROOT)
  : path.resolve(TOOLS_ROOT, "../AgenticLab-v5");
const PAYLOAD_ROOT = process.env.PAYLOAD_ROOT
  ? path.resolve(process.env.PAYLOAD_ROOT)
  : path.resolve(TOOLS_ROOT, "../C03-clean-payload");

function run(command, args, cwd) {
  return new Promise((resolve) => {
    execFile(command, args, { cwd, timeout: 300000 }, (error, stdout, stderr) => {
      resolve({ code: error?.code ?? 0, stdout, stderr });
    });
  });
}

async function testFiles(dir) {
  const output = [];
  async function walk(current) {
    const entries = await fs.readdir(current, { withFileTypes: true });
    for (const entry of entries) {
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) await walk(full);
      else if (entry.name.endsWith(".test.mjs")) output.push(full);
    }
  }
  await walk(dir);
  return output.sort();
}

const checks = [];
checks.push(...await runPayloadStaticChecks(PAYLOAD_ROOT));
checks.push(...await runDocsSyncChecks(CANONICAL_ROOT, EXPECTED_PAYLOAD_REVISION));

const tests = await testFiles(path.join(TOOLS_ROOT, "tests"));
const testRun = await run("node", ["--test", ...tests], TOOLS_ROOT);
const testSummary = testRun.stdout.match(/^ℹ (tests|pass|fail) .*$/gmu) ?? [];
checks.push({
  name: "external AgenticLab-tools suites",
  passed: testRun.code === 0,
  detail: testSummary.join("; ") || `exit=${testRun.code}`,
});

const passed = checks.every((check) => check.passed);
for (const check of checks) {
  process.stdout.write(`${check.passed ? "PASS" : "FAIL"}  ${check.name}${check.detail ? ` — ${check.detail}` : ""}\n`);
}
process.stdout.write(`\nrelease acceptance: ${passed ? "PASS" : "FAIL"}\n`);
process.stdout.write(JSON.stringify({
  canonicalRoot: CANONICAL_ROOT,
  payloadRoot: PAYLOAD_ROOT,
  expectedRevision: EXPECTED_PAYLOAD_REVISION,
  checks,
}, null, 2) + "\n");
process.exit(passed ? 0 : 1);

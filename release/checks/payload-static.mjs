import { execFile } from "node:child_process";
import { promises as fs } from "node:fs";
import path from "node:path";

export const EXPECTED_PAYLOAD_REVISION = "d07ff5d914dcc73a373a00989a9ba7306c39839b";

function run(command, args, cwd) {
  return new Promise((resolve) => {
    execFile(command, args, { cwd, timeout: 120000 }, (error, stdout, stderr) => {
      resolve({ code: error?.code ?? 0, stdout, stderr });
    });
  });
}

async function trackedFiles(root) {
  const result = await run("git", ["ls-files"], root);
  if (result.code !== 0) return [];
  return result.stdout.split("\n").map((line) => line.trim()).filter(Boolean);
}

function check(name, passed, detail = "") {
  return { name, passed, detail };
}

export async function runPayloadStaticChecks(payloadRoot) {
  const checks = [];
  const files = await trackedFiles(payloadRoot);

  const head = await run("git", ["rev-parse", "HEAD"], payloadRoot);
  const revision = head.stdout.trim();
  checks.push(check(
    "payload revision",
    revision === EXPECTED_PAYLOAD_REVISION,
    `expected ${EXPECTED_PAYLOAD_REVISION.slice(0, 7)}, found ${revision.slice(0, 7) || "missing"}`,
  ));

  const mjs = files.filter((file) => file.endsWith(".mjs"));
  let syntaxFailures = 0;
  for (const file of mjs) {
    const result = await run("node", ["--check", file], payloadRoot);
    if (result.code !== 0) syntaxFailures += 1;
  }
  checks.push(check("payload .mjs syntax", syntaxFailures === 0, `${mjs.length} files checked`));

  let jsonFailures = 0;
  for (const file of files.filter((file) => file.endsWith(".json"))) {
    try {
      JSON.parse(await fs.readFile(path.join(payloadRoot, file), "utf8"));
    } catch {
      jsonFailures += 1;
    }
  }
  checks.push(check("payload JSON", jsonFailures === 0, `${files.filter((f) => f.endsWith(".json")).length} files checked`));

  const forbidden = [
    [/\/home\/peet/u, "absolute home path"],
    [/exp[0-9]/iu, "exp experiment id"],
    [/PBI[0-9]/iu, "PBI id"],
    [/ALPHA-/u, "experiment id"],
    [/AlphaProject/u, "project name"],
    [/fastapi-delivery/u, "project key"],
    [/probe-v/u, "probe value"],
  ];
  const offenders = [];
  const textFiles = files.filter((file) => /\.(?:mjs|ts|md|json)$/u.test(file));
  for (const file of textFiles) {
    const text = await fs.readFile(path.join(payloadRoot, file), "utf8");
    for (const [pattern, label] of forbidden) {
      if (pattern.test(text)) offenders.push(`${file}: ${label}`);
    }
  }
  checks.push(check("generic payload identifiers", offenders.length === 0, offenders.slice(0, 4).join("; ") || "no local identifiers"));

  const testLike = files.filter((file) => /(^|\/)(tests?|fixtures?)(\/|$)/iu.test(file));
  checks.push(check("payload has no tests/fixtures", testLike.length === 0, testLike.slice(0, 4).join(", ") || "none"));

  let frontmatterFailures = 0;
  for (const file of files.filter((file) => file.endsWith(".md"))) {
    const text = await fs.readFile(path.join(payloadRoot, file), "utf8");
    if (!text.startsWith("---\n")) continue;
    const end = text.indexOf("\n---\n", 4);
    if (end === -1) {
      frontmatterFailures += 1;
      continue;
    }
    const header = text.slice(4, end);
    for (const line of header.split("\n")) {
      if (!line.trim()) continue;
      if (!/^[A-Za-z0-9_-]+:\s*\S/u.test(line)) frontmatterFailures += 1;
      const value = line.replace(/^[A-Za-z0-9_-]+:\s*/u, "");
      if (!/^["'].*["']$/u.test(value) && /:\s/u.test(value)) frontmatterFailures += 1;
    }
  }
  checks.push(check("prompt frontmatter", frontmatterFailures === 0, `${frontmatterFailures} malformed line(s)`));

  return checks;
}

import { execFile } from "node:child_process";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";

/**
 * Mechanical rubric scoring.
 *
 * A rubric is data: a list of criteria, each naming a check that can be decided by
 * inspecting files or by running the artifact. The scorer reports met/not-met **with
 * the line that decided it**. It never reports pass/fail for the run as a whole and
 * never interprets a score: that stays a human judgement in the evaluation record.
 */

export async function loadRubric(file) {
  const raw = await fs.readFile(path.resolve(file), "utf8");
  const rubric = JSON.parse(raw);
  if (!rubric || typeof rubric.id !== "string" || !Array.isArray(rubric.criteria)) {
    throw new Error("rubric must have an id and a criteria array");
  }
  for (const criterion of rubric.criteria) {
    if (!criterion.id || !criterion.type) throw new Error("each criterion needs id and type");
    if (!["file-exists", "source-contains", "run-expect"].includes(criterion.type)) {
      throw new Error(`unsupported criterion type: ${criterion.type}`);
    }
  }
  return rubric;
}

/** A fixture is materialised outside the artifact so scoring never mutates it. */
async function materialiseFixture(kind, dir) {
  if (kind === "documented") {
    await fs.mkdir(dir, { recursive: true });
    await fs.writeFile(
      path.join(dir, "compose.yml"),
      "services:\n  check:\n    environment:\n      - A=${DOCUMENTED_VAR:-default}\n",
      "utf8",
    );
    await fs.writeFile(path.join(dir, ".env.example"), "DOCUMENTED_VAR=value\n", "utf8");
    return dir;
  }
  if (kind !== "required-var") throw new Error(`unknown fixture: ${kind}`);
  await fs.mkdir(dir, { recursive: true });
  await fs.writeFile(
    path.join(dir, "compose.yml"),
    "services:\n  check:\n    environment:\n      - A=${ALPHA_REQUIRED:?set it}\n",
    "utf8",
  );
  await fs.writeFile(path.join(dir, ".env.example"), "OTHER=1\n", "utf8");
  return dir;
}

function run(command, args, cwd) {
  return new Promise((resolve) => {
    execFile(command, args, { cwd, timeout: 120000 }, (error, stdout, stderr) => {
      resolve({ code: error?.code ?? 0, stdout, stderr });
    });
  });
}

function firstMatch(text, pattern) {
  if (!pattern) return "";
  const regex = new RegExp(pattern, "u");
  return String(text).split("\n").find((line) => regex.test(line)) ?? "";
}

async function evaluate(criterion, artifactDir, tempRoot) {
  const target = path.join(artifactDir, criterion.path ?? "");

  if (criterion.type === "file-exists") {
    const exists = await fs.access(target).then(() => true, () => false);
    return { met: exists, evidence: exists ? `present: ${criterion.path}` : `missing: ${criterion.path}` };
  }

  if (criterion.type === "source-contains") {
    let text = "";
    try {
      text = await fs.readFile(target, "utf8");
    } catch {
      return { met: false, evidence: `missing: ${criterion.path}` };
    }
    const line = firstMatch(text, criterion.pattern);
    return { met: Boolean(line), evidence: line ? line.trim().slice(0, 120) : `no line matching ${criterion.pattern} in ${criterion.path}` };
  }

  // run-expect
  const script = path.join(artifactDir, criterion.script);
  let cwd = artifactDir;
  const args = [...(criterion.args ?? [])];
  if (criterion.fixture) {
    const fixtureDir = await materialiseFixture(criterion.fixture, path.join(tempRoot, criterion.id));
    const rootIndex = args.indexOf("--root");
    if (rootIndex >= 0) args[rootIndex + 1] = fixtureDir;
    else args.push("--root", fixtureDir);
    if (criterion.runInFixture) cwd = fixtureDir;
  } else if (criterion.root === "artifact") {
    args.push("--root", artifactDir);
  }
  const result = await run(criterion.interpreter ?? "python3", [script, ...args], cwd);
  const combined = `${result.stdout}\n${result.stderr}`;
  const pattern = criterion.expectStdout ?? criterion.expectOutput;
  const matched = firstMatch(combined, pattern);
  const exitOk = criterion.expectExit === undefined || result.code === criterion.expectExit;
  const patternOk = pattern === undefined ? true : Boolean(matched);
  const met = patternOk && exitOk;
  const evidence = [
    `exit=${result.code}`,
    pattern === undefined
      ? "exit code only"
      : (matched ? matched.trim().slice(0, 110) : `no output matching ${pattern}`),
  ].join(" | ");
  return { met, evidence };
}

/**
 * Score an artifact directory against a rubric.
 *
 * @param rubric   loaded rubric
 * @param artifactDir  a checkout containing the artifact under test
 */
export async function scoreArtifact(rubric, artifactDir) {
  const resolved = path.resolve(artifactDir);
  const tempRoot = await fs.mkdtemp(path.join(os.tmpdir(), "rubric-"));
  try {
    const results = [];
    for (const criterion of rubric.criteria) {
      const { met, evidence } = await evaluate(criterion, resolved, tempRoot);
      results.push({
        id: criterion.id,
        description: criterion.description ?? criterion.id,
        weight: criterion.weight ?? 1,
        met,
        evidence,
      });
    }
    const total = results.reduce((sum, r) => sum + r.weight, 0);
    const scored = results.reduce((sum, r) => sum + (r.met ? r.weight : 0), 0);
    return {
      rubric: rubric.id,
      artifact: resolved,
      scored,
      total,
      criteria: results,
    };
  } finally {
    await fs.rm(tempRoot, { recursive: true, force: true });
  }
}

/** Compare scored artifacts. Deltas only; no verdict. */
export function compareScores(entries) {
  if (!entries.length) throw new Error("nothing to compare");
  const rubricIds = new Set(entries.map((entry) => entry.score.rubric));
  if (rubricIds.size !== 1) throw new Error(`cannot compare different rubrics: ${[...rubricIds].join(", ")}`);
  const criteriaIds = entries[0].score.criteria.map((criterion) => criterion.id);
  return {
    rubric: entries[0].score.rubric,
    conditions: entries.map((entry) => ({
      label: entry.label,
      scored: entry.score.scored,
      total: entry.score.total,
    })),
    criteria: criteriaIds.map((id) => ({
      id,
      byCondition: entries.map((entry) => {
        const found = entry.score.criteria.find((criterion) => criterion.id === id);
        return { label: entry.label, met: Boolean(found?.met), evidence: found?.evidence ?? "" };
      }),
    })),
  };
}

export function renderComparison(comparison) {
  const head = comparison.conditions.map((condition) => condition.label);
  const lines = [`rubric: ${comparison.rubric}`, ""];
  lines.push(["criterion", ...head].join(" | "));
  lines.push(["---", ...head.map(() => "---")].join(" | "));
  for (const criterion of comparison.criteria) {
    lines.push([criterion.id, ...criterion.byCondition.map((cell) => (cell.met ? "met" : "—"))].join(" | "));
  }
  lines.push(["**total**", ...comparison.conditions.map((condition) => `${condition.scored}/${condition.total}`)].join(" | "));
  return lines.join("\n");
}

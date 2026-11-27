import { promises as fs } from "node:fs";
import path from "node:path";

function exists(file) {
  return fs.access(file).then(() => true, () => false);
}

function check(name, passed, detail = "") {
  return { name, passed, detail };
}

export async function runDocsSyncChecks(canonicalRoot, payloadRevision) {
  const checks = [];
  const short = payloadRevision.slice(0, 7);

  const currentDocs = [
    "V5.2-STATUS.md",
    "V5.2-roadmap.md",
    "backlog/README.md",
  ];

  for (const rel of currentDocs) {
    const file = path.join(canonicalRoot, rel);
    const text = await fs.readFile(file, "utf8");
    checks.push(check(`docs reference payload in ${rel}`, text.includes(short), `expected ${short}`));
  }

  const matrix = path.join(canonicalRoot, "evaluation/RELEASE-CAPABILITY-MATRIX.md");
  const matrixText = await fs.readFile(matrix, "utf8");
  checks.push(check("capability matrix exists", true));
  checks.push(check("capability matrix names payload", matrixText.includes(short), `expected ${short}`));
  checks.push(check("capability matrix limits release claim", /experimental release candidate/u.test(matrixText)));

  const c14 = path.join(canonicalRoot, "backlog/candidates/C14-minimally-viable-release.md");
  checks.push(check("C14 exists", await exists(c14)));

  const plan = path.join(canonicalRoot, "evaluation/plans/RELEASE-CANDIDATE-001.md");
  checks.push(check("release plan exists", await exists(plan)));

  return checks;
}

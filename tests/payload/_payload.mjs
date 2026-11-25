import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

/**
 * Resolve the payload under test.
 *
 * Default: the sibling checkout layout (`Projects/C03-clean-payload`), which is how
 * these suites run locally. Override with PAYLOAD_ROOT to point at any checkout:
 *
 *   PAYLOAD_ROOT=/path/to/payload node --test tests/payload/*.test.mjs
 */

const here = path.dirname(fileURLToPath(import.meta.url));
const payloadRoot = process.env.PAYLOAD_ROOT
  ? path.resolve(process.env.PAYLOAD_ROOT)
  : path.resolve(here, "../../../C03-clean-payload");

const load = (relative) => import(pathToFileURL(path.join(payloadRoot, "adapters/pi", relative)).href);

export const payloadPath = payloadRoot;

export const {
  classifyActionScope,
  classifyImplementationInput,
  extractValidationCommands,
  readApprovedScope,
  CHECKPOINT_RELATIVE_PATH,
} = await load("action-scope-policy.mjs");

export const { registerActionScopeGate } = await load("action-scope-gate.mjs");

export const { shellReferencesKnowledge } = await load("knowledge-write-policy.mjs");

export const { classifySensitivePathAccess } = await load("sensitive-path-policy.mjs");

export const { referencesPrivateQueue, describesSystemBehavior } = await load("knowledge-capture-core.mjs");

export const {
  classifyRefresh,
  collectManifest,
  isManagedPath,
  readRevision,
  summarizeRefresh,
  writeRevision,
} = await load("payload-refresh-policy.mjs");

export const PAYLOAD_REFRESH_CLI_URL = pathToFileURL(
  path.join(payloadRoot, "adapters/pi/payload-refresh-cli.mjs"),
);

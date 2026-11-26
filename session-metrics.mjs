#!/usr/bin/env node
/**
 * Session metrics extractor for AgenticLab delivery runs.
 *
 * Produces the CSV shapes established in
 * AgenticLab-v5/evaluation/metrics/ from Pi session JSONL files, plus a
 * gate-block table that the earlier manual aggregation did not capture.
 *
 * Design rules carried over from evaluation/metrics/README.md:
 * - windows are declared before aggregation, never inferred from wall clock;
 * - session roles are separate rows and are never summed together;
 * - isError is a raw tool-level flag, not a test-failure rate;
 * - reported cost is Pi's own per-message figure, not an invoice;
 * - window span includes idle and approval gaps, not active work;
 * - only metadata and usage are read; no prompt or result text is stored,
 *   except a short block reason used to classify a gate refusal.
 *
 * Usage:
 *   node session-metrics.mjs --config run.json [--out ./out]
 *   node session-metrics.mjs --run ID --session path --role delivery_pi --label exp10
 *
 * Config shape:
 *   {
 *     "runId": "ALPHA-DELIVERY-001",
 *     "outDir": "./out",
 *     "sessions": [
 *       { "path": "/abs/session.jsonl", "role": "delivery_pi", "label": "exp10",
 *         "start": "2026-11-24T09:00:00Z", "end": "2026-11-24T12:00:00Z",
 *         "attributionStatus": "run_window", "acceptancePassed": 4,
 *         "acceptanceTotal": 4, "notes": "free text" }
 *     ]
 *   }
 */

import { promises as fs } from "node:fs";
import path from "node:path";

const SESSION_HEADER = [
  "record_type", "run_id", "session_role", "session_label",
  "window_start_utc", "window_end_utc", "window_span_seconds", "window_basis",
  "user_messages", "assistant_messages", "other_message_entries",
  "tool_calls", "tool_results", "tool_result_isError_flags",
  "read_calls", "write_calls", "edit_calls", "bash_calls",
  "web_scrape_calls", "web_tools_calls", "web_browser_calls",
  "input_tokens_uncached", "cache_read_tokens", "cache_write_tokens",
  "output_tokens", "total_tokens", "reported_cost_usd",
  "provider", "model", "thinking_level", "compactions",
  "playwright_commands_in_session", "playwright_nonzero_tool_results",
  "acceptance_tests_passed", "acceptance_tests_total",
  "retrieval_calls", "retrieval_applicable", "retrieval_no_match",
  "retrieval_filtered", "retrieval_unavailable",
  "attribution_status", "notes", "experiment_id",
];

const ROUNDTRIP_HEADER = [
  "run_id", "session_role", "session_label", "tool_name", "paired_calls",
  "roundtrip_sum_seconds", "median_seconds", "p90_seconds", "max_seconds", "definition",
  "experiment_id",
];

const GATE_HEADER = [
  "run_id", "session_role", "session_label", "category", "tool_name",
  "count", "first_seen_utc", "example_reason", "experiment_id",
];

const ROUNDTRIP_DEFINITION = "Assistant toolCall entry timestamp to matching toolResult entry timestamp";

/** Gate refusals are classified by their stable first words, never by full text. */
const GATE_CATEGORIES = [
  { category: "action_scope", pattern: /^action-scope fidelity:/iu },
  { category: "sensitive_path", pattern: /^sensitive environment files are protected/iu },
  { category: "knowledge_shell", pattern: /shell access to agenticlab knowledge/iu },
  { category: "knowledge_write", pattern: /project knowledge (?:change was not approved|confirmation was unavailable)|changes persistent project knowledge/iu },
  { category: "knowledge_capture", pattern: /candidate .* (?:cannot link|describes agenticlab)|triage decision|staging request/iu },
  { category: "subagent_or_other", pattern: /blocked/iu },
];

function parseArgs(argv) {
  const options = { config: null, out: null, run: null, session: null, role: null, label: null, start: null, end: null, experiment: null };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--config") options.config = argv[++index];
    else if (arg === "--out") options.out = argv[++index];
    else if (arg === "--run") options.run = argv[++index];
    else if (arg === "--session") options.session = argv[++index];
    else if (arg === "--role") options.role = argv[++index];
    else if (arg === "--label") options.label = argv[++index];
    else if (arg === "--start") options.start = argv[++index];
    else if (arg === "--end") options.end = argv[++index];
    else if (arg === "--experiment") options.experiment = argv[++index];
  }
  return options;
}

function iso(ms) {
  return ms ? new Date(ms).toISOString().replace(".000Z", "Z") : "";
}

function csvCell(value) {
  const text = value === undefined || value === null ? "" : String(value);
  return /[",\n]/u.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

function csv(header, rows) {
  return [header.join(","), ...rows.map((row) => row.map(csvCell).join(","))].join("\n") + "\n";
}

export function percentile(values, p) {
  if (!values.length) return "";
  const sorted = [...values].sort((a, b) => a - b);
  const index = Math.min(sorted.length - 1, Math.max(0, Math.ceil((p / 100) * sorted.length) - 1));
  return sorted[index].toFixed(3);
}

export function classifyGate(text) {
  const normalized = String(text ?? "").trim();
  for (const entry of GATE_CATEGORIES) {
    if (entry.pattern.test(normalized)) return entry.category;
  }
  return null;
}

function textOf(message) {
  const content = message?.content;
  if (!Array.isArray(content)) return typeof content === "string" ? content : "";
  return content
    .filter((block) => block && block.type === "text" && typeof block.text === "string")
    .map((block) => block.text)
    .join(" ");
}

export async function readEntries(file) {
  const raw = await fs.readFile(file, "utf8");
  const entries = [];
  for (const line of raw.split("\n")) {
    if (!line.trim()) continue;
    try {
      entries.push(JSON.parse(line));
    } catch {
      // A truncated final line is not a reason to lose the whole session.
    }
  }
  return entries;
}

export function aggregate({ runId, experimentId = "", session }) {
  const { entries, windowStart, windowEnd } = session;

  const counts = {
    user: 0, assistant: 0, other: 0,
    toolCalls: 0, toolResults: 0, toolErrors: 0, compactions: 0,
  };
  const byTool = new Map();
  const usage = { input: 0, cacheRead: 0, cacheWrite: 0, output: 0, total: 0, cost: 0 };
  const roundtrips = new Map();
  const gates = new Map();
  let provider = "";
  let model = "";
  let thinking = "";
  const assistantModels = new Map();
  const retrieval = { calls: 0, applicable: 0, noMatch: 0, filtered: 0, unavailable: 0 };
  let testCommands = 0;
  let playwrightCommands = 0;
  let playwrightFailures = 0;

  const pending = new Map(); // toolCallId → { name, timestamp, command }
  const inWindow = (ms) => ms && (!windowStart || ms >= windowStart) && (!windowEnd || ms <= windowEnd);

  for (const entry of entries) {
    if (entry.type === "compaction") counts.compactions += 1;
    const message = entry.message;
    if (!message) continue;
    if (!inWindow(message.timestamp ?? entry.timestamp)) continue;

    if (message.role === "user") counts.user += 1;
    else if (message.role === "assistant") counts.assistant += 1;
    else if (message.role === "toolResult") counts.toolResults += 1;
    else counts.other += 1;

    if (message.role === "assistant") {
      if (message.model) {
        assistantModels.set(message.model, (assistantModels.get(message.model) ?? 0) + 1);
      }
      // Prefer the dominant model in the declared window; a session file can contain
      // earlier turns on a different model, which would otherwise misattribute cost.
      if (!model || (message.model && (assistantModels.get(message.model) ?? 0) > (assistantModels.get(model) ?? 0))) {
        model = message.model ?? model;
        provider = message.provider ?? provider;
        thinking = message.thinkingLevel ?? thinking;
      }
      const messageUsage = message.usage;
      if (messageUsage) {
        usage.input += messageUsage.input ?? 0;
        usage.cacheRead += messageUsage.cacheRead ?? 0;
        usage.cacheWrite += messageUsage.cacheWrite ?? 0;
        usage.output += messageUsage.output ?? 0;
        usage.total += messageUsage.totalTokens ?? 0;
        usage.cost += messageUsage.cost?.total ?? 0;
      }
      for (const block of Array.isArray(message.content) ? message.content : []) {
        if (block?.type !== "toolCall") continue;
        counts.toolCalls += 1;
        const name = block.name ?? "unknown";
        if (name === "readKnowledgeRecords") retrieval.calls += 1;
        byTool.set(name, (byTool.get(name) ?? 0) + 1);
        const command = typeof block.arguments?.command === "string" ? block.arguments.command : "";
        pending.set(block.id, { name, timestamp: message.timestamp, command });
        if (name === "bash" || name === "user_bash") {
          if (/\b(pytest|playwright|jest|vitest|npm\s+test|bun\s+test|node\s+--test|go\s+test|cargo\s+test)\b/iu.test(command)) {
            testCommands += 1;
          }
          if (/playwright/iu.test(command)) playwrightCommands += 1;
        }
      }
    }

    if (message.role === "toolResult") {
      const name = message.toolName ?? "unknown";
      const call = pending.get(message.toolCallId);
      const start = call?.timestamp;
      const end = message.timestamp;
      if (start && end) {
        if (!roundtrips.has(name)) roundtrips.set(name, []);
        roundtrips.get(name).push((end - start) / 1000);
      }
      if (name === "readKnowledgeRecords") {
        // Outcome is the adapter's own status field, not the model's narration.
        const text = textOf(message);
        let status = null;
        try {
          status = JSON.parse(text).status ?? null;
        } catch {
          const match = text.match(/"status"\s*:\s*"([a-z_]+)"/u);
          status = match ? match[1] : null;
        }
        if (status === "ok") retrieval.applicable += 1;
        else if (status === "no_match" || status === "not_found") retrieval.noMatch += 1;
        else if (status === "filtered") retrieval.filtered += 1;
        else if (status && (status === "unavailable" || status === "no_knowledge_store")) retrieval.unavailable += 1;
        else retrieval.unavailable += 1;
      }
      if (message.isError) {
        counts.toolErrors += 1;
        if (call && (name === "bash" || name === "user_bash") && /playwright/iu.test(call.command ?? "")) {
          playwrightFailures += 1;
        }
        const category = classifyGate(textOf(message));
        if (category) {
          if (!gates.has(category)) gates.set(category, { count: 0, first: end, example: "" });
          const gate = gates.get(category);
          gate.count += 1;
          gate.first = Math.min(gate.first, end);
          if (!gate.example) gate.example = textOf(message).trim().split("\n")[0].slice(0, 140);
          gate.tool = name;
        }
      }
    }
  }

  const callCount = (name) => byTool.get(name) ?? 0;
  const startMs = windowStart ?? Math.min(...entries.map((e) => e.message?.timestamp ?? Infinity));
  const endMs = windowEnd ?? Math.max(...entries.map((e) => e.message?.timestamp ?? 0));
  const spanSeconds = Number.isFinite(startMs) && Number.isFinite(endMs)
    ? Math.max(0, Math.round((endMs - startMs) / 1000))
    : "";

  const row = [
    "run", runId, session.role, session.label,
    iso(startMs), iso(endMs), spanSeconds,
    session.windowBasis ?? (windowStart || windowEnd ? "declared window" : "whole session"),
    counts.user, counts.assistant, counts.other,
    counts.toolCalls, counts.toolResults, counts.toolErrors,
    callCount("read"), callCount("write"), callCount("edit"), callCount("bash") + callCount("user_bash"),
    callCount("web_scrape"), callCount("web_search") + callCount("web_fetch"),
    callCount("browser_navigate") + callCount("browser_click"),
    usage.input, usage.cacheRead, usage.cacheWrite, usage.output, usage.total,
    usage.cost ? usage.cost.toFixed(9) : "",
    provider, model, thinking, counts.compactions,
    playwrightCommands, playwrightFailures,
    session.acceptancePassed ?? "", session.acceptanceTotal ?? "",
    retrieval.calls, retrieval.applicable, retrieval.noMatch, retrieval.filtered, retrieval.unavailable,
    session.attributionStatus ?? "run_window",
    [
      session.notes,
      testCommands ? `${testCommands} test-like bash command(s) attempted; not the same as test cases passed` : "",
    ].filter(Boolean).join(" "),
    experimentId,
  ];

  const roundtripRows = [...roundtrips.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([name, deltas]) => [
      runId, session.role, session.label, name, deltas.length,
      deltas.reduce((a, b) => a + b, 0).toFixed(2),
      percentile(deltas, 50), percentile(deltas, 90),
      Math.max(...deltas).toFixed(3), ROUNDTRIP_DEFINITION, experimentId,
    ]);

  const gateRows = [...gates.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([category, gate]) => [
      runId, session.role, session.label, category, gate.tool ?? "", gate.count,
      iso(gate.first), gate.example, experimentId,
    ]);

  return { row, roundtripRows, gateRows };
}

export async function main(argv = process.argv.slice(2)) {
  const options = parseArgs(process.argv.slice(2));
  let config;
  if (options.config) {
    config = JSON.parse(await fs.readFile(options.config, "utf8"));
  } else if (options.session && options.run && options.role) {
    config = {
      runId: options.run,
      sessions: [{ path: options.session, role: options.role, label: options.label ?? "", start: options.start, end: options.end }],
    };
  } else {
    process.stderr.write("error: pass --config <file> or --run/--session/--role\n");
    process.exit(2);
  }

  const runId = config.runId;
  const experimentId = config.experimentId ?? options.experiment ?? "";
  if (!runId) {
    process.stderr.write("error: a runId is required\n");
    process.exit(2);
  }
  const outDir = path.resolve(options.out ?? config.outDir ?? "./out");
  await fs.mkdir(outDir, { recursive: true });

  const sessionRows = [];
  const roundtripRows = [];
  const gateRows = [];

  for (const session of config.sessions ?? []) {
    const entries = await readEntries(path.resolve(session.path));
    const windowStart = session.start ? Date.parse(session.start) : undefined;
    const windowEnd = session.end ? Date.parse(session.end) : undefined;
    const aggregateResult = aggregate({
      runId,
      experimentId,
      session: { ...session, entries, windowStart, windowEnd },
    });
    sessionRows.push(aggregateResult.row);
    roundtripRows.push(...aggregateResult.roundtripRows);
    gateRows.push(...aggregateResult.gateRows);
  }

  const files = {
    sessions: path.join(outDir, `${runId}-session-metrics.csv`),
    roundtrips: path.join(outDir, `${runId}-tool-roundtrips.csv`),
    gates: path.join(outDir, `${runId}-gate-blocks.csv`),
  };

  await fs.writeFile(files.sessions, csv(SESSION_HEADER, sessionRows), "utf8");
  await fs.writeFile(files.roundtrips, csv(ROUNDTRIP_HEADER, roundtripRows), "utf8");
  await fs.writeFile(files.gates, csv(GATE_HEADER, gateRows), "utf8");

  process.stdout.write(`${JSON.stringify({
    runId,
    experimentId,
    sessions: sessionRows.length,
    roundtripTools: roundtripRows.length,
    gateCategories: gateRows.length,
    files,
  }, null, 2)}\n`);
}

const invokedDirectly = process.argv[1] && path.resolve(process.argv[1]) === path.resolve(new URL(import.meta.url).pathname);
if (invokedDirectly) {
  await main();
}

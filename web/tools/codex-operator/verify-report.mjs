import { execFileSync } from "node:child_process";
import { readFile, stat } from "node:fs/promises";
import { resolve } from "node:path";
import process from "node:process";
import { forbiddenToolEvents, validateDecision } from "./protocol.mjs";

const webRoot = resolve(import.meta.dirname, "../..");
const repositoryRoot = resolve(webRoot, "..");
const evidenceRoot = resolve(repositoryRoot, ".harness/evidence/h2/codex-operator");

function expectedRevision() {
  return process.env.H2_REVISION
    ?? execFileSync("git", ["rev-parse", "HEAD"], { cwd: repositoryRoot, encoding: "utf8" }).trim();
}

async function requireNonempty(path) {
  const metadata = await stat(path);
  if (!metadata.isFile() || metadata.size === 0) throw new Error(`missing nonempty evidence file: ${path}`);
}

async function main() {
  const reportPath = resolve(evidenceRoot, "report.json");
  const report = JSON.parse(await readFile(reportPath, "utf8"));
  if (report.schemaVersion !== 1 || report.scenario !== "h2-codex-vision-route-v1") {
    throw new Error("unexpected Codex operator evidence schema or scenario");
  }
  if (report.revision !== expectedRevision()) throw new Error("Codex operator evidence revision does not match the tested revision");
  if (report.operator !== "codex-cli" || report.visionFirst !== true || report.passed !== true) {
    throw new Error("Codex operator report does not prove a passing vision-first Codex run");
  }
  if (!Array.isArray(report.trace) || report.trace.length !== report.steps || report.trace.length < 1) {
    throw new Error("Codex operator report has an invalid action trace");
  }
  for (const [index, record] of report.trace.entries()) {
    if (record.step !== index) throw new Error(`Codex operator trace is not contiguous at step ${index}`);
    validateDecision(record.decision);
    const prefix = `step-${String(index).padStart(3, "0")}`;
    if (record.screenshot !== `${prefix}-observation.png`) throw new Error(`unexpected screenshot identity at step ${index}`);
    await requireNonempty(resolve(evidenceRoot, record.screenshot));
    await requireNonempty(resolve(evidenceRoot, `${prefix}-decision.json`));
    await requireNonempty(resolve(evidenceRoot, `${prefix}-result.json`));
    const events = await readFile(resolve(evidenceRoot, `${prefix}-codex.jsonl`), "utf8");
    const forbidden = forbiddenToolEvents(events);
    if (forbidden.length > 0) throw new Error(`Codex used forbidden tools at step ${index}: ${forbidden.join(", ")}`);
  }
  const finalEvaluation = report.finalEvaluation;
  if (
    finalEvaluation?.passed !== true
    || finalEvaluation.completionVisible !== true
    || finalEvaluation.state?.checkpoint !== "complete"
    || !Array.isArray(finalEvaluation.runtimeErrors)
    || finalEvaluation.runtimeErrors.length !== 0
  ) {
    throw new Error("private evaluator does not prove clean visible route completion");
  }
  await requireNonempty(resolve(evidenceRoot, "route-complete.png"));
  await requireNonempty(resolve(evidenceRoot, "playwright-trace.zip"));
  process.stdout.write(`verified Codex vision-first route evidence (${report.steps} actions)\n`);
}

await main();


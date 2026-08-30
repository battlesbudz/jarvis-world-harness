import { execFileSync, spawn } from "node:child_process";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import process from "node:process";
import { chromium } from "@playwright/test";
import {
  forbiddenToolEvents,
  publicActionSummary,
  threadIdFromEvents,
  validateDecision,
} from "./protocol.mjs";

const webRoot = resolve(import.meta.dirname, "../..");
const repositoryRoot = resolve(webRoot, "..");
const evidenceRoot = resolve(repositoryRoot, ".harness/evidence/h2/codex-operator");
const schemaPath = resolve(import.meta.dirname, "action.schema.json");
const baseUrl = (process.env.H2_BASE_URL ?? "http://127.0.0.1:4173").replace(/\/?$/, "/");
const revision = process.env.H2_REVISION
  ?? execFileSync("git", ["rev-parse", "HEAD"], { cwd: repositoryRoot, encoding: "utf8" }).trim();
const maxSteps = Number.parseInt(process.env.H2_CODEX_MAX_STEPS ?? "18", 10);
const viewport = { width: 800, height: 600 };

if (!Number.isInteger(maxSteps) || maxSteps < 1 || maxSteps > 40) {
  throw new Error("H2_CODEX_MAX_STEPS must be an integer from 1 through 40");
}
if (!process.env.OPENAI_API_KEY) {
  throw new Error("OPENAI_API_KEY is required for the Codex-operated playtest");
}

async function runCommand(command, args, options) {
  return new Promise((accept, reject) => {
    const child = spawn(command, args, { ...options, stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("error", reject);
    child.on("close", (code, signal) => accept({ code, signal, stdout, stderr }));
  });
}

async function waitForServer(url, child) {
  const deadline = Date.now() + 120_000;
  let lastError = "server did not answer";
  while (Date.now() < deadline) {
    if (child?.exitCode !== null) throw new Error(`local Vite server exited before readiness (${child.exitCode})`);
    try {
      const response = await globalThis.fetch(url);
      if (response.ok) return;
      lastError = `HTTP ${response.status}`;
    } catch (error) {
      lastError = error instanceof Error ? error.message : String(error);
    }
    await new Promise((accept) => globalThis.setTimeout(accept, 250));
  }
  throw new Error(`local Vite server was not ready: ${lastError}`);
}

async function stopServer(child) {
  if (!child || child.exitCode !== null) return;
  child.kill("SIGTERM");
  await Promise.race([
    new Promise((accept) => child.once("close", accept)),
    new Promise((accept) => globalThis.setTimeout(accept, 3000)),
  ]);
  if (child.exitCode === null) child.kill("SIGKILL");
}

async function startLocalServerIfNeeded() {
  if (process.env.H2_BASE_URL) return null;
  const child = spawn("npm", ["run", "dev", "--", "--host", "127.0.0.1"], {
    cwd: webRoot,
    detached: false,
    stdio: ["ignore", "pipe", "pipe"],
  });
  let diagnostics = "";
  child.stdout.on("data", (chunk) => { diagnostics += chunk; });
  child.stderr.on("data", (chunk) => { diagnostics += chunk; });
  try {
    await waitForServer(baseUrl, child);
    return child;
  } catch (error) {
    await stopServer(child);
    throw new Error(`${error instanceof Error ? error.message : String(error)}\n${diagnostics.slice(-2000)}`, {
      cause: error,
    });
  }
}

async function invokeCodex({ codexHome, runtimeDirectory, screenshot, actionPath, threadId, prompt }) {
  const common = [
    "--json",
    "--image", screenshot,
    "--output-schema", schemaPath,
    "--output-last-message", actionPath,
    "--ignore-user-config",
    "--ignore-rules",
    "--skip-git-repo-check",
  ];
  const args = threadId
    ? ["exec", "resume", ...common, threadId, prompt]
    : ["exec", ...common, "--sandbox", "read-only", "-C", runtimeDirectory, prompt];
  if (process.env.H2_CODEX_MODEL) args.splice(2, 0, "--model", process.env.H2_CODEX_MODEL);
  const codexBin = process.env.H2_CODEX_BIN ?? resolve(webRoot, "node_modules/.bin/codex");
  const result = await runCommand(codexBin, args, {
    cwd: runtimeDirectory,
    env: {
      PATH: process.env.PATH,
      HOME: process.env.HOME,
      OPENAI_API_KEY: process.env.OPENAI_API_KEY,
      CODEX_HOME: codexHome,
      NO_COLOR: "1",
    },
  });
  if (result.code !== 0) {
    throw new Error(`Codex operator turn failed (${result.code ?? result.signal}): ${result.stderr.slice(-2000)}`);
  }
  const forbidden = forbiddenToolEvents(result.stdout);
  return { ...result, forbidden, threadId: threadId ?? threadIdFromEvents(result.stdout) };
}

function operatorPrompt({ step, previousAction, finishRejected }) {
  const continuation = step === 0
    ? "This is the initial view from spawn."
    : `This is the new view after your previous action: ${previousAction}.`;
  return `You are Codex operating a Babylon.js game from vision alone.

Goal: guide the blue-cloaked Bio from spawn to the glowing blue village gate. Follow visible road and lantern cues, navigate around blocking walls, and stop only when the page visibly announces route completion.

${continuation}${finishRejected ? " Your previous finish claim was not accepted because the visible objective was not complete." : ""}

You may choose exactly one bounded action. KeyW moves forward relative to the camera, KeyS backward, KeyA left, and KeyD right. drag_camera uses normalized page coordinates from 0 to 1. tap uses endX/endY. wait pauses. finish is only appropriate when completion is visibly confirmed.

Use only the attached screenshot. Do not use the terminal, filesystem, source code, web, hidden coordinates, DOM, accessibility tree, or any tool. Return the required structured decision immediately. Keep rationale limited to what is visible.`;
}

async function applyDecision(page, decision) {
  if (decision.action === "hold_key") {
    await page.keyboard.down(decision.key);
    try {
      await page.waitForTimeout(decision.durationMs);
    } finally {
      await page.keyboard.up(decision.key);
    }
  } else if (decision.action === "wait") {
    await page.waitForTimeout(decision.durationMs);
  } else if (decision.action === "drag_camera") {
    const size = page.viewportSize();
    if (!size) throw new Error("operator page has no viewport");
    await page.mouse.move(decision.startX * size.width, decision.startY * size.height);
    await page.mouse.down();
    await page.mouse.move(decision.endX * size.width, decision.endY * size.height, { steps: 8 });
    await page.mouse.up();
  } else if (decision.action === "tap") {
    const size = page.viewportSize();
    if (!size) throw new Error("operator page has no viewport");
    await page.mouse.click(decision.endX * size.width, decision.endY * size.height);
  }
  await page.waitForTimeout(300);
}

async function privateEvaluation(page, runtimeErrors) {
  return page.evaluate((capturedErrors) => {
    const state = globalThis.__JARVIS_H2__?.snapshot();
    const completion = globalThis.document.querySelector("#completion");
    return {
      passed: Boolean(state?.checkpoint === "complete" && completion && !completion.hidden),
      completionVisible: Boolean(completion && !completion.hidden),
      runtimeErrors: [...capturedErrors, ...(state?.runtimeErrors ?? [])],
      state,
    };
  }, runtimeErrors);
}

async function main() {
  await rm(evidenceRoot, { recursive: true, force: true });
  await mkdir(evidenceRoot, { recursive: true });
  const runtimeDirectory = await mkdtemp(resolve(tmpdir(), "jwh-codex-operator-runtime-"));
  const codexHome = await mkdtemp(resolve(tmpdir(), "jwh-codex-operator-home-"));
  let localServer = null;
  let browser = null;
  let context = null;
  let page = null;
  const runtimeErrors = [];
  const trace = [];
  let threadId = null;
  let previousAction = "none";
  let finishRejected = false;

  try {
    localServer = await startLocalServerIfNeeded();
    browser = await chromium.launch({ headless: true });
    context = await browser.newContext({ viewport, deviceScaleFactor: 1 });
    page = await context.newPage();
    page.on("pageerror", (error) => runtimeErrors.push(error.message));
    page.on("console", (message) => {
      if (message.type() === "error") runtimeErrors.push(`console: ${message.text()}`);
    });
    await context.tracing.start({ screenshots: true, snapshots: true });
    // Load the ordinary player URL. The `test` query parameter intentionally exposes
    // coordinates/checkpoint diagnostics and would invalidate vision-first evidence.
    await page.goto(baseUrl, { waitUntil: "load" });
    await page.waitForFunction(() => globalThis.__JARVIS_H2__?.snapshot().ready === true);

    for (let step = 0; step < maxSteps; step += 1) {
      const prefix = `step-${String(step).padStart(3, "0")}`;
      const screenshot = resolve(evidenceRoot, `${prefix}-observation.png`);
      const actionPath = resolve(evidenceRoot, `${prefix}-decision.json`);
      const eventsPath = resolve(evidenceRoot, `${prefix}-codex.jsonl`);
      await page.screenshot({ path: screenshot });
      const turn = await invokeCodex({
        codexHome,
        runtimeDirectory,
        screenshot,
        actionPath,
        threadId,
        prompt: operatorPrompt({ step, previousAction, finishRejected }),
      });
      await writeFile(eventsPath, turn.stdout);
      if (turn.forbidden.length > 0) {
        throw new Error(`vision-first Codex operator attempted forbidden tools: ${[...new Set(turn.forbidden)].join(", ")}`);
      }
      if (!turn.threadId) throw new Error("Codex operator did not emit a persistent thread id");
      threadId = turn.threadId;
      const decision = validateDecision(JSON.parse(await readFile(actionPath, "utf8")));
      await applyDecision(page, decision);
      const evaluation = await privateEvaluation(page, runtimeErrors);
      const record = {
        step,
        screenshot: `${prefix}-observation.png`,
        decision,
        evaluation,
      };
      trace.push(record);
      await writeFile(resolve(evidenceRoot, `${prefix}-result.json`), `${JSON.stringify(record, null, 2)}\n`);
      previousAction = publicActionSummary(decision);
      finishRejected = decision.action === "finish" && !evaluation.passed;
      if (evaluation.passed) {
        await page.screenshot({ path: resolve(evidenceRoot, "route-complete.png") });
        const report = {
          schemaVersion: 1,
          scenario: "h2-codex-vision-route-v1",
          revision,
          baseUrl,
          operator: "codex-cli",
          visionFirst: true,
          forbiddenOperatorInputs: ["game snapshot", "coordinates", "checkpoint", "DOM", "source code", "terminal", "web"],
          viewport,
          passed: true,
          steps: trace.length,
          finalEvaluation: evaluation,
          trace,
        };
        await writeFile(resolve(evidenceRoot, "report.json"), `${JSON.stringify(report, null, 2)}\n`);
        process.stdout.write(`Codex visually completed the H2 route in ${trace.length} actions.\n`);
        return;
      }
    }
    const finalEvaluation = await privateEvaluation(page, runtimeErrors);
    await writeFile(resolve(evidenceRoot, "report.json"), `${JSON.stringify({
      schemaVersion: 1,
      scenario: "h2-codex-vision-route-v1",
      revision,
      baseUrl,
      operator: "codex-cli",
      visionFirst: true,
      viewport,
      passed: false,
      steps: trace.length,
      finalEvaluation,
      trace,
    }, null, 2)}\n`);
    throw new Error(`Codex did not complete the visible route within ${maxSteps} actions`);
  } catch (error) {
    const finalEvaluation = page ? await privateEvaluation(page, runtimeErrors).catch(() => null) : null;
    await writeFile(resolve(evidenceRoot, "report.json"), `${JSON.stringify({
      schemaVersion: 1,
      scenario: "h2-codex-vision-route-v1",
      revision,
      baseUrl,
      operator: "codex-cli",
      visionFirst: true,
      viewport,
      passed: false,
      steps: trace.length,
      failure: error instanceof Error ? error.message : String(error),
      finalEvaluation,
      trace,
    }, null, 2)}\n`);
    throw error;
  } finally {
    await mkdir(dirname(resolve(evidenceRoot, "playwright-trace.zip")), { recursive: true });
    await context?.tracing.stop({ path: resolve(evidenceRoot, "playwright-trace.zip") }).catch(() => {});
    await context?.close().catch(() => {});
    await browser?.close().catch(() => {});
    await rm(runtimeDirectory, { recursive: true, force: true });
    await rm(codexHome, { recursive: true, force: true });
    await stopServer(localServer);
  }
}

await main();

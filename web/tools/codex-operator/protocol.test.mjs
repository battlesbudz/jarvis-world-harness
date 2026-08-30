import assert from "node:assert/strict";
import test from "node:test";
import { URL } from "node:url";
import {
  forbiddenToolEvents,
  publicActionSummary,
  threadIdFromEvents,
  validateDecision,
} from "./protocol.mjs";

const emptyCoordinates = { startX: null, startY: null, endX: null, endY: null };

test("accepts bounded movement and summarizes only the public action", () => {
  const decision = validateDecision({
    rationale: "The road continues ahead.",
    action: "hold_key",
    key: "KeyW",
    durationMs: 1200,
    ...emptyCoordinates,
  });
  assert.equal(publicActionSummary(decision), "hold_key KeyW for 1200ms");
});

test("rejects actions that could bypass bounded browser input", () => {
  assert.throws(() => validateDecision({
    rationale: "Move a long way.",
    action: "hold_key",
    key: "KeyW",
    durationMs: 50_000,
    ...emptyCoordinates,
  }), /100\.\.4000/);
  assert.throws(() => validateDecision({
    rationale: "Click outside the page.",
    action: "tap",
    key: null,
    durationMs: null,
    startX: null,
    startY: null,
    endX: 2,
    endY: 0.5,
  }), /normalized/);
});

test("extracts the persisted Codex thread and rejects hidden tool use", () => {
  const events = [
    JSON.stringify({ type: "thread.started", thread_id: "thread-123" }),
    JSON.stringify({ type: "item.completed", item: { type: "reasoning" } }),
    JSON.stringify({ type: "item.completed", item: { type: "command_execution" } }),
  ].join("\n");
  assert.equal(threadIdFromEvents(events), "thread-123");
  assert.deepEqual(forbiddenToolEvents(events), ["command_execution"]);
});

test("operator runner never enables the visible test diagnostics query", async () => {
  const runner = await import("node:fs/promises").then(({ readFile }) => readFile(new URL("./run.mjs", import.meta.url), "utf8"));
  assert.doesNotMatch(runner, /page\.goto\(`\$\{baseUrl\}\?test=1`/);
  assert.match(runner, /page\.goto\(baseUrl/);
});

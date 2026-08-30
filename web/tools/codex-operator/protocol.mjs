const ACTIONS = new Set(["hold_key", "drag_camera", "tap", "wait", "finish"]);
const KEYS = new Set(["KeyW", "KeyA", "KeyS", "KeyD"]);

function finiteUnit(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1;
}

export function validateDecision(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Codex decision must be an object");
  }
  if (typeof value.rationale !== "string" || value.rationale.trim().length === 0 || value.rationale.length > 500) {
    throw new Error("Codex decision requires a concise rationale");
  }
  if (!ACTIONS.has(value.action)) {
    throw new Error(`unsupported Codex action: ${String(value.action)}`);
  }
  if (value.action === "hold_key") {
    if (!KEYS.has(value.key)) throw new Error("hold_key requires one movement key");
    if (!Number.isInteger(value.durationMs) || value.durationMs < 100 || value.durationMs > 4000) {
      throw new Error("hold_key duration must be 100..4000 ms");
    }
  }
  if (value.action === "wait") {
    if (!Number.isInteger(value.durationMs) || value.durationMs < 100 || value.durationMs > 4000) {
      throw new Error("wait duration must be 100..4000 ms");
    }
  }
  if (value.action === "drag_camera") {
    for (const field of ["startX", "startY", "endX", "endY"]) {
      if (!finiteUnit(value[field])) throw new Error(`drag_camera requires normalized ${field}`);
    }
  }
  if (value.action === "tap") {
    if (!finiteUnit(value.endX) || !finiteUnit(value.endY)) {
      throw new Error("tap requires normalized endX and endY");
    }
  }
  return Object.freeze({ ...value, rationale: value.rationale.trim() });
}

export function forbiddenToolEvents(jsonLines) {
  const forbidden = [];
  const allowedItemTypes = new Set(["reasoning", "agent_message"]);
  for (const line of jsonLines.split("\n")) {
    if (!line.trim()) continue;
    let event;
    try {
      event = JSON.parse(line);
    } catch {
      continue;
    }
    const itemType = event?.item?.type;
    if (typeof itemType === "string" && !allowedItemTypes.has(itemType)) {
      forbidden.push(itemType);
    }
  }
  return forbidden;
}

export function threadIdFromEvents(jsonLines) {
  for (const line of jsonLines.split("\n")) {
    if (!line.trim()) continue;
    try {
      const event = JSON.parse(line);
      if (event.type === "thread.started" && typeof event.thread_id === "string") return event.thread_id;
    } catch {
      // The caller separately records malformed lines as evidence; they cannot provide a thread id.
    }
  }
  return null;
}

export function publicActionSummary(decision) {
  if (decision.action === "hold_key") return `${decision.action} ${decision.key} for ${decision.durationMs}ms`;
  if (decision.action === "wait") return `${decision.action} for ${decision.durationMs}ms`;
  if (decision.action === "drag_camera") {
    return `${decision.action} (${decision.startX},${decision.startY}) to (${decision.endX},${decision.endY})`;
  }
  if (decision.action === "tap") return `${decision.action} (${decision.endX},${decision.endY})`;
  return decision.action;
}


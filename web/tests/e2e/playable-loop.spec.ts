import { expect, test, type Page, type TestInfo } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import type { GameSnapshot } from "../../src/gameState";

const evidenceDirectory = resolve(process.cwd(), "../.harness/evidence/h2");

async function openGame(page: Page): Promise<void> {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await page.goto("./?test=1", { waitUntil: "load" });
  await expect.poll(() => page.evaluate(() => window.__JARVIS_H2__?.snapshot().ready)).toBe(true);
  await expect.poll(() => page.evaluate(() => {
    const state = window.__JARVIS_H2__?.snapshot();
    if (state?.runtimeErrors.length) throw new Error(state.runtimeErrors.join("\n"));
    return state?.assetsReady;
  }), { timeout: 15_000 }).toBe(true);
  expect(pageErrors).toEqual([]);
}

test("production deployment matches the tested revision", async ({ request }) => {
  test.skip(!process.env.H2_BASE_URL, "deployment metadata exists only in published builds");
  const response = await request.get("./deployment.json", { failOnStatusCode: true });
  expect(await response.json()).toEqual({ revision: projectRevision() });
});

async function snapshot(page: Page): Promise<GameSnapshot> {
  return page.evaluate(() => window.__JARVIS_H2__.snapshot());
}

async function hold(page: Page, key: string, milliseconds: number): Promise<void> {
  await page.keyboard.down(key);
  await page.waitForTimeout(milliseconds);
  await page.keyboard.up(key);
}

async function holdUntil(page: Page, key: string, condition: (state: GameSnapshot) => boolean): Promise<void> {
  await page.keyboard.down(key);
  try {
    await expect.poll(async () => condition(await snapshot(page)), { timeout: 10_000, intervals: [50] }).toBe(true);
  } finally {
    await page.keyboard.up(key);
  }
}

async function centerOnVillageRoad(page: Page): Promise<void> {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    const state = await snapshot(page);
    if (Math.abs(state.position.x) <= 1.5) return;
    await hold(page, state.position.x > 0 ? "KeyA" : "KeyD", 150);
  }
  throw new Error(`could not center on village road: ${JSON.stringify(await snapshot(page))}`);
}

function navigationKey(state: GameSnapshot, target: { x: number; z: number }): string {
  const forward = { x: Math.sin(state.yaw), z: Math.cos(state.yaw) };
  const right = { x: Math.cos(state.yaw), z: -Math.sin(state.yaw) };
  const desired = { x: target.x - state.position.x, z: target.z - state.position.z };
  const candidates = [
    { key: "KeyW", x: forward.x, z: forward.z },
    { key: "KeyS", x: -forward.x, z: -forward.z },
    { key: "KeyD", x: right.x, z: right.z },
    { key: "KeyA", x: -right.x, z: -right.z },
  ];
  candidates.sort((left, rightCandidate) => (
    rightCandidate.x * desired.x + rightCandidate.z * desired.z
  ) - (
    left.x * desired.x + left.z * desired.z
  ));
  return candidates[0].key;
}

async function retreatBeyondAttackRange(page: Page): Promise<void> {
  await page.evaluate(async () => {
    const deadline = performance.now() + 15_000;
    const sleep = (milliseconds: number) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));
    const chooseEvasiveKey = (state: GameSnapshot): string => {
      const stride = state.combat.enemyAttack === "area" ? 3.2 : 2.6;
      const forward = { x: Math.sin(state.yaw), z: Math.cos(state.yaw) };
      const right = { x: Math.cos(state.yaw), z: -Math.sin(state.yaw) };
      const candidates = [
        { key: "KeyA", x: state.position.x - right.x * stride, z: state.position.z - right.z * stride },
        { key: "KeyD", x: state.position.x + right.x * stride, z: state.position.z + right.z * stride },
        { key: "KeyS", x: state.position.x - forward.x * stride, z: state.position.z - forward.z * stride },
        { key: "KeyW", x: state.position.x + forward.x * stride, z: state.position.z + forward.z * stride },
      ];
      const safeCandidates = candidates.filter(({ x, z }) => Math.hypot(x, z - 6.2) <= 5);
      const ranked = safeCandidates.length > 0 ? safeCandidates : candidates;
      ranked.sort((left, rightCandidate) => Math.hypot(
        rightCandidate.x - state.combat.enemyPosition.x,
        rightCandidate.z - state.combat.enemyPosition.z,
      ) - Math.hypot(
        left.x - state.combat.enemyPosition.x,
        left.z - state.combat.enemyPosition.z,
      ));
      return ranked[0].key;
    };
    let activeKey: string | null = null;
    try {
      while (performance.now() < deadline) {
        const state = window.__JARVIS_H2__.snapshot();
        const enemyDistance = Math.hypot(
          state.position.x - state.combat.enemyPosition.x,
          state.position.z - state.combat.enemyPosition.z,
        );
        if (state.combat.phase === "victory" || enemyDistance >= 3.1) return;
        activeKey = chooseEvasiveKey(state);
        window.dispatchEvent(new KeyboardEvent("keydown", { code: activeKey, bubbles: true }));
        await sleep(80);
        window.dispatchEvent(new KeyboardEvent("keyup", { code: activeKey, bubbles: true }));
        activeKey = null;
      }
      throw new Error(`could not disengage beyond attack range: ${JSON.stringify(window.__JARVIS_H2__.snapshot())}`);
    } finally {
      if (activeKey) window.dispatchEvent(new KeyboardEvent("keyup", { code: activeKey, bubbles: true }));
    }
  });
}

async function completeRoute(page: Page): Promise<void> {
  const centeringDeadline = Date.now() + 15_000;
  while (Date.now() < centeringDeadline) {
    const state = await snapshot(page);
    if (Math.abs(state.position.x) <= 0.35) break;
    await hold(page, navigationKey(state, { x: 0, z: state.position.z }), 80);
  }
  const centered = await snapshot(page);
  if (Math.abs(centered.position.x) > 0.65) {
    throw new Error(`could not center on opened gate: ${JSON.stringify(centered)}`);
  }
  const traversalDeadline = Date.now() + 20_000;
  while (Date.now() < traversalDeadline) {
    const state = await snapshot(page);
    if (state.checkpoint === "complete") return;
    const beforeGateExit = state.position.z < 12;
    const target = beforeGateExit && Math.abs(state.position.x) > 0.2
      ? { x: 0, z: state.position.z }
      : { x: 0, z: 14 };
    await hold(page, navigationKey(state, target), 80);
    const advanced = await snapshot(page);
    if (advanced.position.z < 12 && Math.abs(advanced.position.x) > 0.65) {
      throw new Error(`left capsule-safe gate corridor: ${JSON.stringify(advanced)}`);
    }
  }
  throw new Error(`could not traverse opened gate: ${JSON.stringify(await snapshot(page))}`);
}

function projectRevision(): string {
  const revision = process.env.H2_REVISION
    ?? execFileSync("git", ["rev-parse", "HEAD"], { cwd: resolve(process.cwd(), ".."), encoding: "utf8" }).trim();
  if (!/^[0-9a-f]{40}$/i.test(revision)) {
    throw new Error(`H2 evidence revision is not a full Git SHA: ${revision}`);
  }
  return revision;
}

async function evidenceEnvironment(page: Page, testInfo: TestInfo) {
  const browser = page.context().browser();
  return {
    project: testInfo.project.name,
    browser: {
      name: browser?.browserType().name() ?? "unknown",
      version: browser?.version() ?? "unknown",
    },
    viewport: page.viewportSize(),
    deviceScaleFactor: await page.evaluate(() => window.devicePixelRatio),
  };
}

test("loads a deterministic rendered village without runtime errors", async ({ page }, testInfo) => {
  await openGame(page);
  const state = await snapshot(page);
  expect(state).toMatchObject({
    ready: true,
    seed: "h2-babylon-foundation-v1",
    checkpoint: "spawn",
    runtimeErrors: [],
  });
  expect(state.position.x).toBeCloseTo(0, 1);
  expect(state.position.z).toBeCloseTo(-12, 1);
  const canvas = page.locator("#game-canvas");
  await expect(canvas).toBeVisible();
  expect(await canvas.evaluate((element) => Boolean((element as HTMLCanvasElement).getContext("webgl2") ?? (element as HTMLCanvasElement).getContext("webgl")))).toBe(true);
  await mkdir(evidenceDirectory, { recursive: true });
  await page.screenshot({ path: resolve(evidenceDirectory, `establishing-${testInfo.project.name}.png`) });
});

test("keyboard movement is physical and the shortcut wall blocks passage", async ({ page }, testInfo) => {
  await openGame(page);
  await holdUntil(page, "KeyW", (state) => state.collisionCount > 0);
  const blocked = await snapshot(page);
  expect(blocked.position.z).toBeGreaterThan(-12);
  expect(blocked.position.z).toBeLessThan(-2.7);
  expect(blocked.collisionCount).toBeGreaterThan(0);
  expect(blocked.runtimeErrors).toEqual([]);
  const screenshot = `blocked-shortcut-${testInfo.project.name}.png`;
  await page.screenshot({ path: resolve(evidenceDirectory, screenshot) });
  await writeFile(
    resolve(evidenceDirectory, `collision-${testInfo.project.name}.json`),
    `${JSON.stringify({
      revision: projectRevision(),
      scenario: H2_COLLISION_SCENARIO,
      screenshot,
      environment: await evidenceEnvironment(page, testInfo),
      state: blocked,
    }, null, 2)}\n`,
  );
});

test("camera drag changes facing and reset restores the exact spawn", async ({ page }) => {
  await openGame(page);
  const canvas = page.locator("#game-canvas");
  const bounds = await canvas.boundingBox();
  if (!bounds) throw new Error("game canvas has no bounds");
  await page.mouse.move(bounds.x + bounds.width * 0.7, bounds.y + bounds.height * 0.5);
  await page.mouse.down();
  await page.mouse.move(bounds.x + bounds.width * 0.82, bounds.y + bounds.height * 0.5, { steps: 4 });
  await page.mouse.up();
  expect((await snapshot(page)).yaw).not.toBe(0);
  await page.keyboard.down("KeyW");
  await page.waitForTimeout(350);
  await page.getByRole("button", { name: "Reset to Bio spawn" }).click();
  const reset = await snapshot(page);
  await page.waitForTimeout(400);
  await page.keyboard.up("KeyW");
  const settled = await snapshot(page);
  expect(reset.resetId).toBe(2);
  expect(reset.position).toEqual({ x: 0, y: 0.9, z: -12 });
  expect(reset.yaw).toBe(0);
  expect(reset.checkpoint).toBe("spawn");
  expect(settled.position).toEqual({ x: 0, y: 0.9, z: -12 });
  expect(settled.runtimeErrors).toEqual([]);
});

test("focus loss clears held movement", async ({ page }) => {
  await openGame(page);
  await page.keyboard.down("KeyD");
  await page.waitForTimeout(300);
  await page.evaluate(() => window.dispatchEvent(new Event("blur")));
  await expect(page.locator("#pause-overlay")).toBeVisible();
  const stopped = await snapshot(page);
  await page.waitForTimeout(400);
  await page.keyboard.up("KeyD");
  const settled = await snapshot(page);
  expect(Math.abs(settled.position.x - stopped.position.x)).toBeLessThan(0.2);
  expect(settled.paused).toBe(true);
  expect(settled.runtimeErrors).toEqual([]);
  await page.getByRole("button", { name: "Resume" }).click();
  expect((await snapshot(page)).paused).toBe(false);
});

test("a back-forward-cache page hide keeps the game playable", async ({ page }) => {
  await openGame(page);
  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pagehide", { persisted: true })));
  await hold(page, "KeyD", 350);
  const state = await snapshot(page);
  expect(state.position.x).toBeGreaterThan(0.5);
  expect(state.runtimeErrors).toEqual([]);
});

test("touch joystick moves the Bio", async ({ page }) => {
  await openGame(page);
  const joystick = page.locator("#joystick");
  const bounds = await joystick.boundingBox();
  if (!bounds) throw new Error("movement joystick has no bounds");
  const centerX = bounds.x + bounds.width / 2;
  const centerY = bounds.y + bounds.height / 2;
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Input.dispatchTouchEvent", {
    type: "touchStart",
    touchPoints: [{ x: centerX, y: centerY }],
  });
  await cdp.send("Input.dispatchTouchEvent", {
    type: "touchMove",
    touchPoints: [{ x: centerX, y: bounds.y + 4 }],
  });
  await page.waitForTimeout(550);
  await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  const state = await snapshot(page);
  expect(state.position.z).toBeGreaterThan(-10.5);
  expect(state.runtimeErrors).toEqual([]);
});

async function defeatBandit(page: Page, feedbackScreenshot: string): Promise<void> {
  const deadline = Date.now() + 180_000;
  let capturedVisibleFeedback = false;
  let observedEnemyHealth = 100;
  let attacksRemaining = 1;
  while (Date.now() < deadline) {
    const state = await snapshot(page);
    const enemyDamaged = state.combat.enemyHealth < observedEnemyHealth;
    if (!capturedVisibleFeedback && enemyDamaged) {
      const feedback = page.locator("#combat-feedback");
      const feedbackText = await feedback.textContent();
      if (
        await feedback.isVisible()
        && feedbackText === "BLOCKED · HALF DAMAGE"
      ) {
        await page.screenshot({ path: feedbackScreenshot });
        capturedVisibleFeedback = true;
        observedEnemyHealth = state.combat.enemyHealth;
      }
    }
    if (state.combat.phase === "victory") {
      if (!capturedVisibleFeedback) {
        throw new Error("bandit fell without captured visible player-hit feedback");
      }
      return;
    }
    if (state.combat.phase === "defeat") throw new Error("Bio was defeated during deterministic combat path");
    if (state.combat.enemyTelegraph) {
      if (state.combat.enemyAttack === "basic") {
        const healthBeforeGuard = state.combat.playerHealth;
        const resetBeforeGuard = state.resetId;
        await page.keyboard.down("ShiftLeft");
        try {
          await retreatBeyondAttackRange(page);
          await expect.poll(async () => (await snapshot(page)).combat.enemyTelegraph, {
            timeout: 10_000,
            intervals: [30],
          }).toBe(false);
        } finally {
          await page.keyboard.up("ShiftLeft");
        }
        const guarded = await snapshot(page);
        if (guarded.combat.phase === "victory") {
          if (!capturedVisibleFeedback) {
            throw new Error("bandit fell without captured visible player-hit feedback");
          }
          return;
        }
        expect(guarded.resetId).toBe(resetBeforeGuard);
        expect(guarded.combat.phase).toBe("engaged");
        expect(guarded.combat.playerHealth).toBe(healthBeforeGuard);
        attacksRemaining = 2;
        continue;
      }
      const safeDistance = state.combat.enemyAttack === "area" ? 3.1 : 2.5;
      await retreatBeyondAttackRange(page);
      const retreated = await snapshot(page);
      expect(Math.hypot(
        retreated.position.x - retreated.combat.enemyPosition.x,
        retreated.position.z - retreated.combat.enemyPosition.z,
      )).toBeGreaterThanOrEqual(safeDistance);
      await expect
        .poll(async () => (await snapshot(page)).combat.enemyTelegraph, {
          timeout: 10_000,
          intervals: [80],
        })
        .toBe(false);
      attacksRemaining = 2;
      continue;
    }
    const enemyDistance = Math.hypot(
      state.position.x - state.combat.enemyPosition.x,
      state.position.z - state.combat.enemyPosition.z,
    );
    if (
      enemyDistance <= 1.55
      && attacksRemaining > 0
      && state.combat.playerAction === "idle"
      && state.combat.playerStamina >= 50
    ) {
      const staminaBeforeAttack = state.combat.playerStamina;
      const enemyHealthBeforeAttack = state.combat.enemyHealth;
      const comboBeforeAttack = state.combat.comboStep;
      await page.locator("#attack").click();
      await page.waitForFunction(
        ({ staminaBeforeAttack, enemyHealthBeforeAttack, comboBeforeAttack }) => {
          const current = window.__JARVIS_H2__.snapshot();
          return current.combat.playerAction.startsWith("attack-")
            || current.combat.playerStamina <= staminaBeforeAttack - 5
            || current.combat.enemyHealth < enemyHealthBeforeAttack
            || current.combat.comboStep !== comboBeforeAttack;
        },
        { staminaBeforeAttack, enemyHealthBeforeAttack, comboBeforeAttack },
        { timeout: 10_000, polling: "raf" },
      );
      attacksRemaining -= 1;
      await page.keyboard.down("ShiftLeft");
      try {
        await retreatBeyondAttackRange(page);
      } finally {
        await page.keyboard.up("ShiftLeft");
      }
    } else if (
      enemyDistance > 1.55
      && attacksRemaining > 0
      && state.combat.playerAction === "idle"
      && state.combat.playerStamina >= 50
    ) {
      await hold(page, navigationKey(state, state.combat.enemyPosition), 80);
    }
    await page.waitForTimeout(90);
  }
  throw new Error(`bandit did not fall: ${JSON.stringify(await snapshot(page))}`);
}

test("combat controls expose stamina, blocking, dodge, and pause", async ({ page }) => {
  test.setTimeout(120_000);
  await openGame(page);
  await page.locator("#attack").click();
  await page.waitForTimeout(90);
  expect((await snapshot(page)).combat.playerStamina).toBeLessThan(100);
  await expect.poll(async () => (await snapshot(page)).combat.playerAction).toBe("idle");
  await page.keyboard.down("ShiftLeft");
  const staminaBeforeBlock = (await snapshot(page)).combat.playerStamina;
  await page.waitForTimeout(650);
  await page.keyboard.up("ShiftLeft");
  expect((await snapshot(page)).combat.playerStamina).toBeLessThan(staminaBeforeBlock - 2);
  const beforeDodge = await snapshot(page);
  await page.keyboard.press("Space");
  await expect.poll(async () => (await snapshot(page)).combat.playerStamina).toBeLessThan(beforeDodge.combat.playerStamina - 10);
  await expect.poll(async () => (await snapshot(page)).position.z).toBeGreaterThan(beforeDodge.position.z + 0.2);
  await page.getByRole("button", { name: "Pause combat" }).click();
  await expect(page.locator("#pause-overlay")).toBeVisible();
  const paused = await snapshot(page);
  await page.waitForTimeout(350);
  expect(await snapshot(page)).toEqual(paused);
  await page.getByRole("button", { name: "Resume" }).click();
  await page.getByRole("button", { name: "Reset to Bio spawn" }).click();
  await holdUntil(page, "KeyD", (state) => state.position.x >= 6);
  await holdUntil(page, "KeyW", (state) => state.checkpoint === "square");
  await centerOnVillageRoad(page);
  await page.keyboard.down("KeyW");
  const beforeGuardedImpact = await page.evaluate(async () => {
    while (window.__JARVIS_H2__.snapshot().combat.phase !== "engaged") {
      await new Promise(requestAnimationFrame);
    }
    window.dispatchEvent(new KeyboardEvent("keydown", { code: "ShiftLeft", bubbles: true }));
    return window.__JARVIS_H2__.snapshot();
  });
  await page.keyboard.up("KeyW");
  try {
    await page.waitForFunction(() => {
      const state = window.__JARVIS_H2__.snapshot();
      return state.combat.enemyAttack === "basic" && state.combat.enemyTelegraph;
    }, undefined, { timeout: 15_000, polling: "raf" });
    await page.waitForFunction(() => !window.__JARVIS_H2__.snapshot().combat.enemyTelegraph, undefined, {
      timeout: 15_000,
      polling: "raf",
    });
  } finally {
    await page.evaluate(() => {
      window.dispatchEvent(new KeyboardEvent("keyup", { code: "ShiftLeft", bubbles: true }));
    });
  }
  const afterGuardedImpact = await snapshot(page);
  expect(afterGuardedImpact.combat.playerHealth).toBe(beforeGuardedImpact.combat.playerHealth);
  expect(afterGuardedImpact.combat.playerStamina).toBeLessThan(beforeGuardedImpact.combat.playerStamina - 12);
  expect(afterGuardedImpact.combat.phase).toBe("engaged");
});

test("the legitimate route defeats the bandit and unlocks the village gate", async ({ page }, testInfo) => {
  test.setTimeout(240_000);
  await openGame(page);
  await mkdir(evidenceDirectory, { recursive: true });
  await holdUntil(page, "KeyD", (state) => state.position.x >= 6);
  await holdUntil(page, "KeyW", (state) => state.checkpoint === "square");
  await centerOnVillageRoad(page);
  await holdUntil(page, "KeyW", (state) => state.combat.phase === "engaged");
  await holdUntil(page, "KeyW", (state) => state.position.z >= 3.2);
  await defeatBandit(
    page,
    resolve(evidenceDirectory, `combat-victory-${testInfo.project.name}.png`),
  );
  await expect(page.locator("#combat-feedback")).toHaveText("PATH UNLOCKED");
  await expect.poll(async () => (await snapshot(page)).combat.gateOpen).toBe(true);
  const combatState = await snapshot(page);
  expect(combatState.combat.enemyHealth).toBe(0);
  expect(combatState.combat.gateOpen).toBe(true);
  await page.screenshot({ path: resolve(evidenceDirectory, `gate-open-${testInfo.project.name}.png`) });
  await completeRoute(page);
  await expect(page.locator("#completion")).toBeVisible();
  const finalState = await snapshot(page);
  expect(finalState.runtimeErrors).toEqual([]);
  const environment = await evidenceEnvironment(page, testInfo);
  await page.screenshot({ path: resolve(evidenceDirectory, `route-complete-${testInfo.project.name}.png`) });
  await writeFile(
    resolve(evidenceDirectory, `traversal-${testInfo.project.name}.json`),
    `${JSON.stringify({ revision: projectRevision(), scenario: H2_SCENARIO, environment, finalState }, null, 2)}\n`,
  );
  await writeFile(
    resolve(evidenceDirectory, `combat-${testInfo.project.name}.json`),
    `${JSON.stringify({ revision: projectRevision(), scenario: H2_COMBAT_SCENARIO, environment, combatState }, null, 2)}\n`,
  );
});

const H2_SCENARIO = "h2-babylon-greybox-traversal-v1";
const H2_COLLISION_SCENARIO = "h2-babylon-blocked-shortcut-v1";
const H2_COMBAT_SCENARIO = "h2-babylon-bandit-combat-v1";

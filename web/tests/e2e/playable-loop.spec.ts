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

type HoldCondition = "collision" | "x6" | "square" | "engaged" | "z3.2";

async function holdUntil(page: Page, key: string, condition: HoldCondition): Promise<void> {
  await page.keyboard.down(key);
  try {
    await page.waitForFunction((kind) => {
      const state = window.__JARVIS_H2__.snapshot();
      if (kind === "collision") return state.collisionCount > 0;
      if (kind === "x6") return state.position.x >= 6;
      if (kind === "square") return state.checkpoint === "square";
      if (kind === "engaged") return state.combat.phase === "engaged";
      return state.position.z >= 3.2;
    }, condition, { timeout: 90_000, polling: "raf" });
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

async function driveBanditOnBrowserFrames(page: Page, stopOnFirstHit = false): Promise<void> {
  await page.evaluate(async (captureFirstHit) => {
    const deadline = performance.now() + 180_000;
    const initialState = window.__JARVIS_H2__.snapshot();
    const expectedResetId = initialState.resetId;
    const initialEnemyHealth = initialState.combat.enemyHealth;
    let activeMove: string | null = null;
    let blocking = false;
    let attackQueuedAt = 0;
    let dodgedAttack: string | null = null;
    const setMove = (next: string | null): void => {
      if (activeMove === next) return;
      if (activeMove) window.dispatchEvent(new KeyboardEvent("keyup", { code: activeMove, bubbles: true }));
      activeMove = next;
      if (activeMove) window.dispatchEvent(new KeyboardEvent("keydown", { code: activeMove, bubbles: true }));
    };
    const setBlocking = (next: boolean): void => {
      if (blocking === next) return;
      blocking = next;
      window.dispatchEvent(new KeyboardEvent(next ? "keydown" : "keyup", { code: "ShiftLeft", bubbles: true }));
    };
    const rankedKey = (state: GameSnapshot, away: boolean): string => {
      const forward = { x: Math.sin(state.yaw), z: Math.cos(state.yaw) };
      const right = { x: Math.cos(state.yaw), z: -Math.sin(state.yaw) };
      const desired = away
        ? {
            x: state.position.x - state.combat.enemyPosition.x,
            z: state.position.z - state.combat.enemyPosition.z,
          }
        : {
            x: state.combat.enemyPosition.x - state.position.x,
            z: state.combat.enemyPosition.z - state.position.z,
          };
      const candidates = [
        { key: "KeyW", x: forward.x, z: forward.z },
        { key: "KeyS", x: -forward.x, z: -forward.z },
        { key: "KeyD", x: right.x, z: right.z },
        { key: "KeyA", x: -right.x, z: -right.z },
      ];
      const arenaSafeCandidates = away
        ? candidates.filter((candidate) => Math.hypot(
            state.position.x + candidate.x * 3.2,
            state.position.z + candidate.z * 3.2 - 6.2,
          ) <= 5)
        : candidates;
      const rankedCandidates = arenaSafeCandidates.length > 0 ? arenaSafeCandidates : candidates;
      rankedCandidates.sort((left, rightCandidate) => (
        rightCandidate.x * desired.x + rightCandidate.z * desired.z
      ) - (
        left.x * desired.x + left.z * desired.z
      ));
      return rankedCandidates[0].key;
    };
    try {
      while (performance.now() < deadline) {
        const state = window.__JARVIS_H2__.snapshot();
        const feedback = document.querySelector<HTMLElement>("#combat-feedback");
        if (
          captureFirstHit
          && state.combat.enemyHealth < initialEnemyHealth
          && feedback?.textContent === "BLOCKED · HALF DAMAGE"
          && feedback.getClientRects().length > 0
        ) {
          window.dispatchEvent(new KeyboardEvent("keydown", { code: "KeyP", bubbles: true }));
          window.dispatchEvent(new KeyboardEvent("keyup", { code: "KeyP", bubbles: true }));
          while (!window.__JARVIS_H2__.snapshot().paused) await new Promise(requestAnimationFrame);
          const pauseOverlay = document.querySelector<HTMLElement>("#pause-overlay");
          if (pauseOverlay) pauseOverlay.hidden = true;
          return;
        }
        if (state.combat.phase === "victory") return;
        if (state.combat.phase === "defeat" || state.resetId !== expectedResetId) {
          throw new Error(`Bio was defeated during browser-frame combat: ${JSON.stringify(state)}`);
        }
        const distance = Math.hypot(
          state.position.x - state.combat.enemyPosition.x,
          state.position.z - state.combat.enemyPosition.z,
        );
        if (state.combat.enemyTelegraph) {
          const shouldBlock = state.combat.enemyAttack === "basic" && state.combat.playerStamina >= 20;
          setBlocking(shouldBlock);
          if (!shouldBlock && distance < 4.2) {
            setMove(rankedKey(state, true));
            if (state.combat.playerStamina >= 20 && dodgedAttack !== state.combat.enemyAttack) {
              window.dispatchEvent(new KeyboardEvent("keydown", { code: "Space", bubbles: true }));
              window.dispatchEvent(new KeyboardEvent("keyup", { code: "Space", bubbles: true }));
              dodgedAttack = state.combat.enemyAttack;
            }
          } else setMove(null);
        } else {
          setBlocking(false);
          dodgedAttack = null;
          if (distance > 1.5) {
            setMove(rankedKey(state, false));
          } else {
            setMove(null);
            if (
              state.combat.playerAction === "idle"
              && state.combat.playerStamina >= 70
              && performance.now() - attackQueuedAt >= 500
            ) {
              const attackButton = document.querySelector<HTMLButtonElement>("#attack");
              for (let index = 0; index < 3; index += 1) {
                attackButton?.dispatchEvent(new PointerEvent("pointerdown", {
                  bubbles: true,
                  pointerId: index + 1,
                }));
              }
              attackQueuedAt = performance.now();
            }
          }
        }
        await new Promise(requestAnimationFrame);
      }
      throw new Error(`bandit did not fall in browser-frame combat: ${JSON.stringify(window.__JARVIS_H2__.snapshot())}`);
    } finally {
      setMove(null);
      setBlocking(false);
    }
  }, stopOnFirstHit);
}

async function completeRoute(page: Page): Promise<void> {
  const turnTo = async (targetYaw: number): Promise<void> => {
    const canvas = page.locator("#game-canvas");
    const bounds = await canvas.boundingBox();
    if (!bounds) throw new Error("game canvas has no bounds");
    for (let attempt = 0; attempt < 12; attempt += 1) {
      const state = await snapshot(page);
      const difference = Math.atan2(Math.sin(targetYaw - state.yaw), Math.cos(targetYaw - state.yaw));
      if (Math.abs(difference) <= 0.04) return;
      const deltaX = Math.max(-Math.min(120, bounds.width * 0.35), Math.min(Math.min(120, bounds.width * 0.35), difference / 0.005));
      const startX = bounds.x + bounds.width / 2;
      const startY = bounds.y + bounds.height / 2;
      await page.mouse.move(startX, startY);
      await page.mouse.down();
      await page.mouse.move(startX + deltaX, startY, { steps: 2 });
      await page.mouse.up();
    }
    throw new Error(`could not face gate route: ${JSON.stringify(await snapshot(page))}`);
  };

  const initial = await snapshot(page);
  if (initial.combat.phase !== "victory" || !initial.combat.gateOpen) {
    throw new Error(`gate traversal lost its victory state: ${JSON.stringify(initial)}`);
  }
  if (Math.abs(initial.position.x) > 0.18) {
    const centeringFromPositiveX = initial.position.x > 0;
    await turnTo(initial.position.x > 0 ? -Math.PI / 2 : Math.PI / 2);
    await page.keyboard.down("KeyW");
    try {
      await page.waitForFunction((fromPositiveX) => {
        const x = window.__JARVIS_H2__.snapshot().position.x;
        return Math.abs(x) <= 0.18 || (fromPositiveX ? x <= 0 : x >= 0);
      }, centeringFromPositiveX, {
        timeout: 90_000,
        polling: "raf",
      });
    } finally {
      await page.keyboard.up("KeyW");
    }
  }
  const centered = await snapshot(page);
  await turnTo(Math.atan2(-centered.position.x, 14 - centered.position.z));
  await page.keyboard.down("KeyW");
  try {
    await page.waitForFunction(() => window.__JARVIS_H2__.snapshot().checkpoint === "complete", null, {
      timeout: 120_000,
      polling: "raf",
    });
  } finally {
    await page.keyboard.up("KeyW");
  }
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
  await holdUntil(page, "KeyW", "collision");
  const blocked = await snapshot(page);
  expect(blocked.position.z).toBeGreaterThan(-12);
  expect(blocked.position.z).toBeLessThan(-2);
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
  expect(reset.combat.playerFacingYaw).toBe(0);
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
  await driveBanditOnBrowserFrames(page, true);
  expect((await snapshot(page)).paused).toBe(true);
  const feedback = page.locator("#combat-feedback");
  await expect(feedback).toBeVisible();
  await expect(feedback).toHaveText("BLOCKED · HALF DAMAGE");
  await page.screenshot({ path: feedbackScreenshot });
  await page.keyboard.press("KeyP");
  await expect.poll(async () => (await snapshot(page)).paused).toBe(false);
  await driveBanditOnBrowserFrames(page);
}

test("combat controls expose stamina, blocking, dodge, and pause", async ({ page }) => {
  test.setTimeout(240_000);
  await openGame(page);
  await page.locator("#attack").dispatchEvent("pointerdown", { pointerId: 1 });
  await expect.poll(async () => (await snapshot(page)).combat.playerStamina, { timeout: 15_000 }).toBeLessThan(100);
  await expect.poll(async () => (await snapshot(page)).combat.playerAction, { timeout: 15_000 }).toBe("idle");
  await page.keyboard.down("ShiftLeft");
  const staminaBeforeBlock = (await snapshot(page)).combat.playerStamina;
  try {
    await expect.poll(async () => (await snapshot(page)).combat.playerStamina, { timeout: 15_000 })
      .toBeLessThan(staminaBeforeBlock - 2);
  } finally {
    await page.keyboard.up("ShiftLeft");
  }
  const beforeDodge = await snapshot(page);
  const dodge = await page.evaluate(async (initial) => {
    window.dispatchEvent(new KeyboardEvent("keydown", { code: "KeyW", bubbles: true }));
    window.dispatchEvent(new KeyboardEvent("keydown", { code: "Space", bubbles: true }));
    window.dispatchEvent(new KeyboardEvent("keyup", { code: "Space", bubbles: true }));
    let minimumStamina = initial.combat.playerStamina;
    let sawDodge = false;
    let queuedSecondDodge = false;
    const deadline = performance.now() + 15_000;
    try {
      while (performance.now() < deadline) {
        await new Promise(requestAnimationFrame);
        const current = window.__JARVIS_H2__.snapshot();
        minimumStamina = Math.min(minimumStamina, current.combat.playerStamina);
        sawDodge ||= current.combat.playerAction === "dodge";
        if (current.combat.playerAction === "dodge" && !queuedSecondDodge) {
          window.dispatchEvent(new KeyboardEvent("keydown", { code: "Space", bubbles: true }));
          window.dispatchEvent(new KeyboardEvent("keyup", { code: "Space", bubbles: true }));
          queuedSecondDodge = true;
          window.dispatchEvent(new KeyboardEvent("keyup", { code: "KeyW", bubbles: true }));
        }
        const distance = Math.hypot(
          current.position.x - initial.position.x,
          current.position.z - initial.position.z,
        );
        if (sawDodge && queuedSecondDodge && distance > 5.4 && current.combat.playerAction === "idle") {
          return { current, minimumStamina };
        }
      }
      throw new Error(`dodge did not complete: ${JSON.stringify(window.__JARVIS_H2__.snapshot())}`);
    } finally {
      window.dispatchEvent(new KeyboardEvent("keyup", { code: "KeyW", bubbles: true }));
    }
  }, beforeDodge);
  expect(dodge.minimumStamina).toBeLessThan(beforeDodge.combat.playerStamina - 10);
  const dodgeDistance = Math.hypot(
    dodge.current.position.x - beforeDodge.position.x,
    dodge.current.position.z - beforeDodge.position.z,
  );
  expect(dodgeDistance).toBeGreaterThan(5.4);
  expect(dodgeDistance).toBeLessThan(6.3);
  await page.getByRole("button", { name: "Pause combat" }).click();
  await expect(page.locator("#pause-overlay")).toBeVisible();
  const paused = await snapshot(page);
  await page.waitForTimeout(350);
  expect(await snapshot(page)).toEqual(paused);
  await page.getByRole("button", { name: "Resume" }).click();
  await page.getByRole("button", { name: "Reset to Bio spawn" }).click();
  await holdUntil(page, "KeyD", "x6");
  await holdUntil(page, "KeyW", "square");
  await centerOnVillageRoad(page);
  await page.keyboard.down("KeyW");
  await page.evaluate(async () => {
    while (window.__JARVIS_H2__.snapshot().combat.phase !== "engaged") {
      await new Promise(requestAnimationFrame);
    }
    window.dispatchEvent(new KeyboardEvent("keydown", { code: "ShiftLeft", bubbles: true }));
  });
  await page.keyboard.up("KeyW");
  try {
    await page.waitForFunction(() => {
      const state = window.__JARVIS_H2__.snapshot();
      return state.combat.enemyAttack === "basic" && state.combat.enemyTelegraph;
    }, undefined, { timeout: 30_000, polling: "raf" });
    const beforeGuardedImpact = await snapshot(page);
    await page.waitForFunction(() => !window.__JARVIS_H2__.snapshot().combat.enemyTelegraph, undefined, {
      timeout: 30_000,
      polling: "raf",
    });
    const afterGuardedImpact = await snapshot(page);
    expect(afterGuardedImpact.combat.playerHealth).toBe(beforeGuardedImpact.combat.playerHealth);
    expect(afterGuardedImpact.combat.playerStamina).toBeLessThan(beforeGuardedImpact.combat.playerStamina - 12);
    expect(afterGuardedImpact.combat.phase).toBe("engaged");
  } finally {
    await page.evaluate(() => {
      window.dispatchEvent(new KeyboardEvent("keyup", { code: "ShiftLeft", bubbles: true }));
    });
  }
});

test("rapid taps preserve the readable three-strike combo", async ({ page }) => {
  await openGame(page);
  await page.locator("#attack").evaluate((button) => {
    for (let index = 0; index < 3; index += 1) {
      button.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, pointerId: index + 1 }));
    }
  });
  await expect.poll(async () => (await snapshot(page)).combat.playerAction, { timeout: 4_000 }).toBe("attack-3");
  await expect.poll(async () => (await snapshot(page)).combat.comboStep).toBe(0);
  expect((await snapshot(page)).runtimeErrors).toEqual([]);
});

test("target lock keeps the hero and camera facing the bandit", async ({ page }) => {
  test.setTimeout(240_000);
  await openGame(page);
  await holdUntil(page, "KeyD", "x6");
  await holdUntil(page, "KeyW", "square");
  await centerOnVillageRoad(page);
  await holdUntil(page, "KeyW", "engaged");
  await page.keyboard.down("ShiftLeft");
  try {
    const canvas = page.locator("#game-canvas");
    const bounds = await canvas.boundingBox();
    if (!bounds) throw new Error("game canvas has no bounds");
    await page.mouse.move(bounds.x + bounds.width * 0.55, bounds.y + bounds.height * 0.5);
    await page.mouse.down();
    await page.mouse.move(bounds.x + bounds.width * 0.8, bounds.y + bounds.height * 0.5, { steps: 3 });
    await page.mouse.up();
    const lockedFacingError = (state: GameSnapshot): number => state.combat.targetFacingError ?? Number.POSITIVE_INFINITY;
    await expect.poll(async () => lockedFacingError(await snapshot(page)), { timeout: 15_000 }).toBeLessThan(0.02);
    const beforeMove = await snapshot(page);
    const afterMove = await page.evaluate(async () => {
      window.dispatchEvent(new KeyboardEvent("keydown", { code: "KeyD", bubbles: true }));
      try {
        const initial = window.__JARVIS_H2__.snapshot();
        const deadline = performance.now() + 30_000;
        while (performance.now() < deadline) {
          await new Promise(requestAnimationFrame);
          const current = window.__JARVIS_H2__.snapshot();
          if (Math.hypot(
            current.position.x - initial.position.x,
            current.position.z - initial.position.z,
          ) > 0.05) return current;
        }
        throw new Error(`locked strafe did not move Bio: ${JSON.stringify(window.__JARVIS_H2__.snapshot())}`);
      } finally {
        window.dispatchEvent(new KeyboardEvent("keyup", { code: "KeyD", bubbles: true }));
      }
    });
    expect(Math.hypot(
      afterMove.position.x - beforeMove.position.x,
      afterMove.position.z - beforeMove.position.z,
    )).toBeGreaterThan(0.05);
    await expect.poll(async () => lockedFacingError(await snapshot(page)), { timeout: 15_000 }).toBeLessThan(0.02);
    await page.keyboard.down("KeyD");
    await page.keyboard.press("Space");
    await expect.poll(async () => (await snapshot(page)).combat.playerAction, { timeout: 15_000 }).toBe("dodge");
    expect((await snapshot(page)).combat.targetFacingError).toBeNull();
    await page.keyboard.up("KeyD");
    await expect.poll(async () => (await snapshot(page)).combat.playerAction, { timeout: 15_000 }).toBe("idle");
    await expect.poll(async () => lockedFacingError(await snapshot(page)), { timeout: 15_000 }).toBeLessThan(0.02);
    expect((await snapshot(page)).runtimeErrors).toEqual([]);
  } finally {
    await page.keyboard.up("ShiftLeft");
  }
});

test("the legitimate route defeats the bandit and unlocks the village gate", async ({ page }, testInfo) => {
  test.setTimeout(480_000);
  await openGame(page);
  await mkdir(evidenceDirectory, { recursive: true });
  await holdUntil(page, "KeyD", "x6");
  await holdUntil(page, "KeyW", "square");
  await centerOnVillageRoad(page);
  await holdUntil(page, "KeyW", "engaged");
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

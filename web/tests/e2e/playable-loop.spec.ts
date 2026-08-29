import { expect, test, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import type { GameSnapshot } from "../../src/gameState";

const evidenceDirectory = resolve(process.cwd(), "../.harness/evidence/h2");

async function openGame(page: Page): Promise<void> {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await page.goto("/?test=1", { waitUntil: "load" });
  await expect.poll(() => page.evaluate(() => window.__JARVIS_H2__?.snapshot().ready)).toBe(true);
  expect(pageErrors).toEqual([]);
}

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
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const state = await snapshot(page);
    if (Math.abs(state.position.x) <= 1.5) return;
    await hold(page, state.position.x > 0 ? "KeyA" : "KeyD", 150);
  }
  throw new Error(`could not center on village road: ${JSON.stringify(await snapshot(page))}`);
}

function projectRevision(): string {
  const revision = process.env.H2_REVISION
    ?? execFileSync("git", ["rev-parse", "HEAD"], { cwd: resolve(process.cwd(), ".."), encoding: "utf8" }).trim();
  if (!/^[0-9a-f]{40}$/i.test(revision)) {
    throw new Error(`H2 evidence revision is not a full Git SHA: ${revision}`);
  }
  return revision;
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
  await hold(page, "KeyW", 2_200);
  const blocked = await snapshot(page);
  expect(blocked.position.z).toBeGreaterThan(-12);
  expect(blocked.position.z).toBeLessThan(-2.7);
  expect(blocked.collisionCount).toBeGreaterThan(0);
  await page.screenshot({ path: resolve(evidenceDirectory, `blocked-shortcut-${testInfo.project.name}.png`) });
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
});

test("focus loss clears held movement", async ({ page }) => {
  await openGame(page);
  await page.keyboard.down("KeyD");
  await page.waitForTimeout(300);
  await page.evaluate(() => window.dispatchEvent(new Event("blur")));
  const stopped = await snapshot(page);
  await page.waitForTimeout(400);
  await page.keyboard.up("KeyD");
  const settled = await snapshot(page);
  expect(Math.abs(settled.position.x - stopped.position.x)).toBeLessThan(0.2);
  expect(settled.runtimeErrors).toEqual([]);
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

test("the legitimate route reaches the village destination", async ({ page }, testInfo) => {
  test.setTimeout(45_000);
  await openGame(page);
  await holdUntil(page, "KeyD", (state) => state.position.x >= 6);
  await holdUntil(page, "KeyW", (state) => state.checkpoint === "square");
  await centerOnVillageRoad(page);
  await holdUntil(page, "KeyW", (state) => state.checkpoint === "complete");
  await expect(page.locator("#completion")).toBeVisible();
  const finalState = await snapshot(page);
  expect(finalState.runtimeErrors).toEqual([]);
  const browser = page.context().browser();
  const environment = {
    project: testInfo.project.name,
    browser: {
      name: browser?.browserType().name() ?? "unknown",
      version: browser?.version() ?? "unknown",
    },
    viewport: page.viewportSize(),
    deviceScaleFactor: await page.evaluate(() => window.devicePixelRatio),
  };
  await mkdir(evidenceDirectory, { recursive: true });
  await page.screenshot({ path: resolve(evidenceDirectory, `route-complete-${testInfo.project.name}.png`) });
  await writeFile(
    resolve(evidenceDirectory, `traversal-${testInfo.project.name}.json`),
    `${JSON.stringify({ revision: projectRevision(), scenario: H2_SCENARIO, environment, finalState }, null, 2)}\n`,
  );
});

const H2_SCENARIO = "h2-babylon-greybox-traversal-v1";

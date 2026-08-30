import { describe, expect, it } from "vitest";
import { clamp, nextCheckpoint, normalizeMovement, roundedPosition } from "../src/gameState";

describe("game-state helpers", () => {
  it("clamps camera pitch to the allowed interval", () => {
    expect(clamp(-2, -0.15, 0.65)).toBe(-0.15);
    expect(clamp(2, -0.15, 0.65)).toBe(0.65);
  });

  it("normalizes diagonal movement so it is not faster", () => {
    const movement = normalizeMovement({ forward: 1, right: 1 });
    expect(Math.hypot(movement.forward, movement.right)).toBeCloseTo(1);
  });

  it("requires the valid bend before recognizing the square", () => {
    expect(nextCheckpoint({ x: 0, y: 0.9, z: 4 }, "spawn")).toBe("spawn");
    expect(nextCheckpoint({ x: 6, y: 0.9, z: -4 }, "spawn")).toBe("bend");
    expect(nextCheckpoint({ x: 6, y: 0.9, z: 4 }, "bend")).toBe("square");
  });

  it("recognizes the destination and emits stable positions", () => {
    expect(nextCheckpoint({ x: 0.5, y: 0.9, z: 10.5 }, "spawn")).toBe("spawn");
    expect(nextCheckpoint({ x: 0.5, y: 0.9, z: 10.5 }, "square")).toBe("complete");
    expect(roundedPosition({ x: 1.23456, y: 0.9, z: -2.34567 })).toEqual({
      x: 1.235,
      y: 0.9,
      z: -2.346,
    });
  });
});

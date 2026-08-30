import { describe, expect, it } from "vitest";
import {
  COMBAT,
  banditDodgesThirdHit,
  nextEnemyAttack,
  playerAttackDamage,
  regenerateStamina,
  resolveEnemyHit,
  spendStamina,
} from "../src/combatRules";

describe("combat rules", () => {
  it("supports three combos, five dodges, and an eight-second empty guard", () => {
    expect(COMBAT.attackStaminaCost * 9).toBeCloseTo(100);
    expect(COMBAT.dodgeStaminaCost * 5).toBe(100);
    expect(COMBAT.blockDrainPerSecond * 8).toBe(100);
  });

  it("regenerates a full stamina bar in eight seconds", () => {
    expect(regenerateStamina(0, 8)).toBe(100);
    expect(regenerateStamina(95, 8)).toBe(100);
  });

  it("keeps exhausted actions responsive but severely weaker", () => {
    expect(playerAttackDamage(1, 0, false)).toBe(2.5);
    expect(spendStamina(5, COMBAT.attackStaminaCost)).toBe(0);
  });

  it("lets the bandit take half damage while guarding and alternates third-hit dodges", () => {
    expect(playerAttackDamage(1, 100, true)).toBe(5);
    expect(playerAttackDamage(3, 100, true)).toBe(20);
    expect([0, 1, 2, 3].map(banditDodgesThirdHit)).toEqual([false, true, false, true]);
  });

  it("fully blocks basics, partially blocks heavies, and never blocks area attacks", () => {
    expect(resolveEnemyHit("basic", true, 100)).toEqual({
      damage: 0,
      staminaAfter: 88,
      guardBroken: false,
      blocked: true,
    });
    expect(resolveEnemyHit("heavy", true, 100).damage).toBe(10);
    expect(resolveEnemyHit("area", true, 100).damage).toBe(30);
  });

  it("breaks an exhausted guard and applies severe chip damage", () => {
    const result = resolveEnemyHit("basic", true, 5);
    expect(result.guardBroken).toBe(true);
    expect(result.damage).toBe(17);
    expect(result.staminaAfter).toBe(0);
  });

  it("uses the agreed readable attack cycle", () => {
    expect([0, 1, 2, 3, 4].map(nextEnemyAttack)).toEqual(["basic", "basic", "heavy", "area", "basic"]);
  });
});

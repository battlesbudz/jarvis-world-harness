export type EnemyAttackKind = "basic" | "heavy" | "area";

export interface EnemyHitResult {
  damage: number;
  staminaAfter: number;
  guardBroken: boolean;
  blocked: boolean;
}

export const COMBAT = Object.freeze({
  playerMaxHealth: 100,
  playerMaxStamina: 100,
  enemyMaxHealth: 100,
  staminaRegenPerSecond: 12.5,
  attackStaminaCost: 100 / 9,
  dodgeStaminaCost: 20,
  blockDrainPerSecond: 12.5,
  blockImpactCost: Object.freeze({ basic: 12, heavy: 25, area: 0 }),
  enemyDamage: Object.freeze({ basic: 20, heavy: 40, area: 30 }),
  playerDamage: Object.freeze([10, 10, 20] as const),
  attackDurations: Object.freeze([0.36, 0.4, 0.52] as const),
  attackHitTimes: Object.freeze([0.18, 0.2, 0.28] as const),
  comboWindowSeconds: 0.85,
  dodgeDistance: 3,
  exhaustedDodgeDistance: 0.75,
  dodgeDurationSeconds: 0.36,
  dodgeCooldownSeconds: 0.45,
  enemyDodgeSeconds: 0.36,
  hitReactionSeconds: 0.28,
  guardBreakSeconds: 0.75,
  enemyStaggerSeconds: 0.7,
  enemyRecoverySeconds: 0.38,
  defeatResetSeconds: 1.35,
  enemyAttackCycle: Object.freeze(["basic", "basic", "heavy", "area"] as const),
  enemyTelegraphSeconds: Object.freeze({ basic: 0.58, heavy: 0.92, area: 1.12 }),
});

export function staminaStrength(stamina: number, cost: number): number {
  if (stamina >= cost) return 1;
  if (stamina <= 0) return 0.25;
  return 0.25 + 0.75 * (stamina / cost);
}

export function spendStamina(stamina: number, cost: number): number {
  return Math.max(0, stamina - cost);
}

export function regenerateStamina(stamina: number, deltaSeconds: number, blocking = false): number {
  if (blocking) return stamina;
  return Math.min(COMBAT.playerMaxStamina, stamina + COMBAT.staminaRegenPerSecond * deltaSeconds);
}

export function playerAttackDamage(comboStep: 1 | 2 | 3, staminaBefore: number, banditBlocking: boolean): number {
  const base = COMBAT.playerDamage[comboStep - 1];
  const exhaustedScale = staminaStrength(staminaBefore, COMBAT.attackStaminaCost);
  const guardScale = banditBlocking && comboStep < 3 ? 0.5 : 1;
  return base * exhaustedScale * guardScale;
}

export function banditDodgesThirdHit(completedCombos: number): boolean {
  // The first complete combo is allowed to land; the next is dodged, then alternates.
  return completedCombos % 2 === 1;
}

export function resolveEnemyHit(
  kind: EnemyAttackKind,
  blocking: boolean,
  staminaBefore: number,
): EnemyHitResult {
  const baseDamage = COMBAT.enemyDamage[kind];
  if (!blocking || kind === "area") {
    return { damage: baseDamage, staminaAfter: staminaBefore, guardBroken: false, blocked: false };
  }

  const impactCost = COMBAT.blockImpactCost[kind];
  const enoughStamina = staminaBefore >= impactCost && staminaBefore > 0;
  const staminaAfter = spendStamina(staminaBefore, impactCost);
  if (!enoughStamina) {
    return {
      damage: baseDamage * 0.85,
      staminaAfter,
      guardBroken: true,
      blocked: true,
    };
  }

  return {
    damage: kind === "basic" ? 0 : baseDamage * 0.25,
    staminaAfter,
    guardBroken: staminaAfter === 0,
    blocked: true,
  };
}

export function nextEnemyAttack(index: number): EnemyAttackKind {
  return COMBAT.enemyAttackCycle[index % COMBAT.enemyAttackCycle.length];
}

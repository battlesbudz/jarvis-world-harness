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
  attackDurations: Object.freeze([0.46, 0.54, 0.68] as const),
  attackHitTimes: Object.freeze([0.22, 0.29, 0.39] as const),
  attackHalfAngleRadians: Math.PI * 0.31,
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
  frameCapSeconds: 0.1,
  enemyAttackCycle: Object.freeze(["basic", "basic", "heavy", "area"] as const),
  enemyTelegraphSeconds: Object.freeze({ basic: 0.58, heavy: 0.92, area: 1.12 }),
});

export function cappedAnimationTimeScale(rawDeltaSeconds: number, paused: boolean): number {
  if (paused || rawDeltaSeconds <= 0) return 0;
  return Math.min(rawDeltaSeconds, COMBAT.frameCapSeconds) / rawDeltaSeconds;
}

export function targetWithinAttackArc(
  attackerYaw: number,
  attackerX: number,
  attackerZ: number,
  targetX: number,
  targetZ: number,
  halfAngle = COMBAT.attackHalfAngleRadians,
): boolean {
  const offsetX = targetX - attackerX;
  const offsetZ = targetZ - attackerZ;
  const distance = Math.hypot(offsetX, offsetZ);
  if (distance < 0.0001) return true;
  const facingX = Math.sin(attackerYaw);
  const facingZ = Math.cos(attackerYaw);
  const alignment = (facingX * offsetX + facingZ * offsetZ) / distance;
  return alignment >= Math.cos(halfAngle);
}

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

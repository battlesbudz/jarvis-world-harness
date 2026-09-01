export type RouteCheckpoint = "spawn" | "bend" | "square" | "complete";
export type CombatPhase = "approach" | "engaged" | "victory" | "defeat";
export type CombatAction = "idle" | "attack-1" | "attack-2" | "attack-3" | "block" | "dodge" | "hit" | "guard-broken";

export interface Position3 {
  x: number;
  y: number;
  z: number;
}

export interface GameSnapshot {
  ready: boolean;
  seed: string;
  resetId: number;
  position: Position3;
  yaw: number;
  checkpoint: RouteCheckpoint;
  collisionCount: number;
  paused: boolean;
  assetsReady: boolean;
  combat: {
    phase: CombatPhase;
    targetLocked: boolean;
    playerHealth: number;
    playerStamina: number;
    playerAction: CombatAction;
    playerFacingYaw: number;
    targetFacingError: number | null;
    comboStep: number;
    enemyHealth: number;
    enemyHome: Position3;
    enemyPosition: Position3;
    enemyAttack: "basic" | "heavy" | "area" | null;
    enemyTelegraph: boolean;
    gateOpen: boolean;
  };
  runtimeErrors: string[];
}

export interface MovementInput {
  forward: number;
  right: number;
}

export const SPAWN = Object.freeze({ x: 0, y: 0.9, z: -12 });
// The combat gate is centered at z=10.8; completion must be beyond its far side.
export const DESTINATION = Object.freeze({ x: 0, z: 14 });
export const H2_SEED = "h2-babylon-foundation-v1";

export function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}

export function normalizeMovement(input: MovementInput): MovementInput {
  const length = Math.hypot(input.forward, input.right);
  if (length <= 1) {
    return input;
  }
  return { forward: input.forward / length, right: input.right / length };
}

export function nextCheckpoint(position: Position3, current: RouteCheckpoint): RouteCheckpoint {
  if (current === "square" && Math.hypot(position.x - DESTINATION.x, position.z - DESTINATION.z) <= 2) {
    return "complete";
  }
  if ((current === "bend" || current === "square") && position.z >= 2) {
    return "square";
  }
  if (current === "spawn" && Math.abs(position.x) >= 5.5 && position.z >= -6) {
    return "bend";
  }
  return current;
}

export function roundedPosition(position: Position3): Position3 {
  return {
    x: Number(position.x.toFixed(5)),
    y: Number(position.y.toFixed(5)),
    z: Number(position.z.toFixed(5)),
  };
}

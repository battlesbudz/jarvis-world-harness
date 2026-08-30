import type { AnimationGroup } from "@babylonjs/core/Animations/animationGroup.js";
import { FreeCamera } from "@babylonjs/core/Cameras/freeCamera.js";
import "@babylonjs/core/Collisions/collisionCoordinator.js";
import { Engine } from "@babylonjs/core/Engines/engine.js";
import { DirectionalLight } from "@babylonjs/core/Lights/directionalLight.js";
import { HemisphericLight } from "@babylonjs/core/Lights/hemisphericLight.js";
import { ImportMeshAsync } from "@babylonjs/core/Loading/sceneLoader.js";
import { StandardMaterial } from "@babylonjs/core/Materials/standardMaterial.js";
import { Color3, Color4 } from "@babylonjs/core/Maths/math.color.js";
import { Matrix, Vector3 } from "@babylonjs/core/Maths/math.vector.js";
import type { AbstractMesh } from "@babylonjs/core/Meshes/abstractMesh.js";
import { CreateBox } from "@babylonjs/core/Meshes/Builders/boxBuilder.pure.js";
import { CreateCapsule } from "@babylonjs/core/Meshes/Builders/capsuleBuilder.pure.js";
import { CreateCylinder } from "@babylonjs/core/Meshes/Builders/cylinderBuilder.pure.js";
import { CreateGround } from "@babylonjs/core/Meshes/Builders/groundBuilder.pure.js";
import { CreateSphere } from "@babylonjs/core/Meshes/Builders/sphereBuilder.pure.js";
import { CreateTorus } from "@babylonjs/core/Meshes/Builders/torusBuilder.pure.js";
import { Mesh } from "@babylonjs/core/Meshes/mesh.js";
import { Scene } from "@babylonjs/core/scene.js";
import "@babylonjs/loaders/glTF/2.0/glTFLoader.js";
import {
  COMBAT,
  banditDodgesThirdHit,
  nextEnemyAttack,
  playerAttackDamage,
  regenerateStamina,
  resolveEnemyHit,
  spendStamina,
  staminaStrength,
  type EnemyAttackKind,
} from "./combatRules";
import { createInputController, type InputController } from "./input";
import {
  H2_SEED,
  SPAWN,
  clamp,
  nextCheckpoint,
  roundedPosition,
  type CombatAction,
  type CombatPhase,
  type GameSnapshot,
  type RouteCheckpoint,
} from "./gameState";

interface GameElements {
  canvas: HTMLCanvasElement;
  joystick: HTMLElement;
  joystickKnob: HTMLElement;
  reset: HTMLButtonElement;
  pause: HTMLButtonElement;
  resume: HTMLButtonElement;
  attack: HTMLButtonElement;
  block: HTMLButtonElement;
  dodge: HTMLButtonElement;
  status: HTMLElement;
  objective: HTMLElement;
  completion: HTMLOutputElement;
  diagnostics: HTMLOutputElement;
  pauseOverlay: HTMLElement;
  defeatOverlay: HTMLElement;
  healthFill: HTMLElement;
  staminaFill: HTMLElement;
  healthValue: HTMLOutputElement;
  staminaValue: HTMLOutputElement;
  enemyWidget: HTMLElement;
  enemyHealthFill: HTMLElement;
  combatFeedback: HTMLOutputElement;
}

interface Palette {
  grass: StandardMaterial;
  road: StandardMaterial;
  stone: StandardMaterial;
  timber: StandardMaterial;
  roof: StandardMaterial;
  marker: StandardMaterial;
  bio: StandardMaterial;
  enemy: StandardMaterial;
  impact: StandardMaterial;
}

interface AttackState {
  step: 1 | 2 | 3;
  elapsed: number;
  hitApplied: boolean;
  strength: number;
}

interface EnemyAttackState {
  kind: EnemyAttackKind;
  elapsed: number;
  resolved: boolean;
}

interface LoadedActor {
  animations: Map<string, AnimationGroup>;
  current: string | null;
}

const ENEMY_HOME = Object.freeze({ x: 0, y: 0.9, z: 6.5 });
const ARENA_CENTER = new Vector3(0, 0.9, 6.2);
const AGGRO_RADIUS = 5.4;
const LEASH_RADIUS = 6.2;
const PLAYER_RANGE = 2.05;
const ENEMY_RANGE = 2.15;
const BODY_DISTANCE = 0.92;

export class AlbionGame {
  private readonly engine: Engine;
  private readonly scene: Scene;
  private readonly camera: FreeCamera;
  private readonly player: Mesh;
  private readonly enemy: Mesh;
  private readonly gate: Mesh;
  private readonly targetMarker: Mesh;
  private readonly telegraphRing: Mesh;
  private readonly impactFlash: Mesh;
  private readonly input: InputController;
  private yaw = 0;
  private pitch = 0.24;
  private resetId = 1;
  private checkpoint: RouteCheckpoint = "spawn";
  private collisionCount = 0;
  private collisionActive = false;
  private ready = false;
  private assetsReady = false;
  private disposed = false;
  private paused = false;
  private phase: CombatPhase = "approach";
  private targetLocked = false;
  private playerHealth = 100;
  private playerStamina = 100;
  private playerAction: CombatAction = "idle";
  private enemyHealth = 100;
  private attack: AttackState | null = null;
  private enemyAttack: EnemyAttackState | null = null;
  private attackBuffered = false;
  private dodgeBuffered = false;
  private dodgeElapsed = 0;
  private dodgeCooldown = 0;
  private dodgeVelocity = Vector3.Zero();
  private guardBreakElapsed = 0;
  private comboStep = 0;
  private comboWindow = 0;
  private completedCombos = 0;
  private enemyAttackIndex = 0;
  private enemyAttackCooldown = 0.7;
  private enemyStaggerElapsed = 0;
  private defeatElapsed = 0;
  private gateRise = 0;
  private impactElapsed = 0;
  private feedbackElapsed = 0;
  private playerActor: LoadedActor | null = null;
  private enemyActor: LoadedActor | null = null;
  private audioContext: AudioContext | null = null;

  constructor(private readonly elements: GameElements, private readonly runtimeErrors: string[]) {
    this.engine = new Engine(elements.canvas, true, { preserveDrawingBuffer: true, stencil: true, adaptToDeviceRatio: true });
    this.scene = new Scene(this.engine);
    this.scene.clearColor = new Color4(0.055, 0.1, 0.12, 1);
    this.scene.collisionsEnabled = true;
    this.camera = new FreeCamera("bio-camera", new Vector3(0, 4, -18), this.scene);
    this.camera.minZ = 0.1;
    this.camera.fov = 0.9;
    this.camera.inputs.clear();
    const palette = this.createPalette();
    this.createLighting();
    this.gate = this.createVillage(palette);
    this.player = this.createCollider("bio", SPAWN.x, SPAWN.z, palette.bio);
    this.enemy = this.createCollider("bandit", ENEMY_HOME.x, ENEMY_HOME.z, palette.enemy);
    this.targetMarker = CreateSphere("target-lock-marker", { diameter: 0.23 }, this.scene);
    this.targetMarker.material = palette.marker;
    this.targetMarker.isVisible = false;
    this.targetMarker.isPickable = false;
    this.telegraphRing = CreateTorus("enemy-telegraph", { diameter: 3.4, thickness: 0.12, tessellation: 48 }, this.scene);
    this.telegraphRing.rotation.x = Math.PI / 2;
    this.telegraphRing.position.y = 0.08;
    this.telegraphRing.isVisible = false;
    this.impactFlash = CreateSphere("combat-impact", { diameter: 0.5 }, this.scene);
    this.impactFlash.material = palette.impact;
    this.impactFlash.isVisible = false;
    this.input = createInputController(
      elements.canvas,
      elements.joystick,
      elements.joystickKnob,
      { attack: elements.attack, block: elements.block, dodge: elements.dodge, pause: elements.pause },
      (deltaYaw, deltaPitch) => {
        this.yaw += deltaYaw;
        this.pitch = clamp(this.pitch + deltaPitch, -0.15, 0.65);
      },
    );
    elements.reset.addEventListener("click", this.reset);
    elements.resume.addEventListener("click", this.resume);
    window.addEventListener("resize", this.resize);
    window.addEventListener("blur", this.autoPause);
    document.addEventListener("visibilitychange", this.visibilityPause);
    this.updateCamera(0);
    this.updateHud();
    this.engine.runRenderLoop(this.frame);
    void this.loadActors();
  }

  snapshot(): GameSnapshot {
    return {
      ready: this.ready,
      seed: H2_SEED,
      resetId: this.resetId,
      position: roundedPosition(this.player.position),
      yaw: Number(this.yaw.toFixed(4)),
      checkpoint: this.checkpoint,
      collisionCount: this.collisionCount,
      paused: this.paused,
      assetsReady: this.assetsReady,
      combat: {
        phase: this.phase,
        targetLocked: this.targetLocked,
        playerHealth: Number(this.playerHealth.toFixed(2)),
        playerStamina: Number(this.playerStamina.toFixed(2)),
        playerAction: this.playerAction,
        comboStep: this.comboStep,
        enemyHealth: Number(this.enemyHealth.toFixed(2)),
        enemyHome: roundedPosition({ ...ENEMY_HOME }),
        enemyPosition: roundedPosition(this.enemy.position),
        enemyAttack: this.enemyAttack?.kind ?? null,
        enemyTelegraph: Boolean(this.enemyAttack && !this.enemyAttack.resolved),
        gateOpen: this.phase === "victory",
      },
      runtimeErrors: [...this.runtimeErrors],
    };
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.input.dispose();
    this.elements.reset.removeEventListener("click", this.reset);
    this.elements.resume.removeEventListener("click", this.resume);
    window.removeEventListener("resize", this.resize);
    window.removeEventListener("blur", this.autoPause);
    document.removeEventListener("visibilitychange", this.visibilityPause);
    this.engine.stopRenderLoop(this.frame);
    this.scene.dispose();
    this.engine.dispose();
    void this.audioContext?.close();
  }

  private readonly resize = (): void => this.engine.resize();
  private readonly autoPause = (): void => { if (this.ready && this.phase !== "defeat") this.setPaused(true); };
  private readonly visibilityPause = (): void => { if (document.visibilityState === "hidden") this.autoPause(); };
  private readonly resume = (): void => this.setPaused(false);

  private setPaused(value: boolean): void {
    this.paused = value;
    this.input.clear();
    this.elements.pauseOverlay.hidden = !value;
    this.elements.pause.dataset.pressed = String(value);
  }

  private readonly reset = (): void => {
    this.input.clear();
    this.player.position.copyFromFloats(SPAWN.x, SPAWN.y, SPAWN.z);
    this.enemy.position.copyFromFloats(ENEMY_HOME.x, ENEMY_HOME.y, ENEMY_HOME.z);
    this.gate.position.y = 1.35;
    this.gate.checkCollisions = true;
    this.yaw = 0;
    this.pitch = 0.24;
    this.checkpoint = "spawn";
    this.collisionCount = 0;
    this.collisionActive = false;
    this.resetId += 1;
    this.phase = "approach";
    this.targetLocked = false;
    this.playerHealth = 100;
    this.playerStamina = 100;
    this.playerAction = "idle";
    this.enemyHealth = 100;
    this.attack = null;
    this.enemyAttack = null;
    this.attackBuffered = false;
    this.dodgeBuffered = false;
    this.dodgeElapsed = 0;
    this.dodgeCooldown = 0;
    this.guardBreakElapsed = 0;
    this.comboStep = 0;
    this.comboWindow = 0;
    this.completedCombos = 0;
    this.enemyAttackIndex = 0;
    this.enemyAttackCooldown = 0.7;
    this.enemyStaggerElapsed = 0;
    this.defeatElapsed = 0;
    this.gateRise = 0;
    this.telegraphRing.isVisible = false;
    this.targetMarker.isVisible = false;
    this.elements.completion.hidden = true;
    this.elements.defeatOverlay.hidden = true;
    this.elements.status.textContent = "Ready";
    this.elements.objective.textContent = "Follow the lanterns to the village square";
    this.setPaused(false);
    this.playActor(this.playerActor, "Idle", true);
    this.playActor(this.enemyActor, "Idle_Attacking", true);
    this.updateCamera(0);
    this.updateHud();
  };

  private readonly frame = (): void => {
    if (this.disposed) return;
    try {
      const deltaSeconds = Math.min(this.engine.getDeltaTime() / 1000, 0.1);
      if (this.input.consumePause()) this.setPaused(!this.paused);
      if (!this.paused) {
        this.updateCombat(deltaSeconds);
        if (this.phase !== "defeat") this.movePlayer(deltaSeconds);
        this.updateCamera(deltaSeconds);
        this.updateRoute();
        this.updateEffects(deltaSeconds);
      }
      this.updateHud();
      this.scene.render();
      if (!this.ready) {
        this.ready = true;
        this.elements.status.textContent = "Ready";
        this.elements.objective.textContent = "Follow the lanterns to the village square";
        document.body.dataset.gameReady = "true";
      }
      if (!this.elements.diagnostics.hidden) {
        const state = this.snapshot();
        this.elements.diagnostics.textContent = `${state.position.x.toFixed(2)}, ${state.position.z.toFixed(2)} · ${state.checkpoint} · ${state.combat.phase} · HP ${state.combat.playerHealth}/${state.combat.enemyHealth}`;
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.runtimeErrors.push(message);
      this.elements.status.textContent = "Albion stopped safely";
      this.elements.objective.textContent = message;
      this.engine.stopRenderLoop(this.frame);
    }
  };

  private updateCombat(delta: number): void {
    this.playerStamina = regenerateStamina(this.playerStamina, delta);
    this.dodgeCooldown = Math.max(0, this.dodgeCooldown - delta);
    this.comboWindow = Math.max(0, this.comboWindow - delta);
    if (this.comboWindow === 0 && !this.attack) this.comboStep = 0;
    if (this.phase === "defeat") {
      this.defeatElapsed += delta;
      if (this.defeatElapsed >= 1.35) this.reset();
      return;
    }
    const arenaDistance = Vector3.Distance(this.player.position, ARENA_CENTER);
    if (this.phase === "approach" && arenaDistance <= AGGRO_RADIUS) this.engageCombat();
    if (this.phase === "engaged" && arenaDistance > LEASH_RADIUS) {
      this.disengageCombat();
      return;
    }
    if (this.input.consumeAttack()) {
      this.unlockAudio();
      if (!this.input.blocking()) {
        if (this.attack) this.attackBuffered = true;
        else if (this.playerAction === "idle") this.startAttack();
      }
    }
    if (this.input.consumeDodge()) {
      this.unlockAudio();
      if (this.playerAction === "idle" && this.dodgeCooldown === 0) this.startDodge();
      else this.dodgeBuffered = true;
    }
    if (this.guardBreakElapsed > 0) {
      this.guardBreakElapsed = Math.max(0, this.guardBreakElapsed - delta);
      this.playerAction = "guard-broken";
      if (this.guardBreakElapsed === 0) this.playerAction = "idle";
    } else if (this.dodgeElapsed > 0) {
      this.updateDodge(delta);
    } else if (this.attack) {
      this.updatePlayerAttack(delta);
    } else if (this.input.blocking()) {
      this.playerAction = "block";
      this.playerStamina = Math.max(0, this.playerStamina - COMBAT.blockDrainPerSecond * delta);
      this.playActor(this.playerActor, "Idle_Attacking", true);
    } else {
      this.playerAction = "idle";
      if (this.dodgeBuffered && this.dodgeCooldown === 0) {
        this.dodgeBuffered = false;
        this.startDodge();
      } else if (this.attackBuffered) {
        this.attackBuffered = false;
        this.startAttack();
      }
    }
    if (this.phase === "engaged") this.updateEnemy(delta);
    else if (this.phase === "victory") this.openGate(delta);
  }

  private engageCombat(): void {
    this.phase = "engaged";
    this.targetLocked = true;
    this.targetMarker.isVisible = true;
    this.elements.status.textContent = "Bandit engaged";
    this.elements.objective.textContent = "Defeat the sword bandit to open the gate";
    this.showFeedback("COMBAT", "warning");
    this.playTone(185, 0.18, "sawtooth");
  }

  private disengageCombat(): void {
    this.phase = "approach";
    this.targetLocked = false;
    this.targetMarker.isVisible = false;
    this.enemy.position.copyFromFloats(ENEMY_HOME.x, ENEMY_HOME.y, ENEMY_HOME.z);
    this.enemyHealth = 100;
    this.enemyAttack = null;
    this.enemyAttackCooldown = 0.7;
    this.enemyAttackIndex = 0;
    this.telegraphRing.isVisible = false;
    this.completedCombos = 0;
    this.elements.status.textContent = "Bandit disengaged";
    this.elements.objective.textContent = "The bandit returned home and recovered";
    this.playActor(this.enemyActor, "Idle_Attacking", true);
  }

  private startAttack(): void {
    const step = (this.comboStep >= 1 && this.comboStep < 3 && this.comboWindow > 0 ? this.comboStep + 1 : 1) as 1 | 2 | 3;
    const strength = staminaStrength(this.playerStamina, COMBAT.attackStaminaCost);
    this.playerStamina = spendStamina(this.playerStamina, COMBAT.attackStaminaCost);
    this.attack = { step, elapsed: 0, hitApplied: false, strength };
    this.playerAction = `attack-${step}`;
    this.playActor(this.playerActor, step === 2 ? "Attack2" : "Attack", false);
    this.playTone(220 + step * 35, 0.08, "triangle");
  }

  private updatePlayerAttack(delta: number): void {
    const attack = this.attack;
    if (!attack) return;
    attack.elapsed += delta;
    if (!attack.hitApplied && attack.elapsed >= COMBAT.attackHitTimes[attack.step - 1]) {
      attack.hitApplied = true;
      this.applyPlayerHit(attack);
    }
    if (attack.elapsed < COMBAT.attackDurations[attack.step - 1]) return;
    this.comboStep = attack.step;
    this.comboWindow = COMBAT.comboWindowSeconds;
    if (attack.step === 3) {
      this.completedCombos += 1;
      this.comboStep = 0;
    }
    this.attack = null;
    this.playerAction = "idle";
    if (this.dodgeBuffered && this.dodgeCooldown === 0) {
      this.dodgeBuffered = false;
      this.startDodge();
    } else if (this.attackBuffered) {
      this.attackBuffered = false;
      this.startAttack();
    } else this.playActor(this.playerActor, "Idle_Attacking", true);
  }

  private applyPlayerHit(attack: AttackState): void {
    if (this.phase !== "engaged") return;
    const toward = this.enemy.position.subtract(this.player.position);
    const distance = toward.length();
    if (distance > PLAYER_RANGE && distance <= PLAYER_RANGE + 0.65) {
      this.player.moveWithCollisions(toward.normalize().scale(Math.min(0.42, distance - PLAYER_RANGE + 0.08)));
    }
    if (Vector3.Distance(this.player.position, this.enemy.position) > PLAYER_RANGE) {
      this.showFeedback("MISS", "muted");
      return;
    }
    if (attack.step === 3 && banditDodgesThirdHit(this.completedCombos)) {
      const side = new Vector3(toward.z, 0, -toward.x).normalize();
      this.enemy.position.addInPlace(side.scale(1.35));
      this.playActor(this.enemyActor, "Roll", false);
      this.showFeedback("DODGED", "warning");
      this.playTone(150, 0.1, "sine");
      return;
    }
    const guarded = attack.step < 3;
    const damage = playerAttackDamage(attack.step, attack.strength * COMBAT.attackStaminaCost, guarded);
    this.enemyHealth = Math.max(0, this.enemyHealth - damage);
    this.flashImpact(this.enemy.position.add(new Vector3(0, 1.1, 0)));
    this.damageNumber(Math.round(damage), false);
    this.playTone(95 + attack.step * 18, 0.12, "square");
    if (attack.step === 3) {
      this.enemyStaggerElapsed = COMBAT.enemyStaggerSeconds;
      this.enemyAttack = null;
      this.telegraphRing.isVisible = false;
      this.showFeedback("GUARD BROKEN", "success");
      this.playActor(this.enemyActor, "RecieveHit_2", false);
    } else {
      this.showFeedback("BLOCKED · HALF DAMAGE", "muted");
      this.playActor(this.enemyActor, "Idle_Attacking", true);
    }
    if (this.enemyHealth <= 0) this.winCombat();
  }

  private startDodge(): void {
    const movement = this.input.movement();
    const forward = new Vector3(Math.sin(this.yaw), 0, Math.cos(this.yaw));
    const right = new Vector3(Math.cos(this.yaw), 0, -Math.sin(this.yaw));
    const requested = forward.scale(movement.forward).add(right.scale(movement.right));
    const direction = requested.lengthSquared() > 0.02 ? requested.normalize() : forward;
    const strength = staminaStrength(this.playerStamina, COMBAT.dodgeStaminaCost);
    this.playerStamina = spendStamina(this.playerStamina, COMBAT.dodgeStaminaCost);
    const distance = strength <= 0.25 ? COMBAT.exhaustedDodgeDistance : COMBAT.dodgeDistance * strength;
    this.dodgeVelocity = direction.scale(distance / COMBAT.dodgeDurationSeconds);
    this.dodgeElapsed = COMBAT.dodgeDurationSeconds;
    this.dodgeCooldown = COMBAT.dodgeCooldownSeconds;
    this.playerAction = "dodge";
    this.playActor(this.playerActor, "Roll", false);
    this.playTone(310, 0.1, "sine");
  }

  private updateDodge(delta: number): void {
    this.movePlayerCollider(this.dodgeVelocity.scale(Math.min(delta, this.dodgeElapsed)));
    this.dodgeElapsed = Math.max(0, this.dodgeElapsed - delta);
    if (this.dodgeElapsed === 0) {
      this.playerAction = "idle";
      this.playActor(this.playerActor, "Idle_Attacking", true);
    }
  }

  private updateEnemy(delta: number): void {
    if (this.enemyStaggerElapsed > 0) {
      this.enemyStaggerElapsed = Math.max(0, this.enemyStaggerElapsed - delta);
      return;
    }
    const toward = this.player.position.subtract(this.enemy.position);
    const distance = toward.length();
    this.enemy.rotation.y = Math.atan2(toward.x, toward.z);
    if (distance > ENEMY_RANGE) {
      this.enemyAttack = null;
      this.telegraphRing.isVisible = false;
      this.enemy.position.addInPlace(toward.normalize().scale(Math.min(2.35 * delta, distance - BODY_DISTANCE)));
      this.playActor(this.enemyActor, "Run", true);
      return;
    }
    this.enemyAttackCooldown = Math.max(0, this.enemyAttackCooldown - delta);
    if (!this.enemyAttack && this.enemyAttackCooldown === 0) {
      const kind = nextEnemyAttack(this.enemyAttackIndex);
      this.enemyAttack = { kind, elapsed: 0, resolved: false };
      this.telegraphRing.material = this.scene.getMaterialByName(`telegraph-${kind}`);
      this.telegraphRing.isVisible = true;
      this.telegraphRing.scaling.setAll(kind === "area" ? 1.35 : 0.8);
      this.playActor(this.enemyActor, kind === "heavy" ? "Attack2" : "Attack", false);
      this.showFeedback(kind === "area" ? "AREA ATTACK" : kind === "heavy" ? "HEAVY ATTACK" : "SWORD ATTACK", kind);
      this.playTone(kind === "basic" ? 240 : kind === "heavy" ? 155 : 110, 0.16, "sawtooth");
    }
    const attack = this.enemyAttack;
    if (!attack) return;
    attack.elapsed += delta;
    if (!attack.resolved && attack.elapsed >= COMBAT.enemyTelegraphSeconds[attack.kind]) {
      attack.resolved = true;
      this.applyEnemyHit(attack.kind);
      this.telegraphRing.isVisible = false;
    }
    if (attack.elapsed >= COMBAT.enemyTelegraphSeconds[attack.kind] + 0.38) {
      this.enemyAttackIndex += 1;
      this.enemyAttack = null;
      this.enemyAttackCooldown = 0.58;
      this.playActor(this.enemyActor, "Idle_Attacking", true);
    }
  }

  private applyEnemyHit(kind: EnemyAttackKind): void {
    const range = kind === "area" ? 2.75 : ENEMY_RANGE;
    if (Vector3.Distance(this.player.position, this.enemy.position) > range) {
      this.showFeedback("EVADED", "success");
      return;
    }
    const result = resolveEnemyHit(kind, this.playerAction === "block", this.playerStamina);
    this.playerStamina = result.staminaAfter;
    this.playerHealth = Math.max(0, this.playerHealth - result.damage);
    if (result.damage > 0) {
      this.comboStep = 0;
      this.comboWindow = 0;
      this.flashImpact(this.player.position.add(new Vector3(0, 1.05, 0)));
      this.damageNumber(Math.round(result.damage), true);
      this.playActor(this.playerActor, "RecieveHit", false);
      this.playTone(72, 0.18, "square");
    }
    if (result.guardBroken) {
      this.guardBreakElapsed = COMBAT.guardBreakSeconds;
      this.playerAction = "guard-broken";
      this.showFeedback("GUARD BROKEN", "danger");
    } else if (result.blocked && result.damage === 0) {
      this.showFeedback("BLOCKED", "success");
      this.playTone(420, 0.07, "triangle");
    } else if (result.blocked) this.showFeedback("HEAVY BLOCK · CHIP DAMAGE", "warning");
    else this.showFeedback(`-${Math.round(result.damage)} HEALTH`, "danger");
    if (this.playerHealth <= 0) this.loseCombat();
  }

  private winCombat(): void {
    this.phase = "victory";
    this.targetLocked = false;
    this.targetMarker.isVisible = false;
    this.enemyAttack = null;
    this.telegraphRing.isVisible = false;
    this.playActor(this.enemyActor, "Death", false);
    this.elements.status.textContent = "Bandit defeated";
    this.elements.objective.textContent = "The gate is opening — continue through";
    this.showFeedback("PATH UNLOCKED", "success");
    this.playTone(520, 0.35, "triangle");
  }

  private loseCombat(): void {
    this.phase = "defeat";
    this.targetLocked = false;
    this.targetMarker.isVisible = false;
    this.telegraphRing.isVisible = false;
    this.attack = null;
    this.playerAction = "idle";
    this.defeatElapsed = 0;
    this.elements.defeatOverlay.hidden = false;
    this.elements.status.textContent = "Bio defeated";
    this.elements.objective.textContent = "The route will reset";
    this.playActor(this.playerActor, "Death", false);
    this.playTone(65, 0.5, "sawtooth");
  }

  private openGate(delta: number): void {
    this.gateRise = Math.min(3.4, this.gateRise + 2.4 * delta);
    this.gate.position.y = 1.35 + this.gateRise;
    if (this.gateRise >= 2.7) this.gate.checkCollisions = false;
  }

  private movePlayer(delta: number): void {
    if (this.dodgeElapsed > 0) return;
    const movement = this.input.movement();
    if (movement.forward === 0 && movement.right === 0) {
      this.collisionActive = false;
      return;
    }
    const forward = new Vector3(Math.sin(this.yaw), 0, Math.cos(this.yaw));
    const right = new Vector3(Math.cos(this.yaw), 0, -Math.sin(this.yaw));
    this.movePlayerCollider(forward.scale(movement.forward).addInPlace(right.scale(movement.right)).scaleInPlace(4.6 * delta));
    if (this.targetLocked) {
      const toward = this.enemy.position.subtract(this.player.position);
      this.player.rotation.y = Math.atan2(toward.x, toward.z);
    } else this.player.rotation.y = this.yaw;
    if (!this.attack && this.playerAction === "idle") this.playActor(this.playerActor, "Run", true);
  }

  private movePlayerCollider(displacement: Vector3): void {
    const before = this.player.position.clone();
    this.player.moveWithCollisions(displacement);
    if (this.phase === "engaged") {
      const separation = this.player.position.subtract(this.enemy.position);
      if (separation.length() < BODY_DISTANCE) {
        this.player.position.copyFrom(this.enemy.position.add(separation.normalize().scale(BODY_DISTANCE)));
      }
    }
    const actual = Vector3.Distance(before, this.player.position);
    const blocked = displacement.length() > 0.002 && actual < displacement.length() * 0.55;
    if (blocked && !this.collisionActive) this.collisionCount += 1;
    this.collisionActive = blocked;
  }

  private updateCamera(delta: number): void {
    if (this.targetLocked && delta > 0) {
      const toward = this.enemy.position.subtract(this.player.position);
      const desired = Math.atan2(toward.x, toward.z);
      let difference = ((desired - this.yaw + Math.PI) % (Math.PI * 2)) - Math.PI;
      if (difference < -Math.PI) difference += Math.PI * 2;
      this.yaw += difference * Math.min(1, delta * 0.72);
    }
    const distance = 5.4 * Math.cos(this.pitch);
    const target = this.player.position.add(new Vector3(0, 0.65, 0));
    this.camera.position.copyFrom(target.add(new Vector3(
      -Math.sin(this.yaw) * distance,
      2.15 + 5.4 * Math.sin(this.pitch),
      -Math.cos(this.yaw) * distance,
    )));
    this.camera.setTarget(this.targetLocked
      ? Vector3.Lerp(target, this.enemy.position.add(new Vector3(0, 0.8, 0)), 0.32)
      : target.add(new Vector3(0, 0.25, 0)));
  }

  private updateRoute(): void {
    const next = nextCheckpoint(this.player.position, this.checkpoint);
    if (next === this.checkpoint || (next === "complete" && this.phase !== "victory")) return;
    this.checkpoint = next;
    if (next === "bend") this.elements.objective.textContent = "Good. Turn toward the village square";
    else if (next === "square" && this.phase === "approach") this.elements.objective.textContent = "A sword bandit guards the village gate";
    else if (next === "complete") {
      this.elements.status.textContent = "Route complete";
      this.elements.objective.textContent = "The combat path through Albion is proven";
      this.elements.completion.hidden = false;
    }
  }

  private updateEffects(delta: number): void {
    this.targetMarker.position.copyFrom(this.enemy.position.add(new Vector3(0, 2.35, 0)));
    this.targetMarker.rotation.y += delta * 2;
    this.telegraphRing.position.x = this.enemy.position.x;
    this.telegraphRing.position.z = this.enemy.position.z;
    if (this.impactElapsed > 0) {
      this.impactElapsed = Math.max(0, this.impactElapsed - delta);
      this.impactFlash.isVisible = this.impactElapsed > 0;
      this.impactFlash.scaling.setAll(1 + (0.16 - this.impactElapsed) * 5);
    }
    if (this.feedbackElapsed > 0) {
      this.feedbackElapsed = Math.max(0, this.feedbackElapsed - delta);
      if (this.feedbackElapsed === 0) this.elements.combatFeedback.hidden = true;
    }
  }

  private updateHud(): void {
    this.elements.healthFill.style.width = `${this.playerHealth}%`;
    this.elements.staminaFill.style.width = `${this.playerStamina}%`;
    this.elements.healthValue.value = `${Math.ceil(this.playerHealth)} / 100`;
    this.elements.staminaValue.value = `${Math.ceil(this.playerStamina)} / 100`;
    const controlsDisabled = this.paused || this.phase === "defeat";
    this.elements.attack.disabled = controlsDisabled;
    this.elements.block.disabled = controlsDisabled;
    this.elements.dodge.disabled = controlsDisabled || this.dodgeCooldown > 0;
    this.elements.enemyWidget.hidden = !this.targetLocked;
    this.elements.enemyHealthFill.style.width = `${this.enemyHealth}%`;
    if (this.targetLocked) {
      const viewport = this.camera.viewport.toGlobal(this.engine.getRenderWidth(), this.engine.getRenderHeight());
      const point = Vector3.Project(this.enemy.position.add(new Vector3(0, 2.15, 0)), Matrix.IdentityReadOnly, this.scene.getTransformMatrix(), viewport);
      this.elements.enemyWidget.style.left = `${point.x / this.engine.getRenderWidth() * 100}%`;
      this.elements.enemyWidget.style.top = `${point.y / this.engine.getRenderHeight() * 100}%`;
    }
  }

  private showFeedback(message: string, tone: string): void {
    this.elements.combatFeedback.value = message;
    this.elements.combatFeedback.dataset.tone = tone;
    this.elements.combatFeedback.hidden = false;
    this.feedbackElapsed = 1.05;
  }

  private flashImpact(position: Vector3): void {
    this.impactFlash.position.copyFrom(position);
    this.impactFlash.scaling.setAll(1);
    this.impactFlash.isVisible = true;
    this.impactElapsed = 0.16;
  }

  private damageNumber(amount: number, player: boolean): void {
    const value = document.createElement("output");
    value.className = `damage-number ${player ? "player-damage" : "enemy-damage"}`;
    value.value = `-${amount}`;
    value.style.left = player ? "50%" : this.elements.enemyWidget.style.left;
    value.style.top = player ? "42%" : this.elements.enemyWidget.style.top;
    this.elements.canvas.parentElement?.append(value);
    window.setTimeout(() => value.remove(), 720);
  }

  private unlockAudio(): void {
    this.audioContext ??= new AudioContext();
    if (this.audioContext.state === "suspended") void this.audioContext.resume();
  }

  private playTone(frequency: number, duration: number, type: OscillatorType): void {
    if (!this.audioContext || this.audioContext.state !== "running") return;
    const oscillator = this.audioContext.createOscillator();
    const gain = this.audioContext.createGain();
    oscillator.frequency.value = frequency;
    oscillator.type = type;
    gain.gain.setValueAtTime(0.035, this.audioContext.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, this.audioContext.currentTime + duration);
    oscillator.connect(gain).connect(this.audioContext.destination);
    oscillator.start();
    oscillator.stop(this.audioContext.currentTime + duration);
  }

  private async loadActors(): Promise<void> {
    const rootUrl = new URL("./assets/quaternius-rpg/", window.location.href).toString();
    try {
      const [monk, warrior] = await Promise.all([
        ImportMeshAsync(`${rootUrl}Monk.glb`, this.scene),
        ImportMeshAsync(`${rootUrl}Warrior.glb`, this.scene),
      ]);
      this.playerActor = this.bindActor(monk.meshes, monk.animationGroups, this.player, "bio-visual");
      this.enemyActor = this.bindActor(warrior.meshes, warrior.animationGroups, this.enemy, "bandit-visual");
      this.playActor(this.playerActor, "Idle", true);
      this.playActor(this.enemyActor, "Idle_Attacking", true);
      this.player.visibility = 0;
      this.enemy.visibility = 0;
      this.assetsReady = true;
    } catch (error) {
      this.runtimeErrors.push(`temporary character assets failed safely: ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  private bindActor(meshes: AbstractMesh[], groups: AnimationGroup[], collider: Mesh, prefix: string): LoadedActor {
    for (const [index, mesh] of meshes.filter((value) => !value.parent).entries()) {
      mesh.name = `${prefix}-${index}`;
      mesh.parent = collider;
      mesh.position.set(0, -0.9, 0);
      mesh.rotationQuaternion = null;
      mesh.rotation.y = Math.PI;
      mesh.isPickable = false;
    }
    return { animations: new Map(groups.map((group) => [group.name, group])), current: null };
  }

  private playActor(actor: LoadedActor | null, name: string, loop: boolean): void {
    if (!actor || actor.current === name) return;
    for (const animation of actor.animations.values()) animation.stop();
    const animation = actor.animations.get(name) ?? actor.animations.get("Idle");
    animation?.start(loop, 1, animation.from, animation.to, false);
    actor.current = name;
  }

  private createCollider(name: string, x: number, z: number, material: StandardMaterial): Mesh {
    const collider = CreateCapsule(name, { height: 1.8, radius: 0.42 }, this.scene);
    collider.position.copyFromFloats(x, 0.9, z);
    collider.material = material;
    collider.ellipsoid.copyFromFloats(0.42, 0.9, 0.42);
    collider.checkCollisions = name === "bio";
    return collider;
  }

  private createPalette(): Palette {
    const make = (name: string, color: Color3, emissive = Color3.Black(), alpha = 1): StandardMaterial => {
      const value = new StandardMaterial(name, this.scene);
      value.diffuseColor = color;
      value.emissiveColor = emissive;
      value.specularColor = new Color3(0.08, 0.08, 0.08);
      value.alpha = alpha;
      return value;
    };
    make("telegraph-basic", new Color3(0.94, 0.76, 0.18), new Color3(0.35, 0.2, 0.02), 0.85);
    make("telegraph-heavy", new Color3(0.94, 0.28, 0.08), new Color3(0.45, 0.08, 0.01), 0.9);
    make("telegraph-area", new Color3(0.74, 0.12, 0.86), new Color3(0.34, 0.02, 0.48), 0.9);
    return {
      grass: make("moss-grass", new Color3(0.14, 0.25, 0.17)),
      road: make("old-road", new Color3(0.35, 0.31, 0.24)),
      stone: make("village-stone", new Color3(0.5, 0.48, 0.4)),
      timber: make("dark-timber", new Color3(0.19, 0.11, 0.07)),
      roof: make("slate-roof", new Color3(0.12, 0.17, 0.2)),
      marker: make("wayfinder", new Color3(0.18, 0.62, 0.82), new Color3(0.03, 0.28, 0.5)),
      bio: make("bio-cloak", new Color3(0.2, 0.46, 0.62)),
      enemy: make("bandit-red", new Color3(0.55, 0.12, 0.09)),
      impact: make("hit-flash", new Color3(1, 0.88, 0.3), new Color3(0.8, 0.35, 0.05), 0.86),
    };
  }

  private createLighting(): void {
    const sky = new HemisphericLight("sky-light", new Vector3(0, 1, 0), this.scene);
    sky.intensity = 0.72;
    sky.diffuse = new Color3(0.65, 0.78, 0.86);
    sky.groundColor = new Color3(0.15, 0.2, 0.14);
    const sun = new DirectionalLight("late-sun", new Vector3(-0.45, -1, 0.3), this.scene);
    sun.intensity = 1.25;
    sun.diffuse = new Color3(1, 0.73, 0.48);
  }

  private createVillage(palette: Palette): Mesh {
    const ground = CreateGround("village-ground", { width: 42, height: 42 }, this.scene);
    ground.material = palette.grass;
    ground.checkCollisions = true;
    const road = CreateBox("village-road", { width: 4.2, height: 0.06, depth: 25 }, this.scene);
    road.position.copyFromFloats(0, 0.04, 5.5);
    road.material = palette.road;
    const approach = CreateBox("approach-road", { width: 13, height: 0.065, depth: 3.5 }, this.scene);
    approach.position.copyFromFloats(3.8, 0.05, -5.3);
    approach.material = palette.road;
    const wall = CreateBox("blocked-shortcut", { width: 10.5, height: 2.8, depth: 0.75 }, this.scene);
    wall.position.copyFromFloats(0, 1.4, -2.4);
    wall.material = palette.stone;
    wall.checkCollisions = true;
    this.createHouse("west-cottage", -6.6, 3, 0.05, palette);
    this.createHouse("east-cottage", 6.5, 7.5, -0.08, palette);
    this.createHouse("square-hall", -6.8, 11, 0.04, palette, 1.2);
    for (const [index, [x, z]] of [[2.8, -7], [6.2, -3.8], [6.5, 2], [3.8, 7], [0, 10]].entries()) {
      const post = CreateCylinder(`lantern-${index}`, { height: 1.45, diameter: 0.12 }, this.scene);
      post.position.copyFromFloats(x, 0.72, z);
      post.material = palette.timber;
      const light = CreateSphere(`lantern-light-${index}`, { diameter: index === 4 ? 0.62 : 0.36 }, this.scene);
      light.position.copyFromFloats(x, 1.55, z);
      light.material = index === 4 ? palette.marker : palette.roof;
    }
    const gateLeft = CreateBox("destination-gate-left", { width: 0.45, height: 3.2, depth: 0.45 }, this.scene);
    gateLeft.position.copyFromFloats(-1.4, 1.6, 11.4);
    gateLeft.material = palette.marker;
    const gateRight = gateLeft.clone("destination-gate-right");
    gateRight.position.x = 1.4;
    const gateTop = CreateBox("destination-gate-top", { width: 3.25, height: 0.4, depth: 0.45 }, this.scene);
    gateTop.position.copyFromFloats(0, 3.05, 11.4);
    gateTop.material = palette.marker;
    const gate = CreateBox("combat-gate", { width: 2.35, height: 2.7, depth: 0.32 }, this.scene);
    gate.position.copyFromFloats(0, 1.35, 10.8);
    gate.material = palette.timber;
    gate.checkCollisions = true;
    return gate;
  }

  private createHouse(name: string, x: number, z: number, rotation: number, palette: Palette, scale = 1): void {
    const body = CreateBox(`${name}-body`, { width: 4.5 * scale, height: 3.1, depth: 4 * scale }, this.scene);
    body.position.copyFromFloats(x, 1.55, z);
    body.rotation.y = rotation;
    body.material = palette.stone;
    body.checkCollisions = true;
    const roof = CreateCylinder(`${name}-roof`, { diameter: 5.9 * scale, height: 2.1, tessellation: 4 }, this.scene);
    roof.position.copyFromFloats(x, 3.8, z);
    roof.rotation.y = Math.PI / 4 + rotation;
    roof.material = palette.roof;
  }
}

export type { GameElements };

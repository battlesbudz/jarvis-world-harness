import { FreeCamera } from "@babylonjs/core/Cameras/freeCamera.js";
import "@babylonjs/core/Collisions/collisionCoordinator.js";
import { Engine } from "@babylonjs/core/Engines/engine.js";
import { DirectionalLight } from "@babylonjs/core/Lights/directionalLight.js";
import { HemisphericLight } from "@babylonjs/core/Lights/hemisphericLight.js";
import { StandardMaterial } from "@babylonjs/core/Materials/standardMaterial.js";
import { Color3, Color4 } from "@babylonjs/core/Maths/math.color.js";
import { Vector3 } from "@babylonjs/core/Maths/math.vector.js";
import { CreateBox } from "@babylonjs/core/Meshes/Builders/boxBuilder.pure.js";
import { CreateCapsule } from "@babylonjs/core/Meshes/Builders/capsuleBuilder.pure.js";
import { CreateCylinder } from "@babylonjs/core/Meshes/Builders/cylinderBuilder.pure.js";
import { CreateGround } from "@babylonjs/core/Meshes/Builders/groundBuilder.pure.js";
import { CreateSphere } from "@babylonjs/core/Meshes/Builders/sphereBuilder.pure.js";
import { Mesh } from "@babylonjs/core/Meshes/mesh.js";
import { Scene } from "@babylonjs/core/scene.js";
import { createInputController, type InputController } from "./input";
import {
  H2_SEED,
  SPAWN,
  clamp,
  nextCheckpoint,
  roundedPosition,
  type GameSnapshot,
  type RouteCheckpoint,
} from "./gameState";

interface GameElements {
  canvas: HTMLCanvasElement;
  joystick: HTMLElement;
  joystickKnob: HTMLElement;
  reset: HTMLButtonElement;
  status: HTMLElement;
  objective: HTMLElement;
  completion: HTMLOutputElement;
  diagnostics: HTMLOutputElement;
}

interface MaterialPalette {
  grass: StandardMaterial;
  road: StandardMaterial;
  stone: StandardMaterial;
  timber: StandardMaterial;
  roof: StandardMaterial;
  marker: StandardMaterial;
  bio: StandardMaterial;
}

export class AlbionGame {
  private readonly engine: Engine;
  private readonly scene: Scene;
  private readonly camera: FreeCamera;
  private readonly player: Mesh;
  private readonly input: InputController;
  private yaw = 0;
  private pitch = 0.24;
  private resetId = 1;
  private checkpoint: RouteCheckpoint = "spawn";
  private collisionCount = 0;
  private collisionActive = false;
  private ready = false;
  private disposed = false;

  constructor(
    private readonly elements: GameElements,
    private readonly runtimeErrors: string[],
  ) {
    this.engine = new Engine(elements.canvas, true, {
      preserveDrawingBuffer: true,
      stencil: true,
      adaptToDeviceRatio: true,
    });
    this.scene = new Scene(this.engine);
    this.scene.clearColor = new Color4(0.055, 0.1, 0.12, 1);
    this.scene.collisionsEnabled = true;

    this.camera = new FreeCamera("bio-camera", new Vector3(0, 4, -18), this.scene);
    this.camera.minZ = 0.1;
    this.camera.fov = 0.9;
    this.camera.inputs.clear();

    const palette = this.createPalette();
    this.createLighting();
    this.createVillage(palette);
    this.player = this.createPlayer(palette.bio);
    this.input = createInputController(
      elements.canvas,
      elements.joystick,
      elements.joystickKnob,
      (deltaYaw, deltaPitch) => {
        this.yaw += deltaYaw;
        this.pitch = clamp(this.pitch + deltaPitch, -0.15, 0.65);
      },
    );

    this.elements.reset.addEventListener("click", this.reset);
    window.addEventListener("resize", this.resize);
    this.updateCamera();
    this.engine.runRenderLoop(this.frame);
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
      runtimeErrors: [...this.runtimeErrors],
    };
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.input.dispose();
    this.elements.reset.removeEventListener("click", this.reset);
    window.removeEventListener("resize", this.resize);
    this.engine.stopRenderLoop(this.frame);
    this.scene.dispose();
    this.engine.dispose();
  }

  private readonly resize = (): void => {
    this.engine.resize();
  };

  private readonly reset = (): void => {
    this.player.position.copyFromFloats(SPAWN.x, SPAWN.y, SPAWN.z);
    this.yaw = 0;
    this.pitch = 0.24;
    this.checkpoint = "spawn";
    this.collisionCount = 0;
    this.collisionActive = false;
    this.resetId += 1;
    this.elements.completion.hidden = true;
    this.elements.status.textContent = "Ready";
    this.elements.objective.textContent = "Follow the lanterns to the village square";
    this.updateCamera();
  };

  private readonly frame = (): void => {
    if (this.disposed) return;
    try {
      const deltaSeconds = Math.min(this.engine.getDeltaTime() / 1000, 0.05);
      this.movePlayer(deltaSeconds);
      this.updateCamera();
      this.updateRoute();
      this.scene.render();
      if (!this.ready) {
        this.ready = true;
        this.elements.status.textContent = "Ready";
        this.elements.objective.textContent = "Follow the lanterns to the village square";
        document.body.dataset.gameReady = "true";
      }
      if (!this.elements.diagnostics.hidden) {
        const position = this.player.position;
        this.elements.diagnostics.textContent = `${position.x.toFixed(2)}, ${position.z.toFixed(2)} · ${this.checkpoint}`;
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.runtimeErrors.push(message);
      this.elements.status.textContent = "Albion stopped safely";
      this.elements.objective.textContent = message;
      this.engine.stopRenderLoop(this.frame);
    }
  };

  private movePlayer(deltaSeconds: number): void {
    const movement = this.input.movement();
    if (movement.forward === 0 && movement.right === 0) {
      this.collisionActive = false;
      return;
    }
    const speed = new URLSearchParams(window.location.search).has("test") ? 8 : 4.6;
    const forward = new Vector3(Math.sin(this.yaw), 0, Math.cos(this.yaw));
    const right = new Vector3(Math.cos(this.yaw), 0, -Math.sin(this.yaw));
    const displacement = forward
      .scale(movement.forward)
      .addInPlace(right.scale(movement.right))
      .scaleInPlace(speed * deltaSeconds);
    const before = this.player.position.clone();
    this.player.moveWithCollisions(displacement);
    const actualDistance = Vector3.Distance(before, this.player.position);
    const blocked = displacement.length() > 0.002 && actualDistance < displacement.length() * 0.55;
    if (blocked && !this.collisionActive) {
      this.collisionCount += 1;
    }
    this.collisionActive = blocked;
  }

  private updateCamera(): void {
    const horizontalDistance = 5.4 * Math.cos(this.pitch);
    const target = this.player.position.add(new Vector3(0, 0.65, 0));
    this.camera.position.copyFrom(
      target.add(
        new Vector3(
          -Math.sin(this.yaw) * horizontalDistance,
          2.15 + 5.4 * Math.sin(this.pitch),
          -Math.cos(this.yaw) * horizontalDistance,
        ),
      ),
    );
    this.camera.setTarget(target.add(new Vector3(0, 0.25, 0)));
  }

  private updateRoute(): void {
    const next = nextCheckpoint(this.player.position, this.checkpoint);
    if (next === this.checkpoint) return;
    this.checkpoint = next;
    if (next === "bend") {
      this.elements.objective.textContent = "Good. Turn toward the village square";
    } else if (next === "square") {
      this.elements.objective.textContent = "Cross the square and reach the blue gate";
    } else if (next === "complete") {
      this.elements.status.textContent = "Route complete";
      this.elements.objective.textContent = "The first playable Albion path is proven";
      this.elements.completion.hidden = false;
    }
  }

  private createPalette(): MaterialPalette {
    const material = (name: string, color: Color3, emissive = Color3.Black()): StandardMaterial => {
      const value = new StandardMaterial(name, this.scene);
      value.diffuseColor = color;
      value.emissiveColor = emissive;
      value.specularColor = new Color3(0.08, 0.08, 0.08);
      return value;
    };
    return {
      grass: material("moss-grass", new Color3(0.14, 0.25, 0.17)),
      road: material("old-road", new Color3(0.35, 0.31, 0.24)),
      stone: material("village-stone", new Color3(0.5, 0.48, 0.4)),
      timber: material("dark-timber", new Color3(0.19, 0.11, 0.07)),
      roof: material("slate-roof", new Color3(0.12, 0.17, 0.2)),
      marker: material("wayfinder", new Color3(0.18, 0.62, 0.82), new Color3(0.03, 0.28, 0.5)),
      bio: material("bio-cloak", new Color3(0.2, 0.46, 0.62)),
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

  private createPlayer(material: StandardMaterial): Mesh {
    const player = CreateCapsule("bio", { height: 1.8, radius: 0.42 }, this.scene);
    player.position.copyFromFloats(SPAWN.x, SPAWN.y, SPAWN.z);
    player.material = material;
    player.ellipsoid.copyFromFloats(0.42, 0.9, 0.42);
    player.ellipsoidOffset.copyFromFloats(0, 0, 0);
    player.checkCollisions = true;
    return player;
  }

  private createVillage(palette: MaterialPalette): void {
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

    for (const [index, [x, z]] of [[2.8, -7], [6.2, -3.8], [6.5, 2], [3.8, 7], [0, 10]] .entries()) {
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
  }

  private createHouse(
    name: string,
    x: number,
    z: number,
    rotation: number,
    palette: MaterialPalette,
    scale = 1,
  ): void {
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

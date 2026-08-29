import { clamp, normalizeMovement, type MovementInput } from "./gameState";

export interface InputController {
  movement(): MovementInput;
  clear(): void;
  dispose(): void;
}

export function createInputController(
  canvas: HTMLCanvasElement,
  joystick: HTMLElement,
  knob: HTMLElement,
  onLook: (deltaYaw: number, deltaPitch: number) => void,
): InputController {
  const keys = new Set<string>();
  let joystickInput: MovementInput = { forward: 0, right: 0 };
  let joystickPointer: number | null = null;
  let lookPointer: number | null = null;
  let lookX = 0;
  let lookY = 0;

  const onKeyDown = (event: KeyboardEvent): void => {
    if (["KeyW", "KeyA", "KeyS", "KeyD", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(event.code)) {
      keys.add(event.code);
      event.preventDefault();
    }
  };
  const onKeyUp = (event: KeyboardEvent): void => {
    keys.delete(event.code);
  };

  const updateJoystick = (event: PointerEvent): void => {
    const bounds = joystick.getBoundingClientRect();
    const radius = bounds.width / 2;
    const x = clamp(event.clientX - (bounds.left + radius), -radius, radius);
    const y = clamp(event.clientY - (bounds.top + radius), -radius, radius);
    const distance = Math.hypot(x, y);
    const scale = distance > radius && distance > 0 ? radius / distance : 1;
    const scaledX = x * scale;
    const scaledY = y * scale;
    knob.style.transform = `translate(${scaledX}px, ${scaledY}px)`;
    joystickInput = normalizeMovement({ forward: -scaledY / radius, right: scaledX / radius });
  };
  const stopJoystick = (): void => {
    joystickPointer = null;
    joystickInput = { forward: 0, right: 0 };
    knob.style.transform = "translate(0, 0)";
  };
  const onJoystickDown = (event: PointerEvent): void => {
    joystickPointer = event.pointerId;
    joystick.setPointerCapture(event.pointerId);
    updateJoystick(event);
    event.preventDefault();
  };
  const onJoystickMove = (event: PointerEvent): void => {
    if (event.pointerId === joystickPointer) {
      updateJoystick(event);
      event.preventDefault();
    }
  };
  const onJoystickUp = (event: PointerEvent): void => {
    if (event.pointerId === joystickPointer) {
      stopJoystick();
    }
  };
  const onFocusLost = (): void => {
    keys.clear();
    stopJoystick();
    lookPointer = null;
  };
  const onVisibilityChange = (): void => {
    if (document.visibilityState === "hidden") onFocusLost();
  };

  const onLookDown = (event: PointerEvent): void => {
    if (event.button !== 0) return;
    lookPointer = event.pointerId;
    lookX = event.clientX;
    lookY = event.clientY;
    canvas.setPointerCapture(event.pointerId);
  };
  const onLookMove = (event: PointerEvent): void => {
    if (event.pointerId !== lookPointer) return;
    onLook((event.clientX - lookX) * 0.005, (event.clientY - lookY) * 0.0035);
    lookX = event.clientX;
    lookY = event.clientY;
  };
  const onLookUp = (event: PointerEvent): void => {
    if (event.pointerId === lookPointer) {
      lookPointer = null;
    }
  };

  window.addEventListener("keydown", onKeyDown, { passive: false });
  window.addEventListener("keyup", onKeyUp);
  window.addEventListener("blur", onFocusLost);
  document.addEventListener("visibilitychange", onVisibilityChange);
  joystick.addEventListener("pointerdown", onJoystickDown);
  joystick.addEventListener("pointermove", onJoystickMove);
  joystick.addEventListener("pointerup", onJoystickUp);
  joystick.addEventListener("pointercancel", onJoystickUp);
  canvas.addEventListener("pointerdown", onLookDown);
  canvas.addEventListener("pointermove", onLookMove);
  canvas.addEventListener("pointerup", onLookUp);
  canvas.addEventListener("pointercancel", onLookUp);

  return {
    movement(): MovementInput {
      const keyboard = {
        forward: Number(keys.has("KeyW") || keys.has("ArrowUp")) - Number(keys.has("KeyS") || keys.has("ArrowDown")),
        right: Number(keys.has("KeyD") || keys.has("ArrowRight")) - Number(keys.has("KeyA") || keys.has("ArrowLeft")),
      };
      return normalizeMovement({
        forward: clamp(keyboard.forward + joystickInput.forward, -1, 1),
        right: clamp(keyboard.right + joystickInput.right, -1, 1),
      });
    },
    clear(): void {
      onFocusLost();
    },
    dispose(): void {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      window.removeEventListener("blur", onFocusLost);
      document.removeEventListener("visibilitychange", onVisibilityChange);
      joystick.removeEventListener("pointerdown", onJoystickDown);
      joystick.removeEventListener("pointermove", onJoystickMove);
      joystick.removeEventListener("pointerup", onJoystickUp);
      joystick.removeEventListener("pointercancel", onJoystickUp);
      canvas.removeEventListener("pointerdown", onLookDown);
      canvas.removeEventListener("pointermove", onLookMove);
      canvas.removeEventListener("pointerup", onLookUp);
      canvas.removeEventListener("pointercancel", onLookUp);
    },
  };
}

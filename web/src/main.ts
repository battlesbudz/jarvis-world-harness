import "./style.css";
import { AlbionGame, type GameElements } from "./game";
import type { GameSnapshot } from "./gameState";

declare global {
  interface Window {
    __JARVIS_H2__: {
      snapshot: () => GameSnapshot;
    };
  }
}

function requiredElement<T extends HTMLElement>(id: string): T {
  const element = document.getElementById(id);
  if (!(element instanceof HTMLElement)) {
    throw new Error(`missing required element: ${id}`);
  }
  return element as T;
}

const runtimeErrors: string[] = [];
const recordError = (value: unknown): void => {
  const message = value instanceof Error ? value.message : String(value);
  runtimeErrors.push(message);
};
window.addEventListener("error", (event) => recordError(event.error ?? event.message));
window.addEventListener("unhandledrejection", (event) => recordError(event.reason));

try {
  const elements: GameElements = {
    canvas: requiredElement<HTMLCanvasElement>("game-canvas"),
    joystick: requiredElement("joystick"),
    joystickKnob: requiredElement("joystick-knob"),
    reset: requiredElement<HTMLButtonElement>("reset"),
    status: requiredElement("status"),
    objective: requiredElement("objective"),
    completion: requiredElement<HTMLOutputElement>("completion"),
    diagnostics: requiredElement<HTMLOutputElement>("diagnostics"),
  };
  elements.diagnostics.hidden = !new URLSearchParams(window.location.search).has("test");
  const game = new AlbionGame(elements, runtimeErrors);
  window.__JARVIS_H2__ = { snapshot: () => game.snapshot() };
  window.addEventListener("pagehide", (event) => {
    if (!event.persisted) game.dispose();
  });
} catch (error) {
  recordError(error);
  const status = document.getElementById("status");
  if (status) status.textContent = "Albion failed to load";
  throw error;
}

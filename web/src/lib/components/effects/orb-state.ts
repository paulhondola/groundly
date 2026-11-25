import type { RunKind, RunPhase } from "../../api/client";

export type OrbState =
  | "working"
  | "searching"
  | "solving"
  | "listening"
  | "connecting"
  | "weaving"
  | "composing"
  | "breathing"
  | "shaping";

export type OrbSize = 20 | 32 | 64;

export function orbStateFor(phase: RunPhase, kind: RunKind): OrbState {
  if (phase === "starting") {
    return "connecting";
  }
  if (phase === "reading") {
    return "searching";
  }
  if (phase === "writing") {
    return kind === "grade" ? "solving" : "composing";
  }
  return "working";
}

export function phaseLabelFor(phase: RunPhase, kind: RunKind, activeTool?: string | null): string {
  if (activeTool && activeTool.trim() !== "") {
    return activeTool;
  }
  if (phase === "starting") {
    return "Starting";
  }
  if (phase === "reading") {
    return "Reading course";
  }
  if (phase === "writing") {
    return kind === "grade" ? "Recording verdicts" : "Drafting questions";
  }
  return "Thinking";
}

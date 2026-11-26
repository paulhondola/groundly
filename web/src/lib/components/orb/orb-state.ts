import type { RunKind, RunPhase } from "../../api/client";
import type { OrbMode } from "./orb";

/** A page run's phase on the orb: a run that is starting is a host attaching. */
export function orbModeForRun(phase: RunPhase): OrbMode {
  if (phase === "starting") {
    return "listening";
  }
  return phase === "reading" ? "reading" : "writing";
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

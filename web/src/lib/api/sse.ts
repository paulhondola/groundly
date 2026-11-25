import type { RunPhase, ThoughtChannel } from "./client";

export interface SSEPhaseEvent {
  id: number;
  phase: RunPhase;
  active_tool: string | null;
}

export interface SSEDeltaEvent {
  id: number;
  channel: Exclude<ThoughtChannel, null>;
  text: string;
  reset: boolean;
}

export interface SSEStepEvent {
  id: number;
  step: string;
  refresh: boolean;
}

export interface EventSourceLike {
  onerror: ((ev: Event) => void) | null;
  addEventListener(type: string, listener: (ev: MessageEvent<string>) => void): void;
  close(): void;
}

let eventStreamFactory: ((url: string) => EventSourceLike | null) | null = null;

export function setEventStreamFactory(
  factory: ((url: string) => EventSourceLike | null) | null,
): void {
  eventStreamFactory = factory;
}

export function createEventStream(url: string): EventSourceLike | null {
  if (eventStreamFactory) {
    return eventStreamFactory(url);
  }
  if (typeof globalThis.EventSource === "undefined") {
    return null;
  }
  return new globalThis.EventSource(url) as unknown as EventSourceLike;
}

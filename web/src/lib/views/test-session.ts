import type { CheckResult, DrawnQuestion } from "../api/client";

export type AnswerMap = Record<number, string | number[]>;

export interface StoredTest {
  n: number;
  token: string;
  questions: DrawnQuestion[];
  answers: AnswerMap;
  current: number;
  /** Choice answers already checked. A checked answer is locked: the server does not enforce
   * it, so the page keeps the lock here, where a reload finds it again. */
  checked?: Record<number, CheckResult>;
}

/** "A", "B", … for an option's place in the order the student sees. */
export function optionLetter(index: number): string {
  return String.fromCharCode(65 + (index % 26));
}

export function storageKey(subject: string, topicId: number | null): string {
  return "groundly-test:" + subject + ":" + (topicId == null ? "all" : topicId);
}

/** Generate an attempt idempotency token, falling back off `crypto.randomUUID()` on plain HTTP. */
export function generateAttemptToken(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

export function shuffled<T>(items: readonly T[]): T[] {
  const out = items.slice();
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

export function isAnswered(q: DrawnQuestion, answer: string | number[] | undefined): boolean {
  if (q.kind === "open") {
    return typeof answer === "string" && answer.trim() !== "";
  }
  return Array.isArray(answer) && answer.length > 0;
}

export function loadTest(key: string): StoredTest | null {
  try {
    const raw = sessionStorage.getItem(key);
    if (!raw) {
      return null;
    }
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") {
      return null;
    }
    return parsed as StoredTest;
  } catch {
    return null;
  }
}

export function saveTest(key: string, test: StoredTest): void {
  try {
    sessionStorage.setItem(key, JSON.stringify(test));
  } catch {
    /* sessionStorage may be missing or full */
  }
}

export function dropTest(key: string): void {
  try {
    sessionStorage.removeItem(key);
  } catch {
    /* nothing kept */
  }
}

export function createTestPersister(key: string) {
  let dead = false;

  return {
    isDead(): boolean {
      return dead;
    },
    markDead(): void {
      dead = true;
      dropTest(key);
    },
    save(test: StoredTest): void {
      if (!dead) {
        saveTest(key, test);
      }
    },
  };
}

export function shouldIgnoreShortcut(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false;
  }
  const tag = target.tagName;
  if (tag === "TEXTAREA" || tag === "INPUT" || tag === "SELECT") {
    return true;
  }
  return target.isContentEditable || target.getAttribute("contenteditable") === "true";
}

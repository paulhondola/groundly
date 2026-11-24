import { beforeEach, describe, expect, it, vi } from "vite-plus/test";
import type { DrawnQuestion } from "../../api/client";
import {
  type StoredTest,
  createTestPersister,
  dropTest,
  isAnswered,
  loadTest,
  saveTest,
  shouldIgnoreShortcut,
  shuffled,
  storageKey,
} from "../test-session";

function makeTest(overrides: Partial<StoredTest> = {}): StoredTest {
  return {
    n: 10,
    token: "abc12345-token",
    questions: [
      {
        id: 1,
        topic_id: 7,
        kind: "single",
        stem: "Pick one",
        code: null,
        options: [
          { id: 10, text: "A" },
          { id: 11, text: "B" },
        ],
      },
    ],
    answers: {},
    current: 0,
    ...overrides,
  };
}

describe("storageKey", () => {
  it("builds groundly-test:<subject>:<topicId|all>", () => {
    expect(storageKey("PDSS", 7)).toBe("groundly-test:PDSS:7");
    expect(storageKey("PDSS", null)).toBe("groundly-test:PDSS:all");
  });
});

describe("createTestPersister", () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.restoreAllMocks();
  });

  it("drops storage on markDead and refuses all subsequent saves", () => {
    const key = storageKey("PDSS", 7);
    const persister = createTestPersister(key);
    const snapshot = makeTest();

    persister.save(snapshot);
    expect(loadTest(key)).toEqual(snapshot);

    persister.markDead();
    expect(persister.isDead()).toBe(true);
    expect(loadTest(key)).toBeNull();

    persister.save({ ...snapshot, current: 1 });
    expect(loadTest(key)).toBeNull();
  });

  it("serializes cleanly and swallows sessionStorage quota/availability errors", () => {
    const key = storageKey("PDSS", null);
    saveTest(key, makeTest());
    expect(loadTest(key)?.token).toBe("abc12345-token");

    dropTest(key);
    expect(loadTest(key)).toBeNull();

    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("QuotaExceededError");
    });
    expect(() => saveTest(key, makeTest())).not.toThrow();
  });
});

describe("isAnswered and shuffled", () => {
  it("checks open answers and choice arrays correctly", () => {
    const openQ: DrawnQuestion = {
      id: 1,
      topic_id: 2,
      kind: "open",
      stem: "Explain",
      code: null,
      options: [],
    };
    const singleQ: DrawnQuestion = {
      id: 2,
      topic_id: 2,
      kind: "single",
      stem: "Choose",
      code: null,
      options: [{ id: 1, text: "Yes" }],
    };

    expect(isAnswered(openQ, undefined)).toBe(false);
    expect(isAnswered(openQ, "   \n ")).toBe(false);
    expect(isAnswered(openQ, "Mutex")).toBe(true);
    expect(isAnswered(singleQ, [])).toBe(false);
    expect(isAnswered(singleQ, [1])).toBe(true);
  });

  it("shuffles without mutating input array", () => {
    const items = [1, 2, 3, 4, 5];
    const out = shuffled(items);
    expect(items).toEqual([1, 2, 3, 4, 5]);
    expect(out.slice().sort((a, b) => a - b)).toEqual([1, 2, 3, 4, 5]);
  });
});

describe("shouldIgnoreShortcut", () => {
  it("ignores keyboard shortcuts inside inputs, textareas, selects, and contentEditable elements", () => {
    const textarea = document.createElement("textarea");
    const input = document.createElement("input");
    const select = document.createElement("select");
    const editable = document.createElement("div");
    editable.setAttribute("contenteditable", "true");
    const button = document.createElement("button");

    expect(shouldIgnoreShortcut(textarea)).toBe(true);
    expect(shouldIgnoreShortcut(input)).toBe(true);
    expect(shouldIgnoreShortcut(select)).toBe(true);
    expect(shouldIgnoreShortcut(editable)).toBe(true);
    expect(shouldIgnoreShortcut(button)).toBe(false);
    expect(shouldIgnoreShortcut(document.body)).toBe(false);
    expect(shouldIgnoreShortcut(null)).toBe(false);
  });
});

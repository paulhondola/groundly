import { describe, expect, it } from "vite-plus/test";
import type { TopicNode, TopicSource } from "../../types";
import { dedupeSources, filterTree, findTopicById, writeButtonInfo } from "../tree-filter";

function makeNode(
  id: number,
  title: string,
  questions: number,
  subtopics: TopicNode[] = [],
  summary = "",
): TopicNode {
  return {
    id,
    title,
    summary,
    sources: [],
    subtopics,
    progress: {
      questions,
      answered: 0,
      recent: 0,
      recent_correct: 0,
      state: "not_started",
      subtopics: subtopics.length > 0 ? subtopics.length : null,
      subtopics_mastered: subtopics.length > 0 ? 0 : null,
    },
  };
}

describe("filterTree", () => {
  const tree: TopicNode[] = [
    makeNode(
      1,
      "Concurrency Primitives",
      0,
      [
        makeNode(2, "Mutexes and Locks", 4, [], "Mutual exclusion patterns"),
        makeNode(3, "Semaphores", 10, [], "Counting signals"),
      ],
      "Thread coordination",
    ),
    makeNode(4, "Memory Models", 10, [], "Sequentially consistent ordering"),
  ];

  it("returns full tree for blank or whitespace queries", () => {
    expect(filterTree(tree, "")).toEqual(tree);
    expect(filterTree(tree, "   \t  ")).toEqual(tree);
  });

  it("keeps parent with matching subtopics only when a subtopic matches", () => {
    const filtered = filterTree(tree, "mutex");
    expect(filtered).toHaveLength(1);
    expect(filtered[0].id).toBe(1);
    expect(filtered[0].subtopics.map((s) => s.id)).toEqual([2]);
  });

  it("keeps parent with all subtopics when parent title or summary matches", () => {
    const filtered = filterTree(tree, "COORDINATION");
    expect(filtered).toHaveLength(1);
    expect(filtered[0].id).toBe(1);
    expect(filtered[0].subtopics).toHaveLength(2);
  });

  it("returns empty list when no topics match", () => {
    expect(filterTree(tree, "distributed consensus")).toEqual([]);
  });
});

describe("findTopicById", () => {
  it("locates updated parent or subtopic node across silent mid-run reloads (Review Focus #4)", () => {
    const treeV1: TopicNode[] = [makeNode(1, "Big", 3, [makeNode(2, "Leaf", 3)])];
    const treeV2: TopicNode[] = [makeNode(1, "Big", 7, [makeNode(2, "Leaf", 7)])];

    expect(findTopicById(treeV1, null)).toBeNull();
    expect(findTopicById(treeV1, 999)).toBeNull();
    expect(findTopicById(treeV1, 1)?.progress?.questions).toBe(3);
    expect(findTopicById(treeV2, 2)?.progress?.questions).toBe(7);
  });
});

describe("writeButtonInfo", () => {
  it("computes Write CTA labels from host availability, target, and subtopic counts", () => {
    const thinLeaf = makeNode(2, "Leaf", 4);
    const fullLeaf = makeNode(3, "Full Leaf", 10);
    const parentWithThin = makeNode(1, "Parent", 14, [thinLeaf, fullLeaf]);
    const parentFull = makeNode(5, "Full Parent", 20, [fullLeaf, makeNode(6, "Leaf B", 12)]);

    expect(writeButtonInfo(false, 10, thinLeaf)).toBeNull();
    expect(writeButtonInfo(true, 10, thinLeaf)).toEqual({ label: "Write questions" });
    expect(writeButtonInfo(true, 10, fullLeaf)).toBeNull();
    expect(writeButtonInfo(true, 10, parentWithThin)).toEqual({
      label: "Write questions for its subtopics",
    });
    expect(writeButtonInfo(true, 10, parentFull)).toBeNull();
  });
});

describe("dedupeSources", () => {
  it("deduplicates PDF links on the same filename and page while preserving text chunks", () => {
    const base: Omit<TopicSource, "chunk_id" | "text" | "pdf"> = {
      material_id: 1,
      filename: "slides.pdf",
      page: 3,
      heading_path: null,
    };
    const sources: TopicSource[] = [
      { ...base, chunk_id: 1, text: "A", pdf: true },
      { ...base, chunk_id: 2, text: "B", pdf: true },
      { ...base, chunk_id: 3, page: 4, text: "C", pdf: true },
      { ...base, chunk_id: 4, filename: "notes.txt", page: null, text: "D", pdf: false },
      { ...base, chunk_id: 5, filename: "notes.txt", page: null, text: "E", pdf: false },
    ];
    expect(dedupeSources(sources).map((s) => s.chunk_id)).toEqual([1, 3, 4, 5]);
  });
});
